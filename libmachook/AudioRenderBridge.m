#import <AudioToolbox/AudioToolbox.h>
#import <CoreFoundation/CoreFoundation.h>
#import <substrate.h>

#include <dlfcn.h>
#include <fcntl.h>
#include <mach-o/dyld.h>
#include <mach/mach_time.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#include "macws_audio_bridge.h"

typedef OSStatus (*MacWSAudioUnitSetPropertyFn)(
    AudioUnit, AudioUnitPropertyID, AudioUnitScope, AudioUnitElement,
    const void *, UInt32);
typedef OSStatus (*MacWSAudioUnitGetPropertyFn)(
    AudioUnit, AudioUnitPropertyID, AudioUnitScope, AudioUnitElement,
    void *, UInt32 *);

typedef struct {
    AURenderCallback original;
    void *originalContext;
    AudioStreamBasicDescription format;
    MacWSAudioRingHeader *ring;
    uint64_t producerToken;
    int16_t scratch[4096 * MACWS_AUDIO_CHANNELS];
} MacWSAudioRenderContext;

static MacWSAudioUnitSetPropertyFn gMacWSOriginalAudioUnitSetProperty;
static MacWSAudioUnitGetPropertyFn gMacWSAudioUnitGetProperty;
static _Atomic(bool) gMacWSAudioHookInstalled;
static _Atomic(uint32_t) gMacWSAudioProducerSerial = 1;
static uint64_t gMacWSAudioOwnerSilenceTicks;

static BOOL MacWSAudioContextOwnsRing(MacWSAudioRenderContext *context,
                                     uint16_t peak,
                                     uint64_t now) {
    if (!context || !context->ring || !context->producerToken) return NO;
    MacWSAudioRingHeader *header = context->ring;
    uint64_t owner = __atomic_load_n(
        &header->reserved[MACWS_AUDIO_RESERVED_OWNER_TOKEN],
        __ATOMIC_ACQUIRE);
    if (owner == context->producerToken) {
        if (peak >= 8) {
            __atomic_store_n(
                &header->reserved[MACWS_AUDIO_RESERVED_OWNER_LAST_AUDIBLE],
                now, __ATOMIC_RELEASE);
        }
        return YES;
    }
    // A context cannot take ownership before it has real signal. This keeps
    // Chromium's dormant/silent AudioUnits from interleaving zero blocks with
    // the active YouTube stream.
    if (peak < 8) return NO;
    uint64_t lastAudible = __atomic_load_n(
        &header->reserved[MACWS_AUDIO_RESERVED_OWNER_LAST_AUDIBLE],
        __ATOMIC_ACQUIRE);
    if (owner != 0 && lastAudible != 0 && now > lastAudible &&
        now - lastAudible <= gMacWSAudioOwnerSilenceTicks)
        return NO;
    uint64_t expected = owner;
    if (!__atomic_compare_exchange_n(
            &header->reserved[MACWS_AUDIO_RESERVED_OWNER_TOKEN],
            &expected, context->producerToken, false,
            __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE))
        return expected == context->producerToken;
    __atomic_store_n(
        &header->reserved[MACWS_AUDIO_RESERVED_OWNER_LAST_AUDIBLE],
        now, __ATOMIC_RELEASE);
    return YES;
}

static MacWSAudioRingHeader *MacWSMapAudioRing(void) {
    int descriptor = open(MACWS_AUDIO_RING_CHROOT_PATH,
                          O_RDWR | O_CLOEXEC);
    if (descriptor < 0) return NULL;
    const size_t bytes = (size_t)MacWSAudioRingBytes(
        MACWS_AUDIO_RING_CAPACITY_FRAMES);
    void *mapping = mmap(NULL, bytes, PROT_READ | PROT_WRITE,
                         MAP_SHARED, descriptor, 0);
    close(descriptor);
    if (mapping == MAP_FAILED) return NULL;
    MacWSAudioRingHeader *header = mapping;
    if (__atomic_load_n(&header->magic, __ATOMIC_ACQUIRE) !=
            MACWS_AUDIO_RING_MAGIC ||
        header->version != MACWS_AUDIO_RING_VERSION ||
        header->sampleRate != MACWS_AUDIO_SAMPLE_RATE ||
        header->channels != MACWS_AUDIO_CHANNELS ||
        header->capacityFrames != MACWS_AUDIO_RING_CAPACITY_FRAMES) {
        munmap(mapping, bytes);
        return NULL;
    }
    return header;
}

static float MacWSReadAudioSample(const AudioBufferList *buffers,
                                  const AudioStreamBasicDescription *format,
                                  UInt32 frame, UInt32 channel) {
    if (!buffers || !format || buffers->mNumberBuffers == 0) return 0.0f;
    const bool nonInterleaved =
        (format->mFormatFlags & kAudioFormatFlagIsNonInterleaved) != 0;
    const UInt32 bufferIndex = nonInterleaved &&
            channel < buffers->mNumberBuffers ? channel : 0;
    const AudioBuffer *buffer = &buffers->mBuffers[bufferIndex];
    if (!buffer->mData) return 0.0f;
    const UInt32 channels = nonInterleaved ? 1 :
        (format->mChannelsPerFrame ?: buffer->mNumberChannels ?: 1);
    const UInt64 sampleIndex = (UInt64)frame * channels +
        (nonInterleaved ? 0 : channel % channels);
    const UInt32 bytesPerSample = format->mBitsPerChannel / 8;
    if (bytesPerSample == 0 ||
        (sampleIndex + 1) * bytesPerSample > buffer->mDataByteSize)
        return 0.0f;
    const uint8_t *source = buffer->mData;
    if ((format->mFormatFlags & kAudioFormatFlagIsFloat) &&
        format->mBitsPerChannel == 32) {
        return ((const float *)source)[sampleIndex];
    }
    if ((format->mFormatFlags & kAudioFormatFlagIsSignedInteger) &&
        format->mBitsPerChannel == 16) {
        return ((const int16_t *)source)[sampleIndex] / 32768.0f;
    }
    if ((format->mFormatFlags & kAudioFormatFlagIsSignedInteger) &&
        format->mBitsPerChannel == 32) {
        return ((const int32_t *)source)[sampleIndex] / 2147483648.0f;
    }
    return 0.0f;
}

static void MacWSPublishAudio(MacWSAudioRenderContext *context,
                              const AudioBufferList *buffers,
                              UInt32 sourceFrames) {
    if (!context || !buffers || sourceFrames == 0 ||
        context->format.mFormatID != kAudioFormatLinearPCM)
        return;
    if (!context->ring) context->ring = MacWSMapAudioRing();
    MacWSAudioRingHeader *header = context->ring;
    if (!header) return;

    double sourceRate = context->format.mSampleRate;
    if (sourceRate < 1.0) sourceRate = MACWS_AUDIO_SAMPLE_RATE;
    UInt32 outputFrames = (UInt32)(
        sourceFrames * (double)MACWS_AUDIO_SAMPLE_RATE / sourceRate + 0.5);
    if (outputFrames > 4096) outputFrames = 4096;
    uint16_t peak = 0;
    for (UInt32 outputFrame = 0; outputFrame < outputFrames; outputFrame++) {
        UInt32 sourceFrame = (UInt32)(
            outputFrame * sourceRate / (double)MACWS_AUDIO_SAMPLE_RATE);
        if (sourceFrame >= sourceFrames) sourceFrame = sourceFrames - 1;
        for (UInt32 channel = 0; channel < MACWS_AUDIO_CHANNELS; channel++) {
            UInt32 sourceChannel = context->format.mChannelsPerFrame > 1
                ? channel : 0;
            float value = MacWSReadAudioSample(
                buffers, &context->format, sourceFrame, sourceChannel);
            if (value > 1.0f) value = 1.0f;
            if (value < -1.0f) value = -1.0f;
            int16_t converted = (int16_t)(value * 32767.0f);
            context->scratch[outputFrame * MACWS_AUDIO_CHANNELS + channel] =
                converted;
            uint16_t magnitude = converted == INT16_MIN ? 32768 :
                (uint16_t)(converted < 0 ? -converted : converted);
            if (magnitude > peak) peak = magnitude;
        }
    }

    uint64_t now = mach_continuous_time();
    if (!MacWSAudioContextOwnsRing(context, peak, now)) return;

    // Audio render callbacks must never wait behind another process. Drop a
    // single quantum on contention; the next callback arrives in a few ms.
    if (__atomic_exchange_n(
            &header->reserved[MACWS_AUDIO_RESERVED_WRITER_LOCK], 1,
            __ATOMIC_ACQUIRE) != 0)
        return;

    const uint64_t capacity = header->capacityFrames;
    uint64_t writeFrame = __atomic_load_n(
        &header->writeFrame, __ATOMIC_RELAXED);
    uint64_t offset = writeFrame % capacity;
    uint64_t firstFrames = capacity - offset;
    if (firstFrames > outputFrames) firstFrames = outputFrames;
    int16_t *ringSamples = (int16_t *)(header + 1);
    size_t firstSamples = (size_t)firstFrames * MACWS_AUDIO_CHANNELS;
    memcpy(ringSamples + offset * MACWS_AUDIO_CHANNELS, context->scratch,
           firstSamples * sizeof(*ringSamples));
    UInt32 remaining = outputFrames - (UInt32)firstFrames;
    if (remaining) {
        memcpy(ringSamples, context->scratch + firstSamples,
               (size_t)remaining * MACWS_AUDIO_CHANNELS *
                   sizeof(*ringSamples));
    }
    if (peak >= 8) {
        __atomic_store_n(&header->lastAudibleMachTime,
                         now, __ATOMIC_RELEASE);
    }
    __atomic_add_fetch(&header->callbackCount, 1, __ATOMIC_RELAXED);
    __atomic_store_n(&header->writeFrame, writeFrame + outputFrames,
                     __ATOMIC_RELEASE);
    __atomic_store_n(
        &header->reserved[MACWS_AUDIO_RESERVED_WRITER_LOCK], 0,
        __ATOMIC_RELEASE);
}

static OSStatus MacWSAudioRenderCallback(
        void *reference, AudioUnitRenderActionFlags *flags,
        const AudioTimeStamp *timestamp, UInt32 bus, UInt32 frames,
        AudioBufferList *buffers) {
    MacWSAudioRenderContext *context = reference;
    OSStatus status = context && context->original
        ? context->original(context->originalContext, flags, timestamp, bus,
                            frames, buffers)
        : kAudio_ParamError;
    if (status == noErr && buffers) MacWSPublishAudio(context, buffers, frames);
    return status;
}

static OSStatus MacWSAudioUnitSetProperty(
        AudioUnit unit, AudioUnitPropertyID property,
        AudioUnitScope scope, AudioUnitElement element,
        const void *data, UInt32 dataSize) {
    if (!gMacWSOriginalAudioUnitSetProperty) return kAudio_ParamError;
    if (property != kAudioUnitProperty_SetRenderCallback ||
        scope != kAudioUnitScope_Input || !data ||
        dataSize != sizeof(AURenderCallbackStruct)) {
        return gMacWSOriginalAudioUnitSetProperty(
            unit, property, scope, element, data, dataSize);
    }
    const AURenderCallbackStruct *callback = data;
    if (!callback->inputProc) {
        return gMacWSOriginalAudioUnitSetProperty(
            unit, property, scope, element, data, dataSize);
    }
    // Publish only the final output unit. Enabling the bridge for every
    // application must not also publish an effect/generator's intermediate
    // callback and race the real output stream for the shared ring.
    AudioComponent component = AudioComponentInstanceGetComponent(unit);
    AudioComponentDescription description = {0};
    if (!component ||
        AudioComponentGetDescription(component, &description) != noErr ||
        description.componentType != kAudioUnitType_Output ||
        description.componentSubType == kAudioUnitSubType_GenericOutput) {
        return gMacWSOriginalAudioUnitSetProperty(
            unit, property, scope, element, data, dataSize);
    }
    MacWSAudioRenderContext *context = calloc(1, sizeof(*context));
    if (!context) {
        return gMacWSOriginalAudioUnitSetProperty(
            unit, property, scope, element, data, dataSize);
    }
    context->original = callback->inputProc;
    context->originalContext = callback->inputProcRefCon;
    context->producerToken = ((uint64_t)(uint32_t)getpid() << 32) |
        atomic_fetch_add_explicit(&gMacWSAudioProducerSerial, 1,
                                  memory_order_relaxed);
    UInt32 formatSize = sizeof(context->format);
    if (!gMacWSAudioUnitGetProperty ||
        gMacWSAudioUnitGetProperty(
            unit, kAudioUnitProperty_StreamFormat, scope, element,
            &context->format, &formatSize) != noErr) {
        free(context);
        return gMacWSOriginalAudioUnitSetProperty(
            unit, property, scope, element, data, dataSize);
    }
    AURenderCallbackStruct wrapped = {
        .inputProc = MacWSAudioRenderCallback,
        .inputProcRefCon = context,
    };
    OSStatus status = gMacWSOriginalAudioUnitSetProperty(
        unit, property, scope, element, &wrapped, sizeof(wrapped));
    if (status != noErr) free(context);
    return status;
}

void MacWSInstallAudioRenderBridge(void) {
    bool expected = false;
    if (!atomic_compare_exchange_strong(
            &gMacWSAudioHookInstalled, &expected, true))
        return;
    void *setProperty = dlsym(RTLD_DEFAULT, "AudioUnitSetProperty");
    gMacWSAudioUnitGetProperty = (MacWSAudioUnitGetPropertyFn)
        dlsym(RTLD_DEFAULT, "AudioUnitGetProperty");
    if (!setProperty || !gMacWSAudioUnitGetProperty) {
        atomic_store(&gMacWSAudioHookInstalled, false);
        return;
    }
    MSHookFunction(setProperty, (void *)MacWSAudioUnitSetProperty,
                   (void **)&gMacWSOriginalAudioUnitSetProperty);
}

__attribute__((constructor)) static void MacWSInitializeAudioRenderBridge(void) {
    // Audio output is a production capability, including applications
    // launched from Finder/Terminal rather than the curated launcher. The
    // native output daemon consumes this ring outside the chroot; the two
    // macOS audio catalog/HAL servers are not playback clients themselves.
    const char *program = getprogname();
    if (program && (strcmp(program, "coreaudiod") == 0 ||
                    strcmp(program, "AudioComponentRegistrar") == 0))
        return;
    mach_timebase_info_data_t timebase = {0};
    if (mach_timebase_info(&timebase) == KERN_SUCCESS &&
        timebase.numer != 0) {
        long double ticks =
            (long double)MACWS_AUDIO_OWNER_SILENCE_NANOSECONDS *
            timebase.denom / timebase.numer;
        // The interval is fixed at 500 ms and mach timebase ratios are small;
        // its tick representation is therefore far below UINT64_MAX.
        gMacWSAudioOwnerSilenceTicks = (uint64_t)ticks;
    }
    if (gMacWSAudioOwnerSilenceTicks == 0)
        gMacWSAudioOwnerSilenceTicks =
            MACWS_AUDIO_OWNER_SILENCE_NANOSECONDS;
    MacWSInstallAudioRenderBridge();
}

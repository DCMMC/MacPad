// Bounded end-to-end client probe. Run inside the macOS chroot without any
// MACWS_AUDIO_* environment variables. The real output component must open,
// invoke its callback and publish nonzero samples through the production
// bridge. Does not replace devices, change preferences or restart services.
// Build: xcrun clang -arch arm64 -mmacosx-version-min=13.0 -Iinclude \
//   -framework AudioToolbox -framework CoreFoundation \
//   misc/macws_audio_smoke.c -o /tmp/macws_audio_smoke
#include <AudioToolbox/AudioToolbox.h>
#include <CoreFoundation/CoreFoundation.h>
#include <fcntl.h>
#include <math.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#include "macws_audio_bridge.h"

typedef struct {
    uint64_t frame;
    _Atomic(uint64_t) callbacks;
} ToneState;

static OSStatus RenderTone(void *reference, AudioUnitRenderActionFlags *flags,
                           const AudioTimeStamp *timestamp, UInt32 bus,
                           UInt32 frames, AudioBufferList *buffers) {
    (void)flags;
    (void)timestamp;
    (void)bus;
    ToneState *state = reference;
    for (UInt32 buffer = 0; buffer < buffers->mNumberBuffers; buffer++) {
        float *samples = buffers->mBuffers[buffer].mData;
        if (!samples || buffers->mBuffers[buffer].mDataByteSize <
                            frames * sizeof(*samples))
            return kAudio_ParamError;
        for (UInt32 frame = 0; frame < frames; frame++) {
            samples[frame] = 0.025f * sinf((float)(
                (state->frame + frame) % MACWS_AUDIO_SAMPLE_RATE) *
                (2.0f * (float)M_PI * 440.0f / MACWS_AUDIO_SAMPLE_RATE));
        }
    }
    state->frame += frames;
    atomic_fetch_add_explicit(&state->callbacks, 1, memory_order_relaxed);
    return noErr;
}

static OSStatus CreateToneUnit(OSType subtype, ToneState *state,
                                AudioUnit *unit) {
    AudioComponentDescription description = {
        .componentType = kAudioUnitType_Output,
        .componentSubType = subtype,
        .componentManufacturer = kAudioUnitManufacturer_Apple,
    };
    AudioComponent component = AudioComponentFindNext(NULL, &description);
    OSStatus status = component ? AudioComponentInstanceNew(component, unit)
                               : kAudio_ParamError;
    printf("subtype=%08x component=%p instance-status=%d\n", (unsigned)subtype,
           component, (int)status);
    if (status != noErr) return status;
    AudioStreamBasicDescription format = {
        .mSampleRate = MACWS_AUDIO_SAMPLE_RATE,
        .mFormatID = kAudioFormatLinearPCM,
        .mFormatFlags = kAudioFormatFlagIsFloat |
                        kAudioFormatFlagIsPacked |
                        kAudioFormatFlagIsNonInterleaved,
        .mBytesPerPacket = sizeof(float),
        .mFramesPerPacket = 1,
        .mBytesPerFrame = sizeof(float),
        .mChannelsPerFrame = MACWS_AUDIO_CHANNELS,
        .mBitsPerChannel = 32,
    };
    status = AudioUnitSetProperty(*unit, kAudioUnitProperty_StreamFormat,
                                  kAudioUnitScope_Input, 0, &format,
                                  sizeof(format));
    if (status != noErr) return status;
    AURenderCallbackStruct callback = {RenderTone, state};
    status = AudioUnitSetProperty(*unit, kAudioUnitProperty_SetRenderCallback,
                                  kAudioUnitScope_Input, 0, &callback,
                                  sizeof(callback));
    if (status != noErr) return status;
    return AudioUnitInitialize(*unit);
}

static bool CheckOfflineUnit(MacWSAudioRingHeader *ring) {
    AudioUnit unit = NULL;
    ToneState state = {0};
    OSStatus status = CreateToneUnit(kAudioUnitSubType_GenericOutput, &state,
                                     &unit);
    bool initialized = status == noErr;
    float samples[MACWS_AUDIO_CHANNELS][256] = {{0}};
    struct {
        UInt32 count;
        AudioBuffer buffers[MACWS_AUDIO_CHANNELS];
    } output = {MACWS_AUDIO_CHANNELS, {
        {1, sizeof(samples[0]), samples[0]},
        {1, sizeof(samples[1]), samples[1]},
    }};
    AudioTimeStamp timestamp = {.mFlags = kAudioTimeStampSampleTimeValid};
    AudioUnitRenderActionFlags flags = 0;
    if (initialized)
        status = AudioUnitRender(unit, &flags, &timestamp, 0, 256,
                                 (AudioBufferList *)&output);
    if (initialized) AudioUnitUninitialize(unit);
    if (unit) AudioComponentInstanceDispose(unit);
    uint64_t owner = __atomic_load_n(
        &ring->reserved[MACWS_AUDIO_RESERVED_OWNER_TOKEN], __ATOMIC_ACQUIRE);
    uint64_t callbacks = atomic_load_explicit(&state.callbacks,
                                             memory_order_relaxed);
    printf("offline-status=%d offline-callbacks=%llu owner-pid=%u\n",
           (int)status, (unsigned long long)callbacks, (unsigned)(owner >> 32));
    return status == noErr && callbacks > 0 &&
        (uint32_t)(owner >> 32) != (uint32_t)getpid();
}

int main(void) {
    int descriptor = open(MACWS_AUDIO_RING_CHROOT_PATH, O_RDONLY);
    if (descriptor < 0) {
        perror("audio ring");
        return 1;
    }
    struct stat info;
    if (fstat(descriptor, &info) != 0 ||
        info.st_size < (off_t)sizeof(MacWSAudioRingHeader)) {
        fprintf(stderr, "audio ring is incomplete\n");
        close(descriptor);
        return 1;
    }
    MacWSAudioRingHeader *ring = mmap(NULL, sizeof(*ring), PROT_READ,
                                     MAP_SHARED, descriptor, 0);
    close(descriptor);
    if (ring == MAP_FAILED) {
        perror("audio ring map");
        return 1;
    }
    if (ring->magic != MACWS_AUDIO_RING_MAGIC ||
        ring->version != MACWS_AUDIO_RING_VERSION) {
        fprintf(stderr, "audio ring has incompatible header\n");
        munmap(ring, sizeof(*ring));
        return 1;
    }
    if (!CheckOfflineUnit(ring)) {
        fprintf(stderr, "offline render must not publish to physical output\n");
        munmap(ring, sizeof(*ring));
        return 1;
    }
    uint64_t before = __atomic_load_n(&ring->writeFrame, __ATOMIC_ACQUIRE);
    AudioUnit unit = NULL;
    ToneState state = {0};
    OSStatus status = CreateToneUnit(kAudioUnitSubType_DefaultOutput, &state,
                                     &unit);
    bool initialized = false;
    bool started = false;
    if (status != noErr) goto done;
    initialized = true;
    status = AudioOutputUnitStart(unit);
    if (status != noErr) goto done;
    started = true;
    CFRunLoopRunInMode(kCFRunLoopDefaultMode, 1.5, false);
done:
    if (started) AudioOutputUnitStop(unit);
    if (initialized) AudioUnitUninitialize(unit);
    if (unit) AudioComponentInstanceDispose(unit);
    uint64_t after = __atomic_load_n(&ring->writeFrame, __ATOMIC_ACQUIRE);
    uint64_t owner = __atomic_load_n(
        &ring->reserved[MACWS_AUDIO_RESERVED_OWNER_TOKEN], __ATOMIC_ACQUIRE);
    uint64_t callbacks = atomic_load_explicit(&state.callbacks,
                                             memory_order_relaxed);
    printf("status=%d render-callbacks=%llu published-frames=%llu "
           "owner-pid=%u probe-pid=%u\n", (int)status,
           (unsigned long long)callbacks,
           (unsigned long long)(after >= before ? after - before : 0),
           (unsigned)(owner >> 32), (unsigned)getpid());
    munmap(ring, sizeof(*ring));
    return status == noErr && callbacks > 0 && after > before &&
        (uint32_t)(owner >> 32) == (uint32_t)getpid() ? 0 : 1;
}

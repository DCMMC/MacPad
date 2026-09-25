#include <AudioToolbox/AudioQueue.h>
#include <AudioToolbox/AudioSession.h>
#include <CoreFoundation/CoreFoundation.h>
#include <fcntl.h>
#include <mach/mach_time.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#include "macws_audio_bridge.h"

typedef struct {
    MacWSAudioRingHeader *header;
    int16_t *samples;
    uint64_t readFrame;
    uint64_t observedCallbackCount;
    uint64_t observedCallbackMachTime;
    uint64_t pendingStartFrame;
    uint64_t queueCallbackCount;
    int16_t lastSample[MACWS_AUDIO_CHANNELS];
    bool pendingStart;
    bool recoveringFromUnderrun;
    bool reportedQueueCallback;
    AudioQueueRef queue;
    bool acceptingCallbacks;
} OutputState;

static uint64_t RingMappingBytes(void) {
    return MacWSAudioRingBytes(MACWS_AUDIO_RING_CAPACITY_FRAMES);
}

static bool RingIsValid(const OutputState *state) {
    return state && state->header &&
        __atomic_load_n(&state->header->magic, __ATOMIC_ACQUIRE) ==
            MACWS_AUDIO_RING_MAGIC &&
        state->header->version == MACWS_AUDIO_RING_VERSION &&
        state->header->sampleRate == MACWS_AUDIO_SAMPLE_RATE &&
        state->header->channels == MACWS_AUDIO_CHANNELS &&
        state->header->capacityFrames == MACWS_AUDIO_RING_CAPACITY_FRAMES;
}

static void CopyFrames(OutputState *state, int16_t *destination,
                       uint32_t frameCount) {
    size_t outputBytes = (size_t)frameCount * MACWS_AUDIO_CHANNELS *
        sizeof(*destination);
    memset(destination, 0, outputBytes);
    if (!RingIsValid(state)) return;

    uint64_t writer = __atomic_load_n(
        &state->header->writeFrame, __ATOMIC_ACQUIRE);
    uint64_t capacity = state->header->capacityFrames;
    if (writer > state->readFrame + capacity) {
        state->readFrame = writer - MACWS_AUDIO_OUTPUT_PREROLL_FRAMES;
    }
    uint64_t available = writer > state->readFrame
        ? writer - state->readFrame : 0;
    uint32_t copiedFrames = available < frameCount
        ? (uint32_t)available : frameCount;
    uint64_t offset = state->readFrame % capacity;
    uint64_t firstFrames = capacity - offset;
    if (firstFrames > copiedFrames) firstFrames = copiedFrames;
    size_t firstSamples = (size_t)firstFrames * MACWS_AUDIO_CHANNELS;
    memcpy(destination,
           state->samples + offset * MACWS_AUDIO_CHANNELS,
           firstSamples * sizeof(*destination));
    uint32_t remaining = copiedFrames - (uint32_t)firstFrames;
    if (remaining) {
        memcpy(destination + firstSamples, state->samples,
               (size_t)remaining * MACWS_AUDIO_CHANNELS *
                   sizeof(*destination));
    }
    state->readFrame += copiedFrames;

    if (copiedFrames && state->recoveringFromUnderrun) {
        uint32_t rampFrames = copiedFrames < 64 ? copiedFrames : 64;
        for (uint32_t frame = 0; frame < rampFrames; frame++) {
            for (uint32_t channel = 0;
                 channel < MACWS_AUDIO_CHANNELS; channel++) {
                size_t sample = (size_t)frame * MACWS_AUDIO_CHANNELS + channel;
                destination[sample] = (int16_t)(
                    (int32_t)destination[sample] * (int32_t)(frame + 1) /
                    (int32_t)rampFrames);
            }
        }
        state->recoveringFromUnderrun = false;
    }
    if (copiedFrames) {
        for (uint32_t channel = 0; channel < MACWS_AUDIO_CHANNELS; channel++)
            state->lastSample[channel] = destination[
                (size_t)(copiedFrames - 1) * MACWS_AUDIO_CHANNELS + channel];
    }
    if (copiedFrames < frameCount) {
        uint32_t missing = frameCount - copiedFrames;
        uint32_t rampFrames = missing < 64 ? missing : 64;
        for (uint32_t frame = 0; frame < rampFrames; frame++) {
            for (uint32_t channel = 0;
                 channel < MACWS_AUDIO_CHANNELS; channel++) {
                size_t sample = (size_t)(copiedFrames + frame) *
                    MACWS_AUDIO_CHANNELS + channel;
                destination[sample] = (int16_t)(
                    (int32_t)state->lastSample[channel] *
                    (int32_t)(rampFrames - frame - 1) /
                    (int32_t)rampFrames);
            }
        }
        memset(state->lastSample, 0, sizeof(state->lastSample));
        state->recoveringFromUnderrun = true;
        __atomic_add_fetch(
            &state->header->reserved[MACWS_AUDIO_RESERVED_UNDERRUN_COUNT],
            1, __ATOMIC_RELAXED);
    }
}

static void OutputCallback(void *context, AudioQueueRef queue,
                           AudioQueueBufferRef buffer) {
    OutputState *state = context;
    __atomic_add_fetch(&state->queueCallbackCount, 1, __ATOMIC_RELAXED);
    uint32_t frameBytes =
        MACWS_AUDIO_CHANNELS * MACWS_AUDIO_BYTES_PER_SAMPLE;
    uint32_t frames = buffer->mAudioDataBytesCapacity / frameBytes;
    CopyFrames(state, buffer->mAudioData, frames);
    buffer->mAudioDataByteSize = frames * frameBytes;
    if (state->acceptingCallbacks) {
        OSStatus status = AudioQueueEnqueueBuffer(queue, buffer, 0, NULL);
        if (status != noErr) {
            dprintf(STDERR_FILENO,
                    "macwsaudiooutd: re-enqueue failed status=%d\n",
                    (int)status);
        }
    }
}

static OSStatus ConfigureAudioSession(void) {
    OSStatus status = AudioSessionInitialize(NULL, NULL, NULL, NULL);
    if (status != noErr && status != kAudioSessionAlreadyInitialized)
        return status;
    UInt32 category = kAudioSessionCategory_MediaPlayback;
    status = AudioSessionSetProperty(kAudioSessionProperty_AudioCategory,
                                     sizeof(category), &category);
    if (status == noErr) status = AudioSessionSetActive(true);
    return status;
}

static OSStatus StartOutput(OutputState *state) {
    AudioStreamBasicDescription format = {0};
    format.mSampleRate = MACWS_AUDIO_SAMPLE_RATE;
    format.mFormatID = kAudioFormatLinearPCM;
    format.mFormatFlags =
        kLinearPCMFormatFlagIsSignedInteger | kLinearPCMFormatFlagIsPacked;
    format.mBytesPerPacket =
        MACWS_AUDIO_CHANNELS * MACWS_AUDIO_BYTES_PER_SAMPLE;
    format.mFramesPerPacket = 1;
    format.mBytesPerFrame = format.mBytesPerPacket;
    format.mChannelsPerFrame = MACWS_AUDIO_CHANNELS;
    format.mBitsPerChannel = MACWS_AUDIO_BYTES_PER_SAMPLE * 8;

    OSStatus status = AudioQueueNewOutput(&format, OutputCallback, state, NULL,
                                          NULL, 0, &state->queue);
    if (status != noErr) {
        dprintf(STDERR_FILENO,
                "macwsaudiooutd: AudioQueueNewOutput status=%d\n",
                (int)status);
        return status;
    }
    uint64_t writer = __atomic_load_n(
        &state->header->writeFrame, __ATOMIC_ACQUIRE);
    state->readFrame = writer > MACWS_AUDIO_OUTPUT_PREROLL_FRAMES
        ? writer - MACWS_AUDIO_OUTPUT_PREROLL_FRAMES : 0;
    memset(state->lastSample, 0, sizeof(state->lastSample));
    state->recoveringFromUnderrun = false;
    __atomic_store_n(&state->queueCallbackCount, 0, __ATOMIC_RELAXED);
    state->reportedQueueCallback = false;
    state->acceptingCallbacks = true;
    const UInt32 framesPerBuffer = 960;
    const UInt32 bytes = framesPerBuffer * format.mBytesPerFrame;
    for (unsigned index = 0; index < 3; index++) {
        AudioQueueBufferRef buffer = NULL;
        status = AudioQueueAllocateBuffer(state->queue, bytes, &buffer);
        if (status != noErr || !buffer) {
            dprintf(STDERR_FILENO,
                    "macwsaudiooutd: AudioQueueAllocateBuffer[%u] "
                    "status=%d buffer=%p\n",
                    index, (int)status, buffer);
            break;
        }
        OutputCallback(state, state->queue, buffer);
    }
    if (status == noErr) {
        status = AudioQueueStart(state->queue, NULL);
        if (status != noErr) {
            dprintf(STDERR_FILENO,
                    "macwsaudiooutd: AudioQueueStart status=%d\n",
                    (int)status);
        }
    }
    if (status != noErr) {
        state->acceptingCallbacks = false;
        AudioQueueDispose(state->queue, true);
        state->queue = NULL;
    }
    return status;
}

static void StopOutput(OutputState *state) {
    if (!state->queue) return;
    state->acceptingCallbacks = false;
    AudioQueueStop(state->queue, true);
    AudioQueueDispose(state->queue, true);
    state->queue = NULL;
}

static bool MapRing(OutputState *state) {
    int descriptor = open(MACWS_AUDIO_RING_IOS_PATH,
                          O_CREAT | O_RDWR | O_CLOEXEC, 0660);
    if (descriptor < 0) return false;
    // macOS GUI applications launched for the login user run as uid/gid 501,
    // while this native iOS output job is installed from the root bootstrap.
    // Runtime-confirmed on iPad14,5: the old 0600 root:wheel ring made the
    // real uid-501 DefaultOutput probe fail at open(2) with EACCES before any
    // render callback could publish. Keep the transport private to root and
    // the login user; do not make the PCM ring world-writable.
    if (fchown(descriptor, 501, 501) != 0 ||
        fchmod(descriptor, 0660) != 0) {
        close(descriptor);
        return false;
    }
    uint64_t bytes = RingMappingBytes();
    if (ftruncate(descriptor, (off_t)bytes) != 0) {
        close(descriptor);
        return false;
    }
    void *mapping = mmap(NULL, (size_t)bytes, PROT_READ | PROT_WRITE, MAP_SHARED,
                         descriptor, 0);
    close(descriptor);
    if (mapping == MAP_FAILED) return false;
    state->header = mapping;
    state->samples = (int16_t *)(state->header + 1);
    if (__atomic_load_n(&state->header->magic, __ATOMIC_ACQUIRE) !=
            MACWS_AUDIO_RING_MAGIC) {
        memset(mapping, 0, (size_t)bytes);
        state->header->version = MACWS_AUDIO_RING_VERSION;
        state->header->sampleRate = MACWS_AUDIO_SAMPLE_RATE;
        state->header->channels = MACWS_AUDIO_CHANNELS;
        state->header->capacityFrames = MACWS_AUDIO_RING_CAPACITY_FRAMES;
        __atomic_store_n(&state->header->magic, MACWS_AUDIO_RING_MAGIC,
                         __ATOMIC_RELEASE);
    }
    if (!RingIsValid(state)) {
        munmap(mapping, (size_t)bytes);
        state->header = NULL;
        state->samples = NULL;
        return false;
    }
    return true;
}

int main(void) {
    OutputState state = {0};
    OSStatus sessionStatus = ConfigureAudioSession();
    if (sessionStatus != noErr) {
        dprintf(STDERR_FILENO,
                "macwsaudiooutd: audio session setup failed status=%d\n",
                (int)sessionStatus);
        return 2;
    }
    mach_timebase_info_data_t timebase = {0};
    mach_timebase_info(&timebase);
    const uint64_t silenceNanoseconds = 1500ULL * 1000ULL * 1000ULL;
    for (;;) {
        if (!state.header) {
            if (!MapRing(&state)) {
                usleep(100000);
                continue;
            }
            dprintf(STDERR_FILENO,
                    "macwsaudiooutd: ring connected rate=%u channels=%u\n",
                    MACWS_AUDIO_SAMPLE_RATE, MACWS_AUDIO_CHANNELS);
        }
        uint64_t audible = __atomic_load_n(
            &state.header->lastAudibleMachTime, __ATOMIC_ACQUIRE);
        uint64_t now = mach_continuous_time();
        uint64_t callbackCount = __atomic_load_n(
            &state.header->callbackCount, __ATOMIC_ACQUIRE);
        if (state.observedCallbackMachTime == 0 ||
            callbackCount != state.observedCallbackCount) {
            state.observedCallbackCount = callbackCount;
            state.observedCallbackMachTime = now;
        } else if (__atomic_load_n(
                       &state.header->reserved[
                           MACWS_AUDIO_RESERVED_WRITER_LOCK],
                                   __ATOMIC_ACQUIRE) != 0 &&
                   state.observedCallbackMachTime != 0) {
            // A renderer can terminate inside its realtime callback. Its
            // process-shared try-lock would otherwise remain set forever and
            // silence every later renderer. Reclaim it only after an entire
            // second without a completed callback; healthy audio advances
            // callbackCount every few milliseconds.
            uint64_t stalledTicks = now - state.observedCallbackMachTime;
            uint64_t stalledNanoseconds = timebase.denom
                ? stalledTicks * timebase.numer / timebase.denom : 0;
            if (stalledNanoseconds >= 1000ULL * 1000ULL * 1000ULL) {
                uint64_t locked = 1;
                (void)__atomic_compare_exchange_n(
                    &state.header->reserved[
                        MACWS_AUDIO_RESERVED_WRITER_LOCK],
                    &locked, 0, false,
                    __ATOMIC_RELEASE, __ATOMIC_RELAXED);
                state.observedCallbackMachTime = now;
            }
        }
        uint64_t elapsedTicks = now > audible ? now - audible : 0;
        uint64_t elapsedNanoseconds = timebase.denom
            ? elapsedTicks * timebase.numer / timebase.denom : UINT64_MAX;
        bool recentAudio = audible != 0 &&
            elapsedNanoseconds < silenceNanoseconds;
        if (recentAudio && !state.queue) {
            uint64_t writer = __atomic_load_n(
                &state.header->writeFrame, __ATOMIC_ACQUIRE);
            if (!state.pendingStart) {
                state.pendingStart = true;
                state.pendingStartFrame = writer;
            }
            if (writer >= state.pendingStartFrame &&
                writer - state.pendingStartFrame >=
                    MACWS_AUDIO_OUTPUT_PREROLL_FRAMES) {
                OSStatus status = StartOutput(&state);
                state.pendingStart = false;
                dprintf(STDERR_FILENO,
                        "macwsaudiooutd: output start status=%d preroll=%u\n",
                        (int)status, MACWS_AUDIO_OUTPUT_PREROLL_FRAMES);
            }
        } else if (!recentAudio && state.queue) {
            StopOutput(&state);
            dprintf(STDERR_FILENO, "macwsaudiooutd: output idle\n");
        } else if (!recentAudio) {
            state.pendingStart = false;
        }
        uint64_t queueCallbacks = __atomic_load_n(
            &state.queueCallbackCount, __ATOMIC_RELAXED);
        if (state.queue && !state.reportedQueueCallback &&
            queueCallbacks > 3) {
            // StartOutput fills exactly three buffers synchronously.  A fourth
            // invocation can therefore only come from AudioQueue's native
            // output thread after AudioQueueStart succeeded.
            state.reportedQueueCallback = true;
            dprintf(STDERR_FILENO,
                    "macwsaudiooutd: runtime-confirmed hardware callback "
                    "count=%llu read-frame=%llu\n",
                    (unsigned long long)queueCallbacks,
                    (unsigned long long)state.readFrame);
        }
        // AudioQueue owns the active render cadence. This control loop only
        // notices transitions between silence and playback, so 50 ms keeps
        // start latency imperceptible without waking an idle iPad 50 times/s.
        usleep(50000);
    }
}

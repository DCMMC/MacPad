#ifndef MACWS_AUDIO_BRIDGE_H
#define MACWS_AUDIO_BRIDGE_H

#include <stdint.h>

#define MACWS_AUDIO_RING_MAGIC 0x4d574152U
#define MACWS_AUDIO_RING_VERSION 1U
#define MACWS_AUDIO_SAMPLE_RATE 48000U
#define MACWS_AUDIO_CHANNELS 2U
#define MACWS_AUDIO_BYTES_PER_SAMPLE 2U
#define MACWS_AUDIO_RING_CAPACITY_FRAMES (MACWS_AUDIO_SAMPLE_RATE * 2U)
#define MACWS_AUDIO_OUTPUT_PREROLL_FRAMES (MACWS_AUDIO_SAMPLE_RATE / 10U)
#define MACWS_AUDIO_OWNER_SILENCE_NANOSECONDS (500ULL * 1000ULL * 1000ULL)

// reserved[] is part of the version-1 shared-memory ABI. Give each slot one
// stable cross-process meaning instead of letting independent bridge pieces
// reuse anonymous storage.
#define MACWS_AUDIO_RESERVED_WRITER_LOCK 0U
#define MACWS_AUDIO_RESERVED_OWNER_TOKEN 1U
#define MACWS_AUDIO_RESERVED_OWNER_LAST_AUDIBLE 2U
#define MACWS_AUDIO_RESERVED_UNDERRUN_COUNT 3U

#define MACWS_AUDIO_RING_CHROOT_PATH "/private/tmp/macws_audio_ring"
#define MACWS_AUDIO_RING_IOS_PATH \
    "/var/mnt/rootfs/private/tmp/macws_audio_ring"

typedef struct {
    uint32_t magic;
    uint32_t version;
    uint32_t sampleRate;
    uint32_t channels;
    uint64_t capacityFrames;
    uint64_t writeFrame;
    uint64_t lastAudibleMachTime;
    uint64_t callbackCount;
    uint64_t reserved[4];
} MacWSAudioRingHeader;

static inline uint64_t MacWSAudioRingBytes(uint64_t capacityFrames) {
    return sizeof(MacWSAudioRingHeader) +
        capacityFrames * MACWS_AUDIO_CHANNELS * MACWS_AUDIO_BYTES_PER_SAMPLE;
}

#endif

#ifndef MACWS_FINAL_COMPOSITE_PROTOCOL_H
#define MACWS_FINAL_COMPOSITE_PROTOCOL_H

#include <stdbool.h>
#include <mach/mach.h>
#include <stddef.h>
#include <stdint.h>

// WindowServer's coexist compositor already finishes each native AGX frame
// into a process-owned linear BGRA IOSurface.  VNC consumes a CPU copy of that
// final surface, which proves that it contains SkyLight effects absent from an
// exact-window DisplayStream: external shadows, backdrop materials and warped
// window animations.  Transfer the IOSurface right itself to macwsdisplayd so
// fullscreen Host presentation keeps those final pixels without RFB or a
// full-frame CPU upload.
#define MACWS_FINAL_COMPOSITE_MAGIC 0x4d574643u /* "MWFC" */
#define MACWS_FINAL_COMPOSITE_VERSION 1u
#define MACWS_FINAL_COMPOSITE_MACH_SERVICE \
    "com.macwsguide.display.final-composite"
#define MACWS_FINAL_COMPOSITE_MACH_MESSAGE_ID 0x4d574643
#define MACWS_FINAL_COMPOSITE_REPLAY_MACH_SERVICE \
    "com.macwsguide.windowserver.final-composite-replay"
#define MACWS_FINAL_COMPOSITE_REPLAY_MACH_MESSAGE_ID 0x4d574652

#define MACWS_FINAL_COMPOSITE_MAX_DIMENSION 16384u
#define MACWS_FINAL_COMPOSITE_MAX_BYTES_PER_ROW \
    (MACWS_FINAL_COMPOSITE_MAX_DIMENSION * 16u)
#define MACWS_FINAL_COMPOSITE_BGRA 0x42475241u
#define MACWS_FINAL_COMPOSITE_METAL_BGRA8_UNORM 80u

// The stripped iPhoneOS SDK used by the on-device Theos build omits
// servers/bootstrap.h even though these libSystem exports are present.
extern mach_port_t bootstrap_port;
extern kern_return_t bootstrap_look_up(
    mach_port_t bootstrapPort, const char *serviceName,
    mach_port_t *servicePort);
extern kern_return_t bootstrap_check_in(
    mach_port_t bootstrapPort, const char *serviceName,
    mach_port_t *receivePort);
#ifndef BOOTSTRAP_SUCCESS
#define BOOTSTRAP_SUCCESS KERN_SUCCESS
#endif

typedef struct __attribute__((packed)) {
    uint32_t magic;
    uint16_t version;
    uint16_t size;
    int32_t producerPID;
    uint32_t surfaceID;
    uint64_t sequence;
    uint64_t completionTime;
    uint32_t width;
    uint32_t height;
    uint32_t bytesPerRow;
    uint32_t ioSurfacePixelFormat;
    uint32_t metalPixelFormat;
    uint32_t reserved;
} MacWSFinalCompositeRecord;

typedef struct {
    mach_msg_header_t header;
    mach_msg_body_t body;
    mach_msg_port_descriptor_t surfacePort;
    MacWSFinalCompositeRecord record;
} MacWSFinalCompositeMachMessage;

// displayd is independently restartable.  A one-way producer push leaves a
// relaunched receiver on the exact-window fallback until WindowServer happens
// to submit another owned scanout.  This authenticated, payload-free request
// asks the live producer to snapshot its latest completed scanout again.  The
// response remains the normal surface message above, so there is only one
// ownership and validation path for final-composite frames.
#define MACWS_FINAL_COMPOSITE_REPLAY_MAGIC 0x4d574652u /* "MWFR" */
#define MACWS_FINAL_COMPOSITE_REPLAY_VERSION 1u
typedef struct __attribute__((packed)) {
    uint32_t magic;
    uint16_t version;
    uint16_t size;
    int32_t requesterPID;
    uint32_t reserved;
    // Earliest producer-side completion timestamp acceptable to the caller.
    // Startup uses 1 (latest available snapshot). Mutation catch-up uses the
    // earliest producer completion causally compatible with the catalog
    // observation. The receiver may subtract one runtime-measured, bounded
    // observation latency because displayd necessarily sees the topology
    // after the compositor command which created it has completed.
    uint64_t minimumCompletionTime;
} MacWSFinalCompositeReplayRecord;

typedef struct {
    mach_msg_header_t header;
    MacWSFinalCompositeReplayRecord record;
} MacWSFinalCompositeReplayMachMessage;

static inline bool MacWSFinalCompositeReplayRecordIsValid(
        const MacWSFinalCompositeReplayRecord *record, size_t byteCount) {
    return record && byteCount == sizeof(*record) &&
        record->magic == MACWS_FINAL_COMPOSITE_REPLAY_MAGIC &&
        record->version == MACWS_FINAL_COMPOSITE_REPLAY_VERSION &&
        record->size == sizeof(*record) && record->requesterPID > 1 &&
        record->reserved == 0 && record->minimumCompletionTime != 0;
}

static inline bool MacWSFinalCompositeRecordIsValid(
        const MacWSFinalCompositeRecord *record, size_t byteCount) {
    if (!record || byteCount != sizeof(*record) ||
        record->magic != MACWS_FINAL_COMPOSITE_MAGIC ||
        record->version != MACWS_FINAL_COMPOSITE_VERSION ||
        record->size != sizeof(*record) || record->producerPID <= 1 ||
        record->surfaceID == 0 || record->sequence == 0 ||
        record->completionTime == 0 || record->width == 0 ||
        record->height == 0 ||
        record->width > MACWS_FINAL_COMPOSITE_MAX_DIMENSION ||
        record->height > MACWS_FINAL_COMPOSITE_MAX_DIMENSION ||
        record->bytesPerRow > MACWS_FINAL_COMPOSITE_MAX_BYTES_PER_ROW ||
        record->ioSurfacePixelFormat != MACWS_FINAL_COMPOSITE_BGRA ||
        record->metalPixelFormat !=
            MACWS_FINAL_COMPOSITE_METAL_BGRA8_UNORM)
        return false;
    return record->bytesPerRow >= record->width * 4u;
}

#if defined(__cplusplus)
static_assert(sizeof(MacWSFinalCompositeRecord) == 56,
              "MacWS final composite record ABI");
#else
_Static_assert(sizeof(MacWSFinalCompositeRecord) == 56,
               "MacWS final composite record ABI");
#endif

#if defined(__cplusplus)
static_assert(sizeof(MacWSFinalCompositeReplayRecord) == 24,
              "MacWS final composite replay record ABI");
#else
_Static_assert(sizeof(MacWSFinalCompositeReplayRecord) == 24,
               "MacWS final composite replay record ABI");
#endif

#endif

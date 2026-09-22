#ifndef MACWS_DOCK_EXPOSE_NOTIFY_H
#define MACWS_DOCK_EXPOSE_NOTIFY_H

#include <stdbool.h>
#include <stdint.h>

// Dock owns App Expose's modal hit testing. Publish its live handler state
// through Darwin notify rather than a persistent flag file: Host must not
// route wheel records to the application painted beneath an Expose card.
#define MACWS_DOCK_EXPOSE_STATE_NAME "com.macwsguide.dock.expose.state"

static inline uint64_t MacWSDockExposeState(uint32_t writerPID,
                                            bool active) {
    return ((uint64_t)writerPID << 32) | (active ? UINT64_C(1) : 0);
}

static inline uint32_t MacWSDockExposeWriter(uint64_t state) {
    return (uint32_t)(state >> 32);
}

static inline bool MacWSDockExposeIsActive(uint64_t state) {
    return (state & UINT64_C(1)) != 0;
}

#endif

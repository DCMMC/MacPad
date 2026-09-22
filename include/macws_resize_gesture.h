#ifndef MACWS_RESIZE_GESTURE_H
#define MACWS_RESIZE_GESTURE_H

#include <stdint.h>

// The suffix is the exact FBS Scene ID, never an application identifier.
#define MACWS_RESIZE_GESTURE_NOTIFICATION_PREFIX "com.macwsguide.windowing.gesture."

// notifyd can outlive SpringBoard. Include the writer PID so an interrupted
// producer cannot leave a permanent active state after that process exits.
static inline uint64_t MacWSResizeGestureState(uint32_t writerPID, int active) {
    return ((uint64_t)writerPID << 32) | (active ? 1u : 0u);
}
static inline uint32_t MacWSResizeGestureWriter(uint64_t state) {
    return (uint32_t)(state >> 32);
}
static inline int MacWSResizeGestureIsActive(uint64_t state) {
    return MacWSResizeGestureWriter(state) > 1 && (uint32_t)state == 1u;
}

#endif

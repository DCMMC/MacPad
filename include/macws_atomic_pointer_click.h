#ifndef MACWS_ATOMIC_POINTER_CLICK_H
#define MACWS_ATOMIC_POINTER_CLICK_H

#include <stdbool.h>
#include <stdint.h>

// The caller retains the real event position in context. This transaction has
// no coordinate conversion, cursor relocation, modifier policy or held-state
// mutation. The poster forwards its original system-call result unchanged.
typedef int32_t (*MacWSAtomicPointerPost)(void *context, bool left, bool right);
typedef void (*MacWSAtomicPointerPause)(void *context, uint32_t microseconds);
typedef struct {
    int32_t downResult;
    int32_t upResult;
} MacWSAtomicPointerClickResult;

static inline MacWSAtomicPointerClickResult MacWSPostAtomicPointerClick(
        bool secondary, bool leftAlreadyDown, MacWSAtomicPointerPost post,
        MacWSAtomicPointerPause pause, void *context) {
    // Match TouchDown's existing button-free motion at the ACTUAL new point.
    // A click at a new position is not itself a mouseMoved event: leaving it
    // out can leave the old Dock hover alive after tapping another window.
    // Do not introduce extra motion into an already active drag.
    if (!leftAlreadyDown) {
        (void)post(context, false, false);
    }
    MacWSAtomicPointerClickResult result;
    result.downResult = post(context, !secondary, secondary);
    pause(context, 2000);
    result.upResult = post(context, false, false);
    return result;
}

#endif

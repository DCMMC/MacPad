#ifndef MACWS_POINTER_ACTIVATION_H
#define MACWS_POINTER_ACTIVATION_H

#include <math.h>
#include <stdbool.h>
#include "macws_host_protocol.h"

// Activation and native mouse-down must identify the same display point.
// Keep the producer's coordinates AND their extent together until inputd
// converts them to Quartz. Never pair already-converted logical coordinates
// with RFB pixel dimensions (at Retina scale that activates another window).
// The control record intentionally does not inherit a stale captured window,
// target PID, button flags or modifiers: WindowServer resolves its owner.
static inline bool MacWSGlobalActivationRecord(
        const MacWSInputRecord *pointer, double timestamp, uint32_t sequence,
        MacWSInputRecord *activation) {
    if (!pointer || !activation || sequence == 0 ||
        !isfinite(timestamp) || timestamp <= 0.0 ||
        !isfinite(pointer->x) || !isfinite(pointer->y) ||
        pointer->frameWidth == 0 || pointer->frameHeight == 0 ||
        pointer->frameWidth > 8192 || pointer->frameHeight > 8192)
        return false;
    float x = fminf(fmaxf(pointer->x, 0.0f), pointer->frameWidth - 1.0f);
    float y = fminf(fmaxf(pointer->y, 0.0f), pointer->frameHeight - 1.0f);
    *activation = (MacWSInputRecord){
        .magic = MACWS_INPUT_MAGIC,
        .version = MacWSInputWireVersionForKind(MacWSInputKindActivateTarget),
        .kind = MacWSInputKindActivateTarget,
        .sceneID = UINT64_C(0x564e430000000001),
        .timestamp = timestamp,
        .x = x,
        .y = y,
        .contactID = pointer->contactID,
        .frameWidth = pointer->frameWidth,
        .frameHeight = pointer->frameHeight,
        .source = MacWSInputSourceVNC,
        .sampleSequence = sequence,
    };
    return true;
}

#endif

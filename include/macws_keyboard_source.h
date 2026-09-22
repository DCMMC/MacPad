#ifndef MACWS_KEYBOARD_SOURCE_H
#define MACWS_KEYBOARD_SOURCE_H

// UIKit-side modifier evidence, separate from the session's posted key state.
// Aggregate flags identify a pair, not a left/right key. Do not promote that
// inference into a real observed down: a later right-key release must also
// retire an inferred left key. No framework calls or input posting occur here.
#include "macws_keyboard_state.h"

typedef struct {
    uint8_t observedSides; // Sides whose physical down edge was observed.
    uint8_t inferredSides; // Standard-left fallback from aggregate flags only.
} MacWSKeyboardSourceState;

// Inputs describe one event. A physical edge is authoritative for its own pair
// because UIKit can supply pre-edge aggregate flags on a modifier release.
// Unchanged pairs use the aggregate snapshot to recover an unobserved release.
// Caller resets both fields when this producer relinquishes input ownership.
// Reject contradictory edges without changing either state or output.
static inline bool MacWSKeyboardSourceApply(
        MacWSKeyboardSourceState *state, uint32_t aggregateFlags,
        uint8_t downSides, uint8_t upSides, uint8_t *sidesOut) {
    if (!state || !sidesOut || (downSides & upSides)) return false;
    static const uint32_t flags[4] = {
        MacWSKeyboardShift, MacWSKeyboardControl,
        MacWSKeyboardOption, MacWSKeyboardCommand,
    };
    MacWSKeyboardSourceState next = *state;
    for (unsigned pairIndex = 0; pairIndex < 4; pairIndex++) {
        uint8_t pair = (uint8_t)(3u << (pairIndex * 2));
        if ((downSides | upSides) & pair) {
            // Resolve only inferred evidence. An independently observed other
            // side remains held, even when this event releases its sibling.
            next.inferredSides &= (uint8_t)~pair;
            next.observedSides &= (uint8_t)~(upSides & pair);
            next.observedSides |= downSides & pair;
        } else if (!(aggregateFlags & flags[pairIndex])) {
            next.observedSides &= (uint8_t)~pair;
            next.inferredSides &= (uint8_t)~pair;
        } else if (next.observedSides & pair) {
            next.inferredSides &= (uint8_t)~pair;
        } else {
            // Aggregate-only knowledge is deliberately tagged as inferred.
            next.inferredSides = (next.inferredSides & (uint8_t)~pair) |
                (uint8_t)(1u << (pairIndex * 2));
        }
    }
    *state = next;
    *sidesOut = next.observedSides | next.inferredSides;
    return true;
}

// Losing a drawable disables new input, not release of keys already owned.
// Disabled producers cannot acquire ownership or synthesize new held sides.
static inline bool MacWSKeyboardSourceApplyOwned(
        MacWSKeyboardSourceState *state, bool inputEnabled, bool ownsKeyboard,
        uint32_t aggregateFlags, uint8_t downSides, uint8_t upSides,
        uint8_t *sidesOut) {
    if (!state || (!inputEnabled && !ownsKeyboard)) return false;
    if (!inputEnabled) {
        downSides = 0;
        aggregateFlags &= ~MacWSKeyboardModifierMask |
            MacWSKeyboardFlagsForSides(state->observedSides | state->inferredSides);
    }
    return MacWSKeyboardSourceApply(state, aggregateFlags,
                                   downSides, upSides, sidesOut);
}

#endif

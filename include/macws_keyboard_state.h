#ifndef MACWS_KEYBOARD_STATE_H
#define MACWS_KEYBOARD_STATE_H

// Single-consumer keyboard translation state. No framework calls, timers,
// file flags, or global key releases. The producer supplies its current physical
// snapshot; an optional native snapshot repairs divergence after a lost edge.
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

enum {
    MacWSKeyboardShift = 0x20000u,
    MacWSKeyboardControl = 0x40000u,
    MacWSKeyboardOption = 0x80000u,
    MacWSKeyboardCommand = 0x100000u,
    MacWSKeyboardModifierMask = 0x1e0000u,
};

// Wire-side order: left/right Shift, Control, Option, Command.
static inline uint16_t MacWSKeyboardKeyCodeForSide(unsigned side) {
    static const uint16_t codes[8] = {56, 60, 59, 62, 58, 61, 55, 54};
    return side < 8 ? codes[side] : UINT16_MAX;
}

static inline uint8_t MacWSKeyboardSideForKeyCode(uint16_t keyCode) {
    for (unsigned side = 0; side < 8; side++)
        if (MacWSKeyboardKeyCodeForSide(side) == keyCode)
            return (uint8_t)(1u << side);
    return 0;
}

static inline uint32_t MacWSKeyboardFlagsForSides(uint8_t sides) {
    static const uint32_t flags[4] = {
        MacWSKeyboardShift, MacWSKeyboardControl,
        MacWSKeyboardOption, MacWSKeyboardCommand,
    };
    uint32_t result = 0;
    for (unsigned pair = 0; pair < 4; pair++)
        if (sides & (3u << (pair * 2))) result |= flags[pair];
    return result;
}

// Aggregate UIKit flags are authoritative. Keep known left/right identities
// only while their aggregate flag is present. If UIKit reports a held modifier
// without a side edge, use the standard left code, not both sides.
static inline uint8_t MacWSKeyboardSidesForFlags(uint32_t flags,
                                                uint8_t knownSides) {
    static const uint32_t masks[4] = {
        MacWSKeyboardShift, MacWSKeyboardControl,
        MacWSKeyboardOption, MacWSKeyboardCommand,
    };
    uint8_t result = 0;
    for (unsigned pair = 0; pair < 4; pair++) {
        if (!(flags & masks[pair])) continue;
        uint8_t held = knownSides & (uint8_t)(3u << (pair * 2));
        result |= held ? held : (uint8_t)(1u << (pair * 2));
    }
    return result;
}

typedef struct {
    uint8_t physicalSides;     // Latest authoritative producer snapshot.
    uint8_t postedSides;       // Last successfully posted or observed state.
    uint8_t syntheticSides;    // Posted sides not physically held.
    uint8_t acceptedUpSides;   // Last locally accepted edge was up, not down.
    uint32_t physicalExtraFlags;
} MacWSKeyboardState;

// true means the real event constructor/poster accepted this transition.
// No state edge is committed when it returns false.
typedef bool (*MacWSKeyboardPost)(void *context, uint16_t keyCode, bool down,
                                  uint32_t flags);

static inline bool MacWSKeyboardReconcile(
        MacWSKeyboardState *state, uint8_t desiredSides,
        uint8_t observedSides, bool observedValid, uint32_t extraFlags,
        MacWSKeyboardPost post, void *context) {
    if (!state || !post) return false;
    // A native down can also lag an accepted UP. If a new owner still holds
    // that side, it needs a new ordered down after our queued up; observing
    // the old native down must not swallow that transition. Keep submission
    // history until an actual down is accepted, not until a native read of
    // zero (which could itself predate earlier queued events). This is an
    // executable asynchronous-model boundary, not an attributed device fault.
    uint8_t requiredDowns = state->acceptedUpSides & desiredSides;
    state->postedSides &= (uint8_t)~requiredDowns;
    if (observedValid) {
        // CGEventPost is asynchronous: an immediate native read may still
        // report up after our accepted down. Native presence adds knowledge;
        // absence MUST NOT discard an accepted but not-yet-processed down,
        // or the following physical release could be swallowed. A redundant
        // release of a still-observed old down is safe and bounded. This also
        // detects local masks=0/native Control=down without assuming equality.
        state->postedSides |= observedSides & (uint8_t)~requiredDowns;
    }
    state->syntheticSides = state->postedSides & (uint8_t)~state->physicalSides;
    extraFlags &= ~MacWSKeyboardModifierMask;
    // Release obsolete sides before adding new sides. Releasing left Control
    // while right Control remains held must keep the aggregate Control flag.
    for (unsigned phase = 0; phase < 2; phase++) {
        for (unsigned side = 0; side < 8; side++) {
            uint8_t bit = (uint8_t)(1u << side);
            bool wasDown = (state->postedSides & bit) != 0;
            bool wantDown = (desiredSides & bit) != 0;
            if (wasDown == wantDown || wantDown != (phase != 0)) continue;
            uint8_t next = wantDown ? state->postedSides | bit
                : state->postedSides & (uint8_t)~bit;
            if (!post(context, MacWSKeyboardKeyCodeForSide(side), wantDown,
                      MacWSKeyboardFlagsForSides(next) | extraFlags))
                return false;
            state->postedSides = next;
            if (wantDown) state->acceptedUpSides &= (uint8_t)~bit;
            else state->acceptedUpSides |= bit;
            state->syntheticSides = next & (uint8_t)~state->physicalSides;
        }
    }
    state->syntheticSides = state->postedSides & (uint8_t)~state->physicalSides;
    return true;
}

static inline bool MacWSKeyboardApplySnapshot(
        MacWSKeyboardState *state, uint8_t physicalSides,
        uint32_t physicalFlags, uint8_t observedSides, bool observedValid,
        MacWSKeyboardPost post, void *context) {
    if (!state || !post) return false;
    if ((physicalFlags & MacWSKeyboardModifierMask) !=
        MacWSKeyboardFlagsForSides(physicalSides)) return false;
    // Truth from the producer survives a posting failure. postedSides does
    // not advance, so the next snapshot retries the missing transition.
    state->physicalSides = physicalSides;
    state->physicalExtraFlags = physicalFlags & ~MacWSKeyboardModifierMask;
    return MacWSKeyboardReconcile(state, physicalSides, observedSides,
        observedValid, state->physicalExtraFlags, post, context);
}

static inline bool MacWSKeyboardPostHardwareKey(
        MacWSKeyboardState *state, uint16_t keyCode, bool down,
        uint8_t physicalSides, uint32_t physicalFlags,
        MacWSKeyboardPost post, void *context) {
    if (!state || !post || keyCode > 127) return false;
    uint8_t side = MacWSKeyboardSideForKeyCode(keyCode);
    // The caller provides the post-edge snapshot, including this key's own
    // side. Reject contradictory input instead of inventing a held key.
    if (side && (((physicalSides & side) != 0) != down)) return false;
    if (!MacWSKeyboardApplySnapshot(state, physicalSides, physicalFlags,
                                   0, false, post, context)) return false;
    // The real side transition was already posted by reconciliation.
    if (side) return true;
    return post(context, keyCode, down,
        MacWSKeyboardFlagsForSides(state->postedSides) |
        state->physicalExtraFlags);
}

static inline bool MacWSKeyboardPostSoftwareKey(
        MacWSKeyboardState *state, uint16_t keyCode, bool down,
        uint32_t desiredFlags, MacWSKeyboardPost post, void *context) {
    if (!state || !post || keyCode > 127 ||
        MacWSKeyboardSideForKeyCode(keyCode)) return false;
    uint32_t extra = desiredFlags & ~MacWSKeyboardModifierMask;
    if (down) {
        uint8_t desired = state->physicalSides | MacWSKeyboardSidesForFlags(
            desiredFlags, state->physicalSides | state->postedSides);
        if (!MacWSKeyboardReconcile(state, desired, 0, false,
                                   extra, post, context)) return false;
    }
    // Do not consume the software key-up's modifier state until the actual
    // key-up was posted. A failed callback leaves it available for retry.
    if (!post(context, keyCode, down,
              MacWSKeyboardFlagsForSides(state->postedSides) | extra))
        return false;
    if (down) return true;
    return MacWSKeyboardReconcile(state, state->physicalSides, 0, false,
        state->physicalExtraFlags, post, context);
}

#endif

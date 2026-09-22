#ifndef MACWS_COMPOSITE_CANDIDATE_POLICY_H
#define MACWS_COMPOSITE_CANDIDATE_POLICY_H

#include <stdbool.h>
#include <stdint.h>

// Select the authoritative destination within one WindowServer update.
// Process-owned scanout targets outrank intermediate textures regardless of
// their dimensions. Among intermediates, keep only the largest candidate for
// the current update; among owned targets, the later completion wins.
static inline bool MacWSCompositeCandidateShouldReplace(
        bool hasExistingCandidate, bool existingIsOwnedScanout,
        uint64_t existingArea, bool candidateIsOwnedScanout,
        uint64_t candidateArea) {
    if (!hasExistingCandidate) return true;
    if (candidateIsOwnedScanout != existingIsOwnedScanout)
        return candidateIsOwnedScanout;
    if (candidateIsOwnedScanout) return true;
    return candidateArea >= existingArea;
}

#endif

#ifndef MACWS_WINDOW_CONFIGURATION_H
#define MACWS_WINDOW_CONFIGURATION_H

#include <math.h>
#include <stdbool.h>
#include <stdint.h>

typedef enum {
    MacWSWindowConfigurationAckUnrelated,
    MacWSWindowConfigurationAckSuperseded,
    MacWSWindowConfigurationAckApplied,
    MacWSWindowConfigurationAckConstrained,
} MacWSWindowConfigurationAckResult;

// Validate a coalesced reverse-size response without blocking forward resizing.
static inline bool MacWSWindowConfigurationSettlementIsCurrent(
        uint64_t candidateTransaction, uint64_t currentTransaction,
        uint32_t candidateWindow, uint32_t currentWindow,
        int32_t candidateOwner, int32_t currentOwner,
        double candidateWidth, double candidateHeight,
        double currentWidth, double currentHeight,
        double candidateDensity, double currentDensity, bool busy) {
    return !busy && candidateTransaction == currentTransaction &&
        candidateWindow != 0 && candidateWindow == currentWindow &&
        candidateOwner > 1 && candidateOwner == currentOwner &&
        isfinite(candidateWidth) && isfinite(candidateHeight) &&
        isfinite(currentWidth) && isfinite(currentHeight) &&
        fabs(candidateWidth - currentWidth) < 0.25 &&
        fabs(candidateHeight - currentHeight) < 0.25 &&
        isfinite(candidateDensity) && isfinite(currentDensity) &&
        candidateDensity > 0.0 &&
        fabs(candidateDensity - currentDensity) < 0.001;
}

static inline bool MacWSWindowConfigurationSizesMatch(
        double firstWidth, double firstHeight,
        double secondWidth, double secondHeight, double tolerance) {
    return isfinite(firstWidth) && isfinite(firstHeight) &&
        isfinite(secondWidth) && isfinite(secondHeight) &&
        fabs(firstWidth - secondWidth) < tolerance &&
        fabs(firstHeight - secondHeight) < tolerance;
}

// Match the actual UIKit content extent, never a proposed AppKit size whose
// fixed axes have already been replaced with the target. Scene rounding is
// measured in UIKit points and must not grow with the logical density factor.
static inline bool MacWSWindowSceneContentMatchesTarget(
        double contentWidth, double contentHeight, double density,
        double targetLogicalWidth, double targetLogicalHeight) {
    return isfinite(density) && density > 0.0 &&
        isfinite(contentWidth) && contentWidth > 0.0 &&
        isfinite(contentHeight) && contentHeight > 0.0 &&
        isfinite(targetLogicalWidth) && targetLogicalWidth > 0.0 &&
        isfinite(targetLogicalHeight) && targetLogicalHeight > 0.0 &&
        MacWSWindowConfigurationSizesMatch(contentWidth, contentHeight,
            targetLogicalWidth * density, targetLogicalHeight * density, 1.5);
}

// A minimum is a lower bound, not a nearest-point preference. Rounding it
// down can produce an accepted Scene which cannot fit the native content.
static inline double MacWSWindowSceneExtentAtLeastMinimum(
        double preferred, double minimum) {
    if (!isfinite(preferred) || !isfinite(minimum) ||
        preferred <= 0.0 || minimum < 0.0) return NAN;
    return fmax(round(preferred), ceil(minimum));
}

// A catalog snapshot is not a configure response. Only the timestamp and
// sequence echoed after the owning NSWindow's completed layout can bind
// accepted/constrained dimensions to an issued request. A newer queued Scene
// size takes precedence even when that request has not been sent yet.
static inline MacWSWindowConfigurationAckResult
MacWSClassifyWindowConfigurationAcknowledgement(
        double issuedTimestamp, uint32_t issuedSequence,
        double issuedWidth, double issuedHeight, double issuedDensity,
        double queuedWidth, double queuedHeight, double queuedDensity,
        double acknowledgedTimestamp, uint32_t acknowledgedSequence,
        double acknowledgedRequestWidth, double acknowledgedRequestHeight,
        double appliedWidth, double appliedHeight) {
    if (!isfinite(issuedTimestamp) || issuedTimestamp <= 0.0 ||
        issuedSequence == 0 || acknowledgedTimestamp != issuedTimestamp ||
        acknowledgedSequence != issuedSequence ||
        !MacWSWindowConfigurationSizesMatch(
            issuedWidth, issuedHeight, acknowledgedRequestWidth,
            acknowledgedRequestHeight, 0.01) ||
        !isfinite(appliedWidth) || !isfinite(appliedHeight) ||
        appliedWidth < 1.0 || appliedHeight < 1.0)
        return MacWSWindowConfigurationAckUnrelated;
    if (!MacWSWindowConfigurationSizesMatch(
            issuedWidth, issuedHeight, queuedWidth, queuedHeight, 0.25) ||
        !isfinite(issuedDensity) || !isfinite(queuedDensity) ||
        fabs(issuedDensity - queuedDensity) >= 0.001)
        return MacWSWindowConfigurationAckSuperseded;
    return MacWSWindowConfigurationSizesMatch(
        acknowledgedRequestWidth, acknowledgedRequestHeight,
        appliedWidth, appliedHeight, 0.75)
        ? MacWSWindowConfigurationAckApplied
        : MacWSWindowConfigurationAckConstrained;
}

#endif

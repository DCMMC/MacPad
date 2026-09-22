#ifndef MACWS_TOUCH_POLICY_H
#define MACWS_TOUCH_POLICY_H

#include <stdbool.h>
#include <math.h>

#include "macws_host_protocol.h"

// Product-level direct-touch thresholds. Keep these in a pure header so the
// UIKit state machine and local boundary tests cannot silently diverge.
// UIKit's ordinary tap slop on a Retina iPad is materially wider than four
// points.  Four points classified normal fingertip centroid jitter as a
// document scroll before touch-up, so the first half of a Finder double tap
// was frequently lost.  Eight points still begins direct manipulation within
// one short physical movement while preserving a stationary tap transaction.
#define MACWS_DIRECT_GESTURE_THRESHOLD_POINTS 8.0
#define MACWS_DIRECT_LONG_PRESS_SECONDS 0.45
#define MACWS_DIRECT_DOUBLE_TAP_SECONDS 0.42
#define MACWS_DIRECT_DOUBLE_TAP_DISTANCE_POINTS 44.0
// A physical pointer is much more precise than a fingertip.  Restrict the
// second-click transaction to a stationary, short press so a double-click
// does not consume the beginning of a Finder file drag.
#define MACWS_POINTER_CLICK_MAX_SECONDS 0.35
#define MACWS_POINTER_CLICK_TRAVEL_POINTS 6.0
#define MACWS_POINTER_DOUBLE_CLICK_DISTANCE_POINTS 8.0
#define MACWS_SCROLL_MOMENTUM_MINIMUM_POINTS_PER_SECOND 80.0
// Physical trackpads report much smaller point velocities for short scrolls
// than direct UIKit pans.  Runtime capture on the M1 Magic Keyboard measured
// a deliberate short release at roughly 20 pt/s; using the direct-touch 80
// pt/s gate discarded it.  Keep the lower gate scoped to indirect scrolling
// so fingertip gestures retain their existing release behaviour.
#define MACWS_INDIRECT_SCROLL_MOMENTUM_MINIMUM_POINTS_PER_SECOND 8.0
#define MACWS_SCROLL_MOMENTUM_STOP_POINTS_PER_SECOND 18.0
#define MACWS_INDIRECT_SCROLL_MOMENTUM_STOP_POINTS_PER_SECOND 4.0
// Keep only a very recent velocity sample as a fallback when the recognizer's
// terminal velocity is zero or sub-threshold. Runtime diagnostics on the M1
// iPad observed the terminal callback 8-25 ms after the last movement sample;
// a bounded 100-ms witness covers that interval without turning a deliberate
// stop-then-lift into an unwanted fling.
#define MACWS_SCROLL_RELEASE_SAMPLE_MAX_AGE_SECONDS 0.10
#define MACWS_SYSTEM_GESTURE_RECOGNITION_FRACTION 0.012
#define MACWS_SYSTEM_GESTURE_REFERENCE_FRACTION 0.28
#define MACWS_THREE_FINGER_CHORD_GRACE_SECONDS 0.10
#define MACWS_TWO_FINGER_INTENT_THRESHOLD_POINTS 2.0

// A staggered third finger deserves a short chord window only while the first
// two contacts are still effectively stationary. Once either contact has
// travelled far enough to express a pan/pinch/rotation, continuing to hold all
// two-finger recognizers in Possible turns the grace interval into a visible
// dead zone. The threshold is above normal sub-point capacitive jitter but
// below UIKit's own useful gesture displacement.
static inline bool MacWSTwoFingerMotionHasCommitted(double maximumTouchTravel,
                                                     double spanChange) {
    return isfinite(maximumTouchTravel) && isfinite(spanChange) &&
        (maximumTouchTravel >= MACWS_TWO_FINGER_INTENT_THRESHOLD_POINTS ||
         fabs(spanChange) >= MACWS_TWO_FINGER_INTENT_THRESHOLD_POINTS);
}

// A dispatch_after callback is only a visual/feedback hint.  The Host main
// queue can be busy presenting a large IOSurface when that callback becomes
// runnable, while UIKit's already-recorded touch-up is still waiting behind
// it.  Always use the UITouch hardware timestamps at the next real event to
// decide whether the hold duration was actually reached.
static inline bool MacWSTouchReachedLongPress(double elapsedSeconds) {
    return elapsedSeconds >= MACWS_DIRECT_LONG_PRESS_SECONDS;
}

// UITouch.tapCount can restart at one when the first click changes the macOS
// key window or dismisses a transient menu. Use the physical timestamps and
// UIKit-space distance as a fallback without delaying the first click.
static inline bool MacWSIsDirectDoubleTap(double previousTimestamp,
                                          double currentTimestamp,
                                          double deltaX,
                                          double deltaY) {
    if (previousTimestamp <= 0.0 || currentTimestamp <= previousTimestamp ||
        currentTimestamp - previousTimestamp >
            MACWS_DIRECT_DOUBLE_TAP_SECONDS) return false;
    double maximum = MACWS_DIRECT_DOUBLE_TAP_DISTANCE_POINTS;
    return deltaX * deltaX + deltaY * deltaY <= maximum * maximum;
}

static inline bool MacWSIsPointerClick(double elapsedSeconds,
                                       double travelPoints) {
    return isfinite(elapsedSeconds) && isfinite(travelPoints) &&
        elapsedSeconds >= 0.0 &&
        elapsedSeconds <= MACWS_POINTER_CLICK_MAX_SECONDS &&
        travelPoints <= MACWS_POINTER_CLICK_TRAVEL_POINTS;
}

static inline bool MacWSIsPointerDoubleClick(double previousTimestamp,
                                             double currentTimestamp,
                                             double deltaX,
                                             double deltaY) {
    if (previousTimestamp <= 0.0 || currentTimestamp <= previousTimestamp ||
        currentTimestamp - previousTimestamp >
            MACWS_DIRECT_DOUBLE_TAP_SECONDS) return false;
    double maximum = MACWS_POINTER_DOUBLE_CLICK_DISTANCE_POINTS;
    return isfinite(deltaX) && isfinite(deltaY) &&
        deltaX * deltaX + deltaY * deltaY <= maximum * maximum;
}

static inline bool MacWSShouldStartScrollMomentum(double velocityX,
                                                  double velocityY) {
    return hypot(velocityX, velocityY) >=
        MACWS_SCROLL_MOMENTUM_MINIMUM_POINTS_PER_SECOND;
}

static inline bool MacWSShouldStartIndirectScrollMomentum(double velocityX,
                                                          double velocityY) {
    return isfinite(velocityX) && isfinite(velocityY) &&
        hypot(velocityX, velocityY) >=
            MACWS_INDIRECT_SCROLL_MOMENTUM_MINIMUM_POINTS_PER_SECOND;
}

static inline bool MacWSResolveIndirectScrollReleaseVelocity(
        double recognizerVelocityX, double recognizerVelocityY,
        double sampledVelocityX, double sampledVelocityY,
        double sampledVelocityAgeSeconds,
        double *resolvedVelocityX, double *resolvedVelocityY) {
    bool recognizerValid = isfinite(recognizerVelocityX) &&
        isfinite(recognizerVelocityY);
    double velocityX = recognizerValid ? recognizerVelocityX : 0.0;
    double velocityY = recognizerValid ? recognizerVelocityY : 0.0;
    bool sampledValid = isfinite(sampledVelocityX) &&
        isfinite(sampledVelocityY) &&
        isfinite(sampledVelocityAgeSeconds) &&
        sampledVelocityAgeSeconds >= 0.0 &&
        sampledVelocityAgeSeconds <=
            MACWS_SCROLL_RELEASE_SAMPLE_MAX_AGE_SECONDS;
    if (!MacWSShouldStartIndirectScrollMomentum(velocityX, velocityY) &&
        sampledValid &&
        MacWSShouldStartIndirectScrollMomentum(sampledVelocityX,
                                               sampledVelocityY)) {
        velocityX = sampledVelocityX;
        velocityY = sampledVelocityY;
    }
    if (resolvedVelocityX) *resolvedVelocityX = velocityX;
    if (resolvedVelocityY) *resolvedVelocityY = velocityY;
    return MacWSShouldStartIndirectScrollMomentum(velocityX, velocityY);
}

// UIKit measures rotation in its top-left-origin view coordinates, while the
// reconstructed Ventura NSEvent uses AppKit's rotation convention.  Passing
// the UIKit sign through unchanged made Maps rotate horizontally opposite to
// the two physical fingers.  Keep the convention crossing in the shared
// policy instead of compensating inside Maps or the AppKit event consumer.
static inline double MacWSAppKitRotationDegreesForUIKitRadians(
        double radians) {
    return isfinite(radians) ? -radians * 57.2957795130823208768 : 0.0;
}

// A hover immediately followed by a click is one pointer transaction. The
// click is the action whose first visible response matters; retaining the
// older hover as the sole pending latency sample can consume that response
// and make the atomic click disappear from performance evidence.
static inline bool MacWSInputSupersedesPendingVisibilitySample(
        uint16_t pendingKind, uint16_t newKind) {
    return pendingKind == MacWSInputKindHover &&
        (newKind == MacWSInputKindTap ||
         newKind == MacWSInputKindSecondaryTap);
}

typedef enum {
    MacWSTouchCandidateDecisionWait = 0,
    MacWSTouchCandidateDecisionTap,
    MacWSTouchCandidateDecisionScroll,
    MacWSTouchCandidateDecisionLongPress,
} MacWSTouchCandidateDecision;

// Once a direct-touch scroll has crossed the gesture threshold, lock clearly
// horizontal/vertical gestures for stable document scrolling, but preserve
// both axes for genuinely diagonal direct manipulation.  Always forcing one
// axis made Maps and 2-D canvases lag behind the finger because half of the
// physical trajectory was discarded before AppKit ever saw it.
typedef enum {
    MacWSDirectScrollAxisNone = 0,
    MacWSDirectScrollAxisHorizontal,
    MacWSDirectScrollAxisVertical,
    MacWSDirectScrollAxisFree,
} MacWSDirectScrollAxis;

// Fullscreen's three-finger vocabulary mirrors a MacBook trackpad: horizontal
// swipes change Spaces, while vertical swipes enter all-window/current-app
// overviews.  Keep classification independent from UIKit so velocity/travel
// boundaries remain covered by the protocol test instead of silently changing
// with controller refactors.
typedef enum {
    MacWSThreeFingerGestureNone = 0,
    MacWSThreeFingerGestureLeft,
    MacWSThreeFingerGestureRight,
    MacWSThreeFingerGestureUp,
    MacWSThreeFingerGestureDown,
} MacWSThreeFingerGesture;

static inline MacWSThreeFingerGesture MacWSClassifyThreeFingerPan(
        double translationX, double translationY,
        double velocityX, double velocityY, double minimumViewDimension) {
    double travel = hypot(translationX, translationY);
    double velocity = hypot(velocityX, velocityY);
    double minimumTravel = fmax(64.0, minimumViewDimension * 0.085);
    bool decisiveFlick = velocity >= 900.0 && travel >= 28.0;
    if (travel < minimumTravel && !decisiveFlick)
        return MacWSThreeFingerGestureNone;
    double x = fabs(translationX), y = fabs(translationY);
    if (x > y * 1.15)
        return translationX < 0.0 ? MacWSThreeFingerGestureLeft
                                  : MacWSThreeFingerGestureRight;
    if (y > x * 1.15)
        return translationY < 0.0 ? MacWSThreeFingerGestureUp
                                  : MacWSThreeFingerGestureDown;
    return MacWSThreeFingerGestureNone;
}

// A physical Mac trackpad locks the navigation axis after a small slop region,
// then reports a continuous signed progress. Keep that recognition boundary
// independent of UIKit so the Host cannot regress to a one-shot thresholded
// shortcut without the protocol test noticing.
static inline MacWSSystemGestureAxis MacWSSystemGestureAxisForTranslation(
        double translationX, double translationY,
        double minimumViewDimension) {
    double x = fabs(translationX), y = fabs(translationY);
    double recognitionDistance = fmax(
        8.0, minimumViewDimension *
            MACWS_SYSTEM_GESTURE_RECOGNITION_FRACTION);
    if (hypot(x, y) < recognitionDistance) return 0;
    // Once the hardware-sized slop has been crossed, a Mac trackpad commits
    // to one navigation axis even when the fingers travelled diagonally.  A
    // second 12% dominance gate made ordinary slightly-diagonal downward
    // swipes remain forever unrecognised despite ample travel.  Resolve the
    // dominant displacement deterministically; exact ties choose vertical,
    // which is the less destructive overview operation.
    if (x > y) return MacWSSystemGestureAxisHorizontal;
    if (y > x) return MacWSSystemGestureAxisVertical;
    return MacWSSystemGestureAxisVertical;
}

static inline double MacWSSystemGestureReferenceDistance(
        double minimumViewDimension) {
    return fmax(120.0, minimumViewDimension *
        MACWS_SYSTEM_GESTURE_REFERENCE_FRACTION);
}

// Ventura Dock does not use one Cartesian sign convention for both navigation
// axes. RE at Dock __TEXT+0x8d508 plus the live handler table shows:
//   * horizontal finger-left -> positive progress -> swipe-left handler
//   * vertical finger-up     -> negative progress -> swipe-up handler
// UIKit reports both of those finger translations as negative. Preserve the
// verified per-axis convention here for displacement and velocity alike.
static inline double MacWSSystemGestureProgressForDisplacement(
        MacWSSystemGestureAxis axis, double displacement,
        double referenceDistance) {
    if (referenceDistance <= 0.0) return 0.0;
    if (axis == MacWSSystemGestureAxisHorizontal)
        return -displacement / referenceDistance;
    if (axis == MacWSSystemGestureAxisVertical)
        return displacement / referenceDistance;
    return 0.0;
}

static inline MacWSDirectScrollAxis MacWSChooseDirectScrollAxis(
        double displacementX, double displacementY) {
    double x = fabs(displacementX);
    double y = fabs(displacementY);
    if (x == 0.0 && y == 0.0) return MacWSDirectScrollAxisNone;
    if (y >= x * 1.5) return MacWSDirectScrollAxisVertical;
    if (x >= y * 1.5) return MacWSDirectScrollAxisHorizontal;
    return MacWSDirectScrollAxisFree;
}

static inline void MacWSConstrainDirectScrollDelta(
        MacWSDirectScrollAxis axis, double *deltaX, double *deltaY) {
    if (axis == MacWSDirectScrollAxisVertical) {
        *deltaX = 0.0;
    } else if (axis == MacWSDirectScrollAxisHorizontal) {
        *deltaY = 0.0;
    }
}

static inline MacWSTouchCandidateDecision MacWSDecideTouchCandidate(
        double elapsedSeconds, double travelPoints, bool didEnd) {
    // UITouch.timestamp is the hardware event time, independent of when the
    // main queue gets to process it.  A movement sample recorded after the
    // hold threshold therefore means "hold, then move" and must arm a mouse
    // drag even if dispatch_after's visual feedback callback was delayed.
    // Movement whose hardware timestamp is still before the threshold remains
    // scroll-first. Once scrolling starts the UIKit state machine no longer
    // asks this candidate policy, so a long continuous scroll cannot turn
    // into a drag when its total duration later crosses the hold threshold.
    if (MacWSTouchReachedLongPress(elapsedSeconds))
        return MacWSTouchCandidateDecisionLongPress;
    if (travelPoints >= MACWS_DIRECT_GESTURE_THRESHOLD_POINTS)
        return MacWSTouchCandidateDecisionScroll;
    return didEnd ? MacWSTouchCandidateDecisionTap
                  : MacWSTouchCandidateDecisionWait;
}

#endif

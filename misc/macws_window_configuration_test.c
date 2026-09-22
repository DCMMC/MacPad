#include <assert.h>
#include <stdio.h>

#include "macws_window_configuration.h"

int main(void) {
    assert(MacWSWindowSceneExtentAtLeastMinimum(879, 880.5) == 881);
    assert(MacWSWindowSceneExtentAtLeastMinimum(376.25, 376.25) == 377);
    assert(MacWSWindowSceneExtentAtLeastMinimum(634.25, 634.25) == 635);
    assert(MacWSWindowSceneExtentAtLeastMinimum(500, 331.25) == 500);
    assert(isnan(MacWSWindowSceneExtentAtLeastMinimum(500, NAN)));
    // Get Info: fixed-axis policy already says 410, but the visible content
    // is still 676 points tall (541 logical). This is NOT a landed resize.
    assert(!MacWSWindowSceneContentMatchesTarget(500, 676, 1.25, 400, 410));
    assert(!MacWSWindowSceneContentMatchesTarget(500, 592.5, 1.25, 400, 410));
    assert(MacWSWindowSceneContentMatchesTarget(500, 513, 1.25, 400, 410));
    // Fixed width, both fixed, fractional edge rounding, and a mismatched
    // flexible axis obey the same physical postcondition.
    assert(!MacWSWindowSceneContentMatchesTarget(500, 489, 1.5, 301, 326));
    assert(MacWSWindowSceneContentMatchesTarget(451.5, 489, 1.5, 301, 326));
    assert(!MacWSWindowSceneContentMatchesTarget(500, 513, 1.5, 400, 410));
    assert(!MacWSWindowSceneContentMatchesTarget(504, 513, 1.25, 400, 410));
    assert(!MacWSWindowSceneContentMatchesTarget(500, 515, 1.25, 400, 410));
    assert(!MacWSWindowSceneContentMatchesTarget(NAN, 513, 1.25, 400, 410));
    assert(!MacWSWindowSceneContentMatchesTarget(500, 513, 0, 400, 410));
    assert(!MacWSWindowSceneContentMatchesTarget(500, 513, 1.25, 0, 410));
    // Terminal ACK 814x439.2 -> 813x429 preceded another corner-drag
    // update by 19 ms. Only its still-current quiet geometry may spring back.
    assert(!MacWSWindowConfigurationSettlementIsCurrent(
        5, 5, 298, 298, 95352, 95352,
        1017.5, 549, 996, 517, 1.25, 1.25, false));
    assert(MacWSWindowConfigurationSettlementIsCurrent(
        5, 5, 298, 298, 95352, 95352,
        1017.5, 549, 1017.5, 549, 1.25, 1.25, false));
    assert(!MacWSWindowConfigurationSettlementIsCurrent(
        5, 6, 298, 298, 95352, 95352,
        1017.5, 549, 1017.5, 549, 1.25, 1.25, false));
    assert(!MacWSWindowConfigurationSettlementIsCurrent(
        5, 5, 298, 299, 95352, 95352,
        1017.5, 549, 1017.5, 549, 1.25, 1.25, false));
    assert(!MacWSWindowConfigurationSettlementIsCurrent(
        5, 5, 298, 298, 95352, 95353,
        1017.5, 549, 1017.5, 549, 1.25, 1.25, false));
    assert(!MacWSWindowConfigurationSettlementIsCurrent(
        5, 5, 298, 298, 95352, 95352,
        1017.5, 549, 1017.5, 549, 1.25, 1.5, false));
    assert(!MacWSWindowConfigurationSettlementIsCurrent(
        5, 5, 298, 298, 95352, 95352,
        1017.5, 549, 1017.5, 549, 1.25, 1.25, true));
    // The runtime Terminal dimensions at 1789154729.441 lacked request
    // identity. With explicit identities, a hypothetical earlier 648x613
    // response must not reject the later 736x634 Scene request.
    assert(MacWSClassifyWindowConfigurationAcknowledgement(
        100, 42, 736, 634, 1, 736, 634, 1,
        99, 41, 648, 613, 648, 613) ==
        MacWSWindowConfigurationAckUnrelated);

    // An ACK for the latest *issued* record still cannot resize the Scene
    // backward when the user already queued a newer size within 33 ms.
    assert(MacWSClassifyWindowConfigurationAcknowledgement(
        100, 42, 736, 634, 1, 823, 634, 1,
        100, 42, 736, 634, 736, 613) ==
        MacWSWindowConfigurationAckSuperseded);
    assert(MacWSClassifyWindowConfigurationAcknowledgement(
        100, 42, 736, 634, 1, 736, 634, 1.10,
        100, 42, 736, 634, 736, 613) ==
        MacWSWindowConfigurationAckSuperseded);

    // Only the exact causal reply establishes Terminal's cell increments,
    // including a genuine rejection that leaves its old frame unchanged.
    assert(MacWSClassifyWindowConfigurationAcknowledgement(
        100, 42, 610, 633, 1, 610, 633, 1,
        100, 42, 610, 633, 604, 613) ==
        MacWSWindowConfigurationAckConstrained);
    assert(MacWSClassifyWindowConfigurationAcknowledgement(
        100, 42, 300, 300, 1, 300, 300, 1,
        100, 42, 300, 300, 301, 326) ==
        MacWSWindowConfigurationAckConstrained);
    assert(MacWSClassifyWindowConfigurationAcknowledgement(
        100, 42, 736, 634, 1, 736, 634, 1,
        100, 42, 736, 634, 736, 634) ==
        MacWSWindowConfigurationAckApplied);

    // Retried deliveries retain their identity. A restart may reuse sequence
    // numbers, but can never reuse the original uptime timestamp.
    for (unsigned retry = 0; retry < 3; retry++) {
        assert(MacWSClassifyWindowConfigurationAcknowledgement(
            100, 42, 736, 634, 1, 736, 634, 1,
            100, 42, 736, 634, 736, 634) ==
            MacWSWindowConfigurationAckApplied);
    }
    assert(MacWSClassifyWindowConfigurationAcknowledgement(
        200, 42, 736, 634, 1, 736, 634, 1,
        100, 42, 736, 634, 736, 613) ==
        MacWSWindowConfigurationAckUnrelated);
    assert(MacWSClassifyWindowConfigurationAcknowledgement(
        100, 42, 736, 634, 1, 736, 634, 1,
        100, 42, 648, 613, 648, 613) ==
        MacWSWindowConfigurationAckUnrelated);

    // A legacy catalog has no ACK key: elapsed time or a fixed-size flag must
    // never promote this unrelated observation into a constrained reply.
    assert(MacWSClassifyWindowConfigurationAcknowledgement(
        100, 42, 736, 634, 1, 736, 634, 1,
        0, 0, 0, 0, 648, 613) ==
        MacWSWindowConfigurationAckUnrelated);
    assert(MacWSClassifyWindowConfigurationAcknowledgement(
        100, 42, 736, 634, 1, 736, 634, 1,
        100, 42, 736, 634, NAN, 634) ==
        MacWSWindowConfigurationAckUnrelated);
    puts("window configuration causal acknowledgement tests passed");
    return 0;
}

"""Source-contract regression checks, not a SpringBoard/visual acceptance test.

Run: python3 misc/test_window_policy_queue_contract.py
"""
from pathlib import Path
import unittest


SOURCE = (Path(__file__).resolve().parents[1] /
          "MacWSWindowing/Tweak.x").read_text()


def function_body(signature):
    start = SOURCE.index(signature)
    opening = SOURCE.index("{", start)
    depth = 1
    cursor = opening + 1
    while depth:
        if SOURCE[cursor] == "{":
            depth += 1
        elif SOURCE[cursor] == "}":
            depth -= 1
        cursor += 1
    return SOURCE[opening + 1:cursor - 1]


class PolicyQueueContract(unittest.TestCase):
    def test_policy_is_consumed_before_geometry_winner_selection(self):
        body = function_body("static void MacWSHandleResizeRequest(")
        branch = body.index('if ([request[@"policy_only"] boolValue]) {')
        next_winner = body.index('NSDictionary *current = latestByScene[scene];')
        lane = body[branch:next_winner]
        self.assertIn("MacWSApplyResizeRequest(request, path, 0);", lane)
        self.assertIn("continue;", lane)
        self.assertNotIn("MacWSLatestResizeNonceByScene", lane)
        self.assertNotIn("latestByScene[scene] =", lane)

    def test_geometry_cleanup_does_not_delete_metadata_request(self):
        body = function_body("static void MacWSHandleResizeRequest(")
        cleanup = body[body.index("// Remove every queued predecessor"):]
        self.assertLess(
            cleanup.index('if ([request[@"policy_only"] boolValue]) continue;'),
            cleanup.index('NSString *winnerPath ='))

    def test_metadata_does_not_require_or_claim_geometry_nonce(self):
        body = function_body(
            "static void MacWSApplyResizeRequest(NSDictionary *request, NSString *path,\n"
            "                                    NSUInteger attempt) {")
        self.assertIn('if (!policyOnly && ![latestNonce isEqualToString:nonce])', body)
        self.assertNotIn("MacWSLatestResizeNonceByScene[sceneIdentifier] =", body)
        self.assertLess(body.index("if (policyOnly) {"),
                        body.index("MacWSSetStableModelSize("))
        lane = body[body.index("if (policyOnly) {"):
                    body.index("MacWSSetStableModelSize(")]
        self.assertIn("MacWSFinishResizeRequest(path, nil);", lane)
        self.assertIn("return;", lane)

    def test_old_geometry_retry_cannot_republish_obsolete_constraints(self):
        body = function_body(
            "static void MacWSApplyResizeRequest(NSDictionary *request, NSString *path,\n"
            "                                    NSUInteger attempt) {")
        self.assertIn('[currentPolicy[@"issued_at"] doubleValue] > issuedAt', body)
        self.assertIn('if (!newerPolicyExists)\n'
                      '        MacWSResizePolicyByScene[sceneIdentifier] = resizePolicy;', body)
        self.assertIn('if (!policyOnly && newerPolicyExists)', body)

    def test_frame_results_are_not_cached_as_native_minimums(self):
        source = (Path(__file__).resolve().parents[1] /
                  "libmachook/AppInputBridge.m").read_text()
        self.assertNotIn("MacWSLearnedMinimumWidthKey", source)
        self.assertNotIn("MacWSLearnedMinimumHeightKey", source)
        self.assertNotIn("MacWSContentDrivenHeightMinimumWidthKey", source)
        self.assertIn("MacWSRequiredContentSizeLimits(window", source)
        self.assertIn(".appliedWidth = (float)appliedFrame.size.width", source)


if __name__ == "__main__":
    unittest.main()

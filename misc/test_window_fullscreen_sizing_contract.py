"""Presentation/source contracts; device screenshots remain the acceptance test."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
TWEAK = (ROOT / "MacWSWindowing/Tweak.x").read_text()
HOST = (ROOT / "MacWSHost/main.m").read_text()


class FullscreenSizing(unittest.TestCase):
    def test_policy_detached_before_native_maximization(self):
        body = TWEAK[TWEAK.index("static void MacWSApplyFullscreenRequest("):]
        body = body[:body.index("static void MacWSHandleFullscreenRequest(")]
        self.assertLess(body.index("MacWSWorkspaceSinceByScene[requestedIdentifier] = @(issuedAt)"),
                        body.index("MacWSMessageToggleMaximization(switcherController)"))

    def test_workspace_excluded_from_item_and_gesture_sizing(self):
        self.assertGreaterEqual(TWEAK.count("!MacWSWorkspaceSinceByScene[scene]"), 2)
        self.assertIn("!MacWSWorkspaceSinceByScene[sceneIdentifier]", TWEAK)

    def test_old_window_request_cannot_restore_workspace_constraints(self):
        self.assertIn("workspaceSince && issuedAt <= workspaceSince.doubleValue", TWEAK)
        self.assertIn("reason=entered-workspace", TWEAK)

    def test_native_geometry_is_fullscreen_postcondition(self):
        self.assertNotIn("if (systemState || fillsPanel)", HOST)
        self.assertIn("if (fillsPanel)", HOST)


if __name__ == "__main__":
    unittest.main()

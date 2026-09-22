"""Source-contract checks for orphan Scene retirement; NOT visual acceptance.

Run: python3 misc/test_window_lifetime_contract.py
Runtime evidence: docs/evidence/window-orphan-scene-retirement-20260912.md.
"""
from pathlib import Path
import unittest


SOURCE = (Path(__file__).resolve().parents[1] / "MacWSHost/main.m").read_text()


def body(signature):
    start = SOURCE.index(signature)
    opening = SOURCE.index("{", start)
    depth, cursor = 1, opening + 1
    while depth:
        depth += (SOURCE[cursor] == "{") - (SOURCE[cursor] == "}")
        cursor += 1
    return SOURCE[opening + 1:cursor - 1]


CATALOG = body("- (void)metalView:(MacWSMetalView *)view\n  receivedWindows:")
MISSING = CATALOG[CATALOG.index("} else if ((_targetWindowObservedInCatalog") :]


class WindowLifetimeContract(unittest.TestCase):
    def test_dead_owner_never_promotes_independent_scene_to_desktop(self):
        self.assertNotIn("openFullscreenWorkspace", CATALOG)
        self.assertNotIn("recovery=desktop", CATALOG)
        self.assertIn("targetOwnerMissing = kill(_windowOwnerPID, 0) != 0 && errno == ESRCH", CATALOG)
        self.assertIn("_targetWindowObservedInCatalog || targetOwnerMissing", MISSING)
        self.assertLess(CATALOG.index("[self openPendingApplicationWindowFromCatalog:windows]"),
                        CATALOG.index("targetOwnerMissing = kill(_windowOwnerPID"))

    def test_exact_window_and_group_cannot_adopt_another_owner(self):
        match = CATALOG[CATALOG.index("MacWSStreamWindow *exactWindow = nil;"):]
        self.assertLess(match.index("window.descriptor.ownerPID != _windowOwnerPID"),
                        match.index("window.descriptor.windowID == _windowID"))
        self.assertLess(MISSING.index("candidate.descriptor.ownerPID != expectedOwnerPID"),
                        MISSING.index("candidate.descriptor.windowID == expectedWindowID"))

    def test_delayed_retirement_checks_entire_binding(self):
        for guard in ("self->_streamMode != MacWSStreamModeWindow",
                      "self->_windowID != expectedWindowID",
                      "self->_windowOwnerPID != expectedOwnerPID",
                      "self->_windowGroupID != expectedGroupID"):
            self.assertIn(guard, MISSING)
        self.assertIn("650 * NSEC_PER_MSEC", MISSING)
        self.assertIn("serial != self->_targetWindowMissingSerial", MISSING)

    def test_live_owner_requires_fresh_catalog_not_elapsed_time_only(self):
        metal = (Path(__file__).resolve().parents[1] /
                 "MacWSHost/Rendering/MacWSMetalView.m").read_text()
        actual = metal[metal.index("- (void)streamClient:(MacWSStreamClient *)client\n      receivedWindows:"):]
        actual = actual[:actual.index("\n}\n")]
        self.assertIn("++_windowCatalogRevision", actual)
        self.assertEqual(metal.count("++_windowCatalogRevision"), 1)
        self.assertIn("self->_metalView.windowCatalogRevision >\n                    firstMissingCatalogRevision", MISSING)
        self.assertIn("if (!refreshedCatalog &&\n                    !(ownerWasMissing && ownerStillMissing))", MISSING)
        self.assertIn("kill(expectedOwnerPID, 0) != 0 && errno == ESRCH", MISSING)

    def test_pending_successor_guard_still_precedes_destruction(self):
        self.assertLess(MISSING.index("if (!pendingSuccessorConnection)"),
                        MISSING.index("requestSceneSessionDestruction:session"))

    def test_retirement_never_closes_mac_window_or_recycles_old_metadata(self):
        deletion = MISSING.index("requestSceneSessionDestruction:session")
        for required in ("[MacWSSceneSessionsPreservingMacWindow addObject:identifier]",
                         "[MacWSSceneCloseRequestsSent addObject:identifier]",
                         "MacWSSetPersistedSceneBinding(identifier, nil)",
                         "[self suspendSceneStream]"):
            self.assertLess(MISSING.index(required), deletion)
        self.assertNotIn("MacWSSendCloseWindow", MISSING)
        error = MISSING[deletion:]
        self.assertNotIn("removeObject:identifier", error)

    def test_connected_startup_prune_uses_same_reconciliation_path(self):
        prune = body("static void MacWSPruneDeadWindowSceneSessions(void)")
        branch = prune[prune.index("if (controller &&"):]
        self.assertLess(branch.index("[controller requestWindowLifetimeReconciliation]"),
                        branch.index("requestSceneSessionDestruction:session"))
        self.assertIn("detachMissingWorkspaceReturnOwnerPID", prune)

    def test_retiring_scene_cannot_emit_focus_or_geometry_work(self):
        focus = body("- (void)synchronizeMacWindowFocusWithReason:(NSString *)reason {")
        self.assertIn("self->_sceneDestructionRequested", focus)
        geometry = body("- (void)sceneGeometryDidChange {")
        self.assertIn("if (_sceneDestructionRequested) return;", geometry)
        fullscreen = body("- (void)openFullscreenWorkspace {")
        self.assertLess(fullscreen.index("if (_sceneDestructionRequested) return;"),
                        fullscreen.index("if (_streamMode == MacWSStreamModeFullscreen)"))
        emitted = body("- (void)metalView:(MacWSMetalView *)view emittedInput:(MacWSInputRecord)record {")
        guard = emitted[:emitted.index("int32_t presentationTargetPID")]
        self.assertIn("if (_sceneDestructionRequested &&", guard)
        self.assertIn("record.kind == MacWSInputKindConfigureWindow", guard)
        self.assertIn("record.kind == MacWSInputKindActivateTarget", guard)
        self.assertNotIn("MacWSInputKindKeyUp", guard)
        self.assertNotIn("MacWSInputKindTouchCancel", guard)


if __name__ == "__main__":
    unittest.main()

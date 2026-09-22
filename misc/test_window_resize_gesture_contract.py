"""Source-contract checks; actual paused HID drag remains required acceptance."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
TWEAK = (ROOT / 'MacWSWindowing/Tweak.x').read_text()
METAL = (ROOT / 'MacWSHost/Rendering/MacWSMetalView.m').read_text()
APP = (ROOT / 'MacWSHost/main.m').read_text()


class NativeResizeGestureContract(unittest.TestCase):
    def test_original_gesture_lifecycle_is_preserved(self):
        block = TWEAK[TWEAK.index('- (id)_responseForGestureUpdateAtGestureEnd:'):
                      TWEAK.index('- (id)_responseForSceneSizeUpdateToSize:')]
        self.assertIn('MacWSActiveResizeGestureModifier == self', block)
        self.assertIn('return %orig(ended);', block)
        self.assertIn('if (nativeGesture && !ended)', block)
        self.assertIn('if (nativeGesture && ended)', block)
        self.assertIn('- (void)dealloc', block)
        self.assertIn('MacWSPublishResizeGestureState(self, NO)', block)

    def test_publisher_is_exact_scene_and_does_not_write_stock_app_state(self):
        block = TWEAK[TWEAK.index('static void MacWSPublishResizeGestureState('):
                      TWEAK.index('static id MacWSResizeModifierLayoutGrid(')]
        self.assertIn('isEqualToString:@"com.macwsguide.host"', block)
        self.assertIn('NSSelectorFromString(@"uniqueIdentifier")', block)
        self.assertLess(block.index('notify_set_state'), block.index('notify_post'))
        self.assertIn('notify_cancel(token)', block)

    def test_pending_settlement_survives_contact_pause(self):
        block = METAL[METAL.index('- (void)completeConstrainedWindowSettlementIfIdle {'):
                      METAL.index('- (void)catalystDrawableDidPresent:')]
        self.assertLess(block.index('if (self.nativeWindowResizeGestureActive) return;'),
                        block.index('_deferredConstrainedWindowSettlement = nil;'))
        self.assertIn('if (settlement) settlement();', block)

    def test_consumer_reads_current_state_not_notification_edge_guess(self):
        self.assertIn('notify_get_state(_nativeResizeGestureToken, &state)', METAL)
        self.assertIn('MacWSResizeGestureWriter(state)', METAL)
        self.assertIn('return kill(writer, 0) == 0 || errno == EPERM;', METAL)
        self.assertIn('token != strongSelf->_nativeResizeGestureToken', METAL)
        self.assertIn('32 * NSEC_PER_MSEC', METAL)

    def test_catalog_and_autonomous_follow_cannot_bypass_native_gesture(self):
        self.assertIn('if (_metalView.nativeWindowResizeGestureActive) policyOnly = YES;', APP)
        self.assertIn('BOOL configurationPending =\n'
                      '                _metalView.nativeWindowResizeGestureActive ||', APP)

    def test_scene_follow_checks_visible_bounds_not_fixed_axis_proposal(self):
        block = METAL[METAL.index('- (void)scheduleWindowConfiguration {'):
                      METAL.index('- (void)refreshPresentationPolicy {')]
        self.assertIn('BOOL reached = MacWSWindowSceneContentMatchesTarget(\n'
                      '            self.bounds.size.width, self.bounds.size.height, density,', block)
        self.assertIn('CGSize requested = visibleLogicalSize;', block)
        self.assertNotIn('< 6.25', block)

    def test_subscribe_preserves_only_live_exact_target_scene_follow(self):
        block = METAL[METAL.index('- (void)configureStreamMode:'):
                      METAL.index('- (uint64_t)inputSceneIDWithModifiers:')]
        self.assertIn('windowID == _sceneResizeFollowWindowID', block)
        self.assertIn('self.targetPID == _sceneResizeFollowOwnerPID', block)
        self.assertIn('CACurrentMediaTime() <= _sceneResizeFollowDeadline', block)
        self.assertIn('if (!preserveSceneFollow)', block)
        suspend = METAL[METAL.index('- (void)suspendStream {'):]
        self.assertIn('[self cancelSceneResizeFollowingTargetWindow];', suspend)
        self.assertLess(suspend.index('_acceptsCatalystDrawables = NO;'),
                        suspend.index('[self cancelSceneResizeFollowingTargetWindow];'))
        self.assertLess(suspend.index('[self cancelSceneResizeFollowingTargetWindow];'),
                        suspend.index('[_streamClient unsubscribe]'))

    def test_animation_successor_is_bound_to_owner_and_native_input_wins(self):
        block = APP[APP.index('- (void)updateNativeSceneSizeForAppliedLogicalSize:'):
                    APP.index('- (void)viewWillDisappear:')]
        self.assertIn('_deferredAppKitSceneWindowID == expectedWindowID', block)
        self.assertIn('_deferredAppKitSceneOwnerPID ==', block)
        self.assertIn('reason:@"appkit-animation-latest"', block)
        self.assertIn('if (!strongSelf->_metalView.nativeWindowResizeGestureActive &&', block)
        self.assertIn('currentSceneSize.height >= restrictionMinimum.height', block)
        self.assertIn('sceneTarget.height = ceil(sceneTarget.height)', block)
        self.assertIn('BOOL geometryTransactionBusy = configurationPending ||\n'
                      '                _metalView.nativeWindowResizeGestureActive ||', APP)


if __name__ == '__main__':
    unittest.main()

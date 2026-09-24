"""Cross-component protocol contracts; not a visual acceptance test."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
HOST = (ROOT / "MacWSHost/main.m").read_text()
TWEAK = (ROOT / "MacWSWindowing/Tweak.x").read_text()
NOTIFY = (ROOT / 'include/macws_windowing_notify.h').read_text()


class BridgeCapabilities(unittest.TestCase):
    def test_producer_and_consumers_share_one_wire_contract(self):
        for source in (HOST, TWEAK, (ROOT / 'misc/macws_control_probe.c').read_text()):
            self.assertIn('macws_windowing_notify.h', source)
        for source in (HOST, TWEAK):
            self.assertIn('@MACWS_WINDOWING_REQUEST_DIRECTORY', source)

    def test_real_observers_precede_capability_publication(self):
        body = TWEAK.split('static void MacWSInstallRequestObservers(', 1)[1]
        self.assertLess(body.index('MacWSHandleInitialSizeRequest'),
                        body.index('MacWSPublishWindowingCapabilities(NULL'))
        self.assertIn('BOOL denseGridPath = leafGridMethod || ios16GridPath;', body)
        self.assertIn('if (initialMethod && denseGridPath)', body)

    def test_ios16_grid_capability_requires_the_real_legacy_entry_point(self):
        self.assertIn(
            '@"nearestGridSizeForProposedSize:inBounds:contentOrientation:'
            'layoutRestrictionInfo:screenScale:chamoisLayoutAttributes:"',
            TWEAK)
        self.assertIn(
            'BOOL ios16GridPath = ios16GridMethod && !countOnStageGridMethod;',
            TWEAK)
        self.assertIn(
            'class_getInstanceMethod(object_getClass((id)self),\n'
            '                                countOnStageSelector)',
            TWEAK)

    def test_readiness_requires_live_publisher_identity(self):
        self.assertIn('MacWSWindowingStateSupports(state, required)', NOTIFY)
        self.assertIn('KERN_PROC_PID', NOTIFY)
        self.assertIn('strcmp(process.kp_proc.p_comm, "SpringBoard") == 0', NOTIFY)

    def test_notify_service_can_republish_without_restarting(self):
        self.assertIn('notify_post(MACWS_WINDOWING_REFRESH_NAME)', NOTIFY)
        self.assertIn('CFSTR(MACWS_WINDOWING_REFRESH_NAME)', TWEAK)
        self.assertNotIn('MacWSDenseGridLoaded', TWEAK)


if __name__ == '__main__':
    unittest.main()

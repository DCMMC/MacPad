"""Source/ABI guardrails; physical swipe screenshots remain the acceptance test."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class LaunchpadRouting(unittest.TestCase):
    def test_input_catalog_keeps_system_surfaces_out_of_selectable_scenes(self):
        source = (ROOT / 'macwsdisplayd/main.m').read_text().split(
            'static void SendWindowList(', 1)[1].split(
            'static void BroadcastWindowList(', 1)[0]
        self.assertIn('CopyCompleteDesktopWindowInfo()', source)
        self.assertIn('MACWS_STREAM_KEY_SYSTEM_INPUT_WINDOWS', source)
        self.assertIn('kCGWindowIsOnscreen', source)
        self.assertIn('@"LPSpringboard"', source)
        self.assertIn('if (!metricsValue) continue;', source)

    def test_snapshot_changes_and_disconnect_clear_modal_ownership(self):
        client = (ROOT / 'MacWSHost/MacWSStreamClient.m').read_text()
        self.assertIn('if (!connected) self.systemInputWindows = @[];', client)
        self.assertIn('[fingerprint appendBytes:&systemCount', client)
        self.assertIn('self.systemInputWindows = systemWindows;', client)
        self.assertIn('if (index >= 8) return false;', client)

    def test_modal_hit_test_precedes_final_composite_appkit_catalog(self):
        host = (ROOT / 'MacWSHost/Rendering/MacWSMetalView.m').read_text()
        resolver = host.split('- (BOOL)resolveFullscreenLayerAtPoint:', 1)[1]
        self.assertLess(resolver.index('_streamClient.systemInputWindows'),
                        resolver.index('[self resolveFinalCompositeCatalogAtPoint:'))
        self.assertIn('ownerPID == [self dockSystemGestureTargetPID]', host)
        proxy = (ROOT / 'libmachook/mac_hooks.m').read_text()
        self.assertIn('CGEventCreateScrollWheelEvent2', proxy)
        self.assertIn('123 /* CG momentum phase */', proxy)


if __name__ == '__main__':
    unittest.main()

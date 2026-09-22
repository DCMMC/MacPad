"""Source contracts for real-location refresh; device callbacks are acceptance."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = (ROOT / "macwslocationd/main.m").read_text()
LAUNCHER = (ROOT / "MacWSCatalystLauncher/main.m").read_text()


class LocationRefresh(unittest.TestCase):
    def test_missing_provider_requests_real_refresh_before_waiting(self):
        body = LAUNCHER.split("static bool macws_wait_for_location_provider(void) {", 1)[1].split("static bool macws_configure_maps_environment", 1)[0]
        self.assertLess(body.index("notify_post(MACWS_LOCATION_REFRESH_NOTIFICATION)"),
                        body.index("for (unsigned attempt = 0; attempt < 300"))
        self.assertIn("if (macws_live_location_provider_ready()) return true;", body)
        self.assertIn("return false;", body)

    def test_refresh_does_not_set_authorization_or_publish_readiness(self):
        body = BRIDGE.split("- (void)reconnectLocationClientForReason:", 1)[1].split("- (void)locationManagerDidChangeAuthorization:", 1)[0]
        self.assertNotIn("setAuthorizationStatusByType", body)
        self.assertNotIn("macws_location_provider_ready", body)
        self.assertIn("initWithEffectiveBundleIdentifier:", body)
        self.assertIn("stopUpdatingLocation", body)
        self.assertIn("now - self.lastRefreshTime < 3.0", body)

    def test_callbacks_reject_retired_manager(self):
        for signature in ("didUpdateLocations:", "didFailWithError:"):
            body = BRIDGE.split(signature, 1)[1].split("\n}", 1)[0]
            self.assertIn("if (manager != self.locationManager) return;", body)

    def test_registration_readback_is_bounded_and_never_sets_permission(self):
        body = BRIDGE.split("- (void)startAuthorizedLocationClient:", 1)[1].split("- (void)locationManagerDidChangeAuthorization:", 1)[0]
        self.assertIn("authorizationStatusForBundleIdentifier:", body)
        self.assertIn("kCLAuthorizationStatusNotDetermined && attempt < 8", body)
        self.assertIn("manager != self.locationManager", body)
        self.assertNotIn("setAuthorizationStatus", body)


if __name__ == "__main__":
    unittest.main()

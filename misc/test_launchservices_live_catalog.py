"""Exercise the live-catalog repair predicate without touching device state."""
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "layout/usr/macOS/bin/macos_gui.sh").read_text()


def function(name):
    start = SOURCE.index(name + "() {")
    return SOURCE[start:SOURCE.index("\n}\n", start) + 2]


class LaunchServicesLiveCatalog(unittest.TestCase):
    def run_repair(self, verifier_status):
        with tempfile.TemporaryDirectory() as directory:
            script = f'''set -eu
CHROOTEXEC=fake_exec
ROOTFS=/fixture-rootfs
WORKSPACECTL_BIN=/fixture-workspacectl
LAUNCHSERVICES_VERIFY_LOG={directory}/verify.log
VERIFY_STATUS={verifier_status}
fake_exec() {{
    printf 'verify:%s\\n' "$*"
    return "$VERIFY_STATUS"
}}
seed_launchservices_database() {{ printf 'seed-called\\n'; }}
log() {{ printf 'log:%s\\n' "$*"; }}
{function('verify_launchservices_database_for_desktop_repair')}
verify_launchservices_database_for_desktop_repair
'''
            return subprocess.run(
                ["bash"], input=script, text=True, capture_output=True,
                check=True, timeout=10).stdout

    def test_valid_live_catalog_does_not_need_marker_or_reseed(self):
        output = self.run_repair(0)
        self.assertIn("verified without rebuilding", output)
        self.assertNotIn("seed-called", output)
        self.assertIn("verify-launchservices-catalog", function(
            "verify_launchservices_database_for_desktop_repair"))
        self.assertNotIn("LAUNCHSERVICES_CATALOG_MARKER", function(
            "verify_launchservices_database_for_desktop_repair"))

    def test_failed_live_catalog_rebuilds(self):
        self.assertIn("seed-called", self.run_repair(1))

    def test_cold_session_always_uses_clean_seed_and_typed_verification(self):
        seed = function("seed_launchservices_database")
        self.assertIn("-kill -seed", seed)
        self.assertIn("verify-launchservices-catalog", seed)
        self.assertNotIn("LAUNCHSERVICES_CATALOG_MARKER", seed)
        self.assertNotIn("launchservices_source_fingerprint", SOURCE)


if __name__ == "__main__":
    unittest.main()

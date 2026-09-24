"""Keep restored rootfs LaunchServices provisioning ahead of stock lsd."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "layout/usr/macOS/bin/macos_gui.sh").read_text()


class LaunchServicesDataVaultContract(unittest.TestCase):
    def test_root_store_is_provisioned_with_private_mode(self):
        self.assertIn(
            "LSD_SYSTEM_DATA_VAULT_DIR=/var/folders/zz/"
            "zyxvpxvq6csfxvn_n0000000000000/0/"
            "com.apple.LaunchServices.dv",
            SCRIPT)
        function = SCRIPT.split(
            "ensure_launchservices_session_user_dir() {", 1)[1].split(
                "\n}", 1)[0]
        self.assertIn(
            'local system_data_vault="$ROOTFS$LSD_SYSTEM_DATA_VAULT_DIR"',
            function)
        self.assertIn('mkdir -p "$system_data_vault" || return 1', function)
        self.assertIn('chmod 0700 "$system_data_vault" || return 1', function)

    def test_provisioning_precedes_both_lsd_jobs(self):
        start = SCRIPT.index(
            'log "Publishing the private macOS LaunchServices system store')
        system_load = SCRIPT.index('launchctl load "$LSD_SYSTEM_PLIST"', start)
        session_load = SCRIPT.index('launchctl load "$LSD_PLIST"', start)
        provision = SCRIPT.index(
            "ensure_launchservices_session_user_dir || {", start)
        self.assertLess(provision, system_load)
        self.assertLess(provision, session_load)


if __name__ == "__main__":
    unittest.main()

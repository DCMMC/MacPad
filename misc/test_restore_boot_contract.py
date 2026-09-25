"""Guard the iOS-side repairs required by a filtered macOS rootfs restore."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
BIND = (ROOT / "layout/usr/macOS/bin/ensure_jb_usr_bind.sh").read_text()
AUTOSIGND = (ROOT / "layout/usr/macOS/bin/restart_autosignd.sh").read_text()
CONTROL = (ROOT / "control").read_text()
MAKEFILE = (ROOT / "Makefile").read_text()
PACKAGE_POSTINST = (ROOT / "layout/DEBIAN/postinst").read_text()


class RestoreBootContract(unittest.TestCase):
    def test_bind_probe_uses_the_ios_system_mount_binary(self):
        self.assertIn("system_mount=/sbin/mount", BIND)
        self.assertIn('[ -x "$system_mount" ]', BIND)
        self.assertEqual(BIND.count('if "$system_mount" | grep -Fq'), 1)
        self.assertIn('   "$system_mount" | grep -Fq', BIND)

    def test_filtered_restore_recreates_only_the_volatile_tmp_directory(self):
        self.assertIn("ROOTFS=/var/mnt/rootfs", AUTOSIGND)
        self.assertIn(
            '[ ! -f "$ROOTFS/System/Library/CoreServices/SystemVersion.plist" ]',
            AUTOSIGND)
        self.assertIn('mkdir -p "$ROOTFS/private/tmp" || exit 1', AUTOSIGND)
        self.assertIn('chmod 1777 "$ROOTFS/private/tmp" || exit 1', AUTOSIGND)
        self.assertIn('ln -s private/tmp "$ROOTFS/tmp" || exit 1', AUTOSIGND)
        self.assertLess(
            AUTOSIGND.index("SystemVersion.plist"),
            AUTOSIGND.index('mkdir -p "$ROOTFS/private/tmp"'))

    def test_package_declares_ios_tools_used_during_postinstall(self):
        depends = next(
            line.removeprefix("Depends:").strip()
            for line in CONTROL.splitlines() if line.startswith("Depends:"))
        self.assertEqual(
            {item.strip() for item in depends.split(",")},
            {"gawk", "ldid", "odcctools", "plutil", "python3"})

    def test_package_survives_dpkg_fat_macho_thinning(self):
        self.assertIn(
            'arm64="$(THEOS_STAGING_DIR)/usr/macOS/lib/libmachook_arm64.dylib"',
            MAKEFILE)
        self.assertIn('lipo "$$fat" -thin arm64 -output "$$arm64"', MAKEFILE)
        self.assertIn('lipo "$$fat" -thin arm64e -output "$$arm64e"', MAKEFILE)
        self.assertIn('elif [ -f "$LIBMACHOOK_ARM64" ]; then', PACKAGE_POSTINST)
        self.assertIn(
            '"$MACHO_PATCHER" "$LIBMACHOOK_ARM64" || exit 1',
            PACKAGE_POSTINST)

    def test_package_rejects_bootstraps_without_dynamic_trustcache_support(self):
        self.assertIn(
            'if [ ! -x /var/jb/usr/bin/jbctl ]; then',
            PACKAGE_POSTINST)
        self.assertIn(
            'NathanLR cannot admit the macOS shared-cache closure',
            PACKAGE_POSTINST)


if __name__ == "__main__":
    unittest.main()

"""Guard the production metadata adapter, not a protection-check bypass."""
from pathlib import Path
import os
import sys
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class IOSurfaceProtectionContract(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "darwin", "uses native libdispatch")
    def test_actual_initializer_concurrent_publication_and_retry(self):
        source = (ROOT / "libmachook/mac_hooks.m").read_text()
        start = source.index("static BOOL macws_iosurface_protection_abi_ready(void) {")
        end = source.index("\nuint64_t macws_IOSurfaceClientGetProtectionOptions", start)
        fixture = (ROOT / "misc/test_iosurface_protection_init.c").read_text()
        fixture = fixture.replace("/* PRODUCTION_READINESS_FUNCTION */",
                                  source[start:end])
        with tempfile.TemporaryDirectory(prefix="macws-protection-init-") as td:
            binary = str(Path(td) / "test")
            subprocess.run([os.environ.get("CC", "cc"), "-x", "c", "-std=c11",
                            "-D_DARWIN_C_SOURCE", "-fblocks", "-Wall", "-Wextra",
                            "-Werror", "-", "-o", binary], input=fixture,
                           text=True, check=True)
            subprocess.run([binary], check=True, timeout=7)
            for mismatch in range(1, 5):
                subprocess.run([binary, str(mismatch)], check=True, timeout=7)

    def test_actual_value_and_failure_contract(self):
        with tempfile.TemporaryDirectory(prefix="macws-protection-test-") as td:
            binary = str(Path(td) / "test")
            subprocess.run([os.environ.get("CC", "cc"), "-std=c11", "-Wall",
                            "-Wextra", "-Werror", "-I", str(ROOT / "include"),
                            str(ROOT / "misc/test_iosurface_protection_abi.c"),
                            "-o", binary], check=True)
            subprocess.run([binary], check=True)

    def test_all_entry_points_preserve_checks_and_default_enable(self):
        source = (ROOT / "libmachook/mac_hooks.m").read_text()
        section = source.split("// IOSurface protection metadata compatibility.", 1)[1]
        section = section.split("// IOSurface per-plane-layout compatibility.", 1)[0]
        for entry in ("IOSurfaceGetProtectionOptions",
                      "IOSurfaceClientGetProtectionOptions"):
            self.assertIn(f"DYLD_INTERPOSE(macws_{entry},", section)
            self.assertIn(f"return {entry}(", section)
        self.assertIn('sel_registerName("protectionOptions")', section)
        self.assertIn("method_setImplementation", section)
        self.assertIn("macws_macho_uuid_matches", section)
        self.assertIn('strcmp(build, "20D67")', section)
        self.assertIn("memcmp(base + 0x3df8", section)
        self.assertIn("g_macws_iosurface_protection_method(surface, cmd)", section)
        for forbidden in ("MSHookFunction(", "ModifyExecutableRegion(",
                          "getenv(", "access(", "return 0;", "return YES;"):
            self.assertNotIn(forbidden, section)


if __name__ == "__main__":
    unittest.main()

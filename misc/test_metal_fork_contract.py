"""Source guard for the native-Metal -> fork executable-page regression."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class NativeMetalForkContract(unittest.TestCase):
    def test_native_loader_does_not_patch_objc_superclass_dispatch(self):
        source = (ROOT / "libmachook/mac_hooks.m").read_text()
        for obsolete in ("autda_at", "XPACD_X16", "MACWS_AGX_OBJC_AUTDA_PATCH"):
            self.assertTrue(obsolete not in source, f"obsolete global patch: {obsolete}")
        # The driver-local branch-stub repair remains valid and is distinct
        # from rewriting the destination in the shared libobjc text page.
        self.assertTrue("macws_repair_agx_objc_msgsend_super2_stub" in source)
        # Removing the workaround must not disable real native AGX setup.
        self.assertIn("macws_agx_native_enabled()", source)
        self.assertIn("macws_agx_register_classes_enabled()", source)
        self.assertIn('dlsym(RTLD_DEFAULT, "objc_readClassPair")', source)

    def test_smoke_checks_pixels_and_fork_not_just_process_uptime(self):
        source = (ROOT / "misc/macws_metal_fork_smoke.m").read_text()
        self.assertIn("MTLCommandBufferStatusCompleted", source)
        self.assertIn("getBytes:pixels", source)
        self.assertIn("fork-child-reached-main", source)
        self.assertIn("WEXITSTATUS(status) == 0", source)
        self.assertIn("after == before", source)
        self.assertIn("alarm(15)", source)

    def test_selector_inspection_is_debug_only(self):
        source = (ROOT / "libmachook/mac_hooks.m").read_text()
        start = source.index("// Runtime diagnostic: dump the cstring")
        end = source.index("// (Removed LAZY", start)
        inspection = source[start:end]
        self.assertIn("if (macws_agx_native_enabled() && "
                      "macws_runtime_diagnostics_enabled())", inspection)
        self.assertIn("MACWS_AGX_SEL_DIAG", inspection)


if __name__ == "__main__":
    unittest.main()

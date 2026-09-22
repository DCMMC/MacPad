"""Source contracts for diagnostic-only caching; no device or compiler needed.

These assertions cover the real cache macro and call sites. They do not
pretend to be a measured CPU/temperature or native Metal acceptance test.
"""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "libmachook/Metal_hooks.x").read_text()
GATES = {
    "macws_owned_no_read_enabled": "/tmp/macws_owned_no_read",
    "macws_owned_unlocked_read_enabled": "/tmp/macws_owned_unlocked_read",
    "macws_texture_stride_diag_enabled": "/private/tmp/macws_texture_stride_diag",
    "macws_geekbench_numeric_diag_enabled": "/tmp/macws_geekbench_numeric_diag",
}


def section(start, end):
    return SOURCE.split(start, 1)[1].split(end, 1)[0]


class MetalDiagnosticHotPath(unittest.TestCase):
    def test_all_selected_gates_are_default_off_diagnostics(self):
        manifest = {}
        for line in (ROOT / "docs/runtime-switches.tsv").read_text().splitlines():
            if not line or line.startswith("#"):
                continue
            fields = line.split("\t")
            if len(fields) >= 3:
                manifest[tuple(fields[:2])] = fields[2]
        for function, path in GATES.items():
            with self.subTest(path=path):
                self.assertEqual(manifest[("flag", path)], "off")
                invocation = (rf"MACWS_DEFINE_STARTUP_FLAG\({function},\s*"
                              + re.escape('"' + path + '"') + r"\)")
                self.assertEqual(len(re.findall(invocation, SOURCE)), 1)
                # No remaining direct per-frame/per-allocation access site.
                self.assertNotRegex(SOURCE, r"access\(\s*" + re.escape('"' + path + '"'))
                self.assertNotIn(f"static BOOL {function}(void)", SOURCE)

    def test_real_macro_only_queries_unresolved_state(self):
        macro = section("#define MACWS_DEFINE_STARTUP_FLAG", "\n\nMACWS_DEFINE_STARTUP_FLAG")
        self.assertIn("static _Atomic int cached = -1;", macro)
        self.assertIn("atomic_load_explicit(&cached, memory_order_acquire)", macro)
        guarded = macro.split("if (value < 0) {", 1)[1].split("} \\", 1)[0]
        self.assertIn("value = access(path_literal, F_OK) == 0;", guarded)
        self.assertIn("atomic_store_explicit(&cached, value, memory_order_release);", guarded)
        self.assertEqual(macro.count("access("), 1)
        self.assertIn("return value != 0;", macro)

    def test_owned_frame_sync_and_producer_contract_unchanged(self):
        body = section("static BOOL macws_vnc_publish_owned_texture(",
                       "static BOOL macws_vnc_publish")
        self.assertIn("if (macws_owned_no_read_enabled())", body)
        self.assertIn("BOOL unlockedRead = macws_owned_unlocked_read_enabled();", body)
        self.assertIn("IOSurfaceLock(surface, kIOSurfaceLockReadOnly, NULL)", body)
        self.assertIn("IOSurfaceUnlock(surface, kIOSurfaceLockReadOnly, NULL)", body)
        self.assertIn("MTLPixelFormatBGRA8Unorm", body)
        self.assertNotIn("access(\"/tmp/macws_owned_", body)

    def test_buffer_adapter_runs_before_optional_observation(self):
        body = section("static id macws_geekbench_new_buffer_with_length_compat(\n",
                       "static void macws_install_geekbench_transfer_memory_compatibility")
        self.assertIn("macws_geekbench_native_buffer_options(options)", body)
        self.assertLess(body.index("self, selector, length, native_options)"),
                        body.index("if (native_options != options &&"))
        self.assertIn("macws_geekbench_numeric_diag_enabled()", body)
        installer = section("static void macws_install_geekbench_numeric_diagnostics(void)",
                            "static ")
        self.assertIn("if (!macws_geekbench_numeric_diag_enabled()) return;", installer)
        self.assertIn('strcmp(program, "geekbench_aarch64")', installer)

    def test_dynamic_requests_and_circuit_breakers_stay_live(self):
        for path in (
            "/tmp/macws_capture_final",
            "/tmp/macws_inband_pf550",
            "/tmp/macws_allow_unsafe_pf550_capture",
            "/tmp/macws_catalyst_direct_drawable_active",
        ):
            with self.subTest(path=path):
                self.assertIn(f'access("{path}", F_OK)', SOURCE)
                self.assertNotRegex(SOURCE, r"MACWS_DEFINE_STARTUP_FLAG\([^,]+,\s*"
                                    + re.escape('"' + path + '"'))
        self.assertIn('unlink("/tmp/macws_inband_pf550")', SOURCE)
        self.assertIn('open("/tmp/macws_capture_final", O_RDONLY | O_CLOEXEC)', SOURCE)


if __name__ == "__main__":
    unittest.main()

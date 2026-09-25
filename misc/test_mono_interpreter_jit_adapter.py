#!/usr/bin/env python3
"""Source invariants for the opt-in Unity Mono interpreter/JIT adapter."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "libmachook/mac_hooks.m"


class MonoInterpreterJITAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE.read_text()

    def test_interpreter_uses_public_interp_only_mode(self):
        body = self.source.split(
            "static void macws_configure_mono_interpreter_if_requested", 1
        )[1].split("void loadImageCallback", 1)[0]
        self.assertIn('"mono_jit_set_aot_mode");', body)
        self.assertIn("kMonoAOTModeInterpOnly = 8", body)
        self.assertIsNone(re.search(r"(?m)^\s*\*use_interpreter\s*=", body))

    def test_late_import_rebind_is_explicitly_opt_in(self):
        body = self.source.split(
            "static void macws_rebind_mono_jit_write_protect_if_requested", 1
        )[1].split(
            "static void macws_configure_mono_interpreter_if_requested", 1
        )[0]
        self.assertIn('getenv("MACWS_MONO_INTERPRETER")', body)
        self.assertIn('getenv("MACWS_MONO_JIT_COMPAT")', body)
        self.assertIn('getenv("MACWS_JIT_MPROTECT_COMPAT")', body)
        self.assertIn('"_pthread_jit_write_protect_np"', body)
        self.assertIn("pthread_jit_write_protect_np_new", body)

    def test_rebind_is_scoped_to_verified_unity_mono_uuid(self):
        body = self.source.split(
            "static void macws_rebind_mono_jit_write_protect_if_requested", 1
        )[1].split(
            "static void macws_configure_mono_interpreter_if_requested", 1
        )[0]
        self.assertIn('strstr(image_path, "/libmonobdwgc-2.0.dylib")', body)
        self.assertIn("macws_macho_uuid_matches(header, mono_uuid)", body)
        self.assertIn(
            "0xe0, 0x90, 0xf9, 0xf3, 0x50, 0x91, 0x3c, 0x8e", body
        )

    def test_rebind_precedes_interpreter_configuration(self):
        callback = self.source.split("void loadImageCallback", 1)[1].split(
            "__attribute__((constructor)) void InitStuff", 1
        )[0]
        self.assertLess(
            callback.index("macws_rebind_mono_jit_write_protect_if_requested"),
            callback.index("macws_configure_mono_interpreter_if_requested"),
        )

    def test_jit_reservations_and_executable_subranges_are_separate(self):
        capacity = re.search(
            r"#define MACWS_JIT_RESERVATION_CAPACITY (\d+)u", self.source
        )
        self.assertIsNotNone(capacity)
        self.assertGreaterEqual(int(capacity.group(1)), 4096)
        self.assertIn("g_macws_jit_ranges[MACWS_JIT_RESERVATION_CAPACITY]", self.source)
        self.assertIn(
            "g_macws_jit_exec_ranges[MACWS_JIT_EXEC_RANGE_CAPACITY]", self.source
        )
        mprotect = self.source.split("int mprotect_new", 1)[1].split(
            "int munmap_new", 1
        )[0]
        self.assertIn("macws_jit_update_exec_range", mprotect)

    def test_forwarded_fault_trace_uses_preopened_binary_record(self):
        record = self.source.split("static void macws_jit_record_range", 1)[1]
        record = record.split("static void macws_jit_remove_range", 1)[0]
        self.assertIn('open("/tmp/macws_jit_forwarded.bin"', record)
        handler = self.source.split(
            "static void macws_jit_exec_barrier_sigbus", 1
        )[1].split("static void macws_jit_ensure_exec_barrier_handler", 1)[0]
        self.assertIn("MacWSJITForwardRecord record", handler)
        self.assertIn(".version = 2", handler)
        self.assertIn("record.registers[index]", handler)
        self.assertIn("record.link_register", handler)
        self.assertIn("record.stack_pointer", handler)
        self.assertIn("(void)write(record_fd, &record, sizeof(record))", handler)
        self.assertLess(
            handler.index("(void)write(record_fd, &record, sizeof(record))"),
            handler.index("macws_jit_forward_sigbus(signo, info, context)"),
        )

    def test_clone_diagnostic_is_opt_in_and_exact_uuid_scoped(self):
        body = self.source.split(
            "static void macws_install_mono_clone_diagnostic_if_requested", 1
        )[1].split(
            "static void macws_rebind_mono_jit_write_protect_if_requested", 1
        )[0]
        self.assertIn('getenv("MACWS_MONO_CLONE_DIAGNOSTICS")', body)
        self.assertIn("macws_macho_uuid_matches(header, mono_uuid)", body)
        self.assertIn("+ 0x1f1d18", body)
        self.assertIn("memcmp(target, expected, sizeof(expected))", body)
        self.assertIn("0x58000050", body)
        self.assertIn("g_macws_mono_object_copy_internal", body)

if __name__ == "__main__":
    unittest.main()

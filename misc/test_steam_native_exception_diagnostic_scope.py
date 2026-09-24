#!/usr/bin/env python3
"""Source invariants for the opt-in ElleKit exception-port diagnostic."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "libmachook/Diagnostics/MacWSSteamPipeDiagnostics.m"


class SteamNativeExceptionDiagnosticScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE.read_text()

    def test_requires_explicit_diagnostic_switch(self):
        body = self.source.split(
            "static kern_return_t MacWSSteamDiagnosticTaskSetExceptionPorts", 1
        )[1].split("DYLD_INTERPOSE", 1)[0]
        self.assertIn('getenv("MACWS_STEAM_NATIVE_EXCEPTION_DIAGNOSTICS")', body)

    def test_scope_includes_only_top_level_helper_or_runtime_shadow(self):
        body = self.source.split(
            "static bool MacWSIsSteamNativeExceptionDiagnosticTarget", 1
        )[1].split("static bool MacWSIsAnySteamHelper", 1)[0]
        self.assertIn('strcmp(program, "Steam Helper") == 0', body)
        self.assertIn('strncmp(arguments[index], "--type=", 7)', body)
        self.assertIn('"/steamapps/macws-runtime/"', body)

    def test_only_cydiasubstrate_call_is_refused(self):
        body = self.source.split(
            "static kern_return_t MacWSSteamDiagnosticTaskSetExceptionPorts", 1
        )[1].split("DYLD_INTERPOSE", 1)[0]
        self.assertIn('strstr(caller.dli_fname, "/CydiaSubstrate.framework/")', body)
        self.assertIn("return KERN_FAILURE;", body)
        self.assertIn("return task_set_exception_ports", body)


if __name__ == "__main__":
    unittest.main()

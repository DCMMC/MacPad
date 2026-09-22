"""Check actual success-log blocks, not a global stderr suppression policy."""
from pathlib import Path
import unittest


SOURCE = (Path(__file__).resolve().parents[1] / "libmachook/mac_hooks.m").read_text()


def guarded_block(marker):
    """Return the closest containing braced if, including an enclosing else."""
    position = SOURCE.index(marker)
    limit = position
    while limit > 0:
        start = SOURCE.rfind("if (", 0, limit)
        if start < 0:
            break
        opening = SOURCE.index("{", start, position)
        depth = 1
        ending = opening + 1
        while depth and ending < len(SOURCE):
            depth += (SOURCE[ending] == "{") - (SOURCE[ending] == "}")
            ending += 1
        if not depth and ending > position:
            return SOURCE[start:opening].strip(), SOURCE[opening:ending]
        limit = start
    raise AssertionError(f"diagnostic is not inside a braced if: {marker}")


class RuntimeSuccessLogging(unittest.TestCase):
    def test_success_observations_require_existing_diagnostic_switch(self):
        for marker in (
            '"message=10054 task-self=%u bridge-port=%u\\n"',
            '"#### MACWS-QUICKLOOK-XPC connection owner installed "',
        ):
            with self.subTest(marker=marker):
                condition, block = guarded_block(marker)
                self.assertEqual(condition, "if (macws_runtime_diagnostics_enabled())")
                self.assertIn("dprintf(STDERR_FILENO,", block)
                # The entire observation, including argument evaluation, is guarded.
                self.assertNotIn("return", block)
                self.assertNotIn("method_setImplementation", block)

    def test_nonzero_transport_and_mapping_results_stay_visible(self):
        cases = (
            ('"#### CORESERVICES-MAP-BRIDGE client-result pid=%d "',
             "if (result != MACH_MSG_SUCCESS || macws_runtime_diagnostics_enabled())"),
            ('"object=%u size=%#llx result=%#x address=%#llx\\n"',
             "if (*resultOut != KERN_SUCCESS || macws_runtime_diagnostics_enabled())"),
            ('"#### MACWS-STEAM-OVERLAY label optimization %s at %p "',
             "if (sequence[kFormatCallIndex] != replacement || macws_runtime_diagnostics_enabled())"),
        )
        for marker, expected in cases:
            with self.subTest(marker=marker):
                condition, block = guarded_block(marker)
                self.assertEqual(" ".join(condition.split()), expected)
                self.assertIn("dprintf(STDERR_FILENO,", block)
                self.assertNotIn("return", block)

    def test_real_setup_protocol_and_install_errors_not_diagnostic_only(self):
        # These existing error branches must not accidentally be moved into
        # the neighboring success-only gate or a blanket dprintf filter.
        for marker in (
            '"message=10054 setup-failed\\n"',
            '"object=%u message-result=%#x\\n"',
            '"#### MACWS-QUICKLOOK-XPC connection owner unavailable "',
            '"instruction precondition mismatch at %p\\n"',
        ):
            with self.subTest(marker=marker):
                condition, block = guarded_block(marker)
                self.assertNotIn("macws_runtime_diagnostics_enabled", condition)
                self.assertIn("dprintf(STDERR_FILENO,", block)
        self.assertNotIn("#define dprintf", SOURCE)


if __name__ == "__main__":
    unittest.main()

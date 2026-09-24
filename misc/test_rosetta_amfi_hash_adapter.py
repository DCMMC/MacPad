"""Guard the Rosetta AMFI hash bridge as an opt-in diagnostic only."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "libmachook/mac_hooks.m").read_text()


class RosettaAMFIHashAdapter(unittest.TestCase):
    def setUp(self):
        self.adapter = SOURCE.split(
            "static bool macws_rosetta_amfi_public_key_hash_compat(", 1
        )[1].split("int __mac_syscall_new(", 1)[0]

    def test_adapter_is_exactly_scoped(self):
        for witness in (
            'getenv("MACWS_ROSETTA_AMFI_PUBLIC_KEY_HASH")',
            'strcmp(program, "oahd") != 0',
            'strcmp(policy, "AMFI") != 0',
            'operation != 0x5c',
            'originalResult != -1',
            'originalErrno != ENOSYS',
            'output->length != CC_SHA256_DIGEST_LENGTH',
        ):
            self.assertIn(witness, self.adapter)

    def test_adapter_rejects_non_hex_or_wrong_length(self):
        self.assertIn(
            "strlen(hex) != CC_SHA256_DIGEST_LENGTH * 2", self.adapter
        )
        self.assertIn("if (high < 0 || low < 0)", self.adapter)

    def test_native_policy_runs_before_fallback(self):
        wrapper = SOURCE.split(
            "int macws_sandbox_ms(", 1
        )[1].split("int csr_get_active_config_new(", 1)[0]
        self.assertLess(
            wrapper.index("int result = __sandbox_ms"),
            wrapper.index("macws_rosetta_amfi_public_key_hash_compat"),
        )

    def test_no_shipped_job_enables_diagnostic(self):
        for path in [*ROOT.glob("layout/**/*.plist"), *ROOT.glob("misc/*.plist")]:
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertNotIn(
                    b"MACWS_ROSETTA_AMFI_PUBLIC_KEY_HASH", path.read_bytes()
                )


if __name__ == "__main__":
    unittest.main()

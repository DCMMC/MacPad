"""Lock the MTLCompilerService adapter to RE-verified executable identities."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "MTLCompilerBypassOSCheck/Tweak.x").read_text()


class MTLCompilerServiceIdentity(unittest.TestCase):
    def setUp(self):
        self.adapter = SOURCE.split(
            "static void InstallMacOSMetalTargetAdapter(void) {", 1
        )[1].split("static void InstallLegacyTargetBypasses(void) {", 1)[0]

    def test_verified_ios_16_identities_are_both_allowlisted(self):
        self.assertIn(
            "0x6d, 0x2c, 0xfe, 0x56, 0x8d, 0x88, 0x39, 0xaa,",
            self.adapter,
        )
        self.assertIn(
            "0xb4, 0x74, 0x53, 0x94, 0x88, 0xd0, 0x37, 0x39,",
            self.adapter,
        )
        self.assertIn(
            "iOS 16.3.1 (20D67) UUID 6D2CFE56-8D88-39AA-BC25-7FFE5058ED4E",
            self.adapter,
        )
        self.assertIn(
            "iOS 16.0   (20A8372) UUID B4745394-88D0-3739-9E17-4DE2FB12B00E",
            self.adapter,
        )

    def test_unknown_service_builds_keep_stock_behavior(self):
        self.assertIn("static const uint8_t expectedUUIDs[][16]", self.adapter)
        self.assertIn("if (!uuidMatches)", self.adapter)
        self.assertIn("MTLCompilerService UUID mismatch", self.adapter)
        self.assertIn("return;", self.adapter)

    def test_each_allowlisted_build_still_requires_instruction_validation(self):
        for offset in ("0x20e8", "0x25f0", "0x2628"):
            self.assertIn(offset, self.adapter)
        self.assertIn("if (*callSite != callSites[i].expected)", self.adapter)
        self.assertIn("const uint32_t expectedReplyCall = 0x9400047c", self.adapter)
        self.assertIn("target adapter: validation failed", self.adapter)


if __name__ == "__main__":
    unittest.main()

import importlib.util
import pathlib
import unittest


MODULE_PATH = (
    pathlib.Path(__file__).parents[1]
    / "layout/usr/macOS/bin/merge_third_party_entitlements.py"
)
SPEC = importlib.util.spec_from_file_location("macws_entitlements", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class MergeThirdPartyEntitlementsTests(unittest.TestCase):
    def test_preserves_vendor_identity_and_merges_keychain_groups(self):
        vendor = {
            "com.apple.application-identifier": "TEAM.game",
            "com.apple.developer.team-identifier": "TEAM",
            "keychain-access-groups": ["TEAM.shared", "TEAM.game"],
            "com.apple.security.network.client": True,
        }
        project = {
            "keychain-access-groups": ["com.apple.springboard", "TEAM.game"],
            "get-task-allow": True,
            "platform-application": True,
        }

        result = MODULE.merge_entitlements(project, vendor)

        self.assertEqual(result["com.apple.application-identifier"], "TEAM.game")
        self.assertEqual(result["com.apple.developer.team-identifier"], "TEAM")
        self.assertEqual(
            result["keychain-access-groups"],
            ["TEAM.shared", "TEAM.game", "com.apple.springboard"],
        )
        self.assertTrue(result["com.apple.security.network.client"])
        self.assertTrue(result["get-task-allow"])
        self.assertNotIn("platform-application", result)

    def test_rejects_malformed_array_entitlement(self):
        with self.assertRaises(ValueError):
            MODULE.merge_entitlements(
                {"keychain-access-groups": ["valid", 7]}, {}
            )


if __name__ == "__main__":
    unittest.main()

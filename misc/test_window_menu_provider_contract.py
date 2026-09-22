"""Multi-process menu identity regressions; not a substitute for device QA."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
HOST = (ROOT / "MacWSHost/main.m").read_text()
CLIENT = (ROOT / "MacWSHost/MacWSMenuClient.m").read_text()


class MenuProviderIdentity(unittest.TestCase):
    def test_scene_checks_use_request_identity(self):
        self.assertIn("snapshot.representedWindowID == self->_windowID", HOST)
        self.assertIn("snapshot.representedOwnerPID == self->_windowOwnerPID", HOST)
        self.assertIn("selectedWindowID = snapshot.representedWindowID", HOST)

    def test_provider_stays_authoritative_for_action(self):
        self.assertIn(".ownerPID = snapshot.ownerPID", CLIENT)
        self.assertIn(".windowID = snapshot.windowID", CLIENT)
        self.assertIn(".generation = snapshot.generation", CLIENT)

    def test_request_nonce_binds_representation(self):
        self.assertIn("header->nonce != nonce", CLIENT)
        self.assertIn("snapshot.representedOwnerPID = ownerPID", CLIENT)
        self.assertIn("snapshot.representedWindowID = windowID", CLIENT)


if __name__ == "__main__":
    unittest.main()

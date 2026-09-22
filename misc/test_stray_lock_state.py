"""A stale dual-WindowServer IOKit bit must not block an unlocked iPad."""

import unittest

from misc.stray_perf_loop import ios_console_locked


class RemoteStub:
    def __init__(self, probe, ioreg):
        self.probe = probe
        self.ioreg = ioreg

    def run(self, command, **_kwargs):
        return self.probe if "ios_lock_state_probe" in command else self.ioreg


class LockStateTests(unittest.TestCase):
    def test_direct_springboard_probe_is_authoritative(self):
        self.assertEqual(ios_console_locked(RemoteStub(
            "springboard port=1 locked=1 passcode_enabled=1", "= No"))[0],
            True)
        self.assertEqual(ios_console_locked(RemoteStub(
            "springboard port=1 locked=0 passcode_enabled=1", "= Yes"))[0],
            False)

    def test_stale_ioreg_yes_is_unknown_not_locked(self):
        locked, witness = ios_console_locked(RemoteStub(
            "", '  | "IOConsoleLocked" = Yes'))
        self.assertIsNone(locked)
        self.assertIn("stale-prone-ioreg", witness)


if __name__ == "__main__":
    unittest.main()

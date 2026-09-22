"""Keep the Stray runner compatible with the live window-metrics wire ABI."""

import struct
import unittest

from misc.stray_perf_loop import parse_metrics


class WindowMetricsTests(unittest.TestCase):
    def test_v2_and_v3_share_window_identity_prefix(self):
        prefix = struct.pack("<IIIff", 386, 65, 12, 1010.0, 600.0)
        for version, entry in ((2, prefix), (3, prefix + bytes(36))):
            with self.subTest(version=version):
                payload = struct.pack("<IHHIIQ", 0x4D57474D, version,
                                      24, len(entry), 1, 7) + entry
                self.assertEqual(parse_metrics(payload), [{
                    "window": 386,
                    "flags": 65,
                    "logical_group": 12,
                    "width": 1010.0,
                    "height": 600.0,
                }])

    def test_rejects_truncated_v3_entry(self):
        payload = struct.pack("<IHHIIQ", 0x4D57474D, 3, 24, 56, 1, 7)
        self.assertEqual(parse_metrics(payload + bytes(20)), [])


if __name__ == "__main__":
    unittest.main()

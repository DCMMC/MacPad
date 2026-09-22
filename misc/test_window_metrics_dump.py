"""Local ABI fixtures for the read-only metrics inspection tool."""

import math
import unittest

from macws_window_metrics_dump import (
    HEADER, MAGIC, MAX_FILE_BYTES, MetricsError, V2_ENTRY, V3_ENTRY, decode_metrics,
)


def blob(version, *entries):
    layout = {2: V2_ENTRY, 3: V3_ENTRY}[version]
    return HEADER.pack(MAGIC, version, HEADER.size, layout.size, len(entries), 9) + b"".join(
        layout.pack(*entry) for entry in entries)


class MetricsDumpTests(unittest.TestCase):
    def test_exact_packed_abi_sizes(self):
        self.assertEqual((HEADER.size, V2_ENTRY.size, V3_ENTRY.size), (24, 20, 56))

    def test_legacy_stride_and_unavailable_ack(self):
        result = decode_metrics(blob(2, (7, 9, 7, 265, 300), (17, 2057, 17, 265, 500)))
        second = result["windows"][1]
        self.assertEqual(second["window_id"], 17)
        self.assertTrue(second["fixed_height"])
        self.assertIsNone(second["maximum_logical_size"])
        self.assertEqual(second["configuration_ack"], {
            "supported": False, "received": False, "reason": "legacy_v2_has_no_ack"})

    def test_native_maximum_and_exact_ack(self):
        result = decode_metrics(blob(3, (17, 2057, 17, 265, 500, 400, 500,
                                         12345.125, 28, 585, 500, 400, 500)))
        window = result["windows"][0]
        self.assertEqual(window["maximum_logical_size"]["width"], 400)
        self.assertEqual(window["configuration_ack"]["timestamp"], 12345.125)
        self.assertEqual(window["configuration_ack"]["sequence"], 28)
        self.assertEqual(window["configuration_ack"]["applied_logical_size"]["width"], 400)
        self.assertFalse(result["current_frame_included"])

    def test_new_producer_before_first_ack(self):
        result = decode_metrics(blob(3, (17, 9, 17, 265, 300, 400, 16384, 0, 0, 0, 0, 0, 0)))
        self.assertTrue(result["windows"][0]["configuration_ack"]["supported"])
        self.assertFalse(result["windows"][0]["configuration_ack"]["received"])

    def test_zero_timestamp_with_partial_payload_is_malformed(self):
        with self.assertRaises(MetricsError):
            decode_metrics(blob(3, (17, 9, 17, 265, 300, 400, 16384, 0, 4, 0, 0, 0, 0)))

    def test_reject_nan_dimensions_and_timestamps(self):
        for field in (3, 4, 5, 6, 7, 9, 10, 11, 12):
            entry = [17, 9, 17, 265, 300, 400, 16384, 123.5, 4, 300, 400, 300, 400]
            entry[field] = math.nan
            with self.subTest(field=field), self.assertRaises(MetricsError):
                decode_metrics(blob(3, entry))

    def test_reject_truncated_wrong_layout_and_unbounded_count(self):
        valid = blob(2, (17, 9, 17, 265, 300))
        invalid = [valid[:-1], valid + b"x", b"x" * (MAX_FILE_BYTES + 1)]
        for version, size, count in ((3, 20, 1), (2, 56, 1), (4, 56, 1), (3, 56, 257)):
            invalid.append(HEADER.pack(MAGIC, version, 24, size, count, 9))
        for data in invalid:
            with self.subTest(length=len(data)), self.assertRaises(MetricsError):
                decode_metrics(data)


if __name__ == "__main__":
    unittest.main()

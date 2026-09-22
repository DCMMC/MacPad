"""Executable kind-5 container classifier tests; fixtures contain no Apple AIR."""
import ctypes
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MACOS = b"air64-apple-macosx13.4.0"
CATALYST = b"air64-apple-ios19.0.0-macabi"


def wrapped_module(target=MACOS, bitcode_size=None):
    # Synthetic string-table witness, NOT executable/validated LLVM bitcode.
    air = b"BC\xc0\xde" + target + b"\0"
    if bitcode_size is not None:
        if len(air) > bitcode_size:
            raise ValueError("target exceeds fixture size")
        air += bytes(bitcode_size - len(air))
    value = struct.pack("<5I", 0x0B17C0DE, 0, 20, len(air), 0xFFFFFFFF) + air
    return value + bytes((-len(value)) % 16)


def request(modules=None, *, function_count=1, info_offset=28, table_offset=None):
    modules = [wrapped_module()] if modules is None else modules
    if table_offset is None:
        table_offset = (info_offset + 7) & ~7
    value = bytearray(struct.pack("<5I", function_count, info_offset,
                                  len(modules), table_offset, 0))
    # Deliberately opaque metadata: no invented function-info record grammar.
    value += bytes(table_offset - len(value))
    offset = table_offset + 8 * len(modules)
    for module in modules:
        value += struct.pack("<2I", offset, len(module))
        offset += (len(module) + 7) & ~7
    return bytes(value) + b"".join(module + bytes((-len(module)) % 8) for module in modules)


def set_word(value, offset, word):
    copy = bytearray(value)
    struct.pack_into("<I", copy, offset, word)
    return bytes(copy)


def table_offset(value):
    return struct.unpack_from("<I", value, 12)[0]


def module_offset(value, index=0):
    return struct.unpack_from("<I", value, table_offset(value) + index * 8)[0]


class MetalImageFilterRequestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="macws-image-filter-test-")
        library = Path(cls.tmp.name) / "parser.dylib"
        subprocess.run(["clang", "-shared", "-O2", "-Wall", "-Wextra", "-Werror",
                        "-x", "c", "-I", str(ROOT / "include"), "-", "-o", str(library)],
                       input=b'#include "macws_metal_image_filter_request.h"\n'
                             b'int input_target(const void *data, size_t size) {'
                             b'return MacWSMetalImageFilterGetInputTarget(data, size);}\n',
                       check=True)
        cls.lib = ctypes.CDLL(str(library))
        cls.lib.input_target.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        cls.lib.input_target.restype = ctypes.c_int

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def classify(self, value):
        return self.lib.input_target(value, len(value))

    def test_consistent_supported_targets(self):
        for target, expected in [(MACOS, 1), (CATALYST, 6)]:
            self.assertEqual(self.classify(request([wrapped_module(target)])), expected)
            self.assertEqual(self.classify(request([wrapped_module(target)] * 3)), expected)

    def test_exact_capture_geometry_without_apple_bitcode(self):
        value = request([wrapped_module(MACOS, 0x1CEF0), wrapped_module(MACOS, 0x4E7C)],
                        function_count=21, info_offset=0x174, table_offset=0x490)
        self.assertEqual(len(value), 139840)
        self.assertEqual(struct.unpack_from("<4I", value, 0x490),
                         (0x4A0, 0x1CF10, 0x1D3B0, 0x4E90))
        self.assertEqual(self.classify(value), 1)

    def test_actual_word_geometry_uses_eight_not_sixteen_byte_alignment(self):
        # raw-87122-001-5: real Word Save-alert request, SHA ca0362c3...c79b7.
        # Same synthetic module sizes, no captured Apple bitcode is embedded.
        value = request([wrapped_module(MACOS, 0x1CEF0), wrapped_module(MACOS, 0x4E7C)],
                        function_count=9, info_offset=0xB4, table_offset=0x268)
        self.assertEqual(len(value), 139288)
        self.assertEqual(struct.unpack_from("<4I", value, 0x268),
                         (0x278, 0x1CF10, 0x1D188, 0x4E90))
        self.assertEqual(module_offset(value) % 16, 8)
        self.assertEqual(self.classify(value), 1)

    def test_only_re_confirmed_statistics_flag_is_allowed_and_preserved(self):
        for target, expected in [(MACOS, 1), (CATALYST, 6)]:
            value = set_word(request([wrapped_module(target)]), 16, 0x200)
            self.assertEqual(self.classify(value), expected)
            self.assertEqual(struct.unpack_from("<I", value, 16)[0], 0x200)
        for flags in (1, 0x100, 0x201, 0x400, 0xFFFFFFFF):
            self.assertEqual(self.classify(set_word(request(), 16, flags)), 0)

    def test_module_table_length_is_unrounded_and_outer_padding_is_bounded(self):
        # The table stores originalLength; builder advances by align8(length).
        # Trim four zero bytes of the synthetic wrapper's allowed trailing pad.
        module = wrapped_module()[:-4]
        value = request([module, wrapped_module()])
        first, second = module_offset(value), module_offset(value, 1)
        self.assertEqual(second - first - len(module), 4)
        self.assertEqual(self.classify(value), 1)
        corrupt = bytearray(value)
        corrupt[first + len(module)] = 1
        self.assertEqual(self.classify(bytes(corrupt)), 0)
        last = request([module])
        self.assertEqual(self.classify(last), 1)
        self.assertEqual(self.classify(last[:-1]), 0)

    def test_opaque_metadata_is_not_validated_or_used_for_target(self):
        value = bytearray(request([wrapped_module(MACOS)], table_offset=248))
        value[32:32 + len(CATALYST)] = CATALYST
        self.assertEqual(self.classify(bytes(value)), 1)
        # This only classifies module target; Apple still rejects invalid
        # function records / synthetic LLVM. No compilation success is claimed.

    def test_all_small_fixture_truncations_rejected(self):
        value = request([wrapped_module()] * 2)
        for length in range(len(value)):
            self.assertEqual(self.classify(value[:length]), 0, length)

    def test_header_counts_flags_and_offset_bounds(self):
        value = request()
        for offset, word in [(0, 0), (0, 4097), (0, 0xFFFFFFFF),
                             (8, 0), (8, 4097), (8, 0xFFFFFFFF),
                             (16, 1), (16, 0xFFFFFFFF),
                             (4, 0), (4, 20), (4, 24), (4, 25),
                             (4, table_offset(value)), (4, 0xFFFFFFFC),
                             (12, 20), (12, 25), (12, 36), (12, len(value)),
                             (12, 0xFFFFFFFC)]:
            with self.subTest(offset=offset, word=word):
                self.assertEqual(self.classify(set_word(value, offset, word)), 0)

    def test_null_and_oversized_rejected_before_dereference(self):
        self.assertEqual(self.lib.input_target(None, 100), 0)
        self.assertEqual(self.lib.input_target(b"x", 32 * 1024 * 1024 + 1), 0)
        self.assertEqual(self.lib.input_target(b"x", ctypes.c_size_t(-1).value), 0)

    def test_alias_overlap_gap_reordered_and_trailing_rejected(self):
        value = request([wrapped_module()] * 2)
        table = table_offset(value)
        first, second = module_offset(value), module_offset(value, 1)
        for entry, offset in [(table, 0), (table, table), (table, first + 1),
                              (table, first + 16), (table, second),
                              (table + 8, first), (table + 8, second - 16),
                              (table + 8, second + 16), (table + 8, 0xFFFFFFF0)]:
            self.assertEqual(self.classify(set_word(value, entry, offset)), 0)
        self.assertEqual(self.classify(value + bytes(16)), 0)

    def test_module_lengths_rejected(self):
        value = request()
        entry = table_offset(value) + 4
        for length in [0, 16, 31, 33, len(value), 0xFFFFFFFF, 0xFFFFFFF0]:
            self.assertEqual(self.classify(set_word(value, entry, length)), 0, length)

    def test_wrapper_contract_and_bitcode_bounds(self):
        value = request()
        start = module_offset(value)
        for offset, word in [(0, 0), (4, 1), (8, 0), (8, 24), (8, 0xFFFFFFFF),
                             (12, 0), (12, 3), (12, 0xFFFFFFFF), (16, 0), (20, 0)]:
            self.assertEqual(self.classify(set_word(value, start + offset, word)), 0)

    def test_padding_must_be_bounded_zero_and_not_a_target(self):
        value = bytearray(request())
        value[-1] = 1
        self.assertEqual(self.classify(bytes(value)), 0)
        module = wrapped_module() + bytes(16)
        self.assertEqual(self.classify(request([module])), 0)
        # Excluding the triple from declared AIR must not count padding as AIR.
        value = request()
        self.assertEqual(self.classify(set_word(value, module_offset(value) + 12, 4)), 0)

    def test_unknown_native_ambiguous_and_mixed_targets_rejected(self):
        for target in [b"", b"air64-apple-ios16.3.0", b"air64-apple-macosx13.5.0",
                       MACOS + b".1", CATALYST + b"-simulator", MACOS + b"oops",
                       MACOS + b"\0" + CATALYST,
                       MACOS + b"\0air64-apple-ios16.3.0"]:
            self.assertEqual(self.classify(request([wrapped_module(target)])), 0, target)
        self.assertEqual(self.classify(request([wrapped_module(MACOS),
                                                wrapped_module(CATALYST)])), 0)

    def test_unaligned_input_buffer_supported(self):
        value = request()
        storage = ctypes.create_string_buffer(b"x" + value)
        self.assertEqual(self.lib.input_target(ctypes.addressof(storage) + 1, len(value)), 1)


if __name__ == "__main__":
    unittest.main()

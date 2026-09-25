"""Execute the real C segment translator with synthetic protocol fixtures.

Fixtures reproduce the captured framing, not GPU addresses or shader bytes.
Numeric GPU acceptance is separately recorded in docs/evidence.
"""
import ctypes
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def record():
    data = bytearray(0x1f0)
    for offset, value in [(0, 0x10000), (4, 0x1f0), (8, 4),
                          (0x28, 0x1e8), (0x2c, 0x1b8), (0x30, 0x30),
                          (0x34, 3), (0x120, 9), (0x128, 1)]:
        struct.pack_into('<I', data, offset, value)
    data[0x1e0:0x1ec] = b'\xff' * 12
    return data


def segments(count=1):
    data = bytearray(0x10 + count * 0xa0)
    struct.pack_into('<QII', data, 0, 100, count, 0x80000000 | len(data))
    for index in range(count):
        offset = 0x10 + index * 0xa0
        struct.pack_into('<QIIIIII', data, offset, 101 + index,
                         index * 0x1f0, (index + 1) * 0x1f0, 0, 0, 11, 2)
        struct.pack_into('<H', data, offset + 0x20 + 0x3e, 6)
        struct.pack_into('<H', data, offset + 0x60 + 0x3e, 5)
    return data


def resource_record(resources=11, topology=6):
    data = bytearray(0x228)
    for offset, value in [(0, 0x10000), (4, 0x228), (8, 4),
                          (0x24, 0x30), (0x28, 0x1e8),
                          (0x2c, 0x1b8), (0x30, 0x30), (0x34, 3),
                          (0x120, resources), (0x128, topology), (0x1f4, 2),
                          (0x1fc, 0x15)]:
        struct.pack_into('<I', data, offset, value)
    data[0x1e0:0x1ec] = b'\xff' * 12
    return data


def resource_segments():
    data = bytearray(0xf0)
    struct.pack_into('<QII', data, 0, 100, 1, 0x800000f0)
    struct.pack_into('<QIIIIII', data, 0x10, 101, 0, 0x228,
                     0, 0, 13, 3)
    struct.pack_into('<H', data, 0x10 + 0x20 + 0x3e, 6)
    struct.pack_into('<H', data, 0x10 + 0x60 + 0x3e, 6)
    struct.pack_into('<H', data, 0x10 + 0xa0 + 0x3e, 1)
    return data


class ComputeABITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='macws-compute-abi-')
        source = (ROOT / 'libmachook/mac_hooks.m').read_text()
        begin = source.index('static BOOL macws_agx_fragment_entry_length(')
        end = source.index('static struct macws_submit_diag_result\nmacws_inspect_agx_submit', begin)
        unit = '''
#include <stdio.h>
#include <stdbool.h>
#include <stdatomic.h>
#include "macws_agx_compute_abi.h"
typedef bool BOOL;
#define YES true
#define NO false
#define MACWS_AGX_SEGMENT_LIST_MAX_RECORDS 1024u
static _Atomic unsigned g_macws_multisegment_log_batches;
static bool macws_stray_agx_compat_enabled(void) { return false; }
static bool macws_agx_opcode_zero_compat_enabled(void) { return false; }
static bool macws_runtime_diagnostics_enabled(void) { return false; }
static bool macws_kcmd_stray_subtype3_diag_enabled(void) { return false; }
static bool macws_kcmd_field_4d0_diag_enabled(void) { return false; }
static bool macws_submit_bytes_are_zero(const unsigned char *p, size_t n) {
    while (n--) if (*p++) return false; return true;
}
static void macws_subtype1_semantic_field_diagnostic(unsigned a, unsigned b, unsigned char *p) {}
''' + source[begin:end] + '''
unsigned translate(unsigned char *commands, size_t *length,
                   unsigned char *list, size_t *list_length) {
    return macws_translate_agx_segment_list_records(0, commands, length, list, list_length);
}
'''
        library = Path(cls.tmp.name) / 'translator.dylib'
        subprocess.run(['clang', '-shared', '-O1', '-x', 'c', '-I',
                        str(ROOT / 'include'), '-', '-o', str(library)],
                       input=unit.encode(), check=True)
        cls.lib = ctypes.CDLL(str(library))
        cls.lib.translate.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t),
                                      ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
        cls.lib.translate.restype = ctypes.c_uint

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def translate(self, command, segment):
        a = ctypes.create_string_buffer(bytes(command))
        b = ctypes.create_string_buffer(bytes(segment))
        size, list_size = ctypes.c_size_t(len(command)), ctypes.c_size_t(len(segment))
        count = self.lib.translate(a, ctypes.byref(size), b, ctypes.byref(list_size))
        return count, a.raw[:size.value], b.raw[:list_size.value]

    def test_single_and_batched_ranges(self):
        for count in (1, 2, 7):
            a, b = record() * count, segments(count)
            fixed, output, out_list = self.translate(a, b)
            self.assertEqual(fixed, count)
            self.assertEqual(len(output), count * 0x1e0)
            for i in range(count):
                expected = record()[:0x1d0] + record()[0x1e0:]
                for offset, value in [(4, 0x1e0), (0x28, 0x1d8), (0x2c, 0x1a8)]:
                    struct.pack_into('<I', expected, offset, value)
                self.assertEqual(output[i * 0x1e0:(i + 1) * 0x1e0], expected)
                struct.pack_into('<II', b, 0x18 + i * 0xa0, i * 0x1e0, (i + 1) * 0x1e0)
            self.assertEqual(out_list, b)
            # Normalized iOS bytes must not be compacted twice.
            self.assertEqual(self.translate(output, out_list), (0, output, out_list))

    def test_invalid_record_unchanged(self):
        for offset in (4, 8, 0x24, 0x28, 0x2c, 0x30, 0x34, 0x1c8, 0x1d0, 0x1e0, 0x1ec):
            value = record()
            value[offset] ^= 1
            self.assertEqual(self.translate(value, segments()), (0, value, segments()), hex(offset))

    def test_invalid_resource_group_unchanged(self):
        for offset in (0x28, 0x2c, 0x6e, 0xae):
            value = segments()
            value[offset] ^= 1
            self.assertEqual(self.translate(record(), value), (0, record(), value), hex(offset))

    def test_single_mode2_resource_trailer(self):
        command = resource_record()
        segment = resource_segments()
        fixed, output, out_list = self.translate(command, segment)
        self.assertEqual(fixed, 1)
        expected = command[:0x1d0] + command[0x1e0:]
        for offset, value in [(4, 0x218), (0x28, 0x1d8), (0x2c, 0x1a8)]:
            struct.pack_into('<I', expected, offset, value)
        self.assertEqual(output, expected)
        struct.pack_into('<I', segment, 0x1c, 0x218)
        self.assertEqual(out_list, segment)
        self.assertEqual(self.translate(output, out_list),
                         (0, output, out_list))

    def test_single_mode2_resource_trailer_geekbench_topology(self):
        # Runtime fixture from Geekbench PID 42191, submit serial 971.  The
        # producer ABI is identical to the paired 11/6 control above; only
        # the resource topology fields differ.  Those fields are preserved.
        command = resource_record(resources=8, topology=0x12)
        segment = resource_segments()
        fixed, output, out_list = self.translate(command, segment)
        self.assertEqual(fixed, 1)
        expected = command[:0x1d0] + command[0x1e0:]
        for offset, value in [(4, 0x218), (0x28, 0x1d8), (0x2c, 0x1a8)]:
            struct.pack_into('<I', expected, offset, value)
        self.assertEqual(output, expected)
        self.assertEqual(struct.unpack_from('<I', output, 0x120)[0], 8)
        self.assertEqual(struct.unpack_from('<I', output, 0x128)[0], 0x12)
        struct.pack_into('<I', segment, 0x1c, 0x218)
        self.assertEqual(out_list, segment)

    def test_mode2_resource_trailer_rejects_bad_anchors(self):
        for offset in (4, 8, 0x24, 0x28, 0x2c, 0x30, 0x34,
                       0x1d0, 0x1e0, 0x1ec,
                       0x1f0, 0x1fc):
            command = resource_record()
            command[offset] ^= 1
            self.assertEqual(
                self.translate(command, resource_segments()),
                (0, command, resource_segments()), hex(offset))
        for resources in (0, 65):
            command = resource_record(resources=resources)
            self.assertEqual(
                self.translate(command, resource_segments()),
                (0, command, resource_segments()), f'resources={resources}')
        for mode in (0, 0x11):
            command = resource_record()
            struct.pack_into('<I', command, 0x1f4, mode)
            self.assertEqual(
                self.translate(command, resource_segments()),
                (0, command, resource_segments()), f'mode={mode}')

    def test_truncations_and_noncontiguous_ranges(self):
        for length in (0, 4, 0x1d0, 0x1ef):
            value = record()[:length]
            self.assertEqual(self.translate(value, segments()), (0, value, segments()))
        for offset in (0x18, 0x1c):
            value = segments()
            value[offset] ^= 1
            self.assertEqual(self.translate(record(), value), (0, record(), value))


if __name__ == '__main__':
    unittest.main()

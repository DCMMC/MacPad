"""Replay Stray's exact failed serial 11 through the production C translator.

PID 23491, 2026-09-20: the first-error recorder retained seven complete
records, six already translated and one macOS subtype-3 record at 0x1040.
The compressed fixtures contain command/list metadata, not shader/user data.
GPU completion and visible output require separate on-device acceptance.
"""
import base64
import ctypes
import hashlib
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
import zlib


ROOT = Path(__file__).resolve().parents[1]
KCMD_SHA256 = "546b160333e77983a4c96f394cb8640e2b5bcd3a46293678934235582966228d"
LIST_SHA256 = "42275ab1b210eea04a26b586c666b06de587bdfe543a3c89d9a4aeca8e810eea"
KCMD_ZLIB = """
eNrtmr9vEzEUx58vl+SoglQCkZJQQZAq1IGBVoAQDK0gc8WCxFy6AYIJdcO0AUVdmgEkxo79ExgZEKrE0hEEEj/KwA
aIkJAJ/OM5XK/X+JLLlfTOT3KcPvtOtu97z5/4FYBAxQGwYW8rsvI7C7DJyln2nQjvayhAdLZu/bT4/Ssg634tBbcg
D5dwrEkwuU5qvhOe1j8uOwizsagcZxUO+7bnsX6p2un8mNRJ9VAhRk+1+16+cDmEcxznvQUFSJLRWfZy77JB9Z1flv
1tjc5qe7SPugWNfypevJtdO+JeUxKFoG05LgfLoOaI/UEWEnL+STa5RjTP48h2Rnpy7HNRNEk+gD75YDFqPlhuSj6g
TcMHgcJmM558QDR8QJLFB5Xu3JEPHhk+GAofaHRWI8ngg/cR84HSr+GDEdMIfeXwONIkcj3dfDBl9b6eM8FX1vM5kd
/F60nTxUjiUl3uc7c996fZX8Jfx7qB9TOsSQ/df/Bo3t0+pxlO2hMn9uVlHaL5xU3xuLecAl+3x4z55tUz5SOkGbGe
FBcn5dLJRUevEy9HbkZ+ztTBc6aO4chA1okVRx7D/b1q+e/f5xRHWvHmyJzunGnVcGQYfSud2Rqd1axkcOTHA3TOVD
QcOZzf7OJz5jiPI3fh3znTKcEN02Xuv4M9uf9qCG44Q6I+f2rj+VPbcEOgcNqOJzekNNyQSjg3rBluGAo3aHRWSyWD
Gz4ZbkgoN9zM8DhyYRc3LKS5/7wPNwySt2pEzQ1WC88bWoYbAlkrXnkrFWbs3vG8avfmhusk53v9t5XLmM79UfLVFy
2Vd/iD9oNy2V+vkwP18+atvrM9c8lyccOTpHEDgIcbSBh9962z/45NqMOu7pSOJn311C83fI6YG5R+Td5qxNgSdVXj
f1zbmbda0uStGqy8YT03XHmrexHlragj97nGQ8/969LvYD2OdbHe0uattkPkrbJYL+wn5Icwvzgp/pWJvhX75zobw4
2CfIZjfET1L4T7H/A+U9J/lPvnSt38FbWkX4gFrpzg/g3UEfefFvz5dIL77xPo5rtO4iXcP433Uvmxv5keXhg=
"""
LIST_ZLIB = """
eNrNlb1PFEEYxt9hzztyOWUxMUEqQmLiRy6BQlo3xpCojRUYKP3IWRhtJBBIbhONLQXRkNCY2FhSClLwVwBRE1R6oL
Cgg+eZ953bPb1mwxW3m+fenck88/Gbmfe+31kedCJSgY4rkv6wMp+Rfmk9V6AS66B70CQ0UxW5hjhV1fpOcvaWhL2W
5QZ+Z6EX0C3oBKpbWw7wr7/knSXfB/3zCAvQmO9b5Cu06No9iUmiMH7q+6AfU5WDskgNcRqFN/SH9jDHjNc1Uila0h
2xM+in8SGbJFY2a13iE5+TT9yBz0YBPrHx+euUT+yb6Jt4Flq3A+1Cey0+gVck+8aHbBqXlQ/9AzqcZ0IeG7aux/j+
4Nrn1olP2P+PTtfF+VVtTo9y3jisrV+/uTa+kflXEN5C73HgD1FzxHNvvpGcL8h5b9Ta/1+2Pq7t07Cu7xQa6tH93y
q4/5+N6xI8LxFfGevE5XxJdleUbs2fAvp/Gx+y2RvN9n+oR/PHt4L5I8/nNuJEAT70/zE+ZHNyU/ncRcVVxAs9eH42
C54fciGfd5ZDLkLNHJvtWna3OJ+0lWF1/EvSfv/C3dNUW5Yn6Osp9Ax6brmg4UJ7JwfGl2xf17PzF/JzyD+bXco/zC
Vz7v/8E3JJyD+pzx9lGbezQyZfoFWX5dXEmLAQmz+yfx9n/ibvllO+D6D70EOnnvW+jG+a4+esB5bOAI6zgYo=
"""


def fixture():
    commands = zlib.decompress(base64.b64decode(KCMD_ZLIB))
    segments = zlib.decompress(base64.b64decode(LIST_ZLIB))
    assert len(commands) == 0x2d70 and len(segments) == 0x7f0
    assert hashlib.sha256(commands).hexdigest() == KCMD_SHA256
    assert hashlib.sha256(segments).hexdigest() == LIST_SHA256
    return bytearray(commands), bytearray(segments)


def entries(segments):
    cursor = 0x10
    for _ in range(struct.unpack_from('<I', segments, 8)[0]):
        yield cursor
        cursor += 0x20 + 0x40 * struct.unpack_from('<I', segments, cursor + 0x1c)[0]
    assert cursor == len(segments)


def expected_output(commands, segments, record_index):
    offsets = list(entries(segments))
    start, end = struct.unpack_from('<II', segments, offsets[record_index] + 8)
    result = commands[:start + 0x1d0] + commands[start + 0x1e0:]
    for offset, value in ((4, end - start - 0x10), (0x28, 0x1d8), (0x2c, 0x1a8)):
        struct.pack_into('<I', result, start + offset, value)
    result_list = bytearray(segments)
    for index in range(record_index, len(offsets)):
        a, b = struct.unpack_from('<II', result_list, offsets[index] + 8)
        struct.pack_into('<II', result_list, offsets[index] + 8,
                         a - (0x10 if index > record_index else 0), b - 0x10)
    return result, result_list


class StrayOpcodeZeroABI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='macws-stray-zero-opcode-')
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
static bool stray;
static bool opcode_zero_compat;
static bool macws_stray_agx_compat_enabled(void) { return stray; }
static bool macws_agx_opcode_zero_compat_enabled(void) {
    return opcode_zero_compat;
}
static bool macws_runtime_diagnostics_enabled(void) { return false; }
static bool macws_kcmd_stray_subtype3_diag_enabled(void) { return false; }
static bool macws_submit_bytes_are_zero(const unsigned char *p, size_t n) {
    while (n--) if (*p++) return false; return true;
}
static void macws_subtype1_semantic_field_diagnostic(unsigned a, unsigned b, unsigned char *p) {}
''' + source[begin:end] + '''
unsigned translate(unsigned char *commands, size_t *length,
                   unsigned char *list, size_t *list_length, bool is_stray,
                   bool enable_opcode_zero_compat) {
    stray = is_stray;
    opcode_zero_compat = enable_opcode_zero_compat;
    return macws_translate_agx_segment_list_records(0, commands, length, list, list_length);
}
'''
        library = Path(cls.tmp.name) / 'translator.dylib'
        subprocess.run(['clang', '-shared', '-O1', '-x', 'c', '-I',
                        str(ROOT / 'include'), '-', '-o', str(library)],
                       input=unit.encode(), check=True)
        cls.lib = ctypes.CDLL(str(library))
        cls.lib.translate.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t),
                                     ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t),
                                     ctypes.c_bool, ctypes.c_bool]
        cls.lib.translate.restype = ctypes.c_uint

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def translate(self, commands, segments, stray=True, opcode_zero=False):
        a, b = ctypes.create_string_buffer(bytes(commands)), ctypes.create_string_buffer(bytes(segments))
        n, z = ctypes.c_size_t(len(commands)), ctypes.c_size_t(len(segments))
        fixed = self.lib.translate(a, ctypes.byref(n), b, ctypes.byref(z),
                                   stray, opcode_zero)
        return fixed, a.raw[:n.value], b.raw[:z.value]

    def test_exact_seven_record_capture_preserves_opaque_bytes_and_ranges(self):
        commands, segments = fixture()
        expected, expected_list = expected_output(commands, segments, 2)
        self.assertEqual(hashlib.sha256(expected).hexdigest(),
                         '0b5cf361716aa998bb2f1400fe801549abc588da2aa9ca829565c58e07342c1e')
        result = self.translate(commands, segments)
        self.assertEqual(result, (1, expected, expected_list))
        self.assertEqual(self.translate(result[1], result[2]), (0, result[1], result[2]))

    def test_non_stray_opcode_zero_unchanged(self):
        commands, segments = fixture()
        self.assertEqual(self.translate(commands, segments, False), (0, commands, segments))

    def test_exact_game_scope_admits_opcode_zero(self):
        commands, segments = fixture()
        expected, expected_list = expected_output(commands, segments, 2)
        self.assertEqual(
            self.translate(commands, segments, False, True),
            (1, expected, expected_list),
        )

    def test_opcode_four_remains_generic(self):
        commands, segments = fixture()
        struct.pack_into('<I', commands, 0x1048, 4)
        expected, expected_list = expected_output(commands, segments, 2)
        for stray in (False, True):
            self.assertEqual(self.translate(commands, segments, stray), (1, expected, expected_list))

    def test_unknown_opcodes_unchanged(self):
        for opcode in (1, 2, 3, 5, 0xffffffff):
            commands, segments = fixture()
            struct.pack_into('<I', commands, 0x1048, opcode)
            for stray in (False, True):
                with self.subTest(opcode=opcode, stray=stray):
                    self.assertEqual(self.translate(commands, segments, stray), (0, commands, segments))

    def test_existing_family_anchors_required(self):
        for offset in (0x24, 0x28, 0x2c, 0x30, 0x34, 0x1cc, 0x1d0, 0x1e0, 0x1ec, 0x1f0, 0x1fc):
            commands, segments = fixture()
            commands[0x1040 + offset] ^= 1
            with self.subTest(offset=hex(offset)):
                self.assertEqual(self.translate(commands, segments), (0, commands, segments))

    def test_invalid_list_and_out_of_bounds_range_unchanged(self):
        for offset, value in ((8, 0), (8, 1025), (0x0c, 0x800007e0),
                              (0x250 + 0x0c, 0x2d71)):
            commands, segments = fixture()
            struct.pack_into('<I', segments, offset, value)
            with self.subTest(offset=hex(offset), value=hex(value)):
                self.assertEqual(self.translate(commands, segments), (0, commands, segments))

    def test_resource_and_mode_bounds_remain_required(self):
        for offset, value in ((0x120, 0), (0x120, 65), (0x1f4, 0), (0x1f4, 17)):
            commands, segments = fixture()
            struct.pack_into('<I', commands, 0x1040 + offset, value)
            with self.subTest(offset=hex(offset), value=value):
                self.assertEqual(self.translate(commands, segments), (0, commands, segments))

    def test_single_record_dispatch_uses_same_scope_and_opcode_rule(self):
        commands, segments = fixture()
        command = commands[0x1040:0x1268]
        # Extract the real selected resource entry, adjusting only its range
        # and enclosing list header to create a one-record dispatch control.
        entry = bytearray(segments[0x250:0x330])
        struct.pack_into('<II', entry, 8, 0, len(command))
        single = bytearray(segments[:0x10]) + entry
        struct.pack_into('<II', single, 8, 1, 0x80000000 | len(single))
        expected, expected_list = expected_output(command, single, 0)
        self.assertEqual(self.translate(command, single), (1, expected, expected_list))
        self.assertEqual(self.translate(command, single, False), (0, command, single))
        self.assertEqual(
            self.translate(command, single, False, True),
            (1, expected, expected_list),
        )
        struct.pack_into('<I', command, 8, 4)
        expected, expected_list = expected_output(command, single, 0)
        self.assertEqual(self.translate(command, single, False), (1, expected, expected_list))


if __name__ == '__main__':
    unittest.main()

"""Replay the exact Weather submit through the real C ABI translator.

The zlib fixtures contain only the captured command/list bytes, not shader
code, user files, or images. They are SHA-bound to Weather PID21575, serial2.
GPU completion/pixels remain a separate on-device acceptance requirement.
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
KCMD_SHA256 = "e7204ba6b15f529ab638ffbdcf7d6da09a1a02b915b822db5cdbab648fd00fe7"
LIST_SHA256 = "1c045ab427c0c24d63ae1f94c59bb6b53c8e22e7365624376b72ce0df9b93e72"
# Keep the exact capture representation rather than synthesizing only fields
# accepted by the predicate under test. This also protects its opaque trailer.
KCMD_ZLIB = """
eNrtmM1Kw0AQx2fzgYH2EIOKB4O5iTcRLwqSHPoQPoLgybvgGvMgPkY9CDn0LXyBnnroQfAUs18aQ2I+ulvSugPL
pjvTZHb6z/THAiC4cQAsqLezfBzmMfMddo3IIg5gH9TZixHS+wcQ9nqOCXfgwRXL9V8aq5vY/xGfxeesYJuwm72Y
5TmBUaX/gs8z4ccnTDc4aqWf8OmYl2axaJdRy7joU2rc93ua8q/lWUcGuXK541Lpe7kRZv4SO1pF70J3VoPukhr/
+q2kywZdIWjXIkX/mM6vT6HQQ2RnL/RMknL46GsO/f9gA9XvX1uDGSVd4QOAcT6fk/LhMe03u+V1zhXQkSuSWDFX
xCnjCpxqruhjvG7bwhWe4Aqzun97giu43+3IFRnOuvFC67gPqXFlrqA/aIErsK25QiZXCN1ZDbpLzIFyRYOuunLF
q2KuEHrWXDFcriCt5ocfprTf3JNauqtzxe2z6vMKn59X+Jorepm/lVwxs/7u7xOb+YPOXPGoiCuWUuMquQIVzitG
mitUcEXSoDvLHipXLKVyxds6uAJprhg0VzjF84p32m8eSC0L61/G8cGq
"""
LIST_ZLIB = """
eNrNkDsOgkAURR8wGEIwQRuh0Pj/f1Zg4jJchlpbsCQbEy3ci6XL8N5hFIJa2PEm55FJ5r4XzvmxngjKATtHkou5
s7aevCsCCjRBC3RAF/RAH7R/YJmjgEhFBuhDMAJjwGVT85YLinmlk0rPYH6GPgcLPVtkCVaFzMbAn0q3J3oG8zG6
D2ogAFVQ53srzdxtkVBSeE9wAp13df5q/NDNKSy/n8OffvbGi59zQ1fKzpy8vnQVaEO0k+Zvxg/deHHmp1FSP9Gf
fo5f/LBs69OPSN4Py5Un0K4mGw==
"""


def fixture():
    commands = zlib.decompress(base64.b64decode(KCMD_ZLIB))
    segments = zlib.decompress(base64.b64decode(LIST_ZLIB))
    assert len(commands) == 0x1908 and len(segments) == 0x370
    assert hashlib.sha256(commands).hexdigest() == KCMD_SHA256
    assert hashlib.sha256(segments).hexdigest() == LIST_SHA256
    return bytearray(commands), bytearray(segments)


class ZeroDepthABI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="macws-zero-depth-")
        source = (ROOT / "libmachook/mac_hooks.m").read_text()
        begin = source.index("static unsigned macws_translate_agx_wrapped_single_subtype1(")
        end = source.index("static struct macws_submit_diag_result\nmacws_inspect_agx_submit", begin)
        direct_zero = source.split("BOOL subtype1_direct_field_4d0_zero_valid =", 1)[1].split(";", 1)[0]
        direct_ffff = source.split("BOOL subtype1_direct_field_4d0_ffff_compat =", 1)[1].split(";", 1)[0]
        direct_predicate = "allow_fix && off == 0 &&" + source.split(
            "if (allow_fix && off == 0 &&", 1)[1].split(") {", 1)[0]
        unit = '''
#include <stdio.h>
#include <stdbool.h>
#include <stdatomic.h>
#include "macws_agx_compute_abi.h"
typedef bool BOOL;
#define YES true
#define NO false
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
unsigned wrapped(unsigned kind, unsigned char *commands, size_t *length,
                 unsigned char *list, size_t *list_length) {
    if (kind == 1) return macws_translate_agx_wrapped_single_subtype1(
        0, commands, length, list, list_length);
    return macws_translate_agx_trailing_wrapped_subtype1(
        0, commands, length, list, *list_length);
}
'''
        unit += '''
bool direct_eligible(unsigned char *commands, size_t total,
                     unsigned char *list, size_t segment_length) {
    if (total < 0x818 || segment_length < 0x20) return false;
    size_t off = 0;
    int allow_fix = 1;
    uintptr_t segment_start = (uintptr_t)list;
    uint32_t type = *(uint32_t *)commands;
    uint32_t end_offset = *(uint32_t *)(commands + 0x28);
    uint32_t size = *(uint32_t *)(commands + 0x2c);
    uint32_t inner = *(uint32_t *)(commands + 0x30);
    uint32_t subtype = *(uint32_t *)(commands + 0x34);
    BOOL subtype1_direct_field_4d0_zero_valid = ''' + direct_zero + ''';
    BOOL subtype1_direct_field_4d0_ffff_compat = ''' + direct_ffff + ''';
    return ''' + direct_predicate + ''';
}
'''
        library = Path(cls.tmp.name) / "translator.dylib"
        subprocess.run(["clang", "-shared", "-O1", "-x", "c", "-I",
                        str(ROOT / "include"), "-", "-o", str(library)],
                       input=unit.encode(), check=True)
        cls.lib = ctypes.CDLL(str(library))
        cls.lib.translate.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t),
                                     ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
        cls.lib.translate.restype = ctypes.c_uint
        cls.lib.wrapped.argtypes = [ctypes.c_uint, *cls.lib.translate.argtypes]
        cls.lib.wrapped.restype = ctypes.c_uint
        cls.lib.direct_eligible.argtypes = [ctypes.c_void_p, ctypes.c_size_t,
                                           ctypes.c_void_p, ctypes.c_size_t]
        cls.lib.direct_eligible.restype = ctypes.c_bool

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def translate(self, command, segment, wrapper=0):
        a = ctypes.create_string_buffer(bytes(command))
        b = ctypes.create_string_buffer(bytes(segment))
        size, list_size = ctypes.c_size_t(len(command)), ctypes.c_size_t(len(segment))
        args = (a, ctypes.byref(size), b, ctypes.byref(list_size))
        count = self.lib.wrapped(wrapper, *args) if wrapper else self.lib.translate(*args)
        return count, a.raw[:size.value], b.raw[:list_size.value]

    def test_exact_weather_capture_default_no_flags(self):
        commands, segments = fixture()
        fixed, output, out_list = self.translate(commands, segments)
        self.assertEqual(fixed, 3)
        self.assertEqual(len(output), 0x18a8)
        expected = bytearray()
        for index, old_start in enumerate((0, 0x858, 0x10b0)):
            record = commands[old_start:old_start + 0x858]
            self.assertEqual(struct.unpack_from("<I", record, 0x4d0)[0], 0)
            normalized = record[:0x1c0] + record[0x1d0:0x4c0] + record[0x4d0:]
            for offset, value in ((4, 0x838), (0x28, 0x7f8), (0x2c, 0x7c8)):
                struct.pack_into("<I", normalized, offset, value)
            self.assertEqual(struct.unpack_from("<I", normalized, 0x4b0)[0], 0)
            expected.extend(normalized)
            struct.pack_into("<II", segments, 0x18 + index * 0x120,
                             index * 0x838, (index + 1) * 0x838)
        self.assertEqual(output, expected)
        self.assertEqual(out_list, segments)
        self.assertEqual(self.translate(output, out_list), (0, output, out_list))

    def test_bad_list_framing_not_relaxed(self):
        commands, segments = fixture()
        for offset in (8, 0x0c, 0x18, 0x1c):
            mutated = bytearray(segments)
            mutated[offset] ^= 1
            with self.subTest(offset=hex(offset)):
                self.assertEqual(self.translate(commands, mutated),
                                 (0, commands, mutated))

    def test_existing_resource_fallback_does_not_depend_on_zero_payload(self):
        # Existing unique-range fallback accepts these invalid structured
        # group fields for 1.0 as well. This is a separately reported parser
        # limitation, not a newly relaxed check in the zero-depth change.
        zero, segments = fixture()
        one = bytearray(zero)
        for start in (0, 0x858, 0x10b0):
            struct.pack_into("<I", one, start + 0x4d0, 0x3f800000)
        for offset in (0x28, 0x2c, 0x6e):
            mutated = bytearray(segments)
            mutated[offset] ^= 1
            with self.subTest(offset=hex(offset)):
                zero_result = self.translate(zero, mutated)
                one_result = self.translate(one, mutated)
                self.assertEqual(zero_result[0], one_result[0])
                self.assertEqual(zero_result[2], one_result[2])

    def test_wrapped_zero_and_one_preserve_semantic_bytes(self):
        commands, segments = fixture()
        for depth in (0, 0x3f800000):
            record = commands[:0x858]
            struct.pack_into("<I", record, 0x4d0, depth)
            direct_list = segments[:0x130]
            struct.pack_into("<II", direct_list, 8, 1, 0x80000130)
            struct.pack_into("<II", direct_list, 0x18, 0x10, 0x868)
            prefix = bytearray(0x10)
            struct.pack_into("<III", prefix, 0, 9, 0x10, 1)
            list_prefix = bytearray(0x18)
            struct.pack_into("<IIII", list_prefix, 8, 1, 0x40000001, 0, 0x10)
            fixed, output, out_list = self.translate(prefix + record, list_prefix + direct_list, 1)
            self.assertEqual((fixed, len(output), len(out_list)), (1, 0x820, 0x130))
            self.assertEqual(struct.unpack_from("<I", output, 0x4b0)[0], depth)
            self.assertEqual(struct.unpack_from("<II", out_list, 0x18), (0, 0x820))

            record = record[:0x840]
            struct.pack_into("<I", record, 4, 0x840)
            tail = struct.pack("<IIIIQ", 3, 0x18, 0x9b03, 0, 1)
            struct.pack_into("<II", direct_list, 8, 1, 0x130)
            struct.pack_into("<II", direct_list, 0x18, 0, 0x840)
            token = struct.unpack_from("<Q", direct_list)[0]
            list_tail = struct.pack("<QIIII", token, 1, 0xc0000001, 0x840, 0x858)
            fixed, output, out_list = self.translate(record + tail, direct_list + list_tail, 2)
            self.assertEqual((fixed, len(output), len(out_list)), (1, 0x838, 0x148))
            self.assertEqual(struct.unpack_from("<I", output, 0x4b0)[0], depth)
            self.assertEqual(output[0x820:], tail)
            self.assertEqual(struct.unpack_from("<II", out_list, 0x140), (0x820, 0x838))

    def test_real_direct_predicate_admits_zero_and_one_without_optin(self):
        commands, segments = fixture()
        direct = commands[:0x858]
        listing = segments[:0x130]
        struct.pack_into("<II", listing, 8, 1, 0x80000130)
        for depth, accepted in ((0, True), (0x3f800000, True),
                                (0xffff, False), (0x7fc00000, False)):
            struct.pack_into("<I", direct, 0x4d0, depth)
            self.assertEqual(self.lib.direct_eligible(
                bytes(direct), len(direct), bytes(listing), len(listing)), accepted)
        struct.pack_into("<I", direct, 0x4d0, 0)
        for offset in (4, 0x28, 0x2c, 0x30, 0x34, 0xd8, 0x1c0,
                       0x1e0, 0x1e8, 0x1f8, 0x4c0, 0x4d4, 0x4e8):
            broken = bytearray(direct)
            broken[offset] ^= 1
            self.assertFalse(self.lib.direct_eligible(
                bytes(broken), len(broken), bytes(listing), len(listing)), hex(offset))

    def test_bad_core_anchors_not_relaxed(self):
        commands, segments = fixture()
        for offset in (0x28, 0x2c, 0x30, 0x34, 0xd8, 0x1c0,
                       0x1e0, 0x1e8, 0x1f8, 0x4c0, 0x4d4, 0x4e8):
            mutated = bytearray(commands)
            # Mutate all three to prove no valid neighboring record can hide
            # an incorrectly admitted damaged record in the fixed count.
            for start in (0, 0x858, 0x10b0):
                mutated[start + offset] ^= 1
            with self.subTest(offset=hex(offset)):
                self.assertEqual(self.translate(mutated, segments),
                                 (0, mutated, segments))


if __name__ == "__main__":
    unittest.main()

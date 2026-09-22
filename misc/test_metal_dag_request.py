import ctypes
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TARGET = b"air64-apple-ios19.0.0-macabi"
MACOS_TARGET = b"air64-apple-macosx13.4.0"


def module(target=TARGET):
    air = b"BC\xc0\xde" + target + b"llvm.metadata"
    return struct.pack("<5I", 0x0B17C0DE, 0, 20, len(air), 0xFFFFFFFF) + air


def request(modules=None, version=False, triple=None):
    modules = [module()] if modules is None else modules
    data = b" gadDAGS { }\0"
    if version:
        data += b"vria" + struct.pack("<II", 2, 5)
    data += b"fmun" + struct.pack("<I", len(modules))
    for value in modules:
        if triple is not None:
            data += b"lprt" + triple + b"\0"
        data += b"ctib" + struct.pack("<I", len(value)) + value
    return data


class MetalDAGRequestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="macws-dag-test-")
        library = Path(cls.tmp.name) / "parser.dylib"
        subprocess.run(["clang", "-shared", "-O2", "-Wall", "-Werror", "-x", "c",
                        "-I", str(ROOT / "include"), "-", "-o", str(library)],
                       input=b'#include "macws_metal_dag_request.h"\n'
                             b'int accepts(const void *data, size_t size) { '
                             b'return MacWSMetalDAGHasCatalystInputs(data, size); }\n'
                             b'int input_target(const void *data, size_t size) { '
                             b'return MacWSMetalDAGGetInputTarget(data, size); }\n', check=True)
        cls.lib = ctypes.CDLL(str(library))
        cls.lib.accepts.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        cls.lib.accepts.restype = ctypes.c_int
        cls.lib.input_target.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        cls.lib.input_target.restype = ctypes.c_int

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def accepts(self, data):
        return bool(self.lib.accepts(data, len(data)))

    def input_target(self, data):
        return self.lib.input_target(data, len(data))

    def test_catalyst_modules(self):
        self.assertTrue(self.accepts(request()))
        self.assertTrue(self.accepts(request([module(), module()], version=True, triple=TARGET)))
        self.assertEqual(self.input_target(request()), 6)

    def test_ventura_coreimage_request_target(self):
        # Reduced transport fixture from runtime raw-13338-001-e: ten wrapped
        # AIR modules, each exact macOS13.4 target, no optional target/version.
        value = request([module(MACOS_TARGET)] * 10)
        self.assertEqual(self.input_target(value), 1)
        self.assertFalse(self.accepts(value))  # Distinct from existing Catalyst inputs.
        self.assertEqual(self.input_target(request([module(MACOS_TARGET)],
                                                 version=True, triple=MACOS_TARGET)), 1)

    def test_every_macos_truncation_rejected(self):
        value = request([module(MACOS_TARGET)] * 2, triple=MACOS_TARGET)
        for length in range(len(value)):
            self.assertEqual(self.input_target(value[:length]), 0, length)

    def test_mac_and_catalyst_mixture_rejected(self):
        self.assertEqual(self.input_target(request([module(), module(MACOS_TARGET)])), 0)
        self.assertEqual(self.input_target(request([module(MACOS_TARGET)], triple=TARGET)), 0)

    def test_unsupported_or_ambiguous_module_targets_rejected(self):
        for target in [b"air64-apple-ios16.3.0", b"air64-apple-macosx13.5.0",
                       TARGET + b"-simulator", MACOS_TARGET + b".1",
                       MACOS_TARGET + b"oops", TARGET + b"oops",
                       MACOS_TARGET + b"llvm.metadata" + TARGET,
                       MACOS_TARGET + b"llvm.metadataair64-apple-ios16.3.0"]:
            self.assertEqual(self.input_target(request([module(target)])), 0, target)

    def test_native_ios_unchanged(self):
        self.assertFalse(self.accepts(request([module(b"air64-apple-ios16.3.0")])))

    def test_mixed_targets_unchanged(self):
        self.assertFalse(self.accepts(request([module(), module(b"air64-apple-ios16.3.0")])))

    def test_explicit_target_must_agree(self):
        self.assertFalse(self.accepts(request(triple=b"air64-apple-macosx13.4.0")))

    def test_every_truncation_rejected(self):
        value = request([module(), module()], version=True, triple=TARGET)
        for i in range(len(value)):
            self.assertFalse(self.accepts(value[:i]), i)

    def test_unknown_trailing_data_rejected(self):
        self.assertFalse(self.accepts(request() + b"ctib"))

    def test_function_count_bounds(self):
        self.assertFalse(self.accepts(request([])))
        self.assertFalse(self.accepts(request([module()] * 4097)))

    def test_invalid_wrapped_bitcode(self):
        for offset, value in [(0, 0), (4, 1), (8, 0xFFFFFFFF), (12, 0xFFFFFFFF), (20, 0)]:
            data = bytearray(module())
            struct.pack_into("<I", data, offset, value)
            self.assertFalse(self.accepts(request([data])), offset)

    def test_unknown_tags_rejected(self):
        self.assertFalse(self.accepts(request().replace(b"ctib", b"xxxx", 1)))
        self.assertFalse(self.accepts(request().replace(b"fmun", b"xxxx", 1)))

    def test_unterminated_dag_rejected(self):
        self.assertFalse(self.accepts(b" gad" + b"A" * 64))


if __name__ == "__main__":
    unittest.main()

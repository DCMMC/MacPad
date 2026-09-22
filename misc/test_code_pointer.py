"""Execute ARM64 code pointers at both supported instruction alignments."""
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CodePointerTests(unittest.TestCase):
    @unittest.skipUnless(platform.system() == "Darwin" and platform.machine() == "arm64",
                         "requires an ARM64 Darwin host")
    def test_actual_four_and_eight_byte_aligned_functions(self):
        with tempfile.TemporaryDirectory(prefix="macws-code-pointer-") as directory:
            program = Path(directory) / "probe"
            subprocess.run(["clang", "-O2", "-Wall", "-Wextra", "-Werror",
                            str(ROOT / "misc/macws_code_pointer_probe.c"),
                            "-o", str(program)], check=True)
            result = subprocess.run([str(program)], text=True, capture_output=True, check=True)
            self.assertIn("alignment=0", result.stdout)
            self.assertIn("alignment=4", result.stdout)
            self.assertIn("result=PASS", result.stdout)

    def test_compiler_strips_without_adjacent_entry_guess(self):
        source = (ROOT / "MTLCompilerBypassOSCheck/Tweak.x").read_text()
        helper = (ROOT / "include/macws_code_pointer.h").read_text()
        self.assertIn("return MacWSCodeAddress(p);", source)
        self.assertIn("ptrauth_strip(pointer, ptrauth_key_function_pointer)", helper)
        self.assertNotIn("replyTarget += 4", source)
        self.assertNotIn("return (uintptr_t)p &", source)


if __name__ == "__main__":
    unittest.main()

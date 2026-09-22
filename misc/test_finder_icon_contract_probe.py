"""Execute the diagnostic's real empty-image failure contract on macOS."""
import pathlib
import platform
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(platform.system() == "Darwin" and shutil.which("xcrun"),
                     "requires actual AppKit and Apple clang")
class FinderIconProbeContract(unittest.TestCase):
    def test_empty_image_is_nonzero_and_cold_layer_precedes_cpu_draw(self):
        source = pathlib.Path(__file__).with_name("macws_finder_icon_contract_probe.m")
        with tempfile.TemporaryDirectory(prefix="macws-icon-contract-") as directory:
            executable = pathlib.Path(directory) / "probe"
            subprocess.run([
                "xcrun", "clang", "-fobjc-arc", "-O1", "-Wall", "-Wextra", "-Werror",
                "-Wno-deprecated-declarations", str(source), "-framework", "AppKit",
                "-framework", "CoreServices", "-framework", "QuartzCore", "-o", str(executable),
            ], check=True, capture_output=True, text=True, timeout=60)
            result = subprocess.run([str(executable), "--self-test-empty"],
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 74, result.stdout + result.stderr)
            self.assertIn("NEGATIVE_CONTROL absent-visible=0 empty-visible=0", result.stdout)
            self.assertLess(result.stdout.index("CALayer-CPU-cold"),
                            result.stdout.index("NSImage-bitmap"))


if __name__ == "__main__":
    unittest.main()

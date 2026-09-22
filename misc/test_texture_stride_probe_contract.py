"""Safety/acceptance bounds for the optional, explicitly invoked diagnostic.

These source checks are not Office or Weather rendering acceptance.
"""
from pathlib import Path
import os
import platform
import re
import shutil
import subprocess
import tempfile
import unittest

PROBE = Path(__file__).with_name("macws_texture_stride_probe.m")
SOURCE = PROBE.read_text()


class TextureStrideProbeContract(unittest.TestCase):
    def test_owned_resources_and_short_lifetime(self):
        self.assertIn("W = 19, H = 11, SOURCE_ROW = 128, OUTPUT_ROW = 256", SOURCE)
        self.assertIn("alarm(10)", SOURCE)
        for forbidden in ("IOSurfaceLookup", "task_for_pid", "NSApplication",
                          "setenv(", "getenv(", "sleep(", "raise("):
            self.assertNotIn(forbidden, SOURCE)
        self.assertEqual(SOURCE.count("newCommandQueue]"), 1)

    def test_actual_size_whitelist_and_real_storage_bound(self):
        self.assertIn('strcmp(argv[3], "--actual-size")', SOURCE)
        self.assertIn("actualSize && (bufferView || bufferCopy)", SOURCE)
        self.assertIn("uint8_t *direct = output.contents", SOURCE)
        section = SOURCE.split("static unsigned W =", 1)[1].split(
            "static void Provenance", 1)[0]
        program = "#include <assert.h>\nstatic unsigned W =" + section + r'''
int main(void) {
    assert(ConfigureShape(0));
    assert(W==19 && H==11 && SOURCE_ROW==128 && OUTPUT_ROW==256);
    assert(SURFACE_BYTES==16384);
    assert(ConfigureShape(1));
    assert(W==309 && H==250 && SOURCE_ROW==1280 && OUTPUT_ROW==1280);
    assert(SURFACE_BYTES==327680);
    assert(SOURCE_ROW*H+OUTPUT_ROW*H+SURFACE_BYTES==967680);
    assert(ConfigureNoCopyRows(0, 16384)==327680 && SOURCE_ROW==1280);
    assert(ConfigureNoCopyRows(1, 16384)==327680);
    assert(SOURCE_ROW==1248 && OUTPUT_ROW==1280 && SOURCE_ROW*H==312000);
    assert(327680+OUTPUT_ROW*H+SURFACE_BYTES==975360);
    assert(ConfigureNoCopyRows(1, 4096)==315392);
    assert(ConfigureNoCopyRows(1, 65536)==327680);
    assert(!ConfigureNoCopyRows(1, 2048));
    assert(!ConfigureNoCopyRows(1, 8193));
    assert(!ConfigureNoCopyRows(1, 131072));
    assert(ConfigureShape(0) && W==19 && H==11);
    assert(!ConfigureNoCopyRows(1, 16384));
    assert(ConfigureNoCopyRows(0, 16384)==16384 && SOURCE_ROW==256);
    return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix="macws-stride-shape-") as td:
            binary = str(Path(td) / "shape")
            subprocess.run([os.environ.get("CC", "cc"), "-x", "c", "-Wall",
                "-Wextra", "-Werror", "-", "-o", binary], input=program,
                text=True, check=True)
            subprocess.run([binary], check=True, timeout=5)

    def test_nonuniform_pixels_and_real_completion_required(self):
        self.assertIn("MTLCommandBufferStatusCompleted", SOURCE)
        self.assertIn("mismatches += bad", SOURCE)
        self.assertIn("return bad || modifiedPadding ? 12 : 0", SOURCE)
        self.assertIn("MTLRegionMake2D(3, 2, 7, 5)", SOURCE)
        self.assertIn("bytesPerImage:SOURCE_ROW * 5", SOURCE)
        self.assertIn("synchronizeResource:texture", SOURCE)
        self.assertIn('strcmp(argv[1], "ca-image")', SOURCE)
        self.assertIn('strcmp(argv[1], "buffer-view")', SOURCE)
        self.assertIn('strcmp(argv[1], "buffer-copy")', SOURCE)
        self.assertIn("h->filetype == MH_EXECUTE", SOURCE)
        self.assertNotIn("_dyld_get_image_header(0)", SOURCE)

    def test_nocopy_writes_original_memory_only_after_creation(self):
        self.assertIn('strcmp(argv[1], "nocopy-buffer-copy")', SOURCE)
        self.assertIn("page < 4096 || page > 65536 || (page & (page - 1))", SOURCE)
        self.assertIn("ownedPixelBytes > 1024 * 1024", SOURCE)
        self.assertIn("MAP_ANON | MAP_PRIVATE", SOURCE)
        self.assertIn("stackSource[noCopyMode ? 1 : SOURCE_ROW * H]", SOURCE)
        start = SOURCE.index("staging = [device newBufferWithBytesNoCopy:original")
        end = SOURCE.index("} else if (bufferView || bufferCopy)", start)
        block = SOURCE[start:end]
        self.assertIn("staging.contents == original", block)
        self.assertIn("staging.storageMode == desc.storageMode", block)
        self.assertIn("Fill(original)", block)
        self.assertNotIn("Fill(staging.contents)", block)
        self.assertNotIn("memcpy", block)
        self.assertNotIn("memset", block)
        self.assertNotIn("munmap", block.split("deallocator:^", 1)[1].split("}];", 1)[0])
        self.assertLess(block.index("Fill(original)"), block.index("[staging didModifyRange:"))
        self.assertIn("bufferCopy || noCopyBufferCopy", SOURCE)
        self.assertIn("sourceOffset:(noCopyBufferCopy ? 0 : OUTPUT_ROW)", SOURCE)
        self.assertIn("callbacks != 1 || mismatch", SOURCE)
        self.assertIn("munmap(mappedSource, mappedLength)", SOURCE)

    def test_office_stride_is_explicit_and_upload_uses_source_row(self):
        self.assertIn('strcmp(argv[3], "--office-row-stride")', SOURCE)
        self.assertIn("if (officeRowStride && !noCopyMode) return 2", SOURCE)
        self.assertIn("if (officeRowStride && (W != 309 || H != 250)) return 0", SOURCE)
        self.assertIn("SOURCE_ROW = officeRowStride ? 1248 : OUTPUT_ROW", SOURCE)
        self.assertIn("stagingRow = noCopyBufferCopy ? SOURCE_ROW : OUTPUT_ROW", SOURCE)
        self.assertIn("sourceBytesPerRow:stagingRow sourceBytesPerImage:stagingRow * H", SOURCE)

    def test_nocopy_view_preserves_managed_and_skips_upload(self):
        self.assertIn('strcmp(argv[1], "nocopy-buffer-view")', SOURCE)
        self.assertIn("noCopyMode = noCopyBufferCopy || noCopyBufferView", SOURCE)
        self.assertNotIn("noCopyBufferView && managed", SOURCE)
        self.assertIn("offset:(noCopyBufferView ? 0 : OUTPUT_ROW)", SOURCE)
        self.assertIn("bytesPerRow:(noCopyBufferView ? SOURCE_ROW : OUTPUT_ROW)", SOURCE)
        self.assertIn("else if (!bufferView && !noCopyBufferView)", SOURCE)
        self.assertIn("texture.buffer == staging && texture.bufferOffset == 0", SOURCE)
        self.assertIn("texture.bufferBytesPerRow == SOURCE_ROW", SOURCE)
        self.assertIn("result=NIL contract=FAIL", SOURCE)
        self.assertNotIn("MTL_DEBUG_LAYER", SOURCE)


@unittest.skipUnless(platform.system() == "Darwin" and shutil.which("xcrun"),
                     "stock Metal controls require a macOS SDK/device")
class TextureStrideStockControl(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="macws-stride-stock-")
        cls.addClassCleanup(cls.directory.cleanup)
        cls.binary = str(Path(cls.directory.name) / "stride")
        subprocess.run(["xcrun", "--sdk", "macosx", "clang", "-fno-objc-arc",
                        "-fblocks", "-Wall", "-Wextra", "-Werror", str(PROBE),
                        "-framework", "Foundation", "-framework", "Metal",
                        "-framework", "QuartzCore", "-framework", "CoreGraphics",
                        "-framework", "IOSurface", "-o", cls.binary],
                       check=True, timeout=60, capture_output=True, text=True)

    def check_pixels(self, mode, storage, actual_size=False, office_stride=False):
        preset = "--office-row-stride" if office_stride else "--actual-size"
        arguments = [mode, storage] + ([preset] if actual_size or office_stride else [])
        result = subprocess.run([self.binary, *arguments], capture_output=True,
                                text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("libmachook", result.stdout)
        pixels = 77250 if actual_size or office_stride else 209
        for phase in ("gpu", "getBytes"):
            self.assertIn(f"phase={phase} mismatched-pixels=0/{pixels}", result.stdout)
        self.assertIn("phase=gpu-copy status=4 error=none", result.stdout)
        self.assertIn("getBytes-modified-padding=0", result.stdout)
        bound = int(re.search(r"explicit-owned-pixel-storage-upper-bound=(\d+)",
                              result.stdout).group(1))
        self.assertLessEqual(bound, 1024 * 1024)
        return result.stdout

    def test_nocopy_small_and_actual_shared_and_managed(self):
        for storage in ("shared", "managed"):
            for actual_size in (False, True):
                with self.subTest(storage=storage, actual_size=actual_size):
                    output = self.check_pixels("nocopy-buffer-copy", storage, actual_size)
                    expected_storage = 1 if storage == "managed" else 0
                    self.assertRegex(output,
                        rf"alias=1 requested-storage={expected_storage} "
                        rf"actual-storage={expected_storage} early-callbacks=0")
                    self.assertIn("nocopy pattern-written-after-creation=1 "
                                  "target=original-mmap", output)
                    self.assertIn("phase=buffer-upload status=4 error=none", output)
                    self.assertIn("nocopy callbacks-after-drain=1 callback-mismatch=0", output)

    def test_nocopy_recorded_office_row_stride(self):
        for storage in ("shared", "managed"):
            with self.subTest(storage=storage):
                output = self.check_pixels("nocopy-buffer-copy", storage,
                                           office_stride=True)
                self.assertIn("row-stride-preset=office-1248", output)
                self.assertIn("shape=309x250 upload-bpr=1248 gpu-readback-bpr=1280", output)
                self.assertIn("buffer-upload source-offset=0 source-bpr=1248 "
                              "source-bytes-per-image=312000", output)
                self.assertIn("phase=buffer-upload status=4 error=none", output)
                self.assertIn("alias=1", output)
                self.assertIn("nocopy callbacks-after-drain=1 callback-mismatch=0", output)

    def test_nocopy_buffer_view(self):
        for storage in ("shared", "managed"):
            for preset in ("small", "actual", "office"):
                with self.subTest(storage=storage, preset=preset):
                    output = self.check_pixels("nocopy-buffer-view", storage,
                        actual_size=preset == "actual", office_stride=preset == "office")
                    row = 1248 if preset == "office" else 1280 if preset == "actual" else 256
                    self.assertIn(f"buffer-view result=non-NIL buffer-match=1 offset=0 "
                                  f"actual-bpr={row} metadata=PASS", output)
                    self.assertNotIn("phase=buffer-upload", output)
                    self.assertIn("nocopy callbacks-after-drain=1 callback-mismatch=0", output)

    def test_original_modes_preserved(self):
        for mode, storage, actual in (
                ("plain", "shared", False), ("plain", "managed", False),
                ("surface", "shared", False), ("ca-image", "shared", False),
                ("plain", "shared", True), ("surface", "shared", True),
                ("ca-image", "shared", True), ("buffer-view", "shared", False),
                ("buffer-copy", "shared", False), ("buffer-copy", "managed", False)):
            with self.subTest(mode=mode, storage=storage, actual=actual):
                self.check_pixels(mode, storage, actual)

    def test_invalid_inputs_rejected_before_resources(self):
        for args in ([], ["nocopy-buffer-copy"], ["nocopy-buffer-copy", "private"],
                     ["nocopy-buffer-view"], ["nocopy-buffer-view", "private"],
                     ["nocopy-buffer-view", "shared", "--width=309"],
                     ["nocopy-buffer-view", "managed", "--actual-size", "--office-row-stride"],
                     ["nocopy-buffer-copy", "shared", "--size=309x250"],
                     ["nocopy-buffer-copy", "shared", "--actual-size", "extra"],
                     ["nocopy-buffer-copy", "shared", "--actual-size", "--office-row-stride"],
                     ["nocopy-buffer-copy", "shared", "--office-row-stride", "--actual-size"],
                     ["plain", "shared", "--office-row-stride"],
                     ["surface", "shared", "--office-row-stride"],
                     ["ca-image", "shared", "--office-row-stride"],
                     ["buffer-copy", "shared", "--office-row-stride"],
                     ["buffer-view", "shared", "--office-row-stride"],
                     ["buffer-copy", "shared", "--actual-size"],
                     ["buffer-view", "managed"], ["unknown", "shared"]):
            with self.subTest(args=args):
                result = subprocess.run([self.binary, *args], capture_output=True,
                                        text=True, timeout=5)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()

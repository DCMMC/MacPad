"""Bounds and real stock-Metal controls for the opt-in no-copy diagnostic.

The native controls execute on macOS only. They do not load libmachook and do
not establish that the chroot contract, or Office pictures, has been repaired.
"""
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "misc/macws_nocopy_contract_probe.m"
SOURCE = PROBE.read_text()


class NoCopyProbeSourceContract(unittest.TestCase):
    def test_one_owned_page_and_bounded_lifetime(self):
        self.assertIn("alarm(10)", SOURCE)
        self.assertIn("size_t length = (size_t)getpagesize()", SOURCE)
        self.assertIn("length < 4096 || length > 65536", SOURCE)
        self.assertIn("MAP_ANON | MAP_PRIVATE", SOURCE)
        self.assertEqual(SOURCE.count("newCommandQueue]"), 1)
        self.assertEqual(SOURCE.count("[command commit]"), 1)
        for forbidden in ("IOSurfaceLookup", "task_for_pid", "NSApplication",
                          "setenv(", "getenv(", "sleep(", "fork(",
                          "launchctl", "MACWS_AGX_NATIVE"):
            self.assertNotIn(forbidden, SOURCE)

    def test_actual_memory_and_callback_contract_is_required(self):
        self.assertIn("alias = buffer.contents == original", SOURCE)
        self.assertIn("original[0] = 0x67", SOURCE)
        self.assertIn("originalMismatches += original[i] != 0x9b", SOURCE)
        self.assertIn("bufferMismatches += ((uint8_t *)buffer.contents)[i] != 0x9b", SOURCE)
        self.assertLess(SOURCE.index("[command waitUntilCompleted]"),
                        SOURCE.index("unsigned originalMismatches"))
        self.assertIn("command.status == MTLCommandBufferStatusCompleted", SOURCE)
        self.assertIn("alias && lateWrite && validStorage", SOURCE)
        self.assertIn("ownershipPassed && gpuWrite && validCommand", SOURCE)
        self.assertIn("validStorage = (NSUInteger)buffer.storageMode ==", SOURCE)
        self.assertIn("premature == 0 && finalCallbacks == 1", SOURCE)
        self.assertIn("callbacksBeforeRelease == 0", SOURCE)
        self.assertLess(SOURCE.index("callbacksBeforeRelease = atomic_load(&callbacks)"),
                        SOURCE.rindex("[buffer release]"))
        callback = SOURCE.split("deallocator:^(void *pointer, NSUInteger size)", 1)[1]
        callback = callback.split("}];", 1)[0]
        self.assertIn("pointer != original || size != length", callback)
        self.assertNotIn("munmap", callback)
        self.assertNotIn("free(", callback)

    def test_managed_is_explicit_and_diagnostic_is_not_production_linked(self):
        self.assertIn('strcmp(argv[storageArgument], "managed")', SOURCE)
        self.assertIn("#if !TARGET_OS_OSX\n    if (managed) return 2", SOURCE)
        self.assertIn("[buffer didModifyRange:NSMakeRange(0, 1)]", SOURCE)
        self.assertIn("[blit synchronizeResource:buffer]", SOURCE)
        for path in (ROOT / "Makefile", ROOT / "libmachook/Makefile",
                     ROOT / "layout/usr/macOS/bin/macos_gui.sh",
                     ROOT / "layout/usr/macOS/bin/postinst.sh"):
            with self.subTest(path=path.name):
                self.assertNotIn(PROBE.stem, path.read_text())

    def test_allocation_only_excludes_entire_gpu_path(self):
        self.assertIn('strcmp(argv[1], "allocation-only")', SOURCE)
        start = SOURCE.index("if (!allocationOnly) {")
        cursor = SOURCE.index("{", start) + 1
        depth = 1
        while depth:
            depth += (SOURCE[cursor] == "{") - (SOURCE[cursor] == "}")
            cursor += 1
        guarded = SOURCE[start:cursor]
        outside = SOURCE[:start] + SOURCE[cursor:]
        for operation in ("newCommandQueue]", "[queue commandBuffer]",
                          "[command blitCommandEncoder]", "[blit fillBuffer:",
                          "[blit synchronizeResource:", "[command commit]",
                          "[command waitUntilCompleted]"):
            with self.subTest(operation=operation):
                self.assertIn(operation, guarded)
                self.assertNotIn(operation, outside)
        self.assertIn("!allocationOnly && buffer.storageMode == MTLStorageModeManaged",
                      SOURCE)
        self.assertIn("NOCOPY allocation-only=%s gpu-contract=NOT-TESTED", SOURCE)
        ownership_result = SOURCE.index("if (allocationOnly) {", cursor)
        self.assertLess(ownership_result, SOURCE.index('printf("NOCOPY contract='))
        self.assertIn("return ownershipPassed ? 0 : 1;", SOURCE[ownership_result:])


@unittest.skipUnless(platform.system() == "Darwin" and shutil.which("xcrun"),
                     "stock Metal runtime controls require a macOS SDK/device")
class NoCopyStockMetalControl(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="macws-nocopy-stock-")
        cls.addClassCleanup(cls.directory.cleanup)
        cls.binary = str(Path(cls.directory.name) / "nocopy")
        subprocess.run(["xcrun", "--sdk", "macosx", "clang",
                        "-fno-objc-arc", "-fblocks", "-Wall", "-Wextra",
                        str(PROBE), "-framework", "Foundation", "-framework",
                        "Metal", "-o", cls.binary], check=True, timeout=60,
                       capture_output=True, text=True)

    def check_mode(self, mode, allocation_only=False):
        arguments = ["allocation-only"] if allocation_only else []
        if mode:
            arguments.append(mode)
        result = subprocess.run([self.binary, *arguments],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("libmachook", result.stdout)
        requested = "managed" if mode else "shared"
        self.assertRegex(result.stdout,
                         rf"alias=1 cpu-late-write=1 premature-callbacks=0 "
                         rf"requested={requested} actual={'1' if mode else '0'} "
                         r"storage-match=1")
        self.assertIn("callbacks-after-drain=1 callback-mismatch=0", result.stdout)
        self.assertIn("callbacks-before-buffer-release=0", result.stdout)
        if allocation_only:
            self.assertIn("NOCOPY mode=allocation-only gpu-submit=disabled", result.stdout)
            self.assertIn("NOCOPY allocation-only=PASS gpu-contract=NOT-TESTED",
                          result.stdout)
            for forbidden in ("NOCOPY contract=", "NOCOPY status=",
                              "gpu-original-mismatch=", "gpu-buffer-mismatch="):
                self.assertNotIn(forbidden, result.stdout)
        else:
            self.assertIn("NOCOPY mode=full gpu-submit=enabled", result.stdout)
            self.assertRegex(result.stdout,
                             r"status=4 error=none gpu-original-mismatch=0/\d+ "
                             r"gpu-buffer-mismatch=0/\d+ callbacks-before-release=0")
            self.assertIn("NOCOPY contract=PASS", result.stdout)
            self.assertNotIn("gpu-contract=NOT-TESTED", result.stdout)
        length = int(re.search(r"NOCOPY length=(\d+)", result.stdout).group(1))
        self.assertGreaterEqual(length, 4096)
        self.assertLessEqual(length, 65536)

    def test_stock_shared(self):
        self.check_mode(None)

    def test_stock_managed(self):
        self.check_mode("managed")

    def test_stock_shared_allocation_only(self):
        self.check_mode(None, allocation_only=True)

    def test_stock_managed_allocation_only(self):
        self.check_mode("managed", allocation_only=True)

    def test_invalid_options_rejected_before_gpu_allocation(self):
        for arguments in (["invalid"], ["shared"], ["managed", "extra"],
                          ["managed", "allocation-only"],
                          ["allocation-only", "invalid"],
                          ["allocation-only", "allocation-only"],
                          ["allocation-only", "managed", "extra"]):
            with self.subTest(arguments=arguments):
                result = subprocess.run([self.binary, *arguments],
                                        capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertNotIn("NOCOPY", result.stdout)


if __name__ == "__main__":
    unittest.main()

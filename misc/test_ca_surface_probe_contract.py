"""Safety/acceptance contract of the optional CA surface diagnostic.

These source guards do not assert that the on-device b3a8 regression is fixed.
Runtime acceptance additionally requires colored pixels for both controls.
"""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CASurfaceProbeContract(unittest.TestCase):
    def test_resources_and_lifetime_are_bounded(self):
        source = (ROOT / "misc/macws_ca_surface_probe.m").read_text()
        self.assertIn("alarm(10)", source)
        self.assertIn("width:16 height:16", source)
        self.assertIn("index<=8", source)
        self.assertIn("kCARendererMetalCommandQueue:queue", source)
        for forbidden in ("IOSurfaceLookup", "task_for_pid", "NSApplication",
                          "setenv(", "getenv(", "sleep("):
            self.assertNotIn(forbidden, source)

    def test_acceptance_requires_pixels_after_gpu_completion(self):
        source = (ROOT / "misc/macws_ca_surface_probe.m").read_text()
        self.assertIn("clear.status!=MTLCommandBufferStatusCompleted", source)
        self.assertIn("fence.status!=MTLCommandBufferStatusCompleted", source)
        self.assertLess(source.index("[fence waitUntilCompleted]"),
                        source.index("[output getBytes:"))
        self.assertIn("nonzero==64 && opaque==64?0:9", source)
        self.assertIn('strcmp(argv[1],"b3a8")', source)
        self.assertIn('strcmp(argv[1],"bgra")', source)


if __name__ == "__main__":
    unittest.main()

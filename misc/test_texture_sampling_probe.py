"""Bounded real shader controls; passing these does not prove Office renders."""
from pathlib import Path
import hashlib
import platform
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "misc/macws_texture_sampling_probe.m"
SOURCE = PROBE.read_text()
OFFICE_FIXTURE = Path("/private/tmp/macws-office-Metal2DShaders-20260919.metallib")
OFFICE_SHA256 = "9eac296d60f976a0ef4e1cfe90b440101045b4eaed437af3a6dd803997959a02"
HAS_OFFICE_FIXTURE = (OFFICE_FIXTURE.is_file() and OFFICE_FIXTURE.stat().st_size == 155074
                      and hashlib.sha256(OFFICE_FIXTURE.read_bytes()).hexdigest() == OFFICE_SHA256)


class TextureSamplingSourceContract(unittest.TestCase):
    def test_explicit_bounded_owned_resources_only(self):
        self.assertIn("alarm(10)", SOURCE)
        self.assertIn("ownedBytes > 1024 * 1024", SOURCE)
        self.assertIn("page < 4096 || page > 65536 || (page & (page - 1))", SOURCE)
        self.assertIn("MAP_ANON | MAP_PRIVATE", SOURCE)
        self.assertIn("office ? 309 : 19", SOURCE)
        self.assertIn("office ? 250 : 11", SOURCE)
        self.assertIn("office ? 1248 : 256", SOURCE)
        self.assertEqual(SOURCE.count("newCommandQueue]"), 1)
        self.assertEqual(SOURCE.count("[command commit]"), 2)  # compute helper + render helper
        self.assertEqual(SOURCE.count("RunKernel("), 3)  # definition + two calls
        self.assertEqual(SOURCE.count("RunRender("), 2)  # definition + one exclusive call
        for forbidden in ("getenv(", "setenv(", "NSApplication", "IOSurfaceLookup",
                          "task_for_pid", "sleep(", "fork(", "MTL_DEBUG_LAYER",
                          "contentsOfFile", "fopen(", "texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA"):
            self.assertNotIn(forbidden, SOURCE)
        for path in (ROOT / "Makefile", ROOT / "libmachook/Makefile",
                     ROOT / "layout/usr/macOS/bin/macos_gui.sh",
                     ROOT / "layout/usr/macOS/bin/postinst.sh"):
            self.assertNotIn(PROBE.stem, path.read_text())

    def test_actual_office_metadata_and_original_memory(self):
        self.assertIn("texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm", SOURCE)
        self.assertIn("desc.usage = MTLTextureUsageShaderRead", SOURCE)
        self.assertIn("texture.bufferBytesPerRow != row", SOURCE)
        self.assertIn("texture.mipmapLevelCount != 1 || texture.sampleCount != 1", SOURCE)
        start = SOURCE.index("input = [device newBufferWithBytesNoCopy:original")
        end = SOURCE.index("} else {", start)
        block = SOURCE[start:end]
        self.assertIn("Fill(original, width, height, row)", block)
        self.assertIn("input.contents != original", block)
        self.assertIn("input.storageMode != desc.storageMode", block)
        self.assertNotIn("memcpy", block)
        self.assertNotIn("memset", block)
        self.assertLess(block.index("Fill(original"), block.index("didModifyRange:"))
        self.assertIn("if (managed) { puts(\"managed storage is not an iOS-native control\"); return 77; }", SOURCE)

    def test_real_shader_sampling_and_errors_are_required(self):
        self.assertIn("input.read(p)", SOURCE)
        self.assertIn("input.sample(nearest, float2(p) + 0.5f)", SOURCE)
        self.assertIn("sampler nearest(coord::pixel, address::clamp_to_edge, filter::nearest)", SOURCE)
        self.assertIn("[device newLibraryWithSource:ShaderSource options:nil error:&error]", SOURCE)
        self.assertIn("if (!library) goto cleanup", SOURCE)
        self.assertIn("error.description.UTF8String", SOURCE)
        self.assertIn("command.status != MTLCommandBufferStatusCompleted", SOURCE)
        self.assertIn("mismatchedPixels == 0", SOURCE)
        self.assertIn("readPassed && samplePassed && atomic_load(&callbacks) == 0", SOURCE)
        self.assertIn("texture=NIL contract=FAIL", SOURCE)

    def test_render_is_a_real_quad_and_exact_framebuffer_check(self):
        self.assertIn("vertex Raster quad_vertex", SOURCE)
        self.assertIn("fragment float4 quad_fragment", SOURCE)
        self.assertIn("input.sample(nearest, in.position.xy)", SOURCE)
        self.assertIn("pipelineDesc.colorAttachments[0].blendingEnabled = NO", SOURCE)
        self.assertIn("targetDesc.storageMode = MTLStorageModeShared", SOURCE)
        self.assertIn("setViewport:(MTLViewport){0, 0, width, height, 0, 1}", SOURCE)
        self.assertIn("setScissorRect:(MTLScissorRect){0, 0, width, height}", SOURCE)
        self.assertIn("drawPrimitives:MTLPrimitiveTypeTriangleStrip vertexStart:0 vertexCount:4", SOURCE)
        self.assertIn("[blit copyFromTexture:target", SOURCE)
        self.assertIn("CheckPixels(output.contents, outputRow, NULL, 0, width, height, \"render\")", SOURCE)
        self.assertIn("ownedBytes = MAX(uploadPhase, renderPhase)", SOURCE)
        self.assertIn("*originalOwner = NULL", SOURCE)
        self.assertIn("render plain-source-released-before-target=1", SOURCE)
        render_branch = SOURCE.split("if (render) {\n            result = RunRender", 1)[1]
        self.assertIn("goto cleanup;", render_branch.split("readFunction =", 1)[0])

    def test_office_shader_is_exact_fixture_with_verified_bindings(self):
        self.assertIn(str(OFFICE_FIXTURE), SOURCE)
        self.assertIn(OFFICE_SHA256, SOURCE)
        self.assertIn("status.st_size != 155074", SOURCE)
        self.assertIn("strcmp(hexadecimal, OfficeLibrarySHA256)", SOURCE)
        self.assertIn("[device newLibraryWithData:payload error:error]", SOURCE)
        self.assertIn("constant.index == 0 && constant.type == MTLDataTypeBool", SOURCE)
        self.assertIn("BOOL hasStencil = NO", SOURCE)
        self.assertIn("if (officeShader && !CheckOfficeReflection(reflection)) goto cleanup", SOURCE)
        self.assertIn("MTLPipelineOptionBindingInfo | MTLPipelineOptionBufferTypeInfo", SOURCE)
        self.assertIn("setVertexBytes:transform length:sizeof(transform) atIndex:2", SOURCE)
        self.assertIn("setVertexBytes:bitmapTransform length:sizeof(bitmapTransform) atIndex:3", SOURCE)
        self.assertIn("setFragmentBytes:&opacity length:sizeof(opacity) atIndex:0", SOURCE)
        self.assertIn("setFragmentTexture:input atIndex:1", SOURCE)
        self.assertIn("setFragmentSamplerState:sampler atIndex:1", SOURCE)


@unittest.skipUnless(platform.system() == "Darwin" and shutil.which("xcrun"),
                     "stock shader controls require a macOS SDK/device")
class TextureSamplingStockControl(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="macws-sampling-stock-")
        cls.addClassCleanup(cls.directory.cleanup)
        cls.binary = str(Path(cls.directory.name) / "sampling")
        cls.compile(PROBE, cls.binary)

    @staticmethod
    def compile(source, binary):
        subprocess.run(["xcrun", "--sdk", "macosx", "clang", "-fno-objc-arc",
                        "-fblocks", "-Wall", "-Wextra", "-Werror", str(source),
                        "-framework", "Foundation", "-framework", "Metal", "-o", binary],
                       check=True, timeout=60, capture_output=True, text=True)

    def test_actual_read_and_sample_pixels(self):
        for mode in ("plain", "nocopy-buffer-view"):
            for storage in ("shared", "managed"):
                for office in (False, True):
                    with self.subTest(mode=mode, storage=storage, office=office):
                        arguments = [mode, storage] + (["--office-row-stride"] if office else [])
                        result = subprocess.run([self.binary, *arguments], capture_output=True,
                                                text=True, timeout=15)
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                        self.assertNotIn("libmachook", result.stdout)
                        self.assertIn("shader-library=non-NIL error=none", result.stdout)
                        self.assertIn("texture format=70 type=2 usage=1", result.stdout)
                        pixels = 77250 if office else 209
                        for phase in ("read", "sample"):
                            self.assertIn(f"phase={phase} status=4 error=none", result.stdout)
                            self.assertIn(f"phase={phase} mismatched-pixels=0/{pixels} "
                                          f"mismatched-bytes=0/{pixels * 4}", result.stdout)
                        self.assertIn("shader-sampling-contract=PASS", result.stdout)
                        callbacks = 1 if mode == "nocopy-buffer-view" else 0
                        self.assertIn(f"callbacks-after-drain={callbacks} callback-mismatch=0", result.stdout)
                        if callbacks:
                            self.assertIn("nocopy alias=1", result.stdout)
                            self.assertIn("pattern-written-after-creation=1 target=original-mmap", result.stdout)
                        bound = int(re.search(r"pixel-storage-bound=(\d+)", result.stdout).group(1))
                        self.assertLessEqual(bound, 1024 * 1024)

    def test_actual_shader_compile_failure_is_not_pass(self):
        broken = SOURCE.replace("using namespace metal;", "this is deliberately invalid MSL;")
        source = Path(self.directory.name) / "broken.m"
        source.write_text(broken)
        binary = str(Path(self.directory.name) / "broken")
        self.compile(source, binary)
        result = subprocess.run([binary, "plain", "shared"], capture_output=True,
                                text=True, timeout=15)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("shader-library=NIL error=", result.stdout)
        self.assertNotIn("shader-library=NIL error=none", result.stdout)
        self.assertIn("shader-sampling-contract=FAIL", result.stdout)
        self.assertNotIn("shader-sampling-contract=PASS", result.stdout)
        self.assertNotIn("phase=sample status=", result.stdout)

    def test_actual_fragment_render_pixels(self):
        for mode in ("plain", "nocopy-buffer-view"):
            for storage in ("shared", "managed"):
                for office in (False, True):
                    with self.subTest(mode=mode, storage=storage, office=office):
                        arguments = [mode, storage, "--render"] + (["--office-row-stride"] if office else [])
                        result = subprocess.run([self.binary, *arguments], capture_output=True,
                                                text=True, timeout=15)
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                        self.assertNotIn("libmachook", result.stdout)
                        self.assertIn("pipeline-mode=render", result.stdout)
                        self.assertIn("pipeline=render result=non-NIL error=none", result.stdout)
                        self.assertIn("render-target format=70 storage=0", result.stdout)
                        pixels = 77250 if office else 209
                        self.assertIn("phase=render status=4 error=none", result.stdout)
                        self.assertIn(f"phase=render mismatched-pixels=0/{pixels} "
                                      f"mismatched-bytes=0/{pixels * 4}", result.stdout)
                        self.assertIn("render-modified-padding=0", result.stdout)
                        self.assertIn("shader-render-contract=PASS", result.stdout)
                        self.assertNotIn("shader-sampling-contract=PASS", result.stdout)
                        self.assertNotIn("phase=read status=", result.stdout)
                        self.assertNotIn("phase=sample status=", result.stdout)
                        callbacks = 1 if mode == "nocopy-buffer-view" else 0
                        self.assertIn(f"callbacks-after-drain={callbacks} callback-mismatch=0", result.stdout)
                        if not callbacks:
                            self.assertIn("render plain-source-released-before-target=1", result.stdout)
                        bound = int(re.search(r"pixel-storage-bound=(\d+)", result.stdout).group(1))
                        self.assertLessEqual(bound, 1024 * 1024)

    @unittest.skipUnless(HAS_OFFICE_FIXTURE, "exact privately supplied Office fixture is not installed")
    def test_actual_office_shader_and_reflection(self):
        for mode in ("plain", "nocopy-buffer-view"):
            for storage in ("shared", "managed"):
                for office_size in (False, True):
                    with self.subTest(mode=mode, storage=storage, office_size=office_size):
                        arguments = [mode, storage, "--office-shader"]
                        if office_size:
                            arguments.append("--office-row-stride")
                        result = subprocess.run([self.binary, *arguments], capture_output=True,
                                                text=True, timeout=15)
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                        self.assertIn(f"office-fixture bytes=155074 sha256={OFFICE_SHA256}", result.stdout)
                        self.assertIn("office-function-constant name=hasStencil index=0 type=53 required=1", result.stdout)
                        self.assertIn("office-specialize name=bitmapVS hasStencil=0 result=non-NIL error=none", result.stdout)
                        self.assertIn("office-specialize name=bitmapPS hasStencil=0 result=non-NIL error=none", result.stdout)
                        self.assertIn("office-reflect vertex-mask=7 fragment-mask=7 contract=PASS", result.stdout)
                        self.assertIn("phase=render status=4 error=none", result.stdout)
                        pixels = 77250 if office_size else 209
                        self.assertIn(f"phase=render mismatched-pixels=0/{pixels} "
                                      f"mismatched-bytes=0/{pixels * 4}", result.stdout)
                        self.assertIn("shader-render-contract=PASS", result.stdout)

    def test_invalid_arguments_do_not_allocate(self):
        for arguments in ([], ["plain"], ["plain", "private"],
                          ["nocopy-buffer-copy", "shared"],
                          ["nocopy-buffer-view", "shared", "--actual-size"],
                          ["plain", "shared", "--width=309"],
                          ["plain", "shared", "--render", "--render"],
                          ["plain", "shared", "--office-row-stride", "--office-row-stride"],
                          ["plain", "shared", "--office-shader", "--office-shader"],
                          ["plain", "shared", "--office-shader", "/tmp/unapproved.metallib"],
                          ["plain", "shared", "--render", "--office-row-stride", "extra"],
                          ["plain", "shared", "--office-row-stride", "extra"]):
            with self.subTest(arguments=arguments):
                result = subprocess.run([self.binary, *arguments], capture_output=True,
                                        text=True, timeout=5)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()

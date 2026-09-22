"""Executable provisioning transactions with a fake converter, no LLVM/GPU.

The fake delegates manifest creation and verification to the repository's real
manifest module. It tests lifecycle/error handling, not AIR translation.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "misc/ensure_office_metal2metal.py"
spec = importlib.util.spec_from_file_location("office_provision", HELPER)
provision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provision)

FAKE = r'''
import hashlib,json,pathlib,plistlib,sys,time
sys.path.insert(0,REPOSITORY_MISC)
from metal2metal_manifest import build_runtime_manifest,write_runtime_manifest,verify_runtime_manifest
base=pathlib.Path(__file__).parent
with (base/'calls').open('a') as stream: stream.write(json.dumps(sys.argv[1:])+'\n')
mode=(base/'mode').read_text() if (base/'mode').exists() else ''
args=sys.argv[1:]
def option(name): return args[args.index(name)+1]
if args[0]=='verify-runtime-manifest':
    verify_runtime_manifest(pathlib.Path(args[1]),pathlib.Path(option('--source')),pathlib.Path(option('--output')))
    raise SystemExit(0)
assert args[0]=='translate'
assert '--function' not in args and '--in-place-air-target' not in args
assert option('--profile')=='ventura13-ios19-macabi' and '--auto-lower-known-air' in args
if mode=='fail': raise SystemExit(13)
if mode=='timeout': time.sleep(5)
if mode=='slow': time.sleep(.15)
source_path=pathlib.Path(args[1]);source=source_path.read_bytes();output=source+b'-translated'
pathlib.Path(args[2]).write_bytes(output)
if mode=='partial': raise SystemExit(14)
manifest=build_runtime_manifest(source=source,output=output,
    source_runtime_path=option('--runtime-source-path'),output_runtime_path=option('--runtime-output-path'),
    profile=option('--profile'),function_reports=[dict(name='bitmapVS',selected=True,function_type_name='vertex',
        input_sha256=hashlib.sha256(source).hexdigest(),output_sha256=hashlib.sha256(output).hexdigest())])
if mode=='bad-hash':manifest['output']['sha256']='0'*64
if mode=='incomplete':manifest['translation']['complete']=False
if mode=='escape':manifest['output']['runtime_path']='/etc/passwd'
if mode=='source-mutated':source_path.write_bytes(b'MTLBchanged')
if mode=='large-output':pathlib.Path(args[2]).write_bytes(b'MTLB'+b'x'*(4*1024*1024))
write_runtime_manifest(pathlib.Path(option('--runtime-manifest')),manifest)
'''


class OfficeProvisioning(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="macws-office-provision-test-")
        self.directory = Path(self.temporary.name)
        self.root = self.directory / "rootfs"
        self.root.mkdir()
        self.converter = self.directory / "fake_converter.py"
        self.converter.write_text(FAKE.replace("REPOSITORY_MISC", repr(str(ROOT / "misc"))))
        self.llvm_dis = self.directory / "macws-llvm-dis"
        self.llvm_as = self.directory / "macws-llvm-as"
        for tool in (self.llvm_dis, self.llvm_as):
            tool.write_text("fixture, never executed")
            tool.chmod(0o755)
        self.base = self.root / "usr/local/share/macws/metal2metal"
        self.command = [sys.executable, str(HELPER), "--rootfs", str(self.root),
                        "--converter", str(self.converter), "--llvm-dis", str(self.llvm_dis),
                        "--llvm-as", str(self.llvm_as)]

    def tearDown(self):
        self.temporary.cleanup()

    def archive(self, app="Microsoft PowerPoint.app", source=b"MTLB-owned-fixture", *, name="Metal2DShaders.metallib"):
        path = self.root / "Applications" / app / provision.ARCHIVE_RELATIVE
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as handle:
            handle.writestr(name, source)
        return path

    def run_helper(self, success=True, extra=()):
        result = subprocess.run([*self.command, *extra], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0 if success else 1, result.stdout + result.stderr)
        return result

    def mode(self, mode):
        (self.directory / "mode").write_text(mode)

    def calls(self):
        path = self.directory / "calls"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def translations(self):
        return sum(call[0] == "translate" for call in self.calls())

    def routes(self):
        return sorted((self.base / "routes").glob("office-metal2d-*.route.plist"))

    def files(self):
        return {path: path.read_bytes() for path in (self.base / "office").glob("*/*.metallib")}

    def test_no_office_is_successful_no_op_without_tools_or_markers(self):
        self.converter.unlink()
        self.llvm_dis.unlink()
        self.llvm_as.unlink()
        result = self.run_helper()
        self.assertIn("no changes", result.stdout)
        self.assertFalse(self.base.exists())

    def test_three_apps_deduplicated_verified_cache_reused_and_sources_unchanged(self):
        archives = [self.archive(app) for app in provision.APPLICATIONS]
        before = {path: path.read_bytes() for path in archives}
        self.run_helper()
        self.assertEqual(self.translations(), 1)
        self.assertEqual(len(self.routes()), 1)
        self.assertEqual(len(self.files()), 2)
        cache = self.files()
        inodes = {path: path.stat().st_ino for path in cache}
        self.run_helper()
        self.assertEqual(self.translations(), 1)
        self.assertEqual(self.files(), cache)
        self.assertEqual({path: path.stat().st_ino for path in cache}, inodes)
        self.assertEqual({path: path.read_bytes() for path in archives}, before)
        translate = next(call for call in self.calls() if call[0] == "translate")
        self.assertIs(plistlib.loads(self.routes()[0].read_bytes())["requires_source_identity"], True)
        self.assertEqual(translate[translate.index("--llvm-dis") + 1], str(self.llvm_dis))
        self.assertEqual(translate[translate.index("--llvm-as") + 1], str(self.llvm_as))
        self.assertFalse(list(self.base.rglob("*.lock")))
        self.assertFalse(list(self.base.rglob(".build-*")))

    def test_different_versions_get_exact_independent_routes(self):
        first, second = b"MTLB-version-a", b"MTLB-version-b"
        self.archive("Microsoft Word.app", first)
        self.archive("Microsoft PowerPoint.app", second)
        self.archive("Microsoft Excel.app", first)
        self.run_helper()
        self.assertEqual(self.translations(), 2)
        self.assertEqual(len(self.routes()), 2)
        for route in self.routes():
            manifest = plistlib.loads(route.read_bytes())
            digest = manifest["source"]["sha256"]
            source, output = provision.manifest_paths(self.root, digest, manifest)
            self.assertIn(source.read_bytes(), (first, second))
            self.assertEqual(source.parent, output.parent)
            self.assertEqual(source.parent.name, digest)
            self.assertEqual(route.name, f"office-metal2d-{digest}.route.plist")

    def test_old_name_only_route_is_upgraded_before_cache_reuse(self):
        self.archive()
        self.run_helper()
        route = self.routes()[0]
        details = plistlib.loads(route.read_bytes())
        del details["requires_source_identity"]
        route.write_bytes(plistlib.dumps(details))
        self.run_helper()
        self.assertEqual(self.translations(), 2)
        self.assertIs(plistlib.loads(route.read_bytes())["requires_source_identity"], True)
        self.run_helper()
        self.assertEqual(self.translations(), 2)

    def test_corrupt_app_does_not_block_healthy_cold_provision_or_retry(self):
        bad = self.archive("Microsoft Word.app", b"MTLB-bad-version")
        bad.write_bytes(b"broken archive")
        healthy = b"MTLB-healthy-version"
        self.archive("Microsoft PowerPoint.app", healthy)
        self.archive("Microsoft Excel.app", healthy)
        result = self.run_helper(False)
        self.assertIn("Microsoft Word.app", result.stderr)
        self.assertEqual(self.translations(), 1)
        self.assertEqual(len(self.routes()), 1)
        self.assertEqual(plistlib.loads(self.routes()[0].read_bytes())["source"]["sha256"],
                         hashlib.sha256(healthy).hexdigest())
        self.run_helper(False)
        self.assertEqual(self.translations(), 1)
        self.archive("Microsoft Word.app", b"MTLB-repaired-version")
        self.run_helper()
        self.assertEqual(self.translations(), 2)
        self.assertEqual(len(self.routes()), 2)

    def test_corrupt_output_and_missing_cold_route_rebuild_without_destroying_generations(self):
        self.archive()
        self.run_helper()
        route = self.routes()[0]
        manifest = plistlib.loads(route.read_bytes())
        _, output = provision.manifest_paths(self.root, manifest["source"]["sha256"], manifest)
        output.write_bytes(b"MTLB-corruption")
        old = self.files()
        self.run_helper()
        self.assertEqual(self.translations(), 2)
        self.assertTrue(all(path.read_bytes() == data for path, data in old.items()))
        self.assertEqual(len(self.files()), 4)
        self.run_helper()
        self.assertEqual(self.translations(), 2)
        route.unlink()  # Only this test's own route, simulating lost derived metadata.
        self.run_helper()
        self.assertEqual(self.translations(), 3)
        self.assertEqual(len(self.files()), 6)

    def test_failed_rebuild_preserves_old_route_cache_and_original_archive(self):
        archive = self.archive()
        archive_bytes = archive.read_bytes()
        self.run_helper()
        route = self.routes()[0]
        route.write_bytes(b"corrupted existing route")
        before = self.files()
        for failure in ("fail", "partial", "bad-hash", "incomplete", "escape", "source-mutated", "large-output"):
            with self.subTest(failure=failure):
                self.mode(failure)
                self.run_helper(False)
                self.assertEqual(route.read_bytes(), b"corrupted existing route")
                self.assertEqual(self.files(), before)
                self.assertEqual(archive.read_bytes(), archive_bytes)
                self.assertFalse(list(self.base.rglob(".build-*")))
        self.mode("")
        self.run_helper()
        self.assertEqual(len(self.files()), 4)

    def test_failed_second_version_does_not_change_first_version_route(self):
        self.archive()
        self.run_helper()
        previous = {path: path.read_bytes() for path in self.routes()}
        old_files = self.files()
        self.archive("Microsoft Word.app", b"MTLB-second-version")
        self.mode("fail")
        self.run_helper(False)
        self.assertEqual({path: path.read_bytes() for path in self.routes()}, previous)
        self.assertEqual(self.files(), old_files)

    def test_zip_traversal_duplicates_absolute_symlink_and_size_rejected(self):
        for member in ("../Metal2DShaders.metallib", "/Metal2DShaders.metallib", "nested/Metal2DShaders.metallib"):
            with self.subTest(member=member):
                self.archive(name=member)
                self.run_helper(False)
                self.assertEqual(self.translations(), 0)
        archive = self.archive()
        with zipfile.ZipFile(archive, "a") as handle:
            handle.writestr("second.metallib", b"MTLB")
        self.run_helper(False)
        with zipfile.ZipFile(archive, "w") as handle:
            info = zipfile.ZipInfo("Metal2DShaders.metallib")
            info.external_attr = (0o120777 << 16)
            handle.writestr(info, b"MTLB")
        self.run_helper(False)
        self.archive(source=b"MTLB" + b"x" * provision.MAX_ARTIFACT)
        self.run_helper(False)
        archive.write_bytes(b"not a ZIP")
        self.run_helper(False)
        self.assertFalse(self.base.exists())

    def test_source_and_cache_symlinks_are_fail_closed(self):
        archive = self.archive()
        outside = self.directory / "outside.zip"
        archive.rename(outside)
        archive.symlink_to(outside)
        self.run_helper(False)
        self.assertFalse(self.base.exists())
        archive.unlink()
        outside.rename(archive)
        self.base.parent.mkdir(parents=True)
        outside_dir = self.directory / "outside"
        outside_dir.mkdir()
        self.base.symlink_to(outside_dir, target_is_directory=True)
        self.run_helper(False)
        self.assertEqual(list(outside_dir.iterdir()), [])

    def test_concurrent_calls_only_convert_once(self):
        self.archive()
        self.mode("slow")
        first = subprocess.Popen(self.command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        second = subprocess.Popen(self.command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        a = first.communicate(timeout=15)
        b = second.communicate(timeout=15)
        self.assertEqual(first.returncode, 0, str(a))
        self.assertEqual(second.returncode, 0, str(b))
        self.assertEqual(self.translations(), 1)
        self.assertEqual(len(self.routes()), 1)

    def test_final_publish_failure_preserves_old_route_and_retry_recovers(self):
        self.archive()
        self.run_helper()
        route = self.routes()[0]
        route.write_bytes(b"old route requiring repair")
        old = self.files()
        args = type("Arguments", (), dict(converter=self.converter, llvm_dis=self.llvm_dis,
                                         llvm_as=self.llvm_as, timeout=10))()
        sources, errors = provision.discover(self.root)
        self.assertEqual(errors, [])
        digest, source = next(iter(sources.items()))
        with mock.patch.object(provision.os, "replace", side_effect=PermissionError("publication fixture")):
            with self.assertRaises(PermissionError):
                provision.provision(self.root, digest, source, args)
        self.assertEqual(route.read_bytes(), b"old route requiring repair")
        self.assertTrue(all(path.read_bytes() == data for path, data in old.items()))
        self.run_helper()
        self.assertNotEqual(route.read_bytes(), b"old route requiring repair")

    def test_tool_timeout_publishes_nothing_and_missing_tools_is_error(self):
        self.archive()
        self.mode("timeout")
        result = self.run_helper(False, ["--timeout", "0.1"])
        self.assertIn("timed out", result.stderr)
        self.assertEqual(self.routes(), [])
        self.assertEqual(self.files(), {})
        self.llvm_as.unlink()
        result = self.run_helper(False)
        self.assertIn("packaged Apple LLVM", result.stderr)


if __name__ == "__main__":
    unittest.main()

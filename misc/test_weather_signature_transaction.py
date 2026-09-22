"""Execute the actual package Weather-signature function with fixture tools.

No device, app, real code signature, or trustcache is modified by these tests.
"""
from pathlib import Path
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
POSTINST = ROOT / "layout/DEBIAN/postinst"

FAKE_LDID = r'''
import json,os,pathlib,sys
site=pathlib.Path(os.environ['WEATHER_TEST_SITE']);args=sys.argv[1:]
events=site/'events'
def emit(value):
    with events.open('a') as stream:stream.write(json.dumps(value)+'\n')
mode=(site/'mode').read_text() if (site/'mode').exists() else ''
path=pathlib.Path(args[-1])
if any(item.startswith('-S') for item in args):
    count=sum(json.loads(line)['kind']=='sign' for line in events.read_text().splitlines()) if events.exists() else 0
    count+=1;emit(dict(kind='sign',path=str(path),pass_number=count,args=args))
    if mode=='sign'+str(count):raise SystemExit(7)
    path.write_bytes(path.read_bytes()+b'|signed'+str(count).encode())
elif '-e' in args:
    emit(dict(kind='entitlements',path=str(path)))
    if mode=='read-entitlements' and '-arch' in args:raise SystemExit(8)
    print('<plist><dict><key>com.apple.private.security.storage.Weather</key><true/>')
    if mode!='verify':print('<key>com.apple.private.graphics-restart-no-kill</key><true/>')
    print('</dict></plist>')
elif '-q' in args:
    emit(dict(kind='requirement-check',path=str(path)))
    print('wrong.identifier' if mode=='requirement' else 'com.apple.weather')
elif '-h' in args:
    emit(dict(kind='hash',path=str(path)))
    if mode=='no-hash':raise SystemExit(9)
    print('CDHash='+('broken' if mode=='bad-hash' else '1'*40))
else:raise SystemExit('unexpected ldid arguments')
'''
FAKE_JBCTL = r'''
import json,os,pathlib,sys
site=pathlib.Path(os.environ['WEATHER_TEST_SITE'])
original=pathlib.Path(os.environ['WEATHER_TEST_ORIGINAL'])
prior=[json.loads(line) for line in (site/'events').read_text().splitlines()]
candidate=pathlib.Path(next(item['path'] for item in prior if item['kind']=='sign'))
with (site/'events').open('a') as stream:stream.write(json.dumps(dict(kind='trust',args=sys.argv[1:],
    old_bytes=original.read_bytes().hex(),candidate_bytes=candidate.read_bytes().hex()))+'\n')
mode=(site/'mode').read_text() if (site/'mode').exists() else ''
if mode=='trust':raise SystemExit(11)
'''
FAKE_PREPARER = r'''
import pathlib,sys
if sys.argv[1]=='entitlements':pathlib.Path(sys.argv[3]).write_bytes(pathlib.Path(sys.argv[2]).read_bytes())
elif sys.argv[1]=='manifest':pathlib.Path(sys.argv[2]).write_text('prepared fixture manifest')
else:raise SystemExit(2)
'''
FAKE_REQUIREMENT = r'''
import pathlib,sys
assert sys.argv[1]=='com.apple.weather'
pathlib.Path(sys.argv[2]).write_bytes(b'fixture requirement')
'''


class WeatherSignatureTransaction(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="macws-weather-publish-test-")
        self.site = Path(self.temporary.name)
        self.rootfs = self.site / "rootfs"
        contents = self.rootfs / "System/Applications/Weather.app/Contents"
        self.executable = contents / "MacOS/Weather"
        self.executable.parent.mkdir(parents=True)
        self.original = b"original Weather executable fixture"
        self.executable.write_bytes(self.original)
        self.executable.chmod(0o755)
        self.info = contents / "Info.plist"
        self.info.write_text("original fixture manifest")
        self.entitlements = self.site / "entitlements.plist"
        self.entitlements.write_text("common entitlements fixture")
        self.tools = {}
        for name, text in (("ldid", FAKE_LDID), ("jbctl", FAKE_JBCTL),
                           ("prepare", FAKE_PREPARER), ("requirement", FAKE_REQUIREMENT)):
            path = self.site / name
            path.write_text("#!" + sys.executable + "\n" + text)
            path.chmod(0o755)
            self.tools[name] = path
        source = POSTINST.read_text()
        start = source.index("ensure_weather_signature() (")
        end = source.index("\nensure_chroot_extensionkit_signature()", start)
        function = source[start:end]
        function = function.replace("/var/jb/usr/bin/ldid", shlex.quote(str(self.tools["ldid"])))
        function = function.replace("/var/jb/usr/bin/jbctl", shlex.quote(str(self.tools["jbctl"])))
        function = function.replace("/var/jb/usr/bin/python3", shlex.quote(sys.executable))
        self.script = self.site / "exercise.sh"
        self.script.write_text(
            f"ROOTFS={shlex.quote(str(self.rootfs))}\n"
            f"WEATHER_PREPARER={shlex.quote(str(self.tools['prepare']))}\n"
            f"CODE_REQUIREMENT_WRITER={shlex.quote(str(self.tools['requirement']))}\n"
            f"ENTITLEMENTS={shlex.quote(str(self.entitlements))}\n"
            + function + "\nensure_weather_signature\n")
        self.environment = dict(os.environ, WEATHER_TEST_SITE=str(self.site),
                                WEATHER_TEST_ORIGINAL=str(self.executable))

    def tearDown(self):
        self.temporary.cleanup()

    def run_function(self, okay):
        result = subprocess.run(["bash", str(self.script)], env=self.environment,
                                text=True, capture_output=True, timeout=10)
        if okay:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stderr)
        return result

    def events(self):
        path = self.site / "events"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_new_inode_signed_verified_trusted_then_published_old_fd_unchanged(self):
        old_inode = self.executable.stat().st_ino
        unrelated = self.executable.parent / "Weather.package-new.unrelated"
        unrelated.write_text("do not remove")
        with self.executable.open("rb") as old_mapping:
            self.run_function(True)
            self.assertEqual(old_mapping.read(), self.original)
            self.assertEqual(os.fstat(old_mapping.fileno()).st_ino, old_inode)
        self.assertNotEqual(self.executable.stat().st_ino, old_inode)
        self.assertEqual(self.executable.read_bytes(), self.original + b"|signed1|signed2|signed3")
        self.assertEqual(self.executable.stat().st_mode & 0o777, 0o755)
        events = self.events()
        signed_paths = [event["path"] for event in events if event["kind"] == "sign"]
        self.assertEqual(len(signed_paths), 3)
        self.assertEqual(len(set(signed_paths)), 1)
        self.assertNotEqual(signed_paths[0], str(self.executable))
        self.assertEqual(Path(signed_paths[0]).parent, self.executable.parent)
        entitlement_reads = [event["path"] for event in events if event["kind"] == "entitlements"]
        self.assertEqual(entitlement_reads, [str(self.executable), signed_paths[0]])
        for event in events:
            if event["kind"] in ("requirement-check", "hash"):
                self.assertEqual(event["path"], signed_paths[0])
        trusts = [event for event in events if event["kind"] == "trust"]
        self.assertEqual(len(trusts), 2)
        for event in trusts:
            self.assertEqual(bytes.fromhex(event["old_bytes"]), self.original)
            self.assertEqual(bytes.fromhex(event["candidate_bytes"]), self.executable.read_bytes())
        self.assertEqual(self.info.read_text(), "prepared fixture manifest")
        self.assertEqual(list(self.executable.parent.glob("Weather.package-new.*")), [unrelated])

    def test_each_read_sign_validation_and_trust_failure_preserves_original_and_cleans_only_scratch(self):
        old_inode = self.executable.stat().st_ino
        unrelated = self.executable.parent / "Weather.package-new.unrelated"
        unrelated.write_text("do not remove")
        for failure in ("read-entitlements", "sign1", "sign2", "sign3", "verify",
                        "requirement", "no-hash", "bad-hash", "trust"):
            with self.subTest(failure=failure):
                (self.site / "mode").write_text(failure)
                (self.site / "events").unlink(missing_ok=True)
                self.run_function(False)
                self.assertEqual(self.executable.read_bytes(), self.original)
                self.assertEqual(self.executable.stat().st_ino, old_inode)
                self.assertEqual(list(self.executable.parent.glob("Weather.package-new.*")), [unrelated])

    def test_absent_weather_noop_and_symlink_rejected(self):
        self.executable.unlink()
        self.run_function(True)
        target = self.site / "other"
        target.write_bytes(self.original)
        self.executable.symlink_to(target)
        self.run_function(False)
        self.assertEqual(target.read_bytes(), self.original)
        self.assertEqual(self.events(), [])


if __name__ == "__main__":
    unittest.main()

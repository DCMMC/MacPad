"""Execute retirement against temporary fixtures, never the host launchd tree."""
import hashlib
import importlib.util
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('legacy_boot_retirement',
    ROOT / 'layout/usr/macOS/bin/macws_retire_legacy_boot_jobs.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class LegacyBootJobTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='macws-legacy-boot-test-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.boot = self.root / 'Library/LaunchDaemons'
        self.boot.mkdir(parents=True)
        self.name = 'com.macwsguide.cpu-fp-probe.plist'
        self.raw = plistlib.dumps({'Label': 'com.macwsguide.cpu-fp-probe',
                                  'ProgramArguments': ['/tmp/fixture-probe'],
                                  'RunAtLoad': True})
        self.patch = patch.dict(MODULE.RETIRED, {self.name: hashlib.sha256(self.raw).hexdigest()})
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def fixture(self, name=None, raw=None):
        target = self.boot / (name or self.name)
        target.write_bytes(raw or self.raw)
        target.chmod(0o640)
        return target

    def test_check_has_no_mutations_and_retirement_preserves_inode_bytes_mode(self):
        source = self.fixture()
        original = source.stat()
        self.assertEqual(MODULE.migrate(self.root, check=True), [str(source)])
        self.assertTrue(source.exists())
        self.assertFalse((self.root / 'usr').exists())
        archived, = MODULE.migrate(self.root)
        archived = Path(archived)
        self.assertFalse(source.exists())
        self.assertEqual(archived.read_bytes(), self.raw)
        self.assertEqual(MODULE.identity(archived.stat()), MODULE.identity(original))
        self.assertEqual(MODULE.migrate(self.root), [])

    def test_unknown_revision_blocks_before_any_known_file_is_moved(self):
        known = self.fixture()
        unknown = self.fixture('com.macwsguide.zzz-probe.plist')
        with self.assertRaisesRegex(ValueError, 'unrecognized legacy'):
            MODULE.migrate(self.root)
        self.assertTrue(known.exists())
        self.assertTrue(unknown.exists())
        self.assertFalse((self.root / 'usr').exists())

    def test_changed_known_revision_and_symlink_are_preserved(self):
        source = self.fixture(raw=self.raw + b'\n')
        with self.assertRaises(ValueError):
            MODULE.migrate(self.root)
        self.assertTrue(source.exists())
        source.unlink()
        outside = self.root / 'original'
        outside.write_bytes(self.raw)
        source.symlink_to(outside)
        with self.assertRaises(OSError):
            MODULE.migrate(self.root)
        self.assertEqual(outside.read_bytes(), self.raw)

    def test_archive_symlink_refused_without_removing_source(self):
        source = self.fixture()
        (self.root / 'usr/macOS').mkdir(parents=True)
        outside = self.root / 'outside'
        outside.mkdir()
        (self.root / 'usr/macOS/retired-launch-jobs').symlink_to(outside)
        with self.assertRaises(ValueError):
            MODULE.migrate(self.root)
        self.assertTrue(source.exists())
        self.assertEqual(list(outside.iterdir()), [])

    def test_interrupted_link_recovers_without_overwrite(self):
        source = self.fixture()
        unlink = Path.unlink
        def interrupt(path, *args, **kwargs):
            if path == source:
                raise InterruptedError('fixture after durable archive link')
            return unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', interrupt), self.assertRaises(InterruptedError):
            MODULE.migrate(self.root)
        self.assertTrue(source.exists())
        self.assertEqual(source.stat().st_nlink, 2)
        result = MODULE.migrate(self.root)
        self.assertEqual(len(result), 1)
        self.assertEqual(Path(result[0]).read_bytes(), self.raw)

    def test_recreated_exact_legacy_file_retains_both_original_inodes(self):
        self.fixture()
        first, = MODULE.migrate(self.root)
        self.fixture()
        second, = MODULE.migrate(self.root)
        self.assertNotEqual(first, second)
        self.assertEqual(Path(first).read_bytes(), Path(second).read_bytes())

    def test_unrelated_and_current_jobs_stay_unchanged(self):
        unrelated = self.fixture('com.apple.fixture.plist',
                                 plistlib.dumps({'Label': 'com.apple.fixture'}))
        current = self.fixture('com.macwsguide.hostd.plist', plistlib.dumps({
            'Label': 'com.macwsguide.hostd',
            'ProgramArguments': ['/var/jb/usr/macOS/bin/macwshostd'],
            'EnvironmentVariables': {'CA_VSYNC_OFF': '1'}}))
        before = {p: p.read_bytes() for p in (unrelated, current)}
        self.assertEqual(MODULE.migrate(self.root), [])
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_current_boot_job_diagnostic_is_not_silently_accepted(self):
        self.fixture('com.macwsguide.hostd.plist', plistlib.dumps({
            'Label': 'com.macwsguide.hostd',
            'ProgramArguments': ['/var/jb/usr/macOS/bin/macwshostd'],
            'EnvironmentVariables': {'MACWS_SUSPEND_AT_EXEC': '1'}}))
        with self.assertRaisesRegex(ValueError, 'production boot environment'):
            MODULE.migrate(self.root)

    def test_unrelated_links_and_large_jobs_do_not_block_project_upgrade(self):
        large = self.fixture('com.vendor.large.plist', b'x' * (MODULE.LIMIT + 1))
        link = self.boot / 'com.vendor.link.plist'
        link.symlink_to(large)
        self.fixture()
        self.assertEqual(len(MODULE.migrate(self.root)), 1)
        self.assertTrue(link.is_symlink())
        self.assertEqual(large.stat().st_size, MODULE.LIMIT + 1)

    def test_program_override_cannot_impersonate_current_boot_daemon(self):
        self.fixture('com.macwsguide.hostd.plist', plistlib.dumps({
            'Label': 'com.macwsguide.hostd', 'Program': '/tmp/impostor',
            'ProgramArguments': ['/var/jb/usr/macOS/bin/macwshostd']}))
        with self.assertRaisesRegex(ValueError, 'identity mismatch'):
            MODULE.migrate(self.root)

    def test_apple_named_chroot_job_requires_exact_known_receipt(self):
        source = self.fixture('com.apple.unrecognized.plist', plistlib.dumps({
            'Label': 'com.apple.fixture',
            'ProgramArguments': ['/var/jb/usr/macOS/bin/launchdchrootexec']}))
        with self.assertRaises(ValueError):
            MODULE.migrate(self.root)
        self.assertTrue(source.exists())

    def test_existing_legacy_vscode_identity_remains_supported(self):
        self.fixture('com.macwsguide.vscode.plist', plistlib.dumps({
            'Label': 'com.macwsguide.vscode',
            'ProgramArguments': ['/var/jb/usr/macOS/bin/launchdchrootexec', '0', '0',
                '/var/mnt/rootfs', '/Applications/Visual Studio Code.app/Contents/MacOS/Electron',
                '--legacy-option']}))
        self.assertEqual(len(MODULE.migrate(self.root)), 1)

    def test_legacy_program_override_is_not_recognized_as_vscode(self):
        source = self.fixture('com.macwsguide.vscode.plist', plistlib.dumps({
            'Label': 'com.macwsguide.vscode', 'Program': '/tmp/other',
            'ProgramArguments': ['/var/jb/usr/macOS/bin/launchdchrootexec', '0', '0',
                '/var/mnt/rootfs', '/Applications/Visual Studio Code.app/Contents/MacOS/Electron']}))
        with self.assertRaises(ValueError):
            MODULE.migrate(self.root)
        self.assertTrue(source.exists())

    def test_exact_duplicate_job_retired_without_touching_live_directory_version(self):
        relative = 'usr/macOS/LaunchDaemons/com.macwsguide.macos-locationd.plist'
        duplicate = self.root / relative
        duplicate.parent.mkdir(parents=True)
        duplicate.write_bytes(self.raw)
        active = self.root / 'usr/macOS/gui-launchd/com.macwsguide.macos-locationd.plist'
        active.parent.mkdir()
        active.write_bytes(b'current locationd source')
        with patch.dict(MODULE.EXTRA_RETIRED, {relative: hashlib.sha256(self.raw).hexdigest()}):
            self.assertEqual(MODULE.migrate(self.root, check=True), [str(duplicate)])
            archived, = MODULE.migrate(self.root)
        self.assertEqual(Path(archived).read_bytes(), self.raw)
        self.assertEqual(active.read_bytes(), b'current locationd source')

    def test_unknown_duplicate_blocks_all_retirement_before_mutation(self):
        source = self.fixture()
        duplicate = self.root / next(iter(MODULE.EXTRA_RETIRED))
        duplicate.parent.mkdir(parents=True)
        duplicate.write_bytes(b'unknown revision')
        with self.assertRaisesRegex(ValueError, 'unrecognized legacy GUI'):
            MODULE.migrate(self.root)
        self.assertTrue(source.exists())
        self.assertTrue(duplicate.exists())

    def test_shipped_glassdemo_diagnostic_migration_is_preserved(self):
        raw = (ROOT / 'misc/com.macwsguide.glassdemo.plist').read_bytes()
        self.fixture('com.macwsguide.glassdemo.plist', raw)
        self.assertEqual(len(MODULE.migrate(self.root)), 1)

    def test_migration_runs_before_startup_generation_and_postinst_mutations(self):
        gui = (ROOT / 'layout/usr/macOS/bin/macos_gui.sh').read_text()
        for command in ('start', 'restart'):
            body = gui.split('    ' + command + ')\n', 1)[1].split('        ;;', 1)[0]
            self.assertLess(body.index('prepare_production_boot_jobs || exit 1'),
                            body.index('write_plists ||'))
        installer = (ROOT / 'layout/usr/macOS/bin/postinst.sh').read_text()
        self.assertLess(installer.index('macws_retire_legacy_boot_jobs.py'),
                        installer.index('split_libmachook=0'))
        maintainer = (ROOT / 'layout/DEBIAN/postinst').read_text()
        self.assertLess(maintainer.index('macws_retire_legacy_boot_jobs.py'),
                        maintainer.index('for launch_directory in'))
        self.assertIn('macws_metal_cache_migration.py', maintainer)


if __name__ == '__main__':
    unittest.main()

"""Execute package lifecycle decisions against bounded launchd/ps fixtures."""
import importlib.util
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('managed_job_lifecycle',
    ROOT / 'layout/usr/macOS/bin/macws_refresh_managed_job.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='macws-lifecycle-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.messages = []
        self.commands = []
        self.jobs = dict(MODULE.JOBS)
        for name, (label, _, argv, auto) in self.jobs.items():
            path = self.root / (name + '.plist')
            path.write_bytes(plistlib.dumps({'Label': label, 'ProgramArguments': argv}))
            path.chmod(0o644)
            self.jobs[name] = (label, str(path), argv, auto)
        job_patch = patch.dict(MODULE.JOBS, self.jobs, clear=True)
        job_patch.start()
        self.addCleanup(job_patch.stop)
        original_lstat = Path.lstat
        def fixture_root_owner(path):
            values = list(original_lstat(path))
            values[4] = 0  # Fixture owner models the installed root-owned file.
            return os.stat_result(values)
        metadata_patch = patch.object(Path, 'lstat', fixture_root_owner)
        metadata_patch.start()
        self.addCleanup(metadata_patch.stop)
        self.listings = ['PID\tStatus\tLabel\n']
        self.processes = ['1 1 ?s /sbin/launchd\n']
        self.fail_command = None

    def execute(self, argv):
        self.commands.append(argv)
        if self.fail_command and argv[1] == self.fail_command:
            raise MODULE.UnsafeState('fixture permission denied')
        if argv == [MODULE.LAUNCHCTL, 'list']:
            return self.listings.pop(0) if len(self.listings) > 1 else self.listings[0]
        if argv[0] == MODULE.PS:
            return self.processes.pop(0) if len(self.processes) > 1 else self.processes[0]
        self.assertEqual(argv[1], 'load')
        return ''

    def refresh(self, name='hostd'):
        return MODULE.refresh(name, self.execute, self.messages.append)

    def mutations(self):
        return [c for c in self.commands if c[0] == MODULE.LAUNCHCTL and c[1] != 'list']

    def test_real_shared_host_group_preserves_word_terminal_and_daemon(self):
        self.listings = ['PID Status Label\n92633 0 com.macwsguide.hostd\n']
        self.processes = ['1 1 ?s /sbin/launchd\n92633 92633 Ss /var/jb/usr/macOS/bin/macwshostd\n'
                          '8439 92633 S /Applications/Microsoft Word.app/Contents/MacOS/Microsoft Word\n'
                          '13515 92633 S /System/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal\n']
        self.assertEqual(self.refresh(), 0)
        self.assertEqual(self.mutations(), [])
        self.assertIn('shared PGID 92633', self.messages[0])
        self.assertIn('8439', self.messages[0])
        self.assertIn('13515', self.messages[0])

    def test_even_isolated_live_job_is_not_unloaded_to_avoid_spawn_race(self):
        self.listings = ['PID Status Label\n92633 0 com.macwsguide.hostd\n']
        self.processes = ['92633 92633 Ss /var/jb/usr/macOS/bin/macwshostd\n']
        self.assertEqual(self.refresh(), 0)
        self.assertEqual(self.mutations(), [])
        self.assertIn('next normal service lifecycle', self.messages[0])

    def test_every_loaded_allowlisted_job_including_no_pid_is_preserved(self):
        for name, (label, _, _, _) in self.jobs.items():
            for pid in ['-', '123']:
                with self.subTest(name=name, pid=pid):
                    self.commands.clear()
                    self.listings = ['PID Status Label\n%s 0 %s\n' % (pid, label)]
                    self.assertEqual(self.refresh(name), 0)
                    self.assertEqual(self.mutations(), [])

    def test_absent_host_and_keychain_load_once_without_unload(self):
        for name in ['hostd', 'keychain']:
            with self.subTest(name=name):
                self.commands.clear()
                self.assertEqual(self.refresh(name), 0)
                self.assertEqual(self.mutations(), [[MODULE.LAUNCHCTL, 'load', self.jobs[name][1]]])

    def test_absent_gui_jobs_preserve_stopped_state(self):
        for name in ['input', 'dock']:
            self.commands.clear()
            self.assertEqual(self.refresh(name), 0)
            self.assertEqual(self.mutations(), [])
            self.assertIn('remains stopped', self.messages[-1])

    def test_first_install_has_no_generated_dock_plist(self):
        Path(self.jobs['dock'][1]).unlink()
        self.assertEqual(self.refresh('dock'), 0)
        self.assertEqual(self.mutations(), [])
        self.assertIn('no generated GUI job', self.messages[-1])

    def test_missing_required_host_plist_is_an_installation_error(self):
        Path(self.jobs['hostd'][1]).unlink()
        self.assertEqual(self.refresh(), 1)
        self.assertEqual(self.mutations(), [])

    def test_orphan_daemon_blocks_duplicate_load(self):
        for name in ['hostd', 'keychain']:
            self.commands.clear()
            self.processes = ['88 88 S %s\n' % self.jobs[name][2][-1]]
            self.assertEqual(self.refresh(name), 0)
            self.assertEqual(self.mutations(), [])
            self.assertIn('unregistered live', self.messages[-1])

    def test_new_registration_between_snapshots_does_not_start_second_daemon(self):
        self.listings = ['PID Status Label\n', 'PID Status Label\n55 0 com.macwsguide.hostd\n']
        self.assertEqual(self.refresh(), 0)
        self.assertEqual(self.mutations(), [])
        self.assertIn('state changed', self.messages[-1])

    def test_missing_or_malformed_inventory_fails_closed(self):
        for listing, processes in [('denied', self.processes[0]),
                                   ('PID Status Label\nbad', self.processes[0]),
                                   ('PID Status Label\n', ''),
                                   ('PID Status Label\n', '1 garbage S launchd'),
                                   ('PID Status Label\n', '1 1 S launchd\n1 2 S duplicate')]:
            with self.subTest(listing=listing, processes=processes):
                self.commands.clear()
                self.listings, self.processes = [listing], [processes]
                self.assertEqual(self.refresh(), 1)
                self.assertEqual(self.mutations(), [])

    def test_failed_status_command_and_timeout_fail_closed(self):
        self.fail_command = 'list'
        self.assertEqual(self.refresh(), 1)
        self.assertEqual(self.mutations(), [])
        def timeout(argv):
            raise subprocess.TimeoutExpired(argv, 10)
        self.assertEqual(MODULE.refresh('hostd', timeout, self.messages.append), 1)

    def test_malformed_or_wrong_plist_never_starts_any_job(self):
        path = Path(self.jobs['hostd'][1])
        for raw in [b'broken', b'<?xml version="1.0"?><plist><dict>',
                    plistlib.dumps({'Label': 'different'}),
                    plistlib.dumps({'Label': self.jobs['hostd'][0],
                                    'ProgramArguments': self.jobs['hostd'][2], 'Program': '/tmp/other'})]:
            path.write_bytes(raw)
            self.commands.clear()
            self.assertEqual(self.refresh(), 1)
            self.assertEqual(self.mutations(), [])

    def test_symlink_and_writable_plist_fail_closed(self):
        path = Path(self.jobs['hostd'][1])
        path.chmod(0o666)
        self.assertEqual(self.refresh(), 1)
        self.assertEqual(self.mutations(), [])
        path.unlink()
        path.symlink_to(self.jobs['keychain'][1])
        self.assertEqual(self.refresh(), 1)
        self.assertEqual(self.mutations(), [])

    def test_load_failure_is_visible_and_is_not_retried(self):
        self.fail_command = 'load'
        self.assertEqual(self.refresh(), 1)
        self.assertEqual(len(self.mutations()), 1)
        self.assertIn('initial load failed', self.messages[-1])

    def test_maintainer_and_package_bind_the_safe_helper(self):
        maintainer = (ROOT / 'layout/DEBIAN/postinst').read_text()
        self.assertNotIn('launchctl unload', maintainer)
        for name in ['hostd', 'keychain']:
            self.assertIn('macws_refresh_managed_job.py ' + name, maintainer)
        self.assertIn('for managed_gui_job in input dock', maintainer)
        helper = 'layout/usr/macOS/bin/macws_refresh_managed_job.py'
        self.assertTrue((ROOT / helper).is_file())  # Theos automatically stages layout/.
        self.assertIn(helper, (ROOT / 'misc/macws_artifact_contract.py').read_text())
        self.assertNotIn("'unload'", (ROOT / helper).read_text())


if __name__ == '__main__':
    unittest.main()

"""Publish upgrades without unloading live managed services or GUI children.

launchd normally kills the unloaded job's whole process group. Older hostd
versions spawned GUI applications in that same group. An upgrade must defer
their daemon refresh instead of terminating the user's unsaved documents.
This is runtime process state, not an opt-in marker or persistent preference.
"""
import os
from pathlib import Path
import plistlib
import stat
import subprocess
import sys
from xml.parsers.expat import ExpatError

LAUNCHCTL = '/var/jb/usr/bin/launchctl'
PS = '/bin/ps'
CHROOT = ['/var/jb/usr/macOS/bin/launchdchrootexec', '0', '0', '/var/mnt/rootfs']
JOBS = {
    'hostd': ('com.macwsguide.hostd',
              '/var/jb/Library/LaunchDaemons/com.macwsguide.hostd.plist',
              ['/var/jb/usr/macOS/bin/macwshostd'], True),
    'keychain': ('com.macwsguide.keychain',
                 '/var/jb/Library/LaunchDaemons/com.macwsguide.keychain.plist',
                 ['/var/jb/usr/macOS/bin/macwskeychaind'], True),
    'input': ('UIKitApplication:com.macwsguide.input',
              '/var/jb/usr/macOS/LaunchDaemons/com.macwsguide.input.plist',
              CHROOT + ['/usr/local/bin/macwsinputd'], False),
    'dock': ('com.macwsguide.dock',
             '/var/jb/usr/macOS/gui-launchd/com.macwsguide.dock.plist',
             CHROOT + ['/System/Library/CoreServices/Dock.app/Contents/MacOS/Dock'], False),
}


class UnsafeState(RuntimeError):
    pass


def run(argv):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=10)
    if result.returncode:
        raise UnsafeState('%s failed (%d): %s' % (
            ' '.join(argv), result.returncode, result.stderr.strip()[:400]))
    return result.stdout


def validate_plist(path, label, arguments):
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 65536:
        raise UnsafeState('Managed plist is not a bounded regular file')
    if metadata.st_uid != 0 or metadata.st_mode & 0o022:
        raise UnsafeState('Managed plist ownership/permissions are unsafe')
    with path.open('rb') as stream:
        config = plistlib.load(stream)
    if not isinstance(config, dict) or config.get('Label') != label or \
            config.get('ProgramArguments') != arguments or \
            config.get('Program', arguments[0]) != arguments[0]:
        raise UnsafeState('Plist identity does not match the exact managed job')


def parse_jobs(text):
    rows = text.splitlines()
    if not rows or rows[0].split() != ['PID', 'Status', 'Label']:
        raise UnsafeState('Unrecognized launchctl process inventory')
    result = {}
    for row in rows[1:]:
        fields = row.split(None, 2)
        if len(fields) != 3:
            raise UnsafeState('Malformed launchctl inventory row')
        pid, status, label = fields
        if (pid != '-' and (not pid.isdigit() or int(pid) <= 0)) or \
                not status.lstrip('-').isdigit() or label in result:
            raise UnsafeState('Ambiguous launchctl inventory row')
        result[label] = None if pid == '-' else int(pid)
    return result


def parse_processes(text):
    result = {}
    for row in text.splitlines():
        fields = row.split(None, 3)
        if len(fields) != 4:
            raise UnsafeState('Malformed process inventory row')
        pid, group, state, command = fields
        if not pid.isdigit() or not group.isdigit() or not state or not command:
            raise UnsafeState('Unrecognized process inventory')
        pid = int(pid)
        if pid in result:
            raise UnsafeState('Duplicate process identity')
        result[pid] = {'pid': pid, 'group': int(group), 'state': state,
                       'command': command, 'live': not state.startswith('Z')}
    if not result:
        raise UnsafeState('Empty process inventory')
    return result


def inventory(execute):
    jobs = parse_jobs(execute([LAUNCHCTL, 'list']))
    processes = parse_processes(execute([PS, '-axo', 'pid=,pgid=,stat=,comm=']))
    return jobs, processes


def decide(job, jobs, processes):
    label, _, arguments, start_if_absent = JOBS[job]
    if label in jobs:
        pid = jobs[label]
        reason = 'job is already loaded (PID %s)' % (pid if pid else 'not running')
        leader = processes.get(pid)
        if leader:
            peers = [p for p in processes.values() if p['live'] and
                     p['group'] == leader['group'] and p['pid'] != pid]
            if peers:
                reason += '; shared PGID %d contains %s' % (leader['group'], ', '.join(
                    '%s(pid=%d)' % (os.path.basename(p['command']), p['pid']) for p in peers[:12]))
        # Even an apparently isolated old daemon can spawn a same-group GUI
        # child after our snapshot. Never unload a loaded job during install.
        return 'defer', reason
    if not start_if_absent:
        return 'preserve-stopped', 'job is absent; package upgrades preserve the stopped GUI state'
    processes_named_target = [p for p in processes.values() if p['live'] and
                             os.path.basename(p['command']) == os.path.basename(arguments[-1])]
    if processes_named_target:
        return 'defer', 'unregistered live %s; do not start a second daemon' % os.path.basename(arguments[-1])
    return 'load', 'managed job is absent and no matching live daemon exists'


def refresh(job, execute=run, output=print):
    if job not in JOBS:
        raise ValueError('Unknown managed job')
    label, plist_path, arguments, start_if_absent = JOBS[job]
    path = Path(plist_path)
    try:
        state = inventory(execute)
        try:
            path.lstat()
        except FileNotFoundError:
            # Dock's plist is generated at first GUI start, not a mandatory
            # first-install payload. A proved-absent GUI job must stay absent.
            if not start_if_absent and label not in state[0]:
                output('%s remains stopped: no generated GUI job or plist exists.' % label)
                return 0
            raise
        validate_plist(path, label, arguments)
        action, reason = decide(job, *state)
        if action == 'defer':
            output('%s upgrade activation DEFERRED: %s. Existing service/apps were not restarted; '
                   'installed code takes effect at the next normal service lifecycle.' % (label, reason))
            return 0
        if action == 'preserve-stopped':
            output('%s remains stopped: %s.' % (label, reason))
            return 0
        next_action, next_reason = decide(job, *inventory(execute))
        if next_action != 'load':
            output('%s upgrade activation DEFERRED: process/job state changed before load: %s. '
                   'No service mutation was attempted.' % (label, next_reason))
            return 0
    except (OSError, ValueError, plistlib.InvalidFileException, ExpatError,
            subprocess.SubprocessError, UnsafeState) as error:
        output('ERROR: %s upgrade activation state could not be verified: %s. '
               'No service mutation was attempted; package configuration is incomplete.' % (label, error))
        return 1

    try:
        execute([LAUNCHCTL, 'load', str(path)])
    except (OSError, subprocess.SubprocessError, UnsafeState) as error:
        output('ERROR: %s initial load failed: %s. No retry or unload was attempted.' % (label, error))
        return 1
    output('Requested initial load of %s: %s.' % (label, reason))
    return 0


if __name__ == '__main__':
    if len(sys.argv) != 2 or sys.argv[1] not in JOBS:
        raise SystemExit('Usage: python3 macws_refresh_managed_job.py {hostd|keychain|input|dock}')
    raise SystemExit(refresh(sys.argv[1]))

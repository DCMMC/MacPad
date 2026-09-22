"""First-party main-image policy + dynamic-plugin admission before GUI spawn.

Runtime witnesses: TextEdit's trusted stock executable was killed by sandbox
exec policy; Calculator's untrusted calcview bundles loaded only after their
unchanged CodeDirectories were registered. These are distinct prerequisites.
No boot-wide re-sign, dependency entitlement injection, or third-party changes.
"""
import argparse
import fcntl
import hashlib
import json
import os
import plistlib
import shutil
import stat
import subprocess
import sys
import tempfile
import time

import macws_boot_trust as trust
import macws_macho_dependencies as dependencies

ROOT = '/var/mnt/rootfs'
STATE = ROOT + '/var/db/macws/system-app-admission'
PROFILE = '/var/jb/usr/macOS/bin/entitlements.plist'
APP_ROOTS = ('/System/Applications/', '/System/Library/CoreServices/',
             '/System/Volumes/Preboot/Cryptexes/App/System/Applications/')
# Existing MacWS execution-policy markers, not a replacement entitlement set.
# When conversion is needed, ldid merges the COMPLETE shipped profile and
# preserves the app's original private rights/identity, as autosignd does.
REQUIRED = ('com.apple.private.security.no-sandbox',
            'com.apple.private.security.no-container', 'get-task-allow')


def resolve_target(path):
    marker = '.app/Contents/MacOS/'
    if not path.startswith(APP_ROOTS) or marker not in path:
        raise ValueError('not a first-party app main executable')
    bundle, name = path.rsplit(marker, 1)
    if not name or '/' in name or name in ('.', '..'):
        raise ValueError('not a direct app main executable')
    absolute = ROOT + path
    if os.path.realpath(absolute) != os.path.realpath(ROOT) + path:
        raise ValueError('noncanonical target or rootfs escape')
    info = os.lstat(absolute)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0:
        raise ValueError('app executable must be a root-owned regular file')
    return absolute, ROOT + bundle + '.app/Contents/PlugIns'


def native_records(path):
    with open(path, 'rb') as stream:
        result = trust.code_hashes(stream, os.fstat(stream.fileno()).st_size)
    if not result:
        raise ValueError('main executable has no signed native slice')
    return result


def has_profile(path, records):
    for arch in sorted({record['arch'] for record in records}):
        result = subprocess.run([trust.LDID, '-arch', arch, '-e', path],
                                capture_output=True, timeout=5, check=True)
        rights = plistlib.loads(result.stdout) if result.stdout.strip() else {}
        if any(rights.get(key) is not True for key in REQUIRED):
            return False
    return True


def ensure_main_profile(path, state):
    records = native_records(path)
    if has_profile(path, records):
        return False
    with open(PROFILE, 'rb') as stream:
        profile = plistlib.load(stream)
    if any(profile.get(key) is not True for key in REQUIRED):
        raise ValueError('shipped chroot profile is incomplete')
    before = trust.identity(os.stat(path))
    backup = os.path.join(state, 'original-' + records[0]['hash'])
    if not os.path.exists(backup):
        shutil.copy2(path, backup)
    descriptor, candidate = tempfile.mkstemp(prefix='.macws-admission-',
                                            dir=os.path.dirname(path))
    os.close(descriptor)
    try:
        shutil.copy2(path, candidate)
        os.chown(candidate, os.stat(path).st_uid, os.stat(path).st_gid)
        # The established two-pass signing transaction handles ldid growing
        # LC_CODE_SIGNATURE. Never overwrite a potentially mapped inode.
        for _ in range(2):
            subprocess.run([trust.LDID, '-S' + PROFILE, '-M', candidate],
                           capture_output=True, timeout=10, check=True)
        prepared = native_records(candidate)
        if not has_profile(candidate, prepared):
            raise ValueError('candidate still lacks chroot execution policy')
        trust.restore({record['hash'] for record in prepared})
        if trust.identity(os.stat(path)) != before:
            raise ValueError('application changed during admission')
        with open(candidate, 'rb') as stream:
            os.fsync(stream.fileno())
        os.replace(candidate, path)
    finally:
        if os.path.exists(candidate):
            os.unlink(candidate)  # only this invocation's private temporary
    return True


def prepare(path):
    if os.geteuid() != 0:
        raise ValueError('root required')
    executable, plugins = resolve_target(path)
    key = hashlib.sha256(path.encode()).hexdigest()[:24]
    state = os.path.join(STATE, key)
    os.makedirs(state, mode=0o700, exist_ok=True)
    info = os.lstat(state)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('unsafe admission state directory')
    with open(os.path.join(state, 'admission.lock'), 'a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return prepare_locked(executable, plugins, state)


def prepare_locked(executable, plugins, state):
    converted = ensure_main_profile(executable, state)
    paths = [executable] + ([plugins] if os.path.isdir(plugins) else [])
    paths = [os.path.realpath(path) for path in paths]
    manifest = os.path.join(state, 'hashes.json')
    dependency_manifest = os.path.join(state, 'dependencies.json')
    cached = trust.load_cache(manifest)
    resources = trust.ResourceIndex(os.path.join(state, 'resources.sqlite'))
    try:
        records, hashes, counts = trust.scan(paths, cached,
                                             resources=resources)
        # Audio MIDI Setup reached dyld with an unregistered original
        # MobileDevice framework outside Contents/PlugIns. Admit its actual
        # dependency graph, not an ever-growing hard-coded framework list.
        closure, metadata = dependencies.closure(executable, records, ROOT,
            trust.load_cache(dependency_manifest))
        extra, extra_hashes, extra_counts = trust.scan(
            [path for path in closure if path not in records], cached,
            resources=resources)
        records.update(extra)
        hashes.update(extra_hashes)
        counts = {key: value + extra_counts[key] for key, value in counts.items()}
    finally:
        resources.close()
    for target, record in records.items():
        if trust.identity(os.stat(target)) != record['identity']:
            raise ValueError('application changed before trust admission')
    added, backend = trust.restore(hashes)
    trust.save_cache(manifest, records)
    trust.save_cache(dependency_manifest, metadata)
    return dict(counts, converted=converted, added=added, backend=backend,
                dependency_images=len(closure))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', help='canonical chroot-absolute main image')
    args = parser.parse_args()
    began = time.monotonic()
    try:
        result = prepare(args.executable)
        result['seconds'] = round(time.monotonic() - began, 3)
        print('SYSTEM-APP-ADMISSION ' + json.dumps(result, sort_keys=True), flush=True)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print('SYSTEM-APP-ADMISSION FAILED: ' + str(error), file=sys.stderr, flush=True)
        raise SystemExit(1)

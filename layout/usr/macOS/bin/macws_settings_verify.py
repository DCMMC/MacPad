"""Verify all prepared Ventura Settings panes in one iOS process.

Does not rewrite/sign extensions or waive carrier/catalog requirements. The
existing shell repair path remains the owner of missing/stale dependencies.
Unlike the old boot marker fast path, checks every current file identity and
actual live trust membership, including after same-boot app replacement.
"""
import glob
import json
import os
import plistlib
import re
import stat
import subprocess
import sys
import time

import macws_boot_trust as trust

ROOTFS = '/var/mnt/rootfs'
EXTENSIONS = ROOTFS + '/System/Library/ExtensionKit/Extensions'
SETTINGS_PLUGINS = ROOTFS + '/System/Applications/System Settings.app/Contents/PlugIns'
BASE = ['/var/jb/usr/macOS/lib/libmachook.dylib',
        '/var/jb/usr/lib/libellekit.dylib',
        ROOTFS + '/usr/lib/libobjc-trampolines.dylib']
SCHEMA = 'macws-settings-extension-runtime-v2'
MANIFEST = ROOTFS + '/var/db/macws/settings-runtime/hashes.json'
CARRIER_ROOT = '/var/jb/Applications'


def read_plist(path):
    with open(path, 'rb') as stream:
        return plistlib.load(stream)


def extension_metadata(bundle):
    contents = bundle + '/Contents'
    info = read_plist(contents + '/Info.plist')
    attributes = info.get('EXAppExtensionAttributes', {})
    if attributes.get('EXExtensionPointIdentifier') != 'com.apple.Settings.extension.ui':
        return None
    identifier = info.get('CFBundleIdentifier', '')
    executable = info.get('CFBundleExecutable', '')
    if not executable:
        candidates = [os.path.basename(path) for path in glob.glob(contents + '/MacOS/*')
            if os.path.isfile(path) and not path.endswith('.macws-preload-backup')
            and '.new-' not in path]
        if len(candidates) != 1:
            raise ValueError('ambiguous Settings executable: ' + bundle)
        executable = candidates[0]
    if (not re.fullmatch(r'[A-Za-z0-9.-]+', identifier) or
            not isinstance(executable, str) or '/' in executable or executable in ('.', '..')):
        raise ValueError('invalid Settings identity: ' + bundle)
    return identifier, contents + '/MacOS/' + executable


def selected(records, path):
    codes = records.get(path, {}).get('codes', [])
    for arch in ('arm64e', 'arm64'):
        for record in codes:
            if record['arch'] == arch:
                return record['hash']
    raise ValueError('missing signed arm64 image: ' + path)


def verify():
    began = time.monotonic()
    paths, panes = list(BASE), []
    ui = subprocess.run(['/var/jb/usr/bin/uicache', '-l'], capture_output=True,
                        text=True, check=True, timeout=15).stdout
    for bundle in sorted(glob.glob(EXTENSIONS + '/*.appex') +
                         glob.glob(SETTINGS_PLUGINS + '/*.appex')):
        if not os.path.isfile(bundle + '/Contents/Info.plist'):
            continue
        metadata = extension_metadata(bundle)
        if metadata is None:
            continue
        identifier, executable = metadata
        carrier_id = 'com.macwsguide.settings-extension-carrier.' + identifier
        carrier = CARRIER_ROOT + '/MacWSSettingsExtension-' + identifier + '.app'
        carrier_executable = carrier + '/SettingsExtensionProxy'
        frameworks = bundle + '/Contents/Frameworks'
        dependencies = [executable, carrier_executable,
            frameworks + '/libmachook.dylib',
            frameworks + '/.jbroot/Library/Frameworks/CydiaSubstrate.framework/CydiaSubstrate',
            frameworks + '/libobjc-trampolines.dylib']
        for index, path in enumerate(dependencies):
            info = os.stat(path)
            if not stat.S_ISREG(info.st_mode) or (index < 2 and not info.st_mode & 0o111):
                raise ValueError('missing executable/dependency: ' + path)
            if index == 1 and not info.st_mode & stat.S_ISUID:
                raise ValueError('carrier lost setuid: ' + identifier)
        if read_plist(carrier + '/Info.plist').get('CFBundleIdentifier') != carrier_id:
            raise ValueError('wrong carrier identity: ' + identifier)
        if not any(line.startswith(carrier_id + ' : ') for line in ui.splitlines()):
            raise ValueError('carrier not registered with iOS: ' + identifier)
        with open(frameworks + '/.macws-settings-runtime') as stream:
            marker = stream.read(1024).strip().split('|')
        if len(marker) != 9 or marker[0] != SCHEMA:
            raise ValueError('invalid Settings runtime marker: ' + identifier)
        paths.extend(dependencies)
        panes.append((identifier, dependencies, marker))
    if not panes:
        raise ValueError('no Settings UI extensions verified')
    records, hashes, counts = trust.scan(paths, trust.load_cache(MANIFEST))
    base = [selected(records, path) for path in BASE]
    for identifier, dependencies, marker in panes:
        if marker[1:4] != base or marker[4:9] != [selected(records, path) for path in dependencies]:
            raise ValueError('Settings dependency signature changed: ' + identifier)
    for path, record in records.items():
        if trust.identity(os.stat(path)) != record['identity']:
            raise ValueError('Settings image changed during verification: ' + path)
    added, backend = trust.restore(hashes)
    trust.save_cache(MANIFEST, records)
    print('SETTINGS-VERIFY ' + json.dumps(dict(counts, panes=len(panes),
        added=added, backend=backend, total_seconds=round(time.monotonic() - began, 3)),
        sort_keys=True), flush=True)


if __name__ == '__main__':
    try:
        verify()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print('SETTINGS-VERIFY needs-repair: ' + str(error), file=sys.stderr, flush=True)
        raise SystemExit(1)

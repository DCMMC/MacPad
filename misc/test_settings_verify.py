import contextlib
import importlib.util
import io
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_boot_trust import macho, directory, trust, ROOT

sys.modules['macws_boot_trust'] = trust
SPEC = importlib.util.spec_from_file_location('settings_verify',
    ROOT / 'layout/usr/macOS/bin/macws_settings_verify.py')
settings = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(settings)


class SettingsVerification(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        root = Path(self.folder.name)
        self.extensions, self.carriers = root / 'Extensions', root / 'Carriers'
        self.bundle = self.extensions / 'Test.appex'
        self.contents = self.bundle / 'Contents'
        self.info = self.contents / 'Info.plist'
        self.info.parent.mkdir(parents=True)
        self.identifier = 'com.apple.TestSettings'
        self.info.write_bytes(plistlib.dumps({'CFBundleIdentifier': self.identifier,
            'CFBundleExecutable': 'Test', 'EXAppExtensionAttributes': {
                'EXExtensionPointIdentifier': 'com.apple.Settings.extension.ui'}}))
        self.carrier_id = 'com.macwsguide.settings-extension-carrier.' + self.identifier
        carrier = self.carriers / ('MacWSSettingsExtension-' + self.identifier + '.app')
        carrier.mkdir(parents=True)
        (carrier / 'Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': self.carrier_id}))
        self.frameworks = self.contents / 'Frameworks'
        self.dependencies = [self.contents / 'MacOS/Test', carrier / 'SettingsExtensionProxy',
            self.frameworks / 'libmachook.dylib',
            self.frameworks / '.jbroot/Library/Frameworks/CydiaSubstrate.framework/CydiaSubstrate',
            self.frameworks / 'libobjc-trampolines.dylib']
        self.base = [root / 'hook', root / 'substrate', root / 'trampolines']
        for path in self.dependencies + self.base:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(macho())
            path.chmod(0o755)
        self.dependencies[1].chmod(0o4755)
        records, _, _ = trust.scan([str(path) for path in self.base + self.dependencies], {})
        marker = [settings.SCHEMA] + [settings.selected(records, str(path))
                                     for path in self.base + self.dependencies]
        (self.frameworks / '.macws-settings-runtime').write_text('|'.join(marker) + '\n')
        for name, value in (('EXTENSIONS', str(self.extensions)),
                            ('SETTINGS_PLUGINS', str(root / 'Settings.app/Contents/PlugIns')),
                            ('CARRIER_ROOT', str(self.carriers)),
                            ('BASE', [str(path) for path in self.base]),
                            ('MANIFEST', str(root / 'cache/hashes.json'))):
            active = patch.object(settings, name, value)
            active.start()
            self.addCleanup(active.stop)

    def verify(self, ui=None):
        ui = self.carrier_id + ' : /registered/path\n' if ui is None else ui
        with patch.object(settings.subprocess, 'run', return_value=
                subprocess.CompletedProcess([], 0, ui, '')), \
                patch.object(settings.trust, 'restore', return_value=(0, 'test')) as restore, \
                contextlib.redirect_stdout(io.StringIO()):
            settings.verify()
            self.assertEqual(restore.call_count, 1)

    def test_complete_real_signature_verification_passes(self):
        self.verify()
        self.verify()  # positive extraction cache also has to pass all checks

    def test_changed_dependency_rejected_even_same_boot(self):
        self.verify()
        self.dependencies[2].write_bytes(macho(codes=[directory(1)]))
        with self.assertRaisesRegex(ValueError, 'signature changed'):
            self.verify()

    def test_current_registration_required(self):
        with self.assertRaisesRegex(ValueError, 'not registered'):
            self.verify(ui='')

    def test_setuid_carrier_required(self):
        self.dependencies[1].chmod(0o755)
        with self.assertRaisesRegex(ValueError, 'setuid'):
            self.verify()

    def test_marker_and_base_dependency_must_match(self):
        self.base[0].write_bytes(macho(codes=[directory(1)]))
        with self.assertRaisesRegex(ValueError, 'signature changed'):
            self.verify()

    def test_missing_executable_name_requires_unique_image(self):
        info = plistlib.loads(self.info.read_bytes())
        info.pop('CFBundleExecutable')
        self.info.write_bytes(plistlib.dumps(info))
        self.verify()
        (self.contents / 'MacOS/Another').write_bytes(macho())
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            self.verify()

    def test_other_extensions_cannot_satisfy_readiness(self):
        info = plistlib.loads(self.info.read_bytes())
        info['EXAppExtensionAttributes']['EXExtensionPointIdentifier'] = 'other'
        self.info.write_bytes(plistlib.dumps(info))
        with self.assertRaisesRegex(ValueError, 'no Settings'):
            self.verify()

    def test_embedded_settings_pane_must_be_prepared(self):
        contents = Path(settings.SETTINGS_PLUGINS) / 'GeneralSettings.appex/Contents'
        contents.mkdir(parents=True)
        info = plistlib.loads(self.info.read_bytes())
        info['CFBundleIdentifier'] = 'com.apple.systempreferences.GeneralSettings'
        (contents / 'Info.plist').write_bytes(plistlib.dumps(info))
        with self.assertRaises(FileNotFoundError):
            self.verify()  # 48 external panes cannot hide the missing 49th.

    def test_embedded_non_settings_plugin_is_untouched(self):
        contents = Path(settings.SETTINGS_PLUGINS) / 'csimporter.appex/Contents'
        contents.mkdir(parents=True)
        (contents / 'Info.plist').write_bytes(plistlib.dumps({
            'EXAppExtensionAttributes': {'EXExtensionPointIdentifier': 'spotlight'}}))
        self.verify()


if __name__ == '__main__':
    unittest.main()

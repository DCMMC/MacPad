import importlib.util
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_boot_trust import ROOT, macho, trust
from test_macho_dependencies import deps

sys.modules['macws_boot_trust'] = trust
SPEC = importlib.util.spec_from_file_location('app_prepare',
    ROOT / 'layout/usr/macOS/bin/macws_system_app_prepare.py')
app = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(app)


class SystemAppPreparation(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.binary = self.root / 'Main'
        self.binary.write_bytes(macho())
        self.state = self.root / 'state'
        self.state.mkdir()
        self.profile = self.root / 'profile.plist'
        self.profile.write_bytes(plistlib.dumps(dict.fromkeys(app.REQUIRED, True)))
        active = patch.object(app, 'PROFILE', str(self.profile))
        active.start()
        self.addCleanup(active.stop)
        active = patch.object(app, 'ROOT', str(self.root))
        active.start()
        self.addCleanup(active.stop)

    def test_rejects_third_party_nested_executable_and_escape(self):
        for path in ('/Applications/Steam.app/Contents/MacOS/steam_osx',
                     '/System/Applications/A.app/Contents/MacOS/A/child',
                     '/System/Applications/A.app/Contents/MacOS/../Other',
                     '/System/Applications/../Other.app/Contents/MacOS/A'):
            with self.assertRaises(ValueError):
                app.resolve_target(path)

    def test_trusted_stock_is_not_proof_of_chroot_profile(self):
        record = [{'arch': 'arm64e'}]
        rights = {'com.apple.security.app-sandbox': True}
        with patch.object(app.subprocess, 'run', return_value=
                subprocess.CompletedProcess([], 0, plistlib.dumps(rights), b'')):
            self.assertFalse(app.has_profile(str(self.binary), record))
        rights.update(dict.fromkeys(app.REQUIRED, True))
        with patch.object(app.subprocess, 'run', return_value=
                subprocess.CompletedProcess([], 0, plistlib.dumps(rights), b'')):
            self.assertTrue(app.has_profile(str(self.binary), record))

    def test_existing_profile_is_never_resigned(self):
        with patch.object(app, 'has_profile', return_value=True), \
                patch.object(app.subprocess, 'run') as sign:
            self.assertFalse(app.ensure_main_profile(str(self.binary), str(self.state)))
            sign.assert_not_called()
        self.assertEqual(list(self.state.iterdir()), [])

    def test_conversion_is_backed_up_and_uses_a_new_inode(self):
        inode = self.binary.stat().st_ino
        with patch.object(app, 'has_profile', side_effect=[False, True]), \
                patch.object(app.os, 'chown'), \
                patch.object(app.subprocess, 'run') as sign, \
                patch.object(trust, 'restore', return_value=(1, 'test')) as restore:
            self.assertTrue(app.ensure_main_profile(str(self.binary), str(self.state)))
            self.assertEqual(sign.call_count, 2)
            for call in sign.call_args_list:
                self.assertNotEqual(call.args[0][-1], str(self.binary))
                self.assertIn('-M', call.args[0])
            restore.assert_called_once()
        self.assertNotEqual(inode, self.binary.stat().st_ino)
        self.assertEqual(next(self.state.glob('original-*')).read_bytes(), macho())
        self.assertEqual(list(self.root.glob('.macws-admission-*')), [])

    def test_failed_admission_leaves_original_inode_and_bytes(self):
        inode = self.binary.stat().st_ino
        with patch.object(app, 'has_profile', side_effect=[False, True]), \
                patch.object(app.os, 'chown'), patch.object(app.subprocess, 'run'), \
                patch.object(trust, 'restore', side_effect=ValueError('trust failure')):
            with self.assertRaisesRegex(ValueError, 'trust failure'):
                app.ensure_main_profile(str(self.binary), str(self.state))
        self.assertEqual(inode, self.binary.stat().st_ino)
        self.assertEqual(self.binary.read_bytes(), macho())
        self.assertEqual(list(self.root.glob('.macws-admission-*')), [])

    def test_warm_preparation_still_checks_live_main_and_plugin_trust(self):
        plugins = self.root / 'PlugIns'
        plugins.mkdir()
        (plugins / 'Widget').write_bytes(macho())
        with patch.object(app, 'ensure_main_profile', return_value=False), \
                patch.object(trust, 'restore', return_value=(0, 'test')) as restore:
            for _ in range(2):
                result = app.prepare_locked(str(self.binary), str(plugins), str(self.state))
                self.assertEqual(result['images'], 2)
            self.assertEqual(restore.call_count, 2)


if __name__ == '__main__':
    unittest.main()

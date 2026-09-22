"""Exercise the freestanding production validator, including embedded panes."""
import ctypes
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SettingsPaths(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        library = Path(cls.folder.name) / 'paths.dylib'
        source = '#include "macws_settings_paths.h"\n' + '''
int bundle(const char *s) { return MacWSIsStockSettingsBundle(s); }
int executable(const char *s) { return MacWSIsStockSettingsExecutable(s); }
'''
        subprocess.run(['cc', '-x', 'c', '-shared', '-fPIC', '-Wall', '-Werror',
                        '-I', str(ROOT / 'include'), '-o', str(library), '-'],
                       input=source, text=True, check=True)
        cls.library = ctypes.CDLL(str(library))
        for name in ('bundle', 'executable'):
            getattr(cls.library, name).argtypes = [ctypes.c_char_p]
            getattr(cls.library, name).restype = ctypes.c_int

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def test_both_real_roots(self):
        for root, name in (('/System/Library/ExtensionKit/Extensions/', 'Appearance'),
                ('/System/Applications/System Settings.app/Contents/PlugIns/', 'GeneralSettings')):
            bundle = (root + name + '.appex').encode()
            self.assertEqual(self.library.bundle(bundle), 1)
            self.assertEqual(self.library.executable(bundle + b'/Contents/MacOS/' + name.encode()), 1)
            self.assertEqual(self.library.executable(bundle), 0)

    def test_rejects_paths_outside_exact_bundle_boundary(self):
        root = '/System/Library/ExtensionKit/Extensions/'
        for suffix in ('../E.appex', '.appex', 'E.appex/nested.appex',
                       'E.appex/Contents/MacOS/../Other', 'E.appex/Contents/MacOS/',
                       'E.appex/Contents/MacOS/E/extra'):
            self.assertEqual(self.library.executable((root + suffix).encode()), 0)
        for path in (None, b'', b'/tmp/E.appex/Contents/MacOS/E',
                     b'/System/Applications/Other.app/Contents/PlugIns/E.appex/Contents/MacOS/E'):
            self.assertEqual(self.library.executable(path), 0)

    def test_launch_and_identity_use_same_validator(self):
        self.assertIn('MacWSIsStockSettingsExecutable(target)',
                      (ROOT / 'ViewBridgeChrootProxy/main.c').read_text())
        self.assertIn('MacWSIsStockSettingsExecutable(executable)',
                      (ROOT / 'MacWSCatalystLaunch/Tweak.x').read_text())
        self.assertIn('MacWSIsStockSettingsBundle(bundlePath.UTF8String)',
                      (ROOT / 'libmachook/Metal_hooks.x').read_text())


if __name__ == '__main__':
    unittest.main()

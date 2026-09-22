"""Compile/run only owned CPU bitmap fixtures on the local Mac; no iPad access."""
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CGImageObserverTests(unittest.TestCase):
    def test_diagnostic_not_in_production_and_no_pixel_reads(self):
        source = (ROOT / 'misc/macws_cgimage_observer.c').read_text()
        self.assertNotIn('macws_cgimage_observer', (ROOT / 'libmachook/Makefile').read_text())
        for forbidden in ('getenv(', 'stat(', 'CGDataProviderCopyData',
                          'CGBitmapContextGetData', 'MSHookFunction', 'dlsym('):
            self.assertNotIn(forbidden, source)
        self.assertIn('MACWS_CG_OBSERVATION_LIMIT = 32', source)
        self.assertIn('width > 100 || height > 100', source)
        for width, height in ((284, 102), (309, 250), (282, 28), (471, 56)):
            self.assertIn(f'(width == {width} && height == {height})', source)
        self.assertIn('_Thread_local int observing', source)
        self.assertIn('errno = saved_errno', source)

    @unittest.skipUnless(sys.platform == 'darwin', 'requires macOS CoreGraphics')
    def test_real_interpose_preserves_pixels_and_bounds_observation(self):
        self.exercise_observer(False)

    @unittest.skipUnless(sys.platform == 'darwin', 'requires macOS CoreGraphics')
    def test_fixture_mode_ribbon_does_not_exhaust_budget(self):
        self.exercise_observer(True)

    def exercise_observer(self, fixture):
        with tempfile.TemporaryDirectory(prefix='macws-cgimage-observer-') as directory:
            temporary = Path(directory)
            library, program = temporary / 'observer.dylib', temporary / 'probe'
            common = ['xcrun', 'clang', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                      '-framework', 'CoreGraphics', '-framework', 'ImageIO',
                      '-framework', 'CoreFoundation']
            if fixture:
                common += ['-DMACWS_CGIMAGE_PPT_WELCOME_FIXTURE=1']
            subprocess.run(common + ['-dynamiclib', str(ROOT / 'misc/macws_cgimage_observer.c'),
                                     '-o', str(library)], check=True, capture_output=True)
            subprocess.run(common + [str(ROOT / 'misc/macws_cgimage_observer_probe.c'),
                                     '-o', str(program)], check=True, capture_output=True)
            environment = os.environ.copy()
            environment.pop('DYLD_INSERT_LIBRARIES', None)
            stock = subprocess.run([str(program)], env=environment, check=True,
                                   capture_output=True, text=True, timeout=10)
            environment['DYLD_INSERT_LIBRARIES'] = str(library)
            observed = subprocess.run([str(program)], env=environment, check=True,
                                      capture_output=True, text=True, timeout=10)
            self.assertEqual(stock.stdout, observed.stdout)
            width, height = (309, 250) if fixture else (17, 131)
            self.assertIn(f'CGIMAGE-PROBE width={width} height={height}', observed.stdout)
            lines = [line for line in observed.stderr.splitlines()
                     if line.startswith('CGIMAGE-OBS ')]
            self.assertEqual(len(lines), 32)
            self.assertEqual([int(re.search(r'sample=(\d+)/32', s).group(1)) for s in lines],
                             list(range(1, 33)))
            self.assertFalse(any('width=12 height=12' in line for line in lines))
            if fixture:
                self.assertFalse(any('width=230 height=20' in line for line in lines))
                self.assertFalse(any('width=17 height=131' in line for line in lines))
            for api in ('CGImageCreate', 'CGImageSourceCreateImageAtIndex', 'CGContextDrawImage'):
                self.assertTrue(any('api=' + api + ' ' in line for line in lines), api)
            self.assertTrue(any(f'rect=[3,5,{width},{height}] ctm=[1,0,0,1,2,3]' in line for line in lines))
            self.assertTrue(any('caller=probe+0x' in line for line in lines))


if __name__ == '__main__':
    unittest.main()

"""Compile and execute the actual Darwin sendfile adapter against real I/O."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('cc'),
                     'Darwin Mach memory APIs and C compiler required')
class SendfileContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='macws-sendfile-test-')
        cls.binary = Path(cls.directory.name) / 'sendfile-probe'
        subprocess.run(['cc', '-O2', '-Wall', '-Wextra', '-Werror',
                        str(ROOT / 'misc/macws_sendfile_probe.c'),
                        '-o', str(cls.binary)], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_real_copy_errors_offsets_eintr_eagain_and_memory_faults(self):
        result = subprocess.run([str(self.binary)], check=True, capture_output=True,
                                text=True, timeout=12)
        self.assertIn('SENDFILE CONTRACT PASS', result.stdout)
        self.assertIn('196608-exact-bytes', result.stdout)
        self.assertIn('adapter closed-AF_UNIX-peer/ENOTCONN/no-prefix', result.stdout)

    def test_real_macos_header_budget_reference(self):
        result = subprocess.run([str(self.binary), '--stock-reference'], check=True,
                                capture_output=True, text=True, timeout=12)
        self.assertIn('stock-macOS headers/file-budget/trailers', result.stdout)
        self.assertIn('stock-macOS closed-AF_UNIX-peer/ENOTCONN/no-prefix', result.stdout)

    def test_public_api_probe_refuses_missing_candidate(self):
        binary = Path(self.directory.name) / 'sendfile-api-probe'
        subprocess.run(['cc', '-O2', '-Wall', '-Wextra', '-Werror',
                        str(ROOT / 'misc/macws_sendfile_api_probe.c'),
                        '-o', str(binary)], check=True, capture_output=True)
        result = subprocess.run([str(binary)], capture_output=True, text=True,
                                timeout=7)
        self.assertEqual(result.returncode, 77)
        self.assertIn('adapter is not mapped', result.stderr)


class SendfileProductionContract(unittest.TestCase):
    def test_static_default_adapter_is_packaged_without_signal_or_env_override(self):
        source = (ROOT / 'libmachook/Compatibility/MacWSSendfile.c').read_text()
        self.assertIn('DYLD_INTERPOSE(MacWSSendfile, sendfile)', source)
        self.assertIn('Compatibility/MacWSSendfile.c', (ROOT / 'libmachook/Makefile').read_text())
        self.assertNotIn('getenv(', source)
        self.assertNotIn('sigaction(', source)
        self.assertNotIn('MSHookFunction', source)
        self.assertNotIn('SYS_sendfile', source)


if __name__ == '__main__':
    unittest.main()

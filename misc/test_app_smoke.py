import tempfile
from pathlib import Path
import unittest

from macws_app_smoke import bounded_tail, spawned_pid


class AppSmokeTests(unittest.TestCase):
    def test_owned_pid_requires_exact_bundle_in_current_log(self):
        log = ('1 launch-app id=custom-path pid=44 executable=/A.app/Contents/MacOS/A\n'
               '2 launch-app id=custom-path pid=55 executable=/B.app/Contents/MacOS/B\n')
        self.assertEqual(spawned_pid(log, '/A.app'), 44)
        self.assertEqual(spawned_pid(log, '/C.app'), 0)
        self.assertEqual(spawned_pid(log, '/A'), 0)

    def test_tail_limits_memory_and_observes_launch_offset(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, 'log')
            path.write_bytes(b'old\n' + b'\0' * 100000 + b'new\n')
            self.assertEqual(bounded_tail(path, 100004), 'new\n')
            self.assertEqual(bounded_tail(path), 'new\n')


if __name__ == '__main__':
    unittest.main()

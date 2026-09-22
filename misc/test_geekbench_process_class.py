import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
HOSTD_SOURCE = (ROOT / "macwshostd" / "main.m").read_text()
MACHOOK_MAKEFILE = (ROOT / "libmachook" / "Makefile").read_text()


class GeekbenchProcessClassContractTests(unittest.TestCase):
    def test_exception_is_scoped_to_exact_official_executable(self):
        self.assertIn(
            '"/Applications/Geekbench 6.app/Contents/MacOS/Geekbench 6"',
            HOSTD_SOURCE,
        )
        self.assertIn(
            "![rootPath isEqualToString:@(kGeekbenchExecutable)]",
            HOSTD_SOURCE,
        )

    def test_default_appkit_launches_keep_application_process_type(self):
        self.assertIn(
            "if (error == 0 && applicationProcessType)", HOSTD_SOURCE
        )
        self.assertIn(
            "MACWS_POSIX_SPAWN_PROC_TYPE_APP_DEFAULT", HOSTD_SOURCE
        )

    def test_no_score_or_timer_hook_is_built(self):
        self.assertNotIn("MacWSGeekbenchProcess.m", MACHOOK_MAKEFILE)
        self.assertNotIn("setScore", HOSTD_SOURCE)
        self.assertNotIn("setResult", HOSTD_SOURCE)


if __name__ == "__main__":
    unittest.main()

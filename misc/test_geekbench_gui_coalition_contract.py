"""Keep GUI Geekbench out of macwshostd's inherited service coalition."""

from pathlib import Path
import plistlib
import unittest


ROOT = Path(__file__).resolve().parents[1]
PLIST = ROOT / "misc/com.macwsguide.geekbench.plist"
LABEL = "UIKitApplication:com.macwsguide.geekbench"
EXECUTABLE = "/Applications/Geekbench 6.app/Contents/MacOS/Geekbench 6"


class GeekbenchGUICoalitionContract(unittest.TestCase):
    def test_launchd_job_owns_exact_gui_and_production_environment(self):
        with PLIST.open("rb") as stream:
            job = plistlib.load(stream)
        self.assertEqual(job["Label"], LABEL)
        self.assertEqual(job["ProgramArguments"], [
            "/var/jb/usr/macOS/bin/launchdchrootexec", "0", "0",
            "/var/mnt/rootfs", EXECUTABLE,
        ])
        self.assertTrue(job["RunAtLoad"])
        self.assertFalse(job["KeepAlive"])
        self.assertEqual(job["EnvironmentVariables"], {
            "CA_VSYNC_OFF": "1",
            "CA_DISABLE_SWAP_ICC": "1",
            "MACWS_AGX_NATIVE": "1",
            "MACWS_AGX_REGISTER_CLASSES": "1",
            "MACWS_APP_MOUNT_COMPAT": "1",
        })
        self.assertNotIn("ProcessType", job)

    def test_control_center_and_dock_use_job_not_direct_spawn(self):
        source = (ROOT / "macwshostd/main.m").read_text()
        route = source[source.index("static BOOL LaunchRequestedPath("):
                       source.index("static BOOL LaunchAllowedApp(",
                                    source.index("static BOOL LaunchRequestedPath("))]
        self.assertIn("[rootPath isEqualToString:@(kGeekbenchExecutable)]",
                      route)
        self.assertIn("return LaunchGeekbench(message);", route)
        launch = source[source.index("static BOOL LaunchGeekbench("):
                        source.index("static BOOL LaunchRequestedPath(")]
        self.assertIn("InspectJob(kGeekbenchLabel", launch)
        self.assertIn("WaitForJobPID(kGeekbenchLabel", launch)
        self.assertIn("RootExecutablePathForPID(jobPID)", launch)
        self.assertIn("TrackApplicationSession(@\"geekbench\"", launch)

    def test_dock_and_launchpad_open_through_host_route(self):
        source = (ROOT / "libmachook/mac_hooks.m").read_text()
        start = source.rindex("static OSStatus macws_LSOpenFromURLSpec(")
        route = source[start:source.index(
            "// RE-confirmed against the installed Ventura", start)]
        self.assertIn("BOOL dockRequest = macws_process_is_dock();", route)
        self.assertIn("if (status != -10810 && !dockRequest) return status;",
                      route)
        self.assertIn("macws_launch_application_path_via_host(applicationURL)",
                      route)

    def test_package_and_workspace_stop_include_job(self):
        makefile = (ROOT / "Makefile").read_text()
        self.assertIn("misc/com.macwsguide.geekbench.plist", makefile)
        gui = (ROOT / "layout/usr/macOS/bin/macos_gui.sh").read_text()
        self.assertIn('launchctl unload "$GEEKBENCH_PLIST"', gui)
        self.assertIn('launchctl asuser 501 launchctl unload '
                      '"$GEEKBENCH_PLIST"', gui)


if __name__ == "__main__":
    unittest.main()

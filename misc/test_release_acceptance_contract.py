"""The automated gate must not manufacture release acceptance evidence."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import plistlib
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location("release_gate", Path(__file__).with_name("macws_release_regression.py"))
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)
POLICY = {"MACWS_AGX_NATIVE": None, "MACWS_AGX_REGISTER_CLASSES": None}
REQUIRED = ("WindowServer", "macwsdisplayd", "MacWSHost", "UIKitSystem", "MacWSInputLab")


class FakeRemote:
    def __init__(self, plist=None, thermal="nominal", status=0):
        self.plist = plist
        self.commands = []
        self.thermal = thermal
        self.status = status

    def run(self, command, **kwargs):
        self.commands.append(command)
        if command.startswith("/bin/ps"):
            return "\n".join(f"{100 + i} /path/{name}" for i, name in enumerate(REQUIRED))
        if command.startswith("test ! -e"):
            return "0"
        if command.startswith("/var/jb/usr/bin/python3"):
            if self.plist is None:
                return json.dumps(POLICY)
            reader = shlex.split(command)[2].replace(
                "/var/jb/usr/macOS/LaunchDaemons/com.apple.WindowServer.plist", str(self.plist))
            result = subprocess.run([sys.executable, "-c", reader], capture_output=True, text=True)
            if result.returncode:
                raise RuntimeError("remote installed plist read/parse failed")
            return result.stdout
        raise AssertionError(f"unexpected remote command: {command}")

    def run_result(self, command, **kwargs):
        self.commands.append(command)
        if command != "/var/jb/usr/macOS/bin/macwsthermal":
            raise AssertionError(f"unexpected thermal command: {command}")
        raw = {"nominal": 0, "fair": 1, "serious": 2, "critical": 3}.get(self.thermal, 7)
        return subprocess.CompletedProcess([], self.status,
            f"thermal-state={self.thermal} raw={raw} low-power=no effective-temp-centic=3829 uptime=1234.5\n", "")


class ReleaseAcceptanceContract(unittest.TestCase):
    def test_valid_installed_plist_allows_absent_default_on_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "job.plist"
            path.write_bytes(plistlib.dumps({"Label": "com.apple.WindowServer"}))
            result = gate.preflight(FakeRemote(path))
            self.assertEqual(result["result"], "PASS")
            self.assertTrue(all(result["native_agx_switches"].values()))

    def test_missing_invalid_or_mistyped_plist_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "job.plist"
            variants = (None, b"not a plist", plistlib.dumps([]),
                        plistlib.dumps({"EnvironmentVariables": []}),
                        plistlib.dumps({"EnvironmentVariables": {"MACWS_AGX_NATIVE": False}}))
            for content in variants:
                with self.subTest(content=content):
                    if content is not None:
                        path.write_bytes(content)
                    result = gate.preflight(FakeRemote(path))
                    self.assertEqual(result["result"], "FAIL")
                    self.assertFalse(result["installed_native_policy"]["read_valid"])

    def test_explicit_native_optout_is_not_production_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "job.plist"
            path.write_bytes(plistlib.dumps({"EnvironmentVariables": {"MACWS_AGX_NATIVE": "0"}}))
            result = gate.preflight(FakeRemote(path))
            self.assertTrue(result["installed_native_policy"]["read_valid"])
            self.assertEqual(result["result"], "FAIL")

    def test_thermal_sample_is_live_not_a_cached_watchdog_line(self):
        remote = FakeRemote()
        result = gate.thermal_snapshot(remote)
        self.assertTrue(result["current_evidence"])
        self.assertEqual(result["source"], "fresh-macwsthermal")
        self.assertEqual(result["device_uptime_s"], 1234.5)
        self.assertEqual(remote.commands, ["/var/jb/usr/macOS/bin/macwsthermal"])

    def test_unknown_or_failed_thermal_cannot_pass(self):
        for state, status in (("unknown", 5), ("nominal", 127), ("nominal", 2)):
            with self.subTest(state=state, status=status):
                result = gate.preflight(FakeRemote(thermal=state, status=status))
                self.assertEqual(result["result"], "FAIL")
                self.assertFalse(result["thermal"]["current_evidence"])

    def test_thermal_timeout_is_missing_evidence_not_critical(self):
        remote = FakeRemote()
        remote.run_result = mock.Mock(side_effect=subprocess.TimeoutExpired("macwsthermal", 8))
        result = gate.thermal_snapshot(remote)
        self.assertEqual(result["state"], "unknown")
        self.assertFalse(result["current_evidence"])

    def test_only_critical_aborts_benchmark_without_process_intervention(self):
        for state, status in (("fair", 2), ("serious", 3), ("critical", 4)):
            remote = FakeRemote(thermal=state, status=status)
            preflight = gate.preflight(remote)
            report = gate.acceptance_report(preflight, {"result": "PASS"}, {"result": "PASS"}, preflight["thermal"])
            self.assertEqual(report["result"], "ABORTED_CRITICAL_THERMAL" if state == "critical" else "RELEASE_ACCEPTANCE_REQUIRED")
            self.assertFalse(any("kill" in command or "launchctl" in command for command in remote.commands))

    def test_automated_pass_is_never_release_pass(self):
        preflight = gate.preflight(FakeRemote())
        report = gate.acceptance_report(preflight, {"result": "PASS"}, {"result": "PASS"}, preflight["thermal"])
        self.assertEqual(report["automated_result"], "PASS")
        self.assertEqual(report["result"], "RELEASE_ACCEPTANCE_REQUIRED")
        self.assertFalse(report["release_accepted"])
        self.assertEqual(report["visible_output_gate"]["result"], "MANUAL_REQUIRED")

    def test_post_test_thermal_must_be_current_and_noncritical(self):
        preflight = gate.preflight(FakeRemote())
        unknown = gate.thermal_snapshot(FakeRemote(thermal="unknown", status=5))
        critical = gate.thermal_snapshot(FakeRemote(thermal="critical", status=4))
        for sample, expected in ((unknown, "FAIL"), (critical, "ABORTED_CRITICAL_THERMAL")):
            report = gate.acceptance_report(preflight, {"result": "PASS"}, {"result": "PASS"}, sample)
            self.assertEqual(report["result"], expected)

    def test_cli_reports_pending_acceptance_with_distinct_exit_code(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            remote = FakeRemote()
            with mock.patch.object(gate, "Remote", return_value=remote), \
                    mock.patch.object(gate, "load_app_summary", return_value={"result": "PASS"}), \
                    mock.patch.object(gate, "input_gate", return_value={"result": "PASS"}), \
                    mock.patch.object(sys, "argv", ["release", "--host", "test-device", "--output", str(output)]), \
                    contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as ended:
                    gate.main()
            self.assertEqual(ended.exception.code, 3)
            self.assertEqual(json.loads(output.read_text())["result"], "RELEASE_ACCEPTANCE_REQUIRED")
            self.assertEqual(remote.commands.count("/var/jb/usr/macOS/bin/macwsthermal"), 2)

    def test_invalid_preflight_never_runs_dynamic_probes(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            with mock.patch.object(gate, "Remote", return_value=FakeRemote(thermal="unknown", status=5)), \
                    mock.patch.object(gate, "run_app_matrix") as applications, \
                    mock.patch.object(gate, "input_gate") as inputs, \
                    mock.patch.object(sys, "argv", ["release", "--host", "test-device", "--run-apps", "--output", str(output)]), \
                    contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as ended:
                    gate.main()
            self.assertEqual(ended.exception.code, 2)
            applications.assert_not_called()
            inputs.assert_not_called()
            self.assertEqual(json.loads(output.read_text())["result"], "FAIL")


if __name__ == "__main__":
    unittest.main()

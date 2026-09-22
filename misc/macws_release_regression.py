"""One-command MacWS application/input release gate.

The daily gate deliberately uses fixed iPad targets.  A physical MacBook run
is calibration-only and is not a dependency of this script.  Run this from the
MacWS repository on the controlling Mac; the InputLab probes themselves run on
the iPad so they exercise the production MacWSInputRecord-v4 transport.

This automated phase proves application launch topology and AppKit event
delivery.  It cannot measure glass-to-glass latency or judge animation
stutter from synthetic events; the companion manual sheet in
docs/control-center-input-performance-regression-20260811.md is the required
visible-output phase.

Exit 3 / RELEASE_ACCEPTANCE_REQUIRED means the automated checks passed, not
that the release passed. Exit 2 is a failed/incomplete automated check or a
Critical thermal abort. This automated-only tool never emits release PASS.
"""

import argparse
import json
import pathlib
import re
import shlex
import subprocess
import time


DEFAULT_APPS_OUTPUT = pathlib.Path("/tmp/macws-control-center-regression")
APP_NAMES = [
    "glassdemo", "terminal", "activity-monitor", "finder", "vscode",
    "system-settings", "maps", "amadine", "word", "excel", "powerpoint",
    "asphalt",
]
THRESHOLDS = {
    "active_interaction_target_fps": 60.0,
    "active_interaction_1pct_low_fps": 45.0,
    "input_bridge_p95_ms": 8.0,
    "input_to_visible_p95_ms": 50.0,
    "motion_60_min_delivery_hz": 45.0,
    "motion_120_min_delivery_hz": 60.0,
}


class Remote:
    def __init__(self, host, user, port):
        self.base = [
            "ssh", "-p", str(port), "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=8", f"{user}@{host}",
        ]

    def run(self, command, *, timeout=30, check=True):
        result = self.run_result(command, timeout=timeout)
        if check and result.returncode:
            raise RuntimeError(
                f"remote command failed rc={result.returncode}: "
                f"{result.stderr.strip()}")
        return result.stdout

    def run_result(self, command, *, timeout=30):
        return subprocess.run(
            self.base + [command], capture_output=True, text=True,
            timeout=timeout,
        )


def parse_json_output(text, label):
    start = text.find("{")
    if start < 0:
        raise RuntimeError(f"{label} emitted no JSON: {text[-400:]}")
    try:
        return json.loads(text[start:])
    except json.JSONDecodeError as error:
        raise RuntimeError(f"invalid {label} JSON: {error}") from error


def process_table(remote):
    text = remote.run("/bin/ps -axo pid=,comm=")
    table = {}
    for line in text.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) != 2 or not fields[0].isdigit():
            continue
        table.setdefault(pathlib.PurePosixPath(fields[1]).name, []).append(
            int(fields[0]))
    return table


def thermal_snapshot(remote):
    began = time.monotonic()
    observed_at = time.time()
    try:
        result = remote.run_result("/var/jb/usr/macOS/bin/macwsthermal", timeout=8)
        text, status = result.stdout.strip(), result.returncode
        error = result.stderr.strip()[:300] if status not in (0, 2, 3, 4, 6) else None
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exception:
        text, status, error = "", None, str(exception)[:300]
    state_match = re.search(r"(?:^|\s)thermal-state=(nominal|fair|serious|critical)\b", text)
    raw_match = re.search(r"(?:^|\s)raw=(\d+)\b", text)
    uptime_match = re.search(r"(?:^|\s)uptime=(\d+(?:\.\d+)?)\b", text)
    temperature_match = re.search(r"(?:^|\s)effective-temp-centic=(-?\d+)\b", text)
    state = state_match.group(1) if state_match else "unknown"
    expected = {"nominal": (0, 0), "fair": (1, 2), "serious": (2, 3), "critical": (3, 4)}
    elapsed = time.monotonic() - began
    current = bool(state in expected and raw_match and uptime_match and
                   int(raw_match.group(1)) == expected[state][0] and
                   float(uptime_match.group(1)) > 0 and
                   status in (expected[state][1], 6) and elapsed <= 8)
    # Keep an observed Critical state visible even when another telemetry
    # field is malformed: it can stop this benchmark, never control apps.
    return {
        "state": state if current or state == "critical" else "unknown",
        "temperature_c": (int(temperature_match.group(1)) / 100.0
                          if temperature_match and int(temperature_match.group(1)) >= 0 else None),
        "source": "fresh-macwsthermal",
        "current_evidence": current,
        "observed_at_unix": observed_at,
        "sample_elapsed_s": elapsed,
        "device_uptime_s": float(uptime_match.group(1)) if uptime_match else None,
        "exit_status": status,
        "error": error,
        "raw_witness": text,
    }


def installed_native_policy(remote):
    # Read only the two policy keys, never the full environment. A missing or
    # unreadable file is not equivalent to a valid plist with default keys.
    reader = """
import json, pathlib, plistlib
p = pathlib.Path('/var/jb/usr/macOS/LaunchDaemons/com.apple.WindowServer.plist')
if not p.is_file() or p.stat().st_size > 1048576:
    raise ValueError('missing or oversized installed WindowServer plist')
value = plistlib.loads(p.read_bytes())
if not isinstance(value, dict):
    raise ValueError('installed plist must be a dictionary')
environment = value.get('EnvironmentVariables', {})
if not isinstance(environment, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in environment.items()):
    raise ValueError('launch environment must contain string keys and values')
print(json.dumps({key: environment.get(key) for key in ('MACWS_AGX_NATIVE', 'MACWS_AGX_REGISTER_CLASSES')}))
"""
    keys = ("MACWS_AGX_NATIVE", "MACWS_AGX_REGISTER_CLASSES")
    try:
        value = parse_json_output(remote.run(
            "/var/jb/usr/bin/python3 -c " + shlex.quote(reader), timeout=8), "installed plist")
        if not isinstance(value, dict) or set(value) != set(keys) or any(
                item is not None and not isinstance(item, str) for item in value.values()):
            raise ValueError("invalid installed native-policy response")
        return {"read_valid": True, "switches": {key: value[key] != "0" for key in keys}, "error": None}
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        return {"read_valid": False, "switches": {key: False for key in keys}, "error": str(error)[:400]}


def preflight(remote):
    processes = process_table(remote)
    required = [
        "WindowServer", "macwsdisplayd", "MacWSHost", "UIKitSystem",
        "MacWSInputLab",
    ]
    missing = [name for name in required if not processes.get(name)]
    native = installed_native_policy(remote)
    native_keys = native["switches"]
    diagnostics_off = remote.run(
        "test ! -e /var/mnt/rootfs/private/tmp/macws_runtime_diagnostics; "
        "printf $?", check=False,
    ).strip() == "0"
    thermal = thermal_snapshot(remote)
    result = "PASS" if (not missing and native["read_valid"] and all(native_keys.values()) and
                         diagnostics_off and thermal["current_evidence"] and
                         thermal["state"] != "critical") \
        else "FAIL"
    return {
        "result": result,
        "required_processes": {
            name: processes.get(name, []) for name in required
        },
        "missing_processes": missing,
        "native_agx_switches": native_keys,
        "installed_native_policy": native,
        "runtime_diagnostics_sentinel_off": diagnostics_off,
        "thermal": thermal,
        "thermal_policy": "fresh telemetry required; Critical aborts this test only; no process intervention",
    }


def run_remote_python(remote, repo, script, arguments, timeout=30):
    command = "cd {} && python3 {} {}".format(
        shlex.quote(str(pathlib.PurePosixPath(repo) / "misc")),
        shlex.quote(script),
        " ".join(shlex.quote(str(value)) for value in arguments),
    )
    return parse_json_output(remote.run(command, timeout=timeout), script)


def input_gate(remote, repo, pid):
    matrix = run_remote_python(
        remote, repo, "host_input_matrix.py", ["--pid", pid], timeout=30)
    motion = {}
    for rate in (60, 120):
        motion[str(rate)] = run_remote_python(
            remote, repo, "host_input_motion.py",
            ["--pid", pid, "--duration", 5, "--hz", rate], timeout=30)
    rates_ok = (
        motion["60"].get("drag_delivery_hz", 0) >=
            THRESHOLDS["motion_60_min_delivery_hz"] and
        motion["120"].get("drag_delivery_hz", 0) >=
            THRESHOLDS["motion_120_min_delivery_hz"]
    )
    latency_ok = all(
        result.get("latency_ms", {}).get("p95") is not None and
        result["latency_ms"]["p95"] <= THRESHOLDS["input_bridge_p95_ms"]
        for result in motion.values()
    )
    semantic_ok = matrix.get("result") == "PASS" and all(
        result.get("result") == "PASS" for result in motion.values())
    return {
        "result": "PASS" if semantic_ok and rates_ok and latency_ok else "FAIL",
        "semantic_matrix": matrix,
        "motion": motion,
        "gates": {
            "semantic_events": semantic_ok,
            "delivery_rate": rates_ok,
            "p95_latency": latency_ok,
        },
    }


def load_app_summary(path):
    if not path.is_file():
        raise RuntimeError(f"missing app summary: {path}")
    summary = json.loads(path.read_text())
    found = [item.get("app") for item in summary.get("apps", [])]
    return {
        "result": "PASS" if (summary.get("result") == "PASS" and
                                found == APP_NAMES) else "FAIL",
        "source": str(path),
        "summary": summary,
    }


def run_app_matrix(args):
    command = [
        "python3", str(pathlib.Path(__file__).with_name(
            "control_center_app_regression.py")),
        "--host", args.host, "--user", args.user, "--port", str(args.port),
        "--output", str(args.apps_output),
    ]
    subprocess.run(command, check=True)


def acceptance_report(preflight_result, applications, inputs, final_thermal):
    critical = any(sample.get("state") == "critical" for sample in
                   (preflight_result.get("thermal", {}), final_thermal))
    automated_pass = (not critical and preflight_result.get("result") == "PASS" and
                      applications.get("result") == "PASS" and inputs.get("result") == "PASS" and
                      final_thermal.get("current_evidence") is True and
                      final_thermal.get("state") in ("nominal", "fair", "serious"))
    return {
        "schema_version": 2,
        "result": ("ABORTED_CRITICAL_THERMAL" if critical else
                   "RELEASE_ACCEPTANCE_REQUIRED" if automated_pass else "FAIL"),
        "automated_result": "PASS" if automated_pass else "FAIL",
        "release_accepted": False,
        "preflight": preflight_result,
        "applications": applications,
        "input": inputs,
        "final_thermal": final_thermal,
        "visible_output_gate": {
            "result": "MANUAL_REQUIRED",
            "reason": ("synthetic transport events cannot measure physical "
                       "touch-to-photon latency or 1% low frame rate; this "
                       "automated phase cannot authorize release acceptance"),
            "procedure": "docs/control-center-input-performance-regression-20260811.md",
        },
        "macbook_calibration": "not required for daily regression",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", default="mobile")
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument(
        "--remote-repo", default="/var/jb/var/mobile/MacWSBootingGuide")
    parser.add_argument("--apps-output", type=pathlib.Path,
                        default=DEFAULT_APPS_OUTPUT)
    parser.add_argument("--run-apps", action="store_true",
                        help="launch all 12 apps before validating their matrix")
    parser.add_argument("--output", type=pathlib.Path,
                        default=pathlib.Path("/tmp/macws-release-regression.json"))
    args = parser.parse_args()
    remote = Remote(args.host, args.user, args.port)
    began = time.time()
    preflight_result = preflight(remote)
    apps = {"result": "NOT_RUN", "reason": "preflight must pass"}
    input_result = {"result": "NOT_RUN", "reason": "preflight and applications must pass"}
    final_thermal = preflight_result["thermal"]
    if preflight_result["result"] == "PASS":
        if args.run_apps:
            run_app_matrix(args)
            # App launch is the heaviest automated phase.  Refresh both the
            # process and thermal witnesses before the input benchmark; a new
            # Critical sample must never be ignored just because the initial
            # preflight was cool.
            preflight_result = preflight(remote)
        if preflight_result["result"] == "PASS":
            try:
                apps = load_app_summary(args.apps_output / "summary.json")
            except (OSError, RuntimeError, ValueError) as error:
                apps = {"result": "FAIL", "error": str(error)}
            inputlab_pids = preflight_result["required_processes"].get("MacWSInputLab", [])
            if apps["result"] == "PASS" and inputlab_pids:
                input_result = input_gate(remote, args.remote_repo, inputlab_pids[-1])
        final_thermal = thermal_snapshot(remote)
    report = acceptance_report(preflight_result, apps, input_result, final_thermal)
    report.update(device=args.host, elapsed_s=time.time() - began, thresholds=THRESHOLDS)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({
        "result": report["result"],
        "output": str(args.output),
        "automated_result": report["automated_result"],
        "thermal": report["final_thermal"],
    }, ensure_ascii=False, indent=2))
    raise SystemExit(3 if report["result"] == "RELEASE_ACCEPTANCE_REQUIRED" else 2)


if __name__ == "__main__":
    main()

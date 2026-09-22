"""Release regression for every MacWS Control Center application.

This runner exercises the same macwshost:// URL action handled by each Control
Center button, then requires four independent runtime witnesses: a live process,
a non-empty version-2 AppKit window catalog, a process-local input socket, and a
Host DisplayStream scene/focus transition.  A screenshot is retained per app.

It intentionally does not call a private control action inside an application.
App-specific click/type/gesture scenarios are a separate phase so a launch PASS
cannot hide a broken input responder.
"""

import argparse
import hashlib
import json
import pathlib
import re
import struct
import subprocess
import time


APPS = [
    "glassdemo", "terminal", "activity-monitor", "finder", "vscode",
    "system-settings", "maps", "amadine", "word", "excel", "powerpoint",
    "asphalt",
]


class Remote:
    def __init__(self, host, user, port):
        self.base = [
            "ssh", "-p", str(port), "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=8", f"{user}@{host}",
        ]

    def run(self, command, *, check=True, binary=False, timeout=20):
        result = subprocess.run(
            self.base + [command], capture_output=True,
            text=not binary, timeout=timeout,
        )
        if check and result.returncode:
            stderr = result.stderr.decode(errors="replace") if binary \
                else result.stderr
            raise RuntimeError(
                f"remote command failed rc={result.returncode}: {stderr}")
        return result.stdout

    def offset(self, path):
        value = self.run(f"wc -c < {path} 2>/dev/null || printf 0")
        return int(value.strip() or 0)

    def appended(self, path, offset):
        return self.run(f"tail -c +{offset + 1} {path} 2>/dev/null || true")

    def read_binary(self, path):
        return self.run(f"dd if={path} bs=1048576 2>/dev/null",
                        check=False, binary=True, timeout=30)


def parse_metrics(payload):
    if len(payload) < 24:
        return None
    magic, version, header_size, entry_size, count, generation = \
        struct.unpack_from("<IHHIIQ", payload)
    valid = (
        magic == 0x4D57474D and version == 2 and header_size == 24 and
        entry_size == 20 and count >= 1 and generation > 0 and
        len(payload) == header_size + count * entry_size
    )
    if not valid:
        return None
    windows = []
    for index in range(count):
        offset = header_size + index * entry_size
        window, flags, group, minimum_width, minimum_height = \
            struct.unpack_from("<IIIff", payload, offset)
        windows.append({
            "window": window, "flags": flags, "logical_group": group,
            "minimum_width": minimum_width,
            "minimum_height": minimum_height,
        })
    if not any(window["flags"] & 1 for window in windows):
        return None
    return {"generation": generation, "entry_count": count, "windows": windows}


def pid_from_log(app, text):
    patterns = [
        rf"launch-app window-ready id={re.escape(app)} pid=(\d+)",
        rf"launch-app reuse id={re.escape(app)} pid=(\d+)",
        rf"launch-app process-ready id={re.escape(app)} pid=(\d+)",
        rf"launch-app id={re.escape(app)} pid=(\d+)",
    ]
    matches = []
    for pattern in patterns:
        matches.extend(re.findall(pattern, text))
    return int(matches[-1]) if matches else 0


def process_is_live(remote, pid):
    if pid <= 1:
        return False
    # Apps run as root inside the chroot.  `kill -0` from the mobile SSH user
    # returns EPERM even while they are healthy, so use the read-only process
    # table as the liveness witness.
    value = remote.run(f"ps -p {pid} -o pid= 2>/dev/null", check=False)
    return value.strip() == str(pid)


def test_app(remote, app, output, timeout):
    host_log = "/var/mobile/Library/Logs/MacWSHost.log"
    hostd_log = "/var/mobile/Library/Logs/MacWSHostd.log"
    host_offset = remote.offset(host_log)
    hostd_offset = remote.offset(hostd_log)
    screenshot_path = "/var/mobile/Library/Logs/MacWSHost-ui.png"
    began = time.monotonic()
    remote.run(f"uiopen --url macwshost://{app}")

    pid = 0
    metrics = None
    input_socket = False
    host_delta = ""
    hostd_delta = ""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        hostd_delta = remote.appended(hostd_log, hostd_offset)
        host_delta = remote.appended(host_log, host_offset)
        pid = pid_from_log(app, hostd_delta)
        if pid > 1:
            payload = remote.read_binary(
                f"/var/mnt/rootfs/private/tmp/macws_window_metrics.{pid}.bin")
            metrics = parse_metrics(payload)
            input_socket = remote.run(
                f"test -S /var/mnt/rootfs/private/tmp/macws_app_input.{pid}.sock; "
                "printf $?", check=False,
            ).strip() == "0"
            live = process_is_live(remote, pid)
            scene = bool(re.search(
                rf"(?:fullscreen-focus-reconciled|window-auto-scene|"
                rf"launch-auto-window|pending-window).*"
                rf"pid={pid}\b", host_delta))
            if metrics and input_socket and live and scene:
                break
        time.sleep(0.35)

    remote.run("uiopen --url macwshost://screenshot-ui")
    time.sleep(0.8)
    screenshot = remote.read_binary(screenshot_path)
    screenshot_file = output / f"{app}.png"
    screenshot_file.write_bytes(screenshot)
    elapsed = time.monotonic() - began
    scene = bool(pid > 1 and re.search(
        rf"(?:fullscreen-focus-reconciled|window-auto-scene|"
        rf"launch-auto-window|pending-window).*"
        rf"pid={pid}\b", host_delta))
    process_live = process_is_live(remote, pid)
    result = {
        "app": app,
        "result": "PASS" if pid > 1 and process_live and metrics and
            input_socket and scene
            else "FAIL",
        "elapsed_s": elapsed,
        "pid": pid,
        "process_live": process_live,
        "window_metrics": metrics,
        "input_socket": input_socket,
        "host_scene_transition": scene,
        "screenshot": str(screenshot_file),
        "screenshot_bytes": len(screenshot),
        "screenshot_sha256": hashlib.sha256(screenshot).hexdigest(),
        "host_log": host_delta,
        "hostd_log": hostd_delta,
    }
    (output / f"{app}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: result[key] for key in (
        "app", "result", "elapsed_s", "pid", "process_live",
        "input_socket", "host_scene_transition", "screenshot_bytes",
    )}, ensure_ascii=False), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", default="mobile")
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--output", type=pathlib.Path,
                        default=pathlib.Path("/tmp/macws-control-center-regression"))
    parser.add_argument(
        "--summary-only", action="store_true",
        help=("merge the retained per-app JSON witnesses without launching "
              "applications; useful after a bounded subset rerun"))
    parser.add_argument("apps", nargs="*", choices=APPS)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.summary_only:
        if args.apps:
            parser.error("--summary-only does not accept an app subset")
        results = []
        missing = []
        for app in APPS:
            path = args.output / f"{app}.json"
            if not path.is_file():
                missing.append(app)
                continue
            try:
                result = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError) as error:
                raise SystemExit(f"invalid retained result {path}: {error}")
            if result.get("app") != app:
                raise SystemExit(
                    f"retained result app mismatch {path}: {result.get('app')}")
            results.append(result)
        if missing:
            raise SystemExit(
                "missing retained app results: " + ", ".join(missing))
    else:
        remote = Remote(args.host, args.user, args.port)
        apps = args.apps or APPS
        results = [
            test_app(remote, app, args.output, args.timeout) for app in apps
        ]
    summary = {
        "result": "PASS" if all(item["result"] == "PASS" for item in results)
            else "FAIL",
        "device": args.host,
        "apps": results,
    }
    # A bounded app rerun must not silently replace the complete release
    # matrix.  Keep its summary separately; --summary-only then merges every
    # retained per-app witness into the canonical 12-app summary.
    summary_path = args.output / "summary.json"
    if not args.summary_only and args.apps:
        summary_path = args.output / (
            "summary-" + "-".join(args.apps) + ".json")
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({
        "result": summary["result"],
        "passed": sum(item["result"] == "PASS" for item in results),
        "total": len(results),
        "summary": str(summary_path),
    }, ensure_ascii=False, indent=2))
    if summary["result"] != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()

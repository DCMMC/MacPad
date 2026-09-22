"""Collect runtime evidence for MacWS window-mode acceptance.

This harness intentionally scores the first Scene geometry, not eventual
convergence.  Run it from the controlling Mac before performing the physical
Finder gesture and opening a new AppKit panel::

    python3 misc/macws_window_mode_acceptance.py \
        --device 192.168.1.15 --wait 90 --require-drag

The command records append-only offsets first, then reads only fresh Host,
SpringBoard-tweak and Finder evidence.  A stock-size Scene followed by a
corrective resize is a failure even if the final bounds look correct.

Logs cannot prove the final iPadOS composite. Even when every log check
succeeds, this tool returns LOG_CHECKS_PASS_VISUAL_UNVERIFIED (exit 3), never
PASS. Inspect full iPadOS screen captures across the same reproduction before
claiming acceptance. A macOS VNC capture or Host-only drawable is insufficient.
"""

import argparse
import json
import math
import pathlib
import re
import subprocess
import time


HOST_LOG = "/var/mobile/Library/Logs/MacWSHost.log"
WINDOWING_LOG = "/var/mobile/Library/Logs/MacWSWindowing.log"
# Finder's production launchd job owns this stderr file.  The historical
# Finder.host.log belongs to manually launched generations and can remain
# present across reboot, which made a current physical drag look unevidenced.
FINDER_LOG = "/var/jb/var/mobile/finder-desktop.log"


class Remote:
    def __init__(self, host, user, port):
        self.base = [
            "ssh", "-p", str(port), "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=5", f"{user}@{host}",
        ]

    def run(self, command, timeout=15):
        result = subprocess.run(
            self.base + [command], capture_output=True, text=True,
            timeout=timeout,
        )
        if result.returncode:
            raise RuntimeError(
                f"remote command failed rc={result.returncode}: "
                f"{result.stderr.strip()}")
        return result.stdout

    def offset(self, path):
        value = self.run(
            f"wc -c < {path} 2>/dev/null || printf 0").strip()
        return int(value or 0)

    def appended(self, path, offset):
        return self.run(
            f"tail -c +{offset + 1} {path} 2>/dev/null || true")


def _last_match(pattern, text):
    matches = list(re.finditer(pattern, text, re.MULTILINE))
    return matches[-1] if matches else None


def _near(left, right, tolerance=1.5):
    return math.isfinite(left) and math.isfinite(right) and \
        abs(left - right) <= tolerance


def analyze(host_text, windowing_text, finder_text, require_drag,
            require_springback):
    checks = {}
    evidence = {}
    publish = _last_match(
        r"^(?P<timestamp>[0-9.]+) scene-initial-size published "
        r"nonce=(?P<nonce>\S+) owner=(?P<owner>\d+) "
        r"window=(?P<window>\d+).*?"
        r"target=(?P<width>[0-9.]+)x(?P<height>[0-9.]+).*?"
        r"fixed=(?P<fixed_width>YES|NO)x(?P<fixed_height>YES|NO)",
        host_text,
    )
    checks["initial_request_published"] = publish is not None
    if publish:
        expected_width = float(publish.group("width"))
        expected_height = float(publish.group("height"))
        publish_timestamp = float(publish.group("timestamp"))
        owner_pid = publish.group("owner")
        target_window = publish.group("window")
        nonce = publish.group("nonce")
        host_after_publish = host_text[publish.start():]
        evidence["initial_request"] = publish.group(0)

        entry = re.search(
            rf"initial-size entering route=layout-attributes-calculator .*?"
            rf"scene=(?P<fbs_scene>\S+) .*?"
            rf"nonce={re.escape(nonce)}(?:\s|$)", windowing_text,
            re.MULTILINE,
        )
        returned = re.search(
            rf"initial-size returned route=layout-attributes-calculator .*?"
            rf"nonce={re.escape(nonce)} .*?"
            r"default-size-observed=(?P<default>YES|NO) "
            r"grid-observed=(?P<grid>YES|NO)",
            windowing_text,
            re.MULTILINE,
        )
        checks["initial_layout_hook_entered"] = entry is not None
        checks["initial_sizing_path_consumed"] = bool(returned and (
            returned.group("default") == "YES" or
            returned.group("grid") == "YES"))
        if entry:
            evidence["initial_layout_entry"] = entry.group(0)
        if returned:
            evidence["initial_layout_return"] = returned.group(0)

        # The Host can publish unrelated geometry while several Stage Manager
        # windows coexist.  SpringBoard's claim log is the first point where
        # the activation nonce is bound to the concrete FBS Scene.  Join that
        # identity to Host's session log instead of accepting whichever Scene
        # happens to log geometry first.
        geometry = None
        session_identifier = None
        if entry:
            fbs_scene = entry.group("fbs_scene")
            geometry = re.search(
                rf"^(?P<timestamp>[0-9.]+) scene-geometry "
                rf"id=(?P<session>\S+) "
                rf"fbs={re.escape(fbs_scene)} "
                r"bounds=(?P<width>[0-9.]+)x(?P<height>[0-9.]+)",
                host_after_publish,
                re.MULTILINE,
            )
            if geometry:
                session_identifier = geometry.group("session")
        first_width = float(geometry.group("width")) if geometry else math.nan
        first_height = float(geometry.group("height")) if geometry else math.nan
        checks["first_scene_geometry_exact"] = bool(
            geometry and _near(first_width, expected_width) and
            _near(first_height, expected_height))
        if geometry:
            evidence["first_scene_geometry"] = geometry.group(0)

        # A correct eventual size is still a regression if the user waits
        # through a multi-second activation.  Bind timing to this exact native
        # window and Scene.  The candidate edge is optional for manually
        # requested windows, but when present it covers the pre-publication
        # catalog-settlement interval as well.
        candidate = _last_match(
            rf"^(?P<timestamp>[0-9.]+) window-auto-scene candidate .*?"
            rf"pid={re.escape(owner_pid)} window={re.escape(target_window)} "
            r".*$", host_text[:publish.start()])
        prompt = False
        if geometry:
            geometry_timestamp = float(geometry.group("timestamp"))
            activation_delay = geometry_timestamp - publish_timestamp
            prompt = 0.0 <= activation_delay <= 1.25
            evidence["activation_to_first_geometry_ms"] = round(
                activation_delay * 1000.0, 1)
            if candidate:
                discovery_delay = (geometry_timestamp -
                                   float(candidate.group("timestamp")))
                prompt = prompt and 0.0 <= discovery_delay <= 1.75
                evidence["discovery_to_first_geometry_ms"] = round(
                    discovery_delay * 1000.0, 1)
                evidence["window_candidate"] = candidate.group(0)
        checks["initial_scene_ready_promptly"] = prompt

        postcondition = None
        if session_identifier:
            postcondition = re.search(
                rf"scene-initial-size postcondition "
                rf"id={re.escape(session_identifier)} "
                r"landed=(?P<landed>YES|NO).*?action=(?P<action>\S+)",
                host_after_publish,
                re.MULTILINE,
            )
        checks["initial_size_postcondition"] = bool(
            postcondition and postcondition.group("landed") == "YES" and
            postcondition.group("action") == "keep-initial-layout")
        checks["no_corrective_resize"] = (
            "fallback-resize" not in host_after_publish and
            "initial-layout-postcondition-failed" not in host_after_publish)
        if postcondition:
            evidence["initial_size_postcondition"] = postcondition.group(0)

        layout = re.search(
            rf"scene-activation layout-postcondition "
            rf"window={re.escape(target_window)} (?:origin=\S+ )?"
            r"origin-state=(?P<state>-?\d+) .*?"
            r"foreground-window-scenes=(?P<count>\d+)",
            host_after_publish,
            re.MULTILINE,
        )
        checks["stage_manager_windows_coexist"] = bool(
            layout and int(layout.group("state")) in (0, 1) and
            int(layout.group("count")) >= 2)
        if layout:
            evidence["layout_postcondition"] = layout.group(0)
    else:
        for name in (
                "initial_layout_hook_entered", "initial_sizing_path_consumed",
                "first_scene_geometry_exact",
                "initial_scene_ready_promptly",
                "initial_size_postcondition", "no_corrective_resize",
                "stage_manager_windows_coexist"):
            checks[name] = False

    if require_springback:
        scene_pattern = r"\S+"
        if publish:
            bound_entry = re.search(
                rf"initial-size entering route=layout-attributes-calculator "
                rf"scene=(?P<scene>\S+) .*?nonce="
                rf"{re.escape(publish.group('nonce'))}(?:\s|$)",
                windowing_text,
                re.MULTILINE,
            )
            if bound_entry:
                scene_pattern = re.escape(bound_entry.group("scene"))
        springback = _last_match(
            rf"(?:resize-response constrained|resize-policy springback) "
            rf"scene={scene_pattern} .*?fixed=(?:YES|NO)x(?:YES|NO)",
            windowing_text)
        checks["fixed_axis_springback_observed"] = springback is not None
        if springback:
            evidence["fixed_axis_springback"] = springback.group(0)

    if require_drag:
        began_matches = list(re.finditer(
            r"direct-touch lifecycle=began "
            r"window=(?P<window>\d+) contact=(?P<contact>\d+).*",
            host_text))
        began = began_matches[-1] if began_matches else None
        dragging = None
        ended = None
        cancelled = None
        gesture_window = None
        gesture_contact = None
        if began:
            gesture_window = began.group("window")
            gesture_contact = began.group("contact")
            gesture_tail = host_text[began.end():]
            dragging = re.search(
                rf"direct-touch lifecycle=dragging "
                rf"window={re.escape(gesture_window)} "
                rf"contact={re.escape(gesture_contact)}.*",
                gesture_tail)
            ended = re.search(
                rf"direct-touch lifecycle=ended "
                rf"window={re.escape(gesture_window)} "
                rf"contact={re.escape(gesture_contact)}.*",
                gesture_tail)
            cancelled = re.search(
                rf"direct-touch lifecycle="
                rf"(?:cancelled|geometry-reset|input-disabled) "
                rf"window={re.escape(gesture_window)} "
                rf"contact={re.escape(gesture_contact)}.*",
                gesture_tail)
        ordered = bool(dragging and ended and dragging.start() < ended.start())
        cancellation_during_gesture = bool(
            cancelled and ended and cancelled.start() < ended.start())
        checks["physical_single_finger_lifecycle"] = \
            ordered and not cancellation_during_gesture
        content_route = None
        native_moves = []
        native_up = None
        session = None
        if gesture_contact and gesture_window:
            content_route = _last_match(
                rf"APP-INPUT CONTENT-DRAG-ROUTE .*?"
                rf"gesture={re.escape(gesture_contact)} .*?"
                r"route=process-local-tracker", finder_text)
            native_moves = list(re.finditer(
                rf"APP-INPUT CORE-DRAG-NATIVE .*?kind=2 .*?"
                rf"gesture={re.escape(gesture_contact)} "
                rf"window={re.escape(gesture_window)} .*?result=0",
                finder_text))
            native_up = _last_match(
                rf"APP-INPUT CORE-DRAG-NATIVE .*?kind=(?:3|4) .*?"
                rf"gesture={re.escape(gesture_contact)} "
                rf"window={re.escape(gesture_window)} .*?result=0",
                finder_text)
            session = _last_match(
                rf"APP-INPUT CORE-DRAG-SESSION .*?"
                rf"window={re.escape(gesture_window)} .*?"
                r"route=exact-window-cgs", finder_text)
        checks["finder_process_local_drag_route"] = content_route is not None
        checks["finder_core_drag_continuations"] = bool(
            native_moves and native_up and session)
        if began:
            evidence["drag_began"] = began.group(0)
        if dragging:
            evidence["dragging"] = dragging.group(0)
        if ended:
            evidence["drag_ended"] = ended.group(0)
        if cancelled:
            evidence["drag_cancellation_candidate"] = cancelled.group(0)
        if content_route:
            evidence["finder_drag_route"] = content_route.group(0)
        if native_up:
            evidence["finder_core_drag_up"] = native_up.group(0)
        if session:
            evidence["finder_core_drag_session"] = session.group(0)

    return {
        "schema": "macws-window-mode-evidence-v2",
        "result": ("LOG_CHECKS_PASS_VISUAL_UNVERIFIED"
                   if checks and all(checks.values()) else "FAIL"),
        "visual_acceptance": "UNVERIFIED",
        "required_visual_evidence": [
            "Full iPadOS screen before activation and throughout first presentation",
            "Each sibling's visible outer frame remains independent on the same stage",
            "Fixed/minimum axes spring back with correct content size and no clipping",
            "Text and icons retain native detail after resize and settling",
        ],
        "checks": checks,
        "evidence": evidence,
    }


def _self_test():
    host = "\n".join((
        "0.0 window-auto-scene candidate identity=1:w:1 pid=1 window=1 flags=0 reason=new-onscreen-top-level",
        "0.7 scene-initial-size published nonce=N owner=1 window=1 target=300.0x400.0 fixed=NOxYES",
        "0.9 scene-geometry id=S fbs=F bounds=300.0x400.0 preferred=1x1",
        "1.0 scene-initial-size postcondition id=S landed=YES expected=300.0x400.0 actual=300.0x400.0 action=keep-initial-layout",
        "1.1 scene-activation layout-postcondition window=1 origin=O origin-state=1 foreground-window-scenes=2",
        "2.0 direct-touch lifecycle=began window=1 contact=7",
        "2.5 direct-touch lifecycle=dragging window=1 contact=7",
        "3.0 direct-touch lifecycle=ended window=1 contact=7",
    ))
    windowing = "\n".join((
        "1 initial-size entering route=layout-attributes-calculator scene=F nonce=N role=1 target=300.0x400.0",
        "2 initial-size returned route=layout-attributes-calculator scene=F nonce=N frame={{0, 0}, {300, 400}} default-size-observed=YES grid-observed=YES request-consumed=YES",
        "3 resize-policy springback scene=F proposed=200.0x200.0 constrained=300.0x400.0 result=300.0x400.0 fixed=NOxYES",
    ))
    finder = "\n".join((
        "#### APP-INPUT CONTENT-DRAG-ROUTE pid=1 gesture=7 route=process-local-tracker",
        "#### APP-INPUT CORE-DRAG-NATIVE pid=1 kind=2 gesture=7 window=1 result=0",
        "#### APP-INPUT CORE-DRAG-NATIVE pid=1 kind=3 gesture=7 window=1 result=0",
        "#### APP-INPUT CORE-DRAG-SESSION pid=1 window=1 route=exact-window-cgs",
    ))
    report = analyze(host, windowing, finder, True, True)
    if report["result"] != "LOG_CHECKS_PASS_VISUAL_UNVERIFIED":
        raise RuntimeError(json.dumps(report, indent=2))
    if report["visual_acceptance"] != "UNVERIFIED":
        raise RuntimeError("logs alone must never prove visual acceptance")
    grid_only = analyze(host, windowing.replace("default-size-observed=YES",
                        "default-size-observed=NO"), finder, True, True)
    if not grid_only["checks"]["initial_sizing_path_consumed"]:
        raise RuntimeError("valid grid-only sizing path rejected")
    unrelated_layout = analyze(host.replace(
        "layout-postcondition window=1", "layout-postcondition window=9"),
        windowing, finder, True, True)
    if unrelated_layout["checks"]["stage_manager_windows_coexist"]:
        raise RuntimeError("unrelated window coexistence was incorrectly accepted")
    bad = analyze(host.replace("bounds=300.0x400.0", "bounds=1004.0x807.0"),
                  windowing, finder, True, True)
    if bad["checks"]["first_scene_geometry_exact"]:
        raise RuntimeError("stock first geometry was incorrectly accepted")
    unrelated_scene = host.replace(
        "scene-geometry id=S fbs=F bounds=300.0x400.0",
        "scene-geometry id=OTHER fbs=OTHER-F bounds=300.0x400.0")
    bad = analyze(unrelated_scene, windowing, finder, True, True)
    if bad["checks"]["first_scene_geometry_exact"]:
        raise RuntimeError("unrelated Scene geometry was incorrectly accepted")
    unrelated_drag = finder.replace("gesture=7", "gesture=8")
    bad = analyze(host, windowing, unrelated_drag, True, True)
    if (bad["checks"]["finder_process_local_drag_route"] or
            bad["checks"]["finder_core_drag_continuations"]):
        raise RuntimeError("unrelated drag lifecycle was incorrectly accepted")
    print("self-test: PASS")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--device")
    parser.add_argument("--user", default="root")
    parser.add_argument("--port", type=int, default=2222)
    parser.add_argument("--wait", type=float, default=0.0)
    parser.add_argument("--host-log", type=pathlib.Path)
    parser.add_argument("--windowing-log", type=pathlib.Path)
    parser.add_argument("--finder-log", type=pathlib.Path)
    parser.add_argument("--require-drag", action="store_true")
    parser.add_argument("--require-springback", action="store_true")
    parser.add_argument("--output", type=pathlib.Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        _self_test()
        return

    if args.device:
        remote = Remote(args.device, args.user, args.port)
        offsets = {
            HOST_LOG: remote.offset(HOST_LOG),
            WINDOWING_LOG: remote.offset(WINDOWING_LOG),
            FINDER_LOG: remote.offset(FINDER_LOG),
        }
        print(json.dumps({"state": "ARMED", "offsets": offsets}))
        deadline = time.monotonic() + max(0.0, args.wait)
        report = None
        while True:
            host_text = remote.appended(HOST_LOG, offsets[HOST_LOG])
            windowing_text = remote.appended(
                WINDOWING_LOG, offsets[WINDOWING_LOG])
            finder_text = remote.appended(FINDER_LOG, offsets[FINDER_LOG])
            report = analyze(host_text, windowing_text, finder_text,
                             args.require_drag, args.require_springback)
            # Observe the complete interval: a correct first frame can be
            # followed by a delayed resize of an existing sibling.
            if time.monotonic() >= deadline:
                break
            time.sleep(0.25)
    else:
        if not args.host_log or not args.windowing_log:
            parser.error("use --device or supply --host-log and --windowing-log")
        host_text = args.host_log.read_text(errors="replace")
        windowing_text = args.windowing_log.read_text(errors="replace")
        finder_text = (args.finder_log.read_text(errors="replace")
                       if args.finder_log else "")
        report = analyze(host_text, windowing_text, finder_text,
                         args.require_drag, args.require_springback)

    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    print(rendered, end="")
    raise SystemExit(3 if report["result"] ==
                     "LOG_CHECKS_PASS_VISUAL_UNVERIFIED" else 2)


if __name__ == "__main__":
    main()

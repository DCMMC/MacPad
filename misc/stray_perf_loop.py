"""Run one short Stray benchmark through the Steam UI.

This is intentionally separate from ``stray_first_run_pipeline.py``.  The
first-run pipeline diagnoses shader/library failures and can wait for several
long prompt phases.  This runner assumes those failures are already retired
and measures one repeatable production-profile interval:

* wait for an iPadOS ``nominal`` thermal state;
* stop a stale Stray process and apply/verify the requested INI profile;
* click Steam's real Play button so the Steam overlay contract is preserved;
* use ``STRAY-PRESENT`` only as a bounded render-liveness witness, while FPS
  is scored from unique direct drawables actually presented by MacWSHost;
* observe iPadOS thermal pressure throughout startup and measurement without
  changing the Stray or Steam process lifecycle; and
* always stop the game and remove the trace marker unless explicitly asked not
  to do so.

Run this script on the development Mac.  It uses only the standard library.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import os
import pathlib
import re
import select
import shlex
import signal
import statistics
import struct
import subprocess
import sys
import tempfile
import time


GAME_NAME = "Stray-Mac-Shipping"
GAME_CONFIG = (
    "/var/mnt/rootfs/Users/root/Library/Preferences/Stray/"
    "MacNoEditor/GameUserSettings.ini"
)
ENGINE_CONFIG = (
    "/var/mnt/rootfs/Users/root/Library/Preferences/Stray/"
    "MacNoEditor/Engine.ini"
)
RUNTIME_LOG = "/var/jb/var/mobile/steam-runtime.log"
HOST_LOG = "/var/mobile/Library/Logs/MacWSHost.log"
HOST_LAYER_DIRECTORY = "/var/mobile/Library/Logs/MacWSHost-layers"
HOST_PERFORMANCE_PROFILE = (
    "/var/mobile/Library/Logs/MacWSPerformance/latest.json"
)
STEAM_CONFIG = (
    "/var/mnt/rootfs/Users/root/Library/Application Support/Steam/"
    "config/config.vdf"
)
STEAM_LOCAL_CONFIG = (
    "/var/mnt/rootfs/Users/root/Library/Application Support/Steam/"
    "userdata/296809210/config/localconfig.vdf"
)
STEAM_LOG_DIRECTORY = (
    "/var/mnt/rootfs/Users/root/Library/Application Support/Steam/logs"
)
STEAM_LOGIN_LOG = STEAM_LOG_DIRECTORY + "/steamui_login.txt"
STEAM_WEBHELPER_JS_LOG = STEAM_LOG_DIRECTORY + "/webhelper_js.txt"
STEAM_WEBHELPER_LOG = STEAM_LOG_DIRECTORY + "/webhelper.txt"
PRESENT_MARKER = "/var/mnt/rootfs/private/tmp/macws_stray_present_trace"
RENDER_TRACE_MARKER = (
    "/var/mnt/rootfs/private/tmp/macws_stray_render_trace"
)
RENDER_TARGET_CAPTURE_MARKER = (
    "/var/mnt/rootfs/private/tmp/macws_stray_rt_capture_now"
)
PIPELINE_DIAGNOSTIC_MARKER = (
    "/var/mnt/rootfs/private/tmp/macws_pipeline_diag"
)
FORWARD_NIL_TEXTURE_MARKER = (
    "/var/mnt/rootfs/private/tmp/macws_stray_forward_nil_texture"
)
DRAWABLE_TIMING_MARKER = (
    "/var/mnt/rootfs/private/tmp/macws_stray_drawable_timing"
)
DISABLE_DISPLAY_SYNC_MARKER = (
    "/var/mnt/rootfs/private/tmp/macws_stray_disable_display_sync"
)
NO_OVERLAY_INJECTION_MARKER = (
    "/var/mnt/rootfs/private/tmp/macws_stray_no_overlay_injection"
)
APP_INPUT_DIAGNOSTICS_MARKER = (
    "/var/mnt/rootfs/private/tmp/macws_app_input_diagnostics"
)
WAIT_TRACE_INSTALL_MARKER = (
    "/var/mnt/rootfs/private/tmp/macws_stray_wait_trace_install"
)
WAIT_TRACE_CAPTURE_MARKER = (
    "/var/mnt/rootfs/private/tmp/macws_stray_wait_trace_capture"
)
MTL_DATA_DIAGNOSTIC_MARKER = (
    "/var/mnt/rootfs/private/tmp/macws_mtl_data_diag"
)
IOGPU_ERROR_DIAGNOSTIC_MARKER = (
    "/var/mnt/rootfs/private/tmp/macws_iogpu_error_diag"
)
COMMAND_ERROR_DIAGNOSTIC_MARKER = (
    "/var/mnt/rootfs/private/tmp/macws_command_error_diag"
)
SUBMIT_FAST_RING_MARKER = (
    "/var/mnt/rootfs/private/tmp/macws_submit_fast_ring"
)
QUEUE_QOS_DIAGNOSTIC_MARKER = (
    "/var/mnt/rootfs/private/tmp/macws_queue_qos_diag"
)
SUBMIT_TIMING_DIAGNOSTIC_MARKER = (
    "/var/mnt/rootfs/private/tmp/macws_submit_timing_diag"
)
SUBMIT_FORWARD_PROGRESS_BRIDGE_MARKER = (
    "/var/mnt/rootfs/private/tmp/macws_submit_forward_progress_bridge"
)
DIRECT_DRAWABLE_LEASE_MARKER = (
    "/var/mnt/rootfs/private/tmp/macws_catalyst_direct_drawable_lease"
)
DIRECT_DRAWABLE_ACTIVE_MARKER = (
    "/var/mnt/rootfs/private/tmp/macws_catalyst_direct_drawable_active"
)
DIAGNOSTIC_MARKERS = tuple(
    "/var/mnt/rootfs/private/tmp/" + name for name in (
        "macws_runtime_diagnostics",
        "macws_app_input_diagnostics",
        "macws_stray_render_trace",
        "macws_stray_drawable_timing",
        "macws_stray_disable_display_sync",
        "macws_stray_no_overlay_injection",
        "macws_stray_wait_trace_install",
        "macws_stray_wait_trace_capture",
        "macws_stray_rt_capture_now",
        "macws_stray_forward_nil_texture",
        "macws_submit_diag",
        "macws_submit_ring",
        "macws_submit_fast_ring",
        "macws_queue_qos_diag",
        "macws_submit_timing_diag",
        "macws_submit_forward_progress_bridge",
        "macws_iogpu_error_diag",
        "macws_command_error_diag",
        "macws_observe_pf550",
        "macws_probe_small_pf550",
        "macws_res_diag",
        "macws_trace_small_pf550_bind",
        "macws_video_diag",
        "macws_pipeline_diag",
        "macws_pipeline_ab_diag",
        "macws_tile_descriptor_diag",
        "macws_texture_stride_diag",
        "macws_mtl_library_diag",
        "macws_mtl_data_diag",
        "macws_catalyst_direct_drawable_lease",
        "macws_catalyst_direct_drawable_active",
    )
)
QUIET_DESKTOP_LABELS = {
    "lsd-session", "lsd-system", "Finder", "Dock", "WindowServer",
    "MacWSHost", "macwsdisplayd", "OSXvnc", "ControlCenter",
    "ReportCrash",
}
METRICS_PREFIX = "/var/mnt/rootfs/private/tmp/macws_window_metrics"
GESTURE_PROBE = (
    "/var/jb/var/mobile/MacWSBootingGuide/misc/host_gesture_probe.py"
)
KEY_PROBE = "/var/jb/var/mobile/MacWSBootingGuide/misc/host_key_probe.py"
PERF_LEVEL_PROBE = (
    "/var/jb/var/mobile/MacWSBootingGuide/misc/proc_perf_levels_probe"
)
LOCK_STATE_PROBE = (
    "/var/jb/var/mobile/MacWSBootingGuide/misc/ios_lock_state_probe"
)
IPCTOOL_LABEL = "com.valvesoftware.steam.ipctool"
IPCTOOL_PLIST = (
    "/var/jb/usr/macOS/gui-launchd/"
    "com.valvesoftware.steam.ipctool.plist"
)
STEAM_PLIST = (
    "/var/jb/usr/macOS/gui-launchd/"
    "com.macwsguide.steam.runtime.plist"
)
STEAM_JOB_LABEL = "UIKitApplication:com.macwsguide.steam"
STEAM_APPLAUNCH_MARKER = (
    "/var/mnt/rootfs/private/tmp/macws_steam_applaunch_once"
)
CONTROL_CENTER_PLIST = (
    "/var/jb/usr/macOS/gui-launchd/"
    "com.macwsguide.controlcenter.plist"
)
CONTROL_CENTER_COMMAND = (
    "/System/Library/CoreServices/ControlCenter.app/Contents/MacOS/"
    "ControlCenter"
)
STRAY_SAFETY_SCRIPT = (
    "/var/jb/var/mobile/MacWSBootingGuide/misc/stray_thermal_watchdog.sh"
)
STRAY_SAFETY_HEARTBEAT = (
    "/var/mobile/Library/Logs/macws-stray-safety.heartbeat"
)
STRAY_SAFETY_LOG = "/var/mobile/Library/Logs/macws-stray-safety.log"
HOST_HOVER_SCENARIO_SECONDS = 10.0
STEAM_OVERLAY_TEST_ENV = (
    "STEAM_OVERLAY_DISABLE_WAIT_FOR_CEF_FRAME",
    "MACWS_STRAY_OVERLAY_DISABLE_CEF_WAIT_DIAGNOSTIC",
    "MACWS_STRAY_OVERLAY_EVENT_WAIT_DIAGNOSTIC_SELECTOR",
    "MACWS_STRAY_OVERLAY_EVENT_WAIT_DIAGNOSTIC",
    "MACWS_STEAM_SEM_TIMING_DIAGNOSTICS",
    "STEAM_OVERLAY_FRAME_TIME_LOGGING",
    "STEAM_OVERLAY_LOGGING_FLUSH",
    "SteamNoOverlayUIDrawing",
    "MACWS_STRAY_COMPLETION_FPS_DIAGNOSTIC",
    "MACWS_STRAY_RHI_THREAD_SELECTOR",
    "MACWS_STRAY_CRASH_TRACE_SELECTOR",
    "MACWS_STEAM_PROCESS_DIAGNOSTICS",
)
VISION_OCR = pathlib.Path(__file__).resolve().parent / "vision_ocr.swift"
VISION_OCR_BINARY = (
    pathlib.Path(tempfile.gettempdir()) / "macws-stray-tools" / "vision_ocr"
)
STRAY_RT_ANALYZER = (
    pathlib.Path(__file__).resolve().parent /
    "analyze_stray_render_targets.py"
)

THERMAL_RE = re.compile(
    r"thermal-state=(?P<state>[a-z]+).*?"
    r"effective-temp-centic=(?P<temperature>\d+)"
)
PRESENT_RE = re.compile(
    r"STRAY-PRESENT sequence=(?P<sequence>\d+)\s+"
    r"totalSeconds=(?P<total>[0-9.]+).*?"
    r"averageFPS=(?P<average>[0-9.]+).*?"
    r"windowFPS=(?P<window>[0-9.]+).*?"
    r"texture=(?P<width>\d+)x(?P<height>\d+)(?:/pf(?P<format>\d+))?"
)
METALFX_ENCODE_RE = re.compile(
    r"METALFX-ENCODE #(?P<sequence>\d+) scaler=(?P<scaler>0x[0-9a-f]+) "
    r"class=(?P<class>\S+).*?input=(?P<input_width>\d+)x"
    r"(?P<input_height>\d+).*?output=(?P<output_width>\d+)x"
    r"(?P<output_height>\d+)"
)
HOST_FULLSCREEN_WINDOW_RE = re.compile(
    r"window-auto-scene activated-fullscreen-catalog "
    r"pid=(?P<pid>\d+) window=(?P<window>\d+) "
    r"group=(?P<group>\d+) score=(?P<score>\d+)"
)
DRAWABLE_TIMING_RE = re.compile(
    r"STRAY-DRAWABLE-TIMING calls=(?P<count>\d+) "
    r"averageMS=(?P<average>[0-9.]+) maxMS=(?P<maximum>[0-9.]+) "
    r"currentMS=(?P<current>[0-9.]+) slow8=(?P<slow8>\d+) "
    r"slow16=(?P<slow16>\d+)"
)
DRAWABLE_PRESENTED_RE = re.compile(
    r"STRAY-DRAWABLE-PRESENTED count=(?P<count>\d+) "
    r"averageMS=(?P<average>[0-9.]+) maxMS=(?P<maximum>[0-9.]+) "
    r"currentMS=(?P<current>[0-9.]+)"
)
WAIT_TRACE_BEGIN_RE = re.compile(
    r"STRAY-WAIT sequence=(?P<sequence>\d+).*?frames=(?P<frames>\d+) begin"
)
WAIT_TRACE_END_RE = re.compile(r"STRAY-WAIT sequence=(?P<sequence>\d+) end")
SUBMIT_FLAGS_RE = re.compile(
    r"STRAY-SUBMIT-FLAGS sequence=(?P<sequence>\d+) "
    r"flags=(?P<flags>(?:0x)?[0-9a-fA-F]+) "
    r"explicitWait=(?P<explicit_wait>YES|NO) "
    r"runtimeDebugLevel=(?P<debug_level>\d+) "
    r"caller=(?P<caller>0x[0-9a-fA-F]+)"
)
SURFACE_LOCK_BEGIN_RE = re.compile(
    r"STRAY-SURFACE-LOCK sequence=(?P<sequence>\d+).*?frames=(?P<frames>\d+) begin"
)
SURFACE_LOCK_END_RE = re.compile(
    r"STRAY-SURFACE-LOCK sequence=(?P<sequence>\d+).*? end"
)
UE_FATAL_MARKERS = (
    "Shader compilation failures are Fatal.",
    "LowLevelFatalError",
    "Spinning after fatal error..",
)

HIGH_PROFILE = {
    "SteamDeckScreenPercentage": "100",
    "SteamDeckTextureQuality": "2",
    "ScreenPercentage": "100",
    "bUseDynamicResolution": "False",
    "FullscreenMode": "0",
    "LastConfirmedFullscreenMode": "0",
    "PreferredFullscreenMode": "0",
    "sg.EffectsQuality": "2",
    "sg.AntiAliasingQuality": "2",
    "sg.TextureQuality": "2",
    "sg.ShadowQuality": "2",
    "sg.ResolutionQuality": "100.000000",
    "sg.ViewDistanceQuality": "2",
    "sg.PostProcessQuality": "2",
    "sg.FoliageQuality": "2",
    "sg.ShadingQuality": "2",
}

MEDIUM_PROFILE = {
    **HIGH_PROFILE,
    "SteamDeckTextureQuality": "1",
    "sg.EffectsQuality": "1",
    "sg.AntiAliasingQuality": "1",
    "sg.TextureQuality": "1",
    "sg.ShadowQuality": "1",
    "sg.ViewDistanceQuality": "1",
    "sg.PostProcessQuality": "1",
    "sg.FoliageQuality": "1",
    "sg.ShadingQuality": "1",
}

QUALITY_PROFILES = {
    "high": HIGH_PROFILE,
    "medium": MEDIUM_PROFILE,
}


class Remote:
    def __init__(self, host: str, user: str, port: int, password: str):
        self.host = host
        self.user = user
        self.port = port
        self.password = password
        self.stray_safety_armed = False
        self.runtime_log_stream = None
        self.runtime_log_offset = None
        self.host_log_stream = None
        self.host_log_offset = None
        self.input_agent = None
        self.signal_agent = None
        self.sample_agent = None
        self.control_path = f"/tmp/macws-stray-ssh-{os.getpid()}-{port}"
        self.base = [
            "ssh", "-p", str(port), "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=8", "-o", "ControlMaster=auto",
            "-o", "ControlPersist=30", "-o",
            f"ControlPath={self.control_path}", f"{user}@{host}",
        ]

    def run(self, command: str, *, check: bool = True, binary: bool = False,
            timeout: float = 30):
        result = subprocess.run(
            self.base + [command], capture_output=True,
            text=not binary, timeout=timeout,
        )
        if check and result.returncode:
            stderr = (result.stderr.decode(errors="replace") if binary
                      else result.stderr)
            raise RuntimeError(
                f"remote command failed rc={result.returncode}: {stderr}"
            )
        return result.stdout

    def sudo(self, command: str, *, check: bool = True,
             timeout: float = 30):
        # Keep the complete operation inside one privileged shell.  Prefixing
        # an arbitrary command with ``sudo`` only elevates the first command
        # in ``A && B`` / ``A || B``; the rest silently runs in the SSH user's
        # launchd domain.  That made ipctool recovery report attempts without
        # actually submitting the launchd job in the intended context.
        privileged = (
            f"/var/jb/usr/bin/bash -c {shlex.quote(command)}"
        )
        prefix = f"printf '%s\\n' {shlex.quote(self.password)} | sudo -S "
        return self.run(prefix + privileged, check=check, timeout=timeout)

    def copy_from(self, remote_path: str, local_path: pathlib.Path):
        subprocess.run([
            "scp", "-q", "-P", str(self.port), "-o", "ControlMaster=auto",
            "-o", "ControlPersist=30", "-o",
            f"ControlPath={self.control_path}",
            f"{self.user}@{self.host}:{remote_path}", str(local_path),
        ], check=True)

    def close(self):
        try:
            subprocess.run(
                self.base[:-1] + ["-O", "exit", self.base[-1]],
                capture_output=True, text=True, timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            pass


class RemoteSignalAgent:
    """Pre-arm one same-UID process so peak signals require no new fork.

    The agent also resolves a PID with ``proc_pidpath`` before signaling the
    game.  That preserves the executable-identity guard when iPadOS is too
    memory-constrained to fork ``ps`` or a privileged cleanup shell.
    """

    SCRIPT = r'''
import ctypes, os, signal, sys
stopped = set()
libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
libproc.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
libproc.proc_pidpath.restype = ctypes.c_int
libproc.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int,
                                 ctypes.c_uint64, ctypes.c_void_p,
                                 ctypes.c_int]
libproc.proc_pidinfo.restype = ctypes.c_int

# PROC_PIDTBSDINFO is a read-only liveness witness in addition to the path.
# Runtime on 2026-08-23 showed proc_pidpath still returning Stray's executable
# after Valve's fatal-stalled-pipe exit while the suspended Steam parent had
# not reaped the child.  proc_pidinfo returns zero/ESRCH for that zombie, so
# classify it as absent immediately instead of burning the full gameplay
# timeout while no frames can possibly arrive.
PROC_PIDTBSDINFO = 3

def process_basename(pid):
    bsd = ctypes.create_string_buffer(256)
    if libproc.proc_pidinfo(pid, PROC_PIDTBSDINFO, 0,
                            bsd, len(bsd)) <= 0:
        return None
    path = ctypes.create_string_buffer(4096)
    length = libproc.proc_pidpath(pid, path, len(path))
    if length <= 0:
        return None
    return os.path.basename(os.fsdecode(path.value))

print("MACWS_SIGNAL_AGENT_READY", flush=True)
try:
    for raw in sys.stdin:
        fields = raw.strip().split()
        if not fields:
            continue
        if fields[0] == "QUIT":
            print("OK QUIT", flush=True)
            break
        if len(fields) == 3 and fields[0] == "STATUS_EXACT":
            try:
                pid = int(fields[1])
            except ValueError:
                print("ERROR integer", flush=True)
                continue
            observed = process_basename(pid)
            if observed is None:
                state = "missing"
            elif observed == fields[2]:
                state = "match"
            else:
                state = "mismatch"
            print("OK STATUS_EXACT " + str(pid) + " " + state, flush=True)
            continue
        if len(fields) == 4 and fields[0] == "SIGNAL_EXACT":
            try:
                signum = int(fields[1])
                pid = int(fields[2])
            except ValueError:
                print("ERROR integer", flush=True)
                continue
            observed = process_basename(pid)
            if observed is None:
                state = "missing"
            elif observed != fields[3]:
                state = "mismatch"
            else:
                try:
                    os.kill(pid, signum)
                    state = "signaled"
                except ProcessLookupError:
                    state = "missing"
                except PermissionError:
                    state = "denied"
            print("OK SIGNAL_EXACT " + str(signum) + " " + str(pid) +
                  " " + state, flush=True)
            continue
        if len(fields) < 3 or fields[0] != "SIGNAL":
            print("ERROR protocol", flush=True)
            continue
        try:
            signum = int(fields[1])
            pids = [int(value) for value in fields[2:]
                    if int(value) > 1]
        except ValueError:
            print("ERROR integer", flush=True)
            continue
        succeeded = []
        for pid in pids:
            try:
                os.kill(pid, signum)
                succeeded.append(pid)
                if signum == signal.SIGSTOP:
                    stopped.add(pid)
                elif signum == signal.SIGCONT:
                    stopped.discard(pid)
            except ProcessLookupError:
                pass
            except PermissionError:
                pass
        print("OK SIGNAL " + str(signum) + " " +
              ",".join(str(pid) for pid in succeeded), flush=True)
finally:
    for pid in stopped:
        try:
            os.kill(pid, signal.SIGCONT)
        except OSError:
            pass
'''

    def __init__(self, remote: Remote):
        # Steam and every captured helper run as mobile/uid 501, so no
        # privilege transition is required.  The former ``printf password |
        # sudo -S python3`` pipeline made that password pipe Python's stdin;
        # the agent printed READY and then immediately exited on EOF, before
        # its first SIGNAL request.
        command = (
            f"/var/jb/usr/bin/python3 -u -c {shlex.quote(self.SCRIPT)}"
        )
        # Do not multiplex this lifetime-critical channel through the
        # runner's ordinary ControlMaster.  A peak-time command rejection can
        # reset that shared master and used to take the already-armed signal
        # process down with it before it could retire Steam's CEF helpers.
        dedicated_base = [
            "ssh", "-p", str(remote.port), "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=8", "-o", "ControlMaster=no",
            "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=3",
            f"{remote.user}@{remote.host}",
        ]
        self.process = subprocess.Popen(
            dedicated_base + [command], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            bufsize=1,
        )
        try:
            ready = self._readline(12.0)
        except Exception:
            self.close()
            raise
        if ready != "MACWS_SIGNAL_AGENT_READY":
            self.close()
            raise RuntimeError(
                f"remote signal agent did not arm: {ready!r}"
            )

    def _readline(self, timeout: float):
        if not self.process.stdout:
            raise RuntimeError("remote signal agent stdout is unavailable")
        readable, _, _ = select.select(
            [self.process.stdout], [], [], timeout
        )
        if not readable:
            raise RuntimeError("remote signal agent response timed out")
        return self.process.stdout.readline().strip()

    def signal(self, signum: int, pids: set[int]):
        if not pids:
            return []
        if not self.process.stdin or self.process.poll() is not None:
            raise RuntimeError("remote signal agent is not running")
        request = "SIGNAL " + str(int(signum)) + " " + " ".join(
            str(pid) for pid in sorted(pids)
        )
        self.process.stdin.write(request + "\n")
        self.process.stdin.flush()
        response = self._readline(5.0)
        success = f"OK SIGNAL {int(signum)}"
        if response == success:
            return []
        prefix = success + " "
        if not response.startswith(prefix):
            raise RuntimeError(
                f"remote signal agent rejected {request!r}: {response!r}"
            )
        payload = response[len(prefix):]
        return [
            int(value) for value in payload.split(",") if value.isdigit()
        ]

    def exact_status(self, pid: int, executable_basename: str):
        if (not executable_basename or
                any(character.isspace() for character in executable_basename)):
            raise ValueError("exact executable basename must be one token")
        if not self.process.stdin or self.process.poll() is not None:
            raise RuntimeError("remote signal agent is not running")
        request = (
            f"STATUS_EXACT {int(pid)} {executable_basename}"
        )
        self.process.stdin.write(request + "\n")
        self.process.stdin.flush()
        response = self._readline(5.0)
        prefix = f"OK STATUS_EXACT {int(pid)} "
        if not response.startswith(prefix):
            raise RuntimeError(
                f"remote signal agent rejected {request!r}: {response!r}"
            )
        state = response[len(prefix):]
        if state not in {"match", "missing", "mismatch"}:
            raise RuntimeError(
                f"remote signal agent returned invalid state: {response!r}"
            )
        return state

    def signal_exact(self, signum: int, pid: int,
                     executable_basename: str):
        if (not executable_basename or
                any(character.isspace() for character in executable_basename)):
            raise ValueError("exact executable basename must be one token")
        if not self.process.stdin or self.process.poll() is not None:
            raise RuntimeError("remote signal agent is not running")
        request = (
            f"SIGNAL_EXACT {int(signum)} {int(pid)} {executable_basename}"
        )
        self.process.stdin.write(request + "\n")
        self.process.stdin.flush()
        response = self._readline(5.0)
        prefix = f"OK SIGNAL_EXACT {int(signum)} {int(pid)} "
        if not response.startswith(prefix):
            raise RuntimeError(
                f"remote signal agent rejected {request!r}: {response!r}"
            )
        state = response[len(prefix):]
        if state not in {"signaled", "missing", "mismatch", "denied"}:
            raise RuntimeError(
                f"remote signal agent returned invalid state: {response!r}"
            )
        return state

    def close(self):
        process = getattr(self, "process", None)
        if not process or process.poll() is not None:
            return
        try:
            if process.stdin:
                process.stdin.write("QUIT\n")
                process.stdin.flush()
            process.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)


class RemoteSampleAgent:
    """Pre-arm macOS ``sample`` before Stray's allocation peak.

    Starting a fresh SSH -> sudo -> chroot -> sample chain after the game has
    allocated its Metal working set is not reliable on the iPad.  The runtime
    witness was ``zsh: fork failed: resource temporarily unavailable`` before
    the diagnostic tool could inspect a still-live Stray process.  This agent
    enters the chroot while only Steam is resident, waits for one exact PID,
    then replaces its waiting shell with ``sample`` via ``exec``.  No new
    process is created at the failure point.
    """

    def __init__(self, remote: Remote):
        token = f"{os.getpid()}-{time.monotonic_ns()}"
        self.remote = remote
        self.chroot_directory = (
            f"/private/tmp/macws_stray_sample_prearmed.{token}"
        )
        self.chroot_path = self.chroot_directory + "/sample.txt"
        self.ios_path = "/var/mnt/rootfs" + self.chroot_path
        self.ios_directory = "/var/mnt/rootfs" + self.chroot_directory
        # The waiting sample command runs as root, while the already-armed
        # transfer agent is mobile.  Owning the private directory lets that
        # agent remove the root-owned result afterward without another sudo
        # process at the game peak.
        remote.run(
            f"mkdir -m 700 {shlex.quote(self.ios_directory)}"
        )
        script = (
            "export PATH=/usr/bin:/bin:/usr/sbin:/sbin; "
            "echo MACWS_SAMPLE_AGENT_READY; "
            "IFS=' ' read -r sample_pid sample_duration; "
            "if [ \"$sample_pid\" = QUIT ]; then exit 0; fi; "
            "case \"$sample_pid:$sample_duration\" in "
            "*[!0-9:]*|:*|*:) exit 64 ;; esac; "
            f"exec /usr/bin/sample \"$sample_pid\" \"$sample_duration\" "
            f"1 -file {shlex.quote(self.chroot_path)}"
        )
        command = (
            "sudo -S -p '' /var/jb/usr/bin/bash "
            "/var/jb/usr/macOS/bin/run_bash.sh -c " + shlex.quote(script)
        )
        dedicated_base = [
            "ssh", "-p", str(remote.port), "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=8", "-o", "ControlMaster=no",
            "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=3",
            f"{remote.user}@{remote.host}",
        ]
        self.process = subprocess.Popen(
            dedicated_base + [command], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            bufsize=1,
        )
        self.used = False
        try:
            if not self.process.stdin:
                raise RuntimeError("remote sample agent stdin unavailable")
            self.process.stdin.write(remote.password + "\n")
            self.process.stdin.flush()
            ready = self._readline(20.0)
        except Exception:
            self.close()
            raise
        if ready != "MACWS_SAMPLE_AGENT_READY":
            self.close()
            raise RuntimeError(
                f"remote sample agent did not arm: {ready!r}"
            )

    def _readline(self, timeout: float):
        if not self.process.stdout:
            raise RuntimeError("remote sample agent stdout unavailable")
        readable, _, _ = select.select([self.process.stdout], [], [], timeout)
        if not readable:
            raise RuntimeError("remote sample agent response timed out")
        return self.process.stdout.readline().strip()

    def capture(self, remote: Remote, pid: int, seconds: float,
                destination: pathlib.Path):
        if self.used:
            raise RuntimeError("prearmed process sample was already consumed")
        if not self.process.stdin or self.process.poll() is not None:
            raise RuntimeError("remote sample agent is not running")
        duration = max(1, round(seconds))
        self.used = True
        self.process.stdin.write(f"{int(pid)} {duration}\n")
        self.process.stdin.flush()
        self.process.stdin.close()
        self.process.stdin = None
        try:
            stdout, stderr = self.process.communicate(
                timeout=duration + 30
            )
        except subprocess.TimeoutExpired:
            self.process.kill()
            stdout, stderr = self.process.communicate(timeout=5)
            raise RuntimeError("prearmed macOS process sample timed out")
        if self.process.returncode != 0:
            raise RuntimeError(
                "prearmed macOS sample failed rc=" +
                str(self.process.returncode) + ": " + stderr.strip()
            )
        input_agent = getattr(remote, "input_agent", None)
        if input_agent is None:
            raise RuntimeError("prearmed sample requires input transfer agent")
        try:
            transport = input_agent.copy_file(
                self.ios_path, destination, timeout=duration + 20
            )
        finally:
            input_agent.remove_file(self.ios_path)
        if destination.stat().st_size < 100:
            raise RuntimeError("macOS process sample is unexpectedly short")
        return {
            "path": str(destination),
            "duration_seconds": duration,
            "size": destination.stat().st_size,
            "tool_output": stdout.strip(),
            "launch": "prearmed-before-game",
            "process_creation_at_capture": "none; waiting shell exec",
            "copy_transport": transport,
        }

    def close(self):
        process = getattr(self, "process", None)
        if not process or process.poll() is not None:
            return
        try:
            if process.stdin:
                process.stdin.write("QUIT 0\n")
                process.stdin.flush()
            process.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        input_agent = getattr(self.remote, "input_agent", None)
        if input_agent is not None:
            try:
                input_agent.remove_file(self.ios_path)
            except Exception:
                pass


class RemoteInputAgent:
    """Pre-arm the Host input transport before Stray's allocation peak."""

    SCRIPT = r'''
import ctypes, json, os, signal, socket, sys, time
sys.path.insert(0, "/var/jb/var/mobile/MacWSBootingGuide/misc")
from host_input_matrix import (
    ACTIVATE_TARGET, HOVER, KEY_DOWN, KEY_UP, MOD_CAPS_LOCK, MOD_COMMAND,
    MOD_CONTROL, MOD_SHIFT, SOURCE_FINGER, SOURCE_HARDWARE_KEYBOARD, TAP,
    TOUCH_DOWN, TOUCH_UP, record, resolve_window,
)
from host_key_probe import KEY_CODES, SPECIAL_SYMBOLS

SOURCE_INDIRECT_POINTER = 3
destination = "/var/mnt/rootfs/private/tmp/macws_host_input.sock"
local = "/tmp/macws_host_input_agent.%d.sock" % os.getpid()
gui_transaction_lock = "/tmp/.macos_gui.transaction"
gui_transaction_pid = os.path.join(gui_transaction_lock, "pid")
diagnostic_marker_directory = "/var/mnt/rootfs/private/tmp"
gui_lease_held = False
stopped_processes = set()

libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
libproc.proc_pidpath.argtypes = [
    ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32
]
libproc.proc_pidpath.restype = ctypes.c_int
libproc.proc_pidinfo.argtypes = [
    ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int
]
libproc.proc_pidinfo.restype = ctypes.c_int
PROC_PIDTBSDINFO = 3

def process_basename(pid):
    bsd = ctypes.create_string_buffer(256)
    if libproc.proc_pidinfo(pid, PROC_PIDTBSDINFO, 0,
                            bsd, len(bsd)) <= 0:
        return None
    path = ctypes.create_string_buffer(4096)
    length = libproc.proc_pidpath(pid, path, len(path))
    if length <= 0:
        return None
    return os.path.basename(os.fsdecode(path.value))

def send_signal(signum, pid):
    if signum not in {
            signal.SIGSTOP, signal.SIGCONT, signal.SIGTERM, signal.SIGKILL}:
        raise ValueError("unsupported signal")
    os.kill(pid, signum)
    if signum == signal.SIGSTOP:
        stopped_processes.add(pid)
    elif signum == signal.SIGCONT:
        stopped_processes.discard(pid)

def gui_owner_alive(owner):
    try:
        os.kill(owner, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # A mobile-owned observer cannot signal a root-owned macos_gui.sh,
        # but EPERM still proves that the exact PID exists.
        return True

def acquire_gui_lease():
    global gui_lease_held
    if gui_lease_held:
        return {"state": "already-held", "owner": os.getpid()}
    try:
        os.mkdir(gui_transaction_lock, 0o755)
    except FileExistsError:
        owner = 0
        try:
            with open(gui_transaction_pid, "r", encoding="ascii") as source:
                owner = int(source.readline().strip())
        except (OSError, ValueError):
            pass
        state = "active" if owner > 1 and gui_owner_alive(owner) else "stale"
        raise RuntimeError(
            "GUI transaction busy state=%s owner=%d" % (state, owner))
    try:
        descriptor = os.open(
            gui_transaction_pid,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o644,
        )
        with os.fdopen(descriptor, "w", encoding="ascii") as sink:
            sink.write(str(os.getpid()) + "\n")
    except Exception:
        try:
            os.rmdir(gui_transaction_lock)
        except OSError:
            pass
        raise
    gui_lease_held = True
    return {"state": "acquired", "owner": os.getpid()}

def release_gui_lease():
    global gui_lease_held
    if not gui_lease_held:
        return {"state": "not-held", "owner": os.getpid()}
    with open(gui_transaction_pid, "r", encoding="ascii") as source:
        owner = int(source.readline().strip())
    if owner != os.getpid():
        raise RuntimeError(
            "GUI transaction ownership changed expected=%d actual=%d" %
            (os.getpid(), owner))
    os.unlink(gui_transaction_pid)
    os.rmdir(gui_transaction_lock)
    gui_lease_held = False
    return {"state": "released", "owner": owner}

def update_diagnostic_markers(paths, present):
    changed = []
    for value in paths:
        path = os.path.normpath(str(value))
        if (os.path.dirname(path) != diagnostic_marker_directory or
                not os.path.basename(path).startswith("macws_")):
            raise ValueError("unsafe diagnostic marker path: " + path)
        if present:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT, 0o644)
            os.close(descriptor)
        else:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
        changed.append(path)
    return {
        "state": "present" if present else "absent",
        "paths": changed,
    }

# Preload the same LaunchServices route used by uiopen while the device is
# below Stray's allocation peak.  Later screenshot requests therefore need
# neither a new SSH session nor a remote fork/exec.
objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
ctypes.CDLL("/System/Library/Frameworks/Foundation.framework/Foundation")
ctypes.CDLL(
    "/System/Library/Frameworks/MobileCoreServices.framework/"
    "MobileCoreServices"
)
objc.objc_getClass.restype = ctypes.c_void_p
objc.objc_getClass.argtypes = [ctypes.c_char_p]
objc.sel_registerName.restype = ctypes.c_void_p
objc.sel_registerName.argtypes = [ctypes.c_char_p]
msg0 = ctypes.cast(
    objc.objc_msgSend,
    ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p),
)
msg1 = ctypes.cast(
    objc.objc_msgSend,
    ctypes.CFUNCTYPE(
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
    ),
)
msg2_bool = ctypes.cast(
    objc.objc_msgSend,
    ctypes.CFUNCTYPE(
        ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p
    ),
)

def objc_class(name):
    return objc.objc_getClass(name.encode())

def objc_sel(name):
    return objc.sel_registerName(name.encode())

workspace = msg0(objc_class("LSApplicationWorkspace"),
                 objc_sel("defaultWorkspace"))
empty_options = msg0(objc_class("NSDictionary"), objc_sel("dictionary"))

def open_url(value):
    encoded = value.encode()
    ns_string = msg1(
        objc_class("NSString"), objc_sel("stringWithUTF8String:"),
        ctypes.cast(ctypes.c_char_p(encoded), ctypes.c_void_p),
    )
    url = msg1(objc_class("NSURL"), objc_sel("URLWithString:"), ns_string)
    return bool(msg2_bool(
        workspace, objc_sel("openSensitiveURL:withOptions:"),
        url, empty_options,
    ))

try:
    os.unlink(local)
except FileNotFoundError:
    pass
sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
sock.bind(local)
# The device broker consumes a datagram at a time and normally returns to
# recvfrom immediately.  Do not let a saturated or replaced destination
# socket wedge this persistent test agent forever: a bounded send failure is
# reported to the host by the per-request exception handler, which leaves the
# control channel alive for marker cleanup and GUI-lease release.
sock.settimeout(1.0)
sequence = 0

def send_record(kind, request, **values):
    global sequence
    sequence += 1
    sock.sendto(record(
        kind, sequence, request["pid"], request["window"],
        request["width"], request["height"],
        values.get("x", request["width"] / 2),
        values.get("y", request["height"] / 2),
        pressure=values.get("pressure", 0),
        contact=values.get("contact", 0),
        source=values.get("source", SOURCE_FINGER),
        modifiers=values.get("modifiers", 0)), destination)

print("MACWS_INPUT_AGENT_READY", flush=True)
try:
    for raw in sys.stdin:
        try:
            request = json.loads(raw)
            if request.get("op") == "quit":
                print("OK QUIT", flush=True)
                break
            if request.get("op") == "open_url":
                opened = open_url(str(request["url"]))
                print("OK " + json.dumps({
                    "op": "open_url", "opened": opened,
                }, separators=(",", ":")), flush=True)
                continue
            if request.get("op") == "get_file":
                path = str(request["path"])
                with open(path, "rb", buffering=0) as source:
                    size = os.fstat(source.fileno()).st_size
                    print("DATA " + str(size), flush=True)
                    while True:
                        chunk = source.read(65536)
                        if not chunk:
                            break
                        sys.stdout.buffer.write(chunk)
                    sys.stdout.buffer.flush()
                continue
            if request.get("op") == "remove_file":
                path = str(request["path"])
                prefix = ("/var/mnt/rootfs/private/tmp/"
                          "macws_stray_sample_prearmed.")
                if (not path.startswith(prefix) or
                        os.path.basename(path) != "sample.txt"):
                    raise ValueError("unsafe sample cleanup path")
                try:
                    os.unlink(path)
                    removed = True
                except FileNotFoundError:
                    removed = False
                try:
                    os.rmdir(os.path.dirname(path))
                except FileNotFoundError:
                    pass
                print("OK " + json.dumps({
                    "op": "remove_file", "removed": removed,
                }, separators=(",", ":")), flush=True)
                continue
            if request.get("op") == "acquire_gui_lease":
                print("OK " + json.dumps(
                    acquire_gui_lease(), separators=(",", ":")), flush=True)
                continue
            if request.get("op") == "release_gui_lease":
                print("OK " + json.dumps(
                    release_gui_lease(), separators=(",", ":")), flush=True)
                continue
            if request.get("op") == "diagnostic_markers":
                response = update_diagnostic_markers(
                    request.get("paths", []), bool(request.get("present")))
                print("OK " + json.dumps(
                    response, separators=(",", ":")), flush=True)
                continue
            if request.get("op") == "status_exact":
                pid = int(request["pid"])
                expected = str(request["basename"])
                observed = process_basename(pid)
                state = ("missing" if observed is None else
                         "match" if observed == expected else "mismatch")
                print("OK " + json.dumps({
                    "op": "status_exact", "pid": pid, "state": state,
                }, separators=(",", ":")), flush=True)
                continue
            if request.get("op") == "signal_exact":
                pid = int(request["pid"])
                signum = int(request["signum"])
                expected = str(request["basename"])
                observed = process_basename(pid)
                if observed is None:
                    state = "missing"
                elif observed != expected:
                    state = "mismatch"
                else:
                    try:
                        send_signal(signum, pid)
                        state = "signaled"
                    except ProcessLookupError:
                        state = "missing"
                    except PermissionError:
                        state = "denied"
                print("OK " + json.dumps({
                    "op": "signal_exact", "pid": pid,
                    "signum": signum, "state": state,
                }, separators=(",", ":")), flush=True)
                continue
            if request.get("op") == "signal":
                signum = int(request["signum"])
                succeeded = []
                for pid in sorted({
                        int(value) for value in request.get("pids", [])
                        if int(value) > 1}):
                    try:
                        send_signal(signum, pid)
                        succeeded.append(pid)
                    except (ProcessLookupError, PermissionError):
                        pass
                print("OK " + json.dumps({
                    "op": "signal", "signum": signum,
                    "pids": succeeded,
                }, separators=(",", ":")), flush=True)
                continue
            for name in ("pid", "window", "width", "height"):
                request[name] = int(request[name])
            if (request["pid"] <= 1 or request["width"] <= 0 or
                    request["height"] <= 0):
                raise ValueError("invalid target geometry")
            start_sequence = sequence
            if request.get("op") == "key":
                key = str(request["key"])
                name = key.lower()
                code = KEY_CODES[name]
                symbol = SPECIAL_SYMBOLS.get(
                    name, ord(key) if len(key) == 1 else 0)
                modifiers = (MOD_COMMAND if request.get("command") else 0) | \
                    (MOD_CONTROL if request.get("control") else 0) | \
                    (MOD_SHIFT if request.get("shift") else 0) | \
                    (MOD_CAPS_LOCK if request.get("caps_lock") else 0)
                request["window"] = resolve_window(
                    request["pid"], request["window"])
                send_record(
                    KEY_DOWN, request, pressure=code, contact=symbol,
                    source=SOURCE_HARDWARE_KEYBOARD, modifiers=modifiers)
                hold = max(0.0, float(request.get("hold", 0.0)))
                if hold:
                    time.sleep(hold)
                send_record(
                    KEY_UP, request, pressure=code, contact=symbol,
                    source=SOURCE_HARDWARE_KEYBOARD, modifiers=modifiers)
            elif request.get("op") == "gesture":
                gesture = str(request["gesture"])
                x = float(request.get("x", request["width"] / 2))
                y = float(request.get("y", request["height"] / 2))
                if request.get("activate_first") or gesture == "activate":
                    send_record(ACTIVATE_TARGET, request)
                    delay = max(0.0, float(
                        request.get("activation_delay", 0.25)))
                    if delay:
                        time.sleep(delay)
                if gesture == "hover":
                    send_record(
                        HOVER, request, x=x, y=y, contact=0x47535452,
                        source=SOURCE_INDIRECT_POINTER)
                elif gesture == "tap":
                    hold = max(0.0, float(request.get("hold", 0.0)))
                    if hold:
                        send_record(TOUCH_DOWN, request, x=x, y=y,
                                    pressure=1.0)
                        time.sleep(hold)
                        send_record(TOUCH_UP, request, x=x, y=y)
                    else:
                        send_record(TAP, request, x=x, y=y, pressure=1.0)
                elif gesture != "activate":
                    raise ValueError("unsupported gesture")
            else:
                raise ValueError("unsupported operation")
            response = {
                "records": sequence - start_sequence,
                "pid": request["pid"], "window": request["window"],
                "op": request["op"],
            }
            print("OK " + json.dumps(response, separators=(",", ":")),
                  flush=True)
        except Exception as error:
            print("ERROR " + type(error).__name__ + ":" + str(error),
                  flush=True)
finally:
    for pid in stopped_processes:
        try:
            os.kill(pid, signal.SIGCONT)
        except OSError:
            pass
    if gui_lease_held:
        try:
            release_gui_lease()
        except Exception:
            pass
    sock.close()
    try:
        os.unlink(local)
    except FileNotFoundError:
        pass
'''

    def __init__(self, remote: Remote):
        dedicated_base = [
            "ssh", "-p", str(remote.port), "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=8", "-o", "ControlMaster=no",
            "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=3",
            f"{remote.user}@{remote.host}",
        ]
        command = (
            f"/var/jb/usr/bin/python3 -u -c {shlex.quote(self.SCRIPT)}"
        )
        self.process = subprocess.Popen(
            dedicated_base + [command], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
        )
        try:
            ready = self._readline(12.0)
        except Exception:
            self.close()
            raise
        if ready != "MACWS_INPUT_AGENT_READY":
            self.close()
            raise RuntimeError(f"remote input agent did not arm: {ready!r}")

    def _readline(self, timeout: float):
        if not self.process.stdout:
            raise RuntimeError("remote input agent stdout is unavailable")
        readable, _, _ = select.select([self.process.stdout], [], [], timeout)
        if not readable:
            raise RuntimeError("remote input agent response timed out")
        return self.process.stdout.readline().decode(
            errors="replace"
        ).strip()

    def _request(self, request: dict, timeout: float = 5.0):
        if not self.process.stdin or self.process.poll() is not None:
            raise RuntimeError("remote input agent is not running")
        self.process.stdin.write(
            (json.dumps(request, separators=(",", ":")) + "\n").encode()
        )
        self.process.stdin.flush()
        response = self._readline(timeout)
        if not response.startswith("OK "):
            raise RuntimeError(
                "remote input agent rejected request: " + response
            )
        return json.loads(response[3:])

    def open_url(self, url: str):
        result = self._request({"op": "open_url", "url": url}, timeout=8.0)
        if not result.get("opened"):
            raise RuntimeError("prearmed LaunchServices agent rejected " + url)
        return "prearmed-LaunchServices-agent"

    def copy_file(self, remote_path: str, destination: pathlib.Path,
                  timeout: float = 20.0):
        if not self.process.stdin or self.process.poll() is not None:
            raise RuntimeError("remote input/transfer agent is not running")
        request = {"op": "get_file", "path": remote_path}
        self.process.stdin.write(
            (json.dumps(request, separators=(",", ":")) + "\n").encode()
        )
        self.process.stdin.flush()
        header = self._readline(timeout)
        if not header.startswith("DATA "):
            raise RuntimeError(
                "remote input/transfer agent rejected file request: " +
                header
            )
        try:
            remaining = int(header[5:])
        except ValueError as error:
            raise RuntimeError("invalid remote file header: " + header) from error
        if remaining < 0 or remaining > 64 * 1024 * 1024:
            raise RuntimeError("unsafe remote file size: " + str(remaining))
        deadline = time.monotonic() + timeout
        with destination.open("wb") as sink:
            while remaining:
                if not self.process.stdout:
                    raise RuntimeError("remote transfer stdout is unavailable")
                readable, _, _ = select.select(
                    [self.process.stdout], [], [],
                    max(0.0, deadline - time.monotonic()),
                )
                if not readable:
                    raise RuntimeError("remote file transfer timed out")
                chunk = self.process.stdout.read(min(65536, remaining))
                if not chunk:
                    raise RuntimeError("remote file transfer ended early")
                sink.write(chunk)
                remaining -= len(chunk)
        return "prearmed-input-agent-file-stream"

    def remove_file(self, remote_path: str):
        return self._request({
            "op": "remove_file", "path": remote_path,
        }, timeout=5.0)

    def acquire_gui_lease(self):
        return self._request({"op": "acquire_gui_lease"}, timeout=5.0)

    def release_gui_lease(self):
        return self._request({"op": "release_gui_lease"}, timeout=5.0)

    def set_diagnostic_markers(self, paths, present: bool):
        return self._request({
            "op": "diagnostic_markers",
            "paths": list(paths),
            "present": bool(present),
        }, timeout=5.0)

    def signal(self, signum: int, pids: set[int]):
        if not pids:
            return []
        result = self._request({
            "op": "signal", "signum": int(signum),
            "pids": sorted(int(pid) for pid in pids if int(pid) > 1),
        }, timeout=5.0)
        return [int(pid) for pid in result.get("pids", [])]

    def exact_status(self, pid: int, executable_basename: str):
        if (not executable_basename or
                any(character.isspace() for character in executable_basename)):
            raise ValueError("exact executable basename must be one token")
        result = self._request({
            "op": "status_exact", "pid": int(pid),
            "basename": executable_basename,
        }, timeout=5.0)
        state = result.get("state")
        if state not in {"match", "missing", "mismatch"}:
            raise RuntimeError("remote input agent returned invalid state: " +
                               repr(result))
        return state

    def signal_exact(self, signum: int, pid: int,
                     executable_basename: str):
        if (not executable_basename or
                any(character.isspace() for character in executable_basename)):
            raise ValueError("exact executable basename must be one token")
        result = self._request({
            "op": "signal_exact", "signum": int(signum), "pid": int(pid),
            "basename": executable_basename,
        }, timeout=5.0)
        state = result.get("state")
        if state not in {"signaled", "missing", "mismatch", "denied"}:
            raise RuntimeError("remote input agent returned invalid state: " +
                               repr(result))
        return state

    def key(self, pid: int, window: int, width: int, height: int, key: str,
            *, hold: float = 0.0, command: bool = False,
            control: bool = False, shift: bool = False,
            caps_lock: bool = False):
        result = self._request({
            "op": "key", "pid": pid, "window": window,
            "width": width, "height": height, "key": key,
            "hold": hold, "command": command, "control": control,
            "shift": shift, "caps_lock": caps_lock,
        }, timeout=max(15.0, hold + 5.0))
        return (
            f"keys=1 records={result['records']} pid={result['pid']} "
            f"window={result['window']} transport=prearmed-agent"
        )

    def gesture(self, gesture: str, pid: int, window: int, width: int,
                height: int, x: float | None = None,
                y: float | None = None, *, activate_first: bool = False,
                hold: float = 0.0):
        request = {
            "op": "gesture", "gesture": gesture, "pid": pid,
            "window": window, "width": width, "height": height,
            "activate_first": activate_first, "hold": hold,
        }
        if x is not None:
            request["x"] = x
        if y is not None:
            request["y"] = y
        result = self._request(request, timeout=max(5.0, hold + 3.0))
        return (
            f"gesture={gesture} records={result['records']} "
            f"route-pid={result['pid']} route-window={result['window']} "
            "transport=prearmed-agent"
        )

    def close(self):
        process = getattr(self, "process", None)
        if not process or process.poll() is not None:
            return
        try:
            if process.stdin:
                process.stdin.write(b'{"op":"quit"}\n')
                process.stdin.flush()
            process.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)


class RemoteThermalStream:
    """Read iPadOS thermal telemetry without peak-time remote forks."""

    def __init__(self, remote: Remote, interval_seconds: float):
        interval_ms = max(250, min(60000, round(interval_seconds * 1000)))
        dedicated_base = [
            "ssh", "-p", str(remote.port), "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=8", "-o", "ControlMaster=no",
            "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=3",
            f"{remote.user}@{remote.host}",
        ]
        command = (
            "/var/jb/usr/macOS/bin/macwsthermal stream " + str(interval_ms)
        )
        self.process = subprocess.Popen(
            dedicated_base + [command], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            bufsize=1,
        )
        self.latest = None
        try:
            self.snapshot(12.0)
        except Exception:
            self.close()
            raise

    def snapshot(self, timeout: float = 5.0):
        if not self.process.stdout:
            raise RuntimeError("remote thermal stream stdout is unavailable")
        deadline = time.monotonic() + timeout
        newest = None
        lines_read = 0
        while True:
            # Runtime-confirmed via /tmp/macws-runner-hang.sample.txt for
            # host PID 33850: after Stray cleanup the main thread remained in
            # select_select_impl/_textiowrapper_readline at 100% CPU.  A
            # continuously readable pipe made the old "drain until empty"
            # contract ignore its nominal deadline forever.  Bound both wall
            # time and work while retaining the newest complete sample.
            remaining = max(0.0, deadline - time.monotonic())
            if remaining == 0.0 or lines_read >= 256:
                break
            readable, _, _ = select.select(
                [self.process.stdout], [], [], remaining
            )
            if not readable:
                break
            raw = self.process.stdout.readline().strip()
            lines_read += 1
            match = THERMAL_RE.search(raw)
            if match:
                newest = {
                    "time": time.time(),
                    "state": match.group("state"),
                    "temperature_c": int(match.group("temperature")) / 100.0,
                    "raw": raw,
                }
            # Drain already-buffered samples, but do not wait for the next
            # interval after a valid fresh line has arrived.
            if newest:
                more, _, _ = select.select(
                    [self.process.stdout], [], [], 0
                )
                if not more:
                    break
        if newest:
            self.latest = newest
            return dict(newest)
        if self.process.poll() is not None:
            stderr = ""
            if self.process.stderr:
                stderr = self.process.stderr.read().strip()
            raise RuntimeError(
                "remote thermal stream exited rc=" +
                str(self.process.returncode) + ": " + stderr
            )
        if self.latest:
            return dict(self.latest)
        raise RuntimeError("remote thermal stream produced no valid sample")

    def close(self):
        process = getattr(self, "process", None)
        if not process or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)


class RemoteGPUPowerStream:
    """Pre-arm chroot powermetrics and retain sample-window GPU evidence.

    Starting a new privileged chroot process at Stray's allocation peak can
    fail with ENOMEM.  Keep one read-only powermetrics stream alive from
    before launch, then mark byte offsets around the scored interval.  This
    observer neither requests a performance state nor controls the game.
    """

    MAX_BYTES = 8 * 1024 * 1024
    SAMPLE_RE = re.compile(
        r"(?ms)^\*\*\* Sampled system activity "
        r"(?P<header>[^\n]+?) \*\*\*[ \t]*$"
        r".*?^Current pressure level:[ \t]*(?P<pressure>[^\n]+?)[ \t]*$"
        r".*?^GPU active frequency:[ \t]*(?P<frequency>[0-9.]+) MHz[ \t]*$"
        r".*?^GPU active residency:[ \t]*(?P<residency>[0-9.]+)%[^\n]*$"
        r".*?^GPU requested frequency:[ \t]*(?P<requested>\([^\n]+\))[ \t]*$"
        r".*?^GPU Power:[ \t]*(?P<power>[0-9.]+) mW[ \t]*$"
    )

    def __init__(self, remote: Remote, interval_seconds: float = 1.0):
        interval_ms = max(250, min(60000, round(interval_seconds * 1000)))
        dedicated_base = [
            "ssh", "-tt", "-p", str(remote.port), "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=8", "-o", "ControlMaster=no",
            "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=3",
            f"{remote.user}@{remote.host}",
        ]
        powermetrics = (
            "exec /usr/bin/powermetrics --samplers gpu_power,thermal "
            f"-n 86400 -i {interval_ms}"
        )
        chroot = (
            "exec bash /var/jb/usr/macOS/bin/run_bash.sh -c " +
            shlex.quote(powermetrics)
        )
        privileged = (
            "/var/jb/usr/bin/bash -c " + shlex.quote(chroot)
        )
        command = (
            "stty -echo; sudo -S " + privileged +
            "; macws_gpu_stream_rc=$?; stty echo; "
            "exit $macws_gpu_stream_rc"
        )
        self.process = subprocess.Popen(
            dedicated_base + [command], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.buffer = bytearray()
        self.buffer_start = 0
        if not self.process.stdin:
            self.close()
            raise RuntimeError("remote GPU power stream has no stdin")
        if not self.process.stdout:
            self.close()
            raise RuntimeError("remote GPU power stream has no stdout")
        # Require one complete sample up front.  This proves powermetrics and
        # the chroot are usable before an expensive game launch.
        deadline = time.monotonic() + 12.0
        credential_sent = False
        while time.monotonic() < deadline:
            self._drain(min(1.0, deadline - time.monotonic()))
            if (not credential_sent and
                    b"password for" in self.buffer.lower()):
                # Wait for the remote PTY to disable echo and emit sudo's
                # prompt before sending the credential.  Writing immediately
                # after ssh spawn races terminal setup and echoes the line.
                self.process.stdin.write((remote.password + "\n").encode())
                self.process.stdin.flush()
                credential_sent = True
            if self._parse(bytes(self.buffer).decode(errors="replace")):
                return
            if self.process.poll() is not None:
                break
        stderr = b""
        if self.process.stderr:
            readable, _, _ = select.select([self.process.stderr], [], [], 0)
            if readable:
                stderr = os.read(self.process.stderr.fileno(), 65536)
        self.close()
        raise RuntimeError(
            "remote GPU power stream produced no complete sample: " +
            stderr.decode(errors="replace").strip()
        )

    def _drain(self, timeout: float):
        if not self.process.stdout:
            return 0
        deadline = time.monotonic() + max(0.0, timeout)
        drained = 0
        while True:
            remaining = max(0.0, deadline - time.monotonic())
            readable, _, _ = select.select(
                [self.process.stdout], [], [], remaining
            )
            if not readable:
                break
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                break
            self.buffer.extend(chunk)
            drained += len(chunk)
            if len(self.buffer) > self.MAX_BYTES:
                discard = len(self.buffer) - self.MAX_BYTES
                del self.buffer[:discard]
                self.buffer_start += discard
            if drained >= 1024 * 1024:
                break
            more, _, _ = select.select([self.process.stdout], [], [], 0)
            if not more:
                break
        return drained

    def _drain_backlog(self, initial_timeout: float):
        """Drain every byte already produced before taking an offset mark.

        A four-minute Stray load can leave more than the per-call 1 MiB
        fairness limit queued in the SSH pipe.  A single ``_drain`` then
        places the mark in the middle of old powermetrics output and falsely
        attributes pre-game/cooldown GPU samples to the scored interval.
        Keep the per-call bound, but repeat non-blocking drains until the
        absolute stream end stops advancing.  The 32 MiB aggregate bound is
        well above the class's retained 8 MiB window and turns an abnormal
        producer backlog into an explicit observer failure.
        """
        absolute_start = self.buffer_start + len(self.buffer)
        deadline = time.monotonic() + 2.0
        self._drain(initial_timeout)
        while time.monotonic() < deadline:
            before = self.buffer_start + len(self.buffer)
            # Require a short quiet period.  A zero-time select can observe a
            # scheduling gap between two writes from the still-draining SSH
            # PTY and place the mark a few chunks too early.
            self._drain(0.02)
            after = self.buffer_start + len(self.buffer)
            if after == before:
                return after
            if after - absolute_start > 32 * 1024 * 1024:
                raise RuntimeError(
                    "GPU power stream backlog exceeded 32 MiB"
                )
        raise RuntimeError("GPU power stream backlog did not quiesce")

    @classmethod
    def _parse(cls, text: str):
        # ssh -tt intentionally gives powermetrics a terminal so it flushes
        # every interval.  OpenSSH's PTY path expands the device's CRLF into
        # CRCRLF; normalize only line endings before matching the unchanged
        # metric text.
        text = text.replace("\r", "")
        samples = []
        for match in cls.SAMPLE_RE.finditer(text):
            samples.append({
                "header": match.group("header").strip(),
                "thermal_pressure": match.group("pressure").strip(),
                "active_frequency_mhz": float(match.group("frequency")),
                "active_residency_percent": float(
                    match.group("residency")
                ),
                "requested_frequency_histogram":
                    match.group("requested").strip(),
                "gpu_power_mw": float(match.group("power")),
            })
        return samples

    def mark(self):
        return self._drain_backlog(0.05)

    def samples_since(self, offset: int, timeout: float = 1.2):
        self._drain_backlog(timeout)
        start = int(offset) - self.buffer_start
        if start < 0:
            raise RuntimeError("GPU power stream mark fell behind buffer")
        if start > len(self.buffer):
            raise RuntimeError("GPU power stream mark is ahead of buffer")
        text = bytes(self.buffer[start:]).decode(errors="replace")
        return self._parse(text)

    def close(self):
        process = getattr(self, "process", None)
        if not process or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)


class RemoteRuntimeLogStream:
    """Tail Steam's runtime log from a process armed before game launch.

    A new remote ``tail`` can fail with ENOMEM at Stray's IOSurface allocation
    peak even while the game continues presenting.  The already-running tail
    preserves byte offsets and makes subsequent polling host-local.
    """

    MAX_BYTES = 32 * 1024 * 1024

    def __init__(self, remote: Remote, path: str, offset: int):
        self.path = path
        self.buffer_start = int(offset)
        self.buffer = bytearray()
        dedicated_base = [
            "ssh", "-p", str(remote.port), "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=8", "-o", "ControlMaster=no",
            "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=3",
            f"{remote.user}@{remote.host}",
        ]
        command = (
            "exec /var/jb/usr/bin/tail -c +" + str(int(offset) + 1) +
            " -f " + shlex.quote(path)
        )
        self.process = subprocess.Popen(
            dedicated_base + [command], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if not self.process.stdout:
            self.close()
            raise RuntimeError("remote runtime-log stream has no stdout")

    def _drain(self, timeout: float):
        if not self.process.stdout:
            raise RuntimeError("remote runtime-log stream stdout is closed")
        deadline = time.monotonic() + max(0.0, timeout)
        drained = 0
        while True:
            remaining = max(0.0, deadline - time.monotonic())
            if remaining == 0.0 or drained >= 4 * 1024 * 1024:
                break
            readable, _, _ = select.select(
                [self.process.stdout], [], [], remaining
            )
            if not readable:
                break
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                break
            self.buffer.extend(chunk)
            drained += len(chunk)
            if len(self.buffer) > self.MAX_BYTES:
                discard = len(self.buffer) - self.MAX_BYTES
                del self.buffer[:discard]
                self.buffer_start += discard
            more, _, _ = select.select([self.process.stdout], [], [], 0)
            if not more:
                break
        if self.process.poll() is not None:
            stderr = b""
            if self.process.stderr:
                stderr = self.process.stderr.read()
            raise RuntimeError(
                "remote runtime-log stream exited rc=" +
                str(self.process.returncode) + ": " +
                stderr.decode(errors="replace").strip()
            )

    def snapshot_from(self, offset: int, timeout: float = 0.05):
        self._drain(timeout)
        requested = int(offset)
        if requested < self.buffer_start:
            raise RuntimeError(
                f"runtime-log offset {requested} fell behind retained "
                f"stream start {self.buffer_start}"
            )
        start = requested - self.buffer_start
        if start > len(self.buffer):
            # A future offset can occur when an external size read races the
            # follower's filesystem-poll interval.  Wait once, then require
            # byte-contiguous evidence rather than silently returning empty.
            self._drain(max(timeout, 1.1))
            start = requested - self.buffer_start
        if start > len(self.buffer):
            raise RuntimeError(
                f"runtime-log stream has {len(self.buffer)} bytes after "
                f"{self.buffer_start}, cannot serve offset {requested}"
            )
        return bytes(self.buffer[start:]).decode(errors="replace")

    def close(self):
        process = getattr(self, "process", None)
        if not process or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)


class ProcessNotFound(RuntimeError):
    pass


def process_table(remote: Remote, columns: str):
    """Read one process snapshot and distinguish empty from failed.

    Under system pressure iPadOS's shell can stay alive while its attempt to
    fork ``ps`` returns ENOMEM.  An empty stdout is therefore not evidence of
    an empty process set.  The shell-built-in status marker makes that failure
    explicit without adding an awk/grep child on the device.
    """
    marker = "__MACWS_PS_STATUS__"
    witnesses = []
    for attempt in range(5):
        output = remote.run(
            f"ps -axo {columns}; macws_ps_rc=$?; "
            f"echo {marker}:$macws_ps_rc",
            check=False,
        ).splitlines()
        status_index = next(
            (index for index in range(len(output) - 1, -1, -1)
             if output[index].startswith(marker + ":")),
            None,
        )
        if (status_index is not None and
                output[status_index] == marker + ":0"):
            return output[:status_index]
        witnesses.append(
            output[status_index] if status_index is not None else "missing"
        )
        if attempt != 4:
            # Runtime-confirmed on the Stray launch peak: XNU can reject one
            # transient ``ps`` allocation while the already-running game and
            # SSH control socket remain healthy.  Retry the observation; an
            # absent snapshot is not evidence that the game exited.
            time.sleep(0.2 * (attempt + 1))
    raise RuntimeError(
        f"process table unavailable columns={columns!r} "
        f"status={','.join(witnesses)}"
    )


def parse_metrics(payload: bytes):
    if len(payload) < 24:
        return []
    magic, version, header_size, entry_size, count, generation = \
        struct.unpack_from("<IHHIIQ", payload)
    # The v3 producer appends window maxima and configure acknowledgements
    # after the unchanged v2 20-byte prefix. Rejecting the new version made
    # a visibly loaded Steam window look absent and exhausted both UI-start
    # attempts before the benchmark ever reached Stray (device 2026-09-21).
    known_shape = (version == 2 and entry_size == 20) or \
        (version == 3 and entry_size == 56)
    if (magic != 0x4D57474D or not known_shape or header_size != 24 or
            count < 1 or generation == 0 or
            len(payload) != header_size + count * entry_size):
        return []
    result = []
    for index in range(count):
        offset = header_size + index * entry_size
        window, flags, group, width, height = struct.unpack_from(
            "<IIIff", payload, offset
        )
        result.append({
            "window": window,
            "flags": flags,
            "logical_group": group,
            "width": width,
            "height": height,
        })
    return result


def thermal_snapshot(remote: Remote):
    stream = getattr(remote, "thermal_stream", None)
    if stream is not None:
        try:
            return stream.snapshot()
        except Exception as error:
            # Once a run is in its allocation peak, falling back to a fresh
            # ssh -> zsh -> sudo -> bash fork recreates the ENOMEM failure the
            # stream exists to prevent.  Preserve an explicit unknown sample;
            # runtime policy is observation-only and never controls Stray.
            return {
                "time": time.time(),
                "state": "unknown",
                "temperature_c": None,
                "raw": "thermal-stream-error: " + str(error),
            }
    heartbeat = (
        f"touch {shlex.quote(STRAY_SAFETY_HEARTBEAT)}; "
        if remote.stray_safety_armed else ""
    )
    raw = ""
    match = None
    for _ in range(3):
        raw = remote.sudo(
            heartbeat + "/var/jb/usr/macOS/bin/macwsthermal",
            check=False, timeout=10,
        ).strip()
        match = THERMAL_RE.search(raw)
        if match:
            break
        time.sleep(0.10)
    return {
        "time": time.time(),
        "state": match.group("state") if match else "unknown",
        "temperature_c": (
            int(match.group("temperature")) / 100.0 if match else None
        ),
        "raw": raw,
    }


def power_snapshot(remote: Remote):
    """Read the physical battery/adapter state before admitting a run."""
    raw = remote.sudo(
        "/usr/sbin/ioreg -r -d 1 -c AppleSmartBattery -w 0",
        check=False, timeout=10,
    )

    def yes_no(name: str):
        match = re.search(rf'"{re.escape(name)}"\s*=\s*(Yes|No)', raw)
        return None if not match else match.group(1) == "Yes"

    def integer(name: str):
        match = re.search(rf'"{re.escape(name)}"\s*=\s*(-?[0-9]+)', raw)
        return None if not match else int(match.group(1))

    adapter = re.search(r'"AdapterDetails"\s*=\s*\{([^\n]*)\}', raw)
    watts = (re.search(r'"Watts"=([0-9]+)', adapter.group(1))
             if adapter else None)
    return {
        "time": time.time(),
        "is_charging": yes_no("IsCharging"),
        "external_connected": yes_no("ExternalConnected"),
        "adapter_watts": int(watts.group(1)) if watts else None,
        "amperage_ma": integer("Amperage"),
        "temperature_c": (
            integer("Temperature") / 100.0
            if integer("Temperature") is not None else None
        ),
        "source": "AppleSmartBattery ioreg",
    }


def gpu_snapshot(remote: Remote):
    """Capture the driver's compact utilization/memory counters.

    Keep this outside the repeating thermal loop: a full IORegistry walk on
    every cadence sample would itself perturb the workload we are measuring.
    One snapshot immediately before and after the scored interval is enough
    to distinguish an AGX-saturated scene from CPU/transport-only pressure.
    """
    raw = remote.sudo(
        "/usr/sbin/ioreg -r -c AGXAccelerator -l", check=False, timeout=15,
    )

    def integer(name: str):
        match = re.search(
            rf'"{re.escape(name)}"\s*=\s*(-?[0-9]+)', raw
        )
        return None if not match else int(match.group(1))

    return {
        "time": time.time(),
        "tiler_utilization_percent": integer("Tiler Utilization %"),
        "renderer_utilization_percent": integer("Renderer Utilization %"),
        "device_utilization_percent": integer("Device Utilization %"),
        "allocated_pb_bytes": integer("Allocated PB Size"),
        "in_use_system_memory_bytes": integer("In use system memory"),
        "allocated_system_memory_bytes": integer("Alloc system memory"),
        "source": "AGXAccelerator PerformanceStatistics ioreg",
    }


def gpu_power_summary(samples):
    if not samples:
        return {"count": 0}
    frequencies = [sample["active_frequency_mhz"] for sample in samples]
    residencies = [sample["active_residency_percent"] for sample in samples]
    powers = [sample["gpu_power_mw"] for sample in samples]
    return {
        "count": len(samples),
        "minimum_active_frequency_mhz": min(frequencies),
        "mean_active_frequency_mhz": statistics.fmean(frequencies),
        "maximum_active_frequency_mhz": max(frequencies),
        "minimum_active_residency_percent": min(residencies),
        "mean_active_residency_percent": statistics.fmean(residencies),
        "maximum_active_residency_percent": max(residencies),
        "minimum_gpu_power_mw": min(powers),
        "mean_gpu_power_mw": statistics.fmean(powers),
        "maximum_gpu_power_mw": max(powers),
        "source": "prearmed chroot powermetrics --samplers gpu_power,thermal",
    }


def arm_stray_safety(remote: Remote, heartbeat_timeout: int = 90):
    """Remove legacy watchdog state; thermal telemetry is observation-only."""
    output = remote.sudo(
        f"bash {shlex.quote(STRAY_SAFETY_SCRIPT)} arm "
        f"{int(heartbeat_timeout)}",
        timeout=20,
    ).strip()
    remote.stray_safety_armed = False
    return {
        "armed": False,
        "heartbeat_timeout_seconds": heartbeat_timeout,
        "thermal_state_policy": "observe-only",
        "numeric_temperature_enforced": False,
        "witness": output,
    }


def disarm_stray_safety(remote: Remote):
    log = remote.run(
        f"tail -80 {shlex.quote(STRAY_SAFETY_LOG)} 2>/dev/null || true",
        check=False,
    )
    output = remote.sudo(
        f"bash {shlex.quote(STRAY_SAFETY_SCRIPT)} disarm",
        check=False,
    ).strip()
    remote.stray_safety_armed = False
    return {"armed": False, "output": output, "log": log}


def thermally_safe(sample: dict, _legacy_ceiling_c: float | None = None):
    # Temperature remains useful telemetry, but the iPadOS thermal state is
    # the authoritative admission/abort contract.  A charging device can have
    # a higher absolute sensor value without pressure or frequency limiting.
    return sample["state"] == "nominal"


class ThermalPreflightError(RuntimeError):
    def __init__(self, message: str, history: list[dict]):
        super().__init__(message)
        self.history = list(history)


class ThermalPreflightTimeout(ThermalPreflightError):
    pass


class ThermalPreflightInterrupted(ThermalPreflightError):
    pass


def wait_for_cool_device(remote: Remote, legacy_ceiling_c: float,
                         timeout: float,
                         poll_interval: float, stable_samples: int,
                         admission_states: tuple[str, ...] = ("nominal",),
                         progress_label: str = "thermal-preflight"):
    started = time.monotonic()
    deadline = time.monotonic() + timeout
    history = []
    consecutive_safe = 0
    last_report = float("-inf")
    last_state = None
    while True:
        sample = thermal_snapshot(remote)
        history.append(sample)
        elapsed = time.monotonic() - started
        if (sample["state"] != last_state or
                elapsed - last_report >= 30.0):
            print(
                f"[stray-perf] stage={progress_label} "
                f"elapsed={elapsed:.1f}s state={sample['state']} "
                f"stable={consecutive_safe}/{stable_samples}",
                flush=True,
            )
            last_report = elapsed
            last_state = sample["state"]
        if sample["state"] in admission_states:
            consecutive_safe += 1
            if consecutive_safe >= stable_samples:
                print(
                    f"[stray-perf] stage={progress_label} admitted "
                    f"elapsed={elapsed:.1f}s state={sample['state']} "
                    f"stable={consecutive_safe}/{stable_samples}",
                    flush=True,
                )
                return sample, history
        else:
            consecutive_safe = 0
        if time.monotonic() >= deadline:
            raise ThermalPreflightTimeout(
                "thermal preflight timed out: " + sample["raw"], history
            )
        try:
            time.sleep(min(poll_interval,
                           max(0.1, deadline - time.monotonic())))
        except KeyboardInterrupt:
            raise ThermalPreflightInterrupted(
                "thermal preflight interrupted: " + sample["raw"], history
            ) from None


def command_uses_executable(command: str, executable: str):
    """Match a known executable at argv[0] without splitting a spaced path.

    Darwin's ``ps command=`` renders argv as one unquoted string.  Stray's
    executable path contains ``Application Support``, so whitespace splitting
    cannot recover argv[0].  Looking after the final slash is also incorrect:
    diagnostic arguments such as ``-abslog=/tmp/MacWS-Stray.log`` contain a
    later slash.  The executable's own basename is unique and is followed by
    either end-of-line or the first rendered argument delimiter.
    """
    marker = "/" + executable
    index = command.find(marker)
    if index < 0:
        return False
    end = index + len(marker)
    executable_token = command[:end]
    return (
        executable_token.endswith("/Contents/MacOS/" + executable) and
        (end == len(command) or command[end].isspace())
    )


def game_pid(remote: Remote):
    candidates = []
    for line in process_table(remote, "pid=,uid=,state=,command="):
        fields = line.strip().split(None, 3)
        if (len(fields) != 4 or not fields[0].isdigit() or
                fields[1] != "501" or fields[2].startswith("Z")):
            continue
        if command_uses_executable(fields[3], GAME_NAME):
            candidates.append(int(fields[0]))
    return candidates[-1] if candidates else 0


def validate_exact_game_pid(remote: Remote, pid: int):
    """Return True/False only after checking one PID's UID and executable."""
    marker = "__MACWS_GAME_PID_STATUS__"
    output = remote.run(
        f"ps -p {int(pid)} -o uid=,state=,command=; macws_ps_rc=$?; "
        f"echo {marker}:$macws_ps_rc",
        check=False,
    ).splitlines()
    status = next(
        (line for line in reversed(output) if line.startswith(marker + ":")),
        None,
    )
    if status != marker + ":0":
        live = root_pid_liveness(remote, pid)
        if live is False:
            return False
        raise RuntimeError(
            f"cannot validate live Stray candidate pid={pid}: "
            f"ps-status={status or 'missing'}"
        )
    rows = [line for line in output if not line.startswith(marker + ":")]
    if not rows:
        live = root_pid_liveness(remote, pid)
        if live is False:
            return False
        raise RuntimeError(
            f"live pid={pid} was omitted from its direct process query"
        )
    fields = rows[-1].strip().split(None, 2)
    if len(fields) >= 2 and fields[1].startswith("Z"):
        return False
    executable_matches = len(fields) == 3 and command_uses_executable(
        fields[2], GAME_NAME
    )
    if len(fields) != 3 or fields[0] != "501" or not executable_matches:
        raise RuntimeError(
            f"refusing unexpected pid={pid} identity: {rows[-1].strip()}"
        )
    return True


def terminate_exact_game(remote: Remote, pid: int = 0,
                         grace_seconds: float = 2.0):
    """Terminate only a currently path-validated Stray process.

    ``killall`` made the automation capable of affecting a late user launch
    that was not owned by the current run.  Resolve the UID-501 executable
    identity first, require it to match any captured PID, and repeat that
    validation before escalating from TERM to KILL.
    """
    if pid > 1:
        if not validate_exact_game_pid(remote, pid):
            return {"pid": pid, "signal": None, "confirmed_exited": True}
        target = pid
    else:
        target = game_pid(remote)
        if target <= 1:
            return {"pid": 0, "signal": None, "confirmed_exited": True}
        if not validate_exact_game_pid(remote, target):
            return {"pid": target, "signal": None,
                    "confirmed_exited": True}
    remote.sudo(f"kill -TERM {target} 2>/dev/null || true", check=False)
    deadline = time.monotonic() + max(0.0, grace_seconds)
    live = root_pid_liveness(remote, target)
    while live is not False and time.monotonic() < deadline:
        time.sleep(min(0.25, max(0.01, deadline - time.monotonic())))
        live = root_pid_liveness(remote, target)
    signal = "TERM"
    if live is not False:
        if not validate_exact_game_pid(remote, target):
            return {"pid": target, "signal": signal,
                    "confirmed_exited": True, "final_liveness": False}
        remote.sudo(f"kill -KILL {target} 2>/dev/null || true", check=False)
        signal = "KILL"
        time.sleep(0.25)
        live = root_pid_liveness(remote, target)
    return {
        "pid": target,
        "signal": signal,
        "confirmed_exited": live is False,
        "final_liveness": live,
    }


def terminate_exact_game_with_agent(agent: RemoteSignalAgent, pid: int,
                                    grace_seconds: float = 2.0):
    """Stop one path-validated game without allocating a device process.

    ``proc_pidpath`` runs inside the signal agent that was created before
    Steam launched the game.  A missing PID is already clean; a reused PID
    with any other executable is an explicit refusal, never a signal target.
    """
    if pid <= 1:
        return {"pid": 0, "signal": None, "confirmed_exited": True,
                "transport": "prearmed-libproc-agent"}
    initial = agent.exact_status(pid, GAME_NAME)
    if initial == "missing":
        return {"pid": pid, "signal": None, "confirmed_exited": True,
                "initial_identity": initial,
                "transport": "prearmed-libproc-agent"}
    if initial != "match":
        raise RuntimeError(
            f"refusing reused pid={pid}: libproc executable mismatch"
        )
    term = agent.signal_exact(int(signal.SIGTERM), pid, GAME_NAME)
    if term not in {"signaled", "missing"}:
        raise RuntimeError(
            f"exact TERM for pid={pid} was not admitted: {term}"
        )
    deadline = time.monotonic() + max(0.0, grace_seconds)
    final = agent.exact_status(pid, GAME_NAME)
    while final == "match" and time.monotonic() < deadline:
        time.sleep(0.10)
        final = agent.exact_status(pid, GAME_NAME)
    signal_name = "TERM"
    if final == "match":
        killed = agent.signal_exact(int(signal.SIGKILL), pid, GAME_NAME)
        if killed not in {"signaled", "missing"}:
            raise RuntimeError(
                f"exact KILL for pid={pid} was not admitted: {killed}"
            )
        signal_name = "KILL"
        time.sleep(0.25)
        final = agent.exact_status(pid, GAME_NAME)
    if final == "mismatch":
        raise RuntimeError(
            f"pid={pid} identity changed during exact cleanup"
        )
    return {
        "pid": pid,
        "signal": signal_name,
        "confirmed_exited": final == "missing",
        "initial_identity": initial,
        "final_identity": final,
        "transport": "prearmed-libproc-agent",
    }


def root_pid_liveness(remote: Remote, pid: int):
    """Return game liveness without allocating a peak-time device process.

    Once the prearmed libproc agent exists, use its already-running SSH
    channel.  Steam's cold-start allocation peak runtime-triggered a timeout
    in the former ``sudo ps`` probe even though Stray had a real window and
    was presenting.  The fallback remains for pre-launch cleanup and modes
    that intentionally do not arm the agent.
    """
    signal_agent = getattr(remote, "signal_agent", None)
    if signal_agent is not None and pid > 1:
        state = signal_agent.exact_status(pid, GAME_NAME)
        if state == "match":
            return True
        if state == "missing":
            return False
        return None
    output = remote.sudo(
        f"macws_state=$(/bin/ps -p {int(pid)} -o state= 2>/dev/null || true); "
        "case \"$macws_state\" in "
        "Z*) echo dead-zombie ;; "
        f"*) if kill -0 {int(pid)} 2>/dev/null; then echo live; "
        "else echo dead; fi ;; esac",
        check=False, timeout=10,
    ).strip().splitlines()
    if output and output[-1] == "live":
        return True
    if output and output[-1] in {"dead", "dead-zombie"}:
        return False
    return None


def mtl_compiler_service_pids(remote: Remote):
    """Return exact Metal compiler workers so one run can own its cleanup."""
    result = set()
    for line in process_table(remote, "pid=,command="):
        fields = line.strip().split(None, 1)
        if len(fields) != 2 or not fields[0].isdigit():
            continue
        if fields[1].rstrip().endswith("/MTLCompilerService"):
            process = int(fields[0])
            if process > 1:
                result.add(process)
    return result


def wait_for_game_pid(remote: Remote, timeout: float):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pid = game_pid(remote)
        if pid > 1:
            return pid
        time.sleep(0.25)
    raise RuntimeError("Steam Play did not create a new Stray process")


def largest_window(remote: Remote, pid: int, timeout: float,
                   minimum_area: float = 100000):
    path = f"{METRICS_PREFIX}.{pid}.bin"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = remote.run(
            f"dd if={shlex.quote(path)} bs=1048576 2>/dev/null",
            check=False, binary=True,
        )
        windows = parse_metrics(payload)
        # Runtime-confirmed via macws_window_metrics.73418.bin on 2026-08-22:
        # Stray's real fullscreen input owner was window 161 with flags=64
        # (Focused), while the splash/menu transition could leave a different,
        # higher-numbered window briefly Visible.  Host independently selected
        # 161 as its score-7 foreground layer and treated 168 as score-3.  An
        # AppKit key window is therefore the authoritative input target even
        # when ``isVisible`` is false during the native-fullscreen transition.
        # Filter on the required visual postcondition before applying focus
        # preference.  Runtime-confirmed on Stray PID 40082: UE's diagnostic
        # ``-log`` window was Focused but advertised only 0x28, while the real
        # fullscreen FCocoaWindow in the same exact metrics snapshot was
        # 1280x894.  Selecting the focused set first made the runner reject
        # that snapshot wholesale and then wait until timeout even though a
        # content-sized game surface was already available.
        content_sized = [
            item for item in windows
            if item["width"] * item["height"] >= minimum_area
        ]
        focused = [
            item for item in content_sized
            if item["flags"] & (1 << 6)
        ]
        visible = [item for item in content_sized if item["flags"] & 1]
        candidates = focused or visible or content_sized
        if candidates:
            return max(candidates, key=lambda item:
                       (item["width"] * item["height"], item["window"]))
        time.sleep(0.2)

    # Runtime-confirmed via MacWSHost.log for pid 13061 on 2026-08-23: the
    # host activated fullscreen catalog window 288 (score=7) while the
    # short-lived process-local metrics file was absent.  The same Stray
    # process kept publishing 1280x894 CAMetalDrawables through sequence 840.
    # Accept that cross-process pair only for events after this run's pre-Play
    # offsets; a historical host catalog line or an unproven size is not a
    # valid input endpoint.
    host_offset = getattr(remote, "host_log_offset", None)
    runtime_offset = getattr(remote, "runtime_log_offset", None)
    if host_offset is not None and runtime_offset is not None:
        host_text = host_log_suffix(remote, int(host_offset))
        candidates = []
        for index, match in enumerate(
                HOST_FULLSCREEN_WINDOW_RE.finditer(host_text)):
            if int(match.group("pid")) != int(pid):
                continue
            candidates.append({
                "window": int(match.group("window")),
                "logical_group": int(match.group("group")),
                "score": int(match.group("score")),
                "index": index,
                "witness": match.group(0),
            })
        samples = present_samples(log_suffix(remote, int(runtime_offset)))
        if candidates and samples:
            # A score-7 catalog entry is the host's focused foreground layer;
            # a later score-3 auxiliary surface must not steal game input.
            selected = max(
                candidates, key=lambda item: (item["score"], item["index"])
            )
            newest = samples[-1]
            width = float(newest["texture_width"])
            height = float(newest["texture_height"])
            if width * height >= minimum_area:
                return {
                    "window": selected["window"],
                    "flags": (1 << 6) if selected["score"] >= 7 else 1,
                    "logical_group": selected["logical_group"],
                    "width": width,
                    "height": height,
                    "source": (
                        "current-run MacWSHost fullscreen catalog + "
                        "CAMetalDrawable"
                    ),
                    "host_score": selected["score"],
                    "host_witness": selected["witness"],
                    "present_sequence": newest["sequence"],
                }
    raise RuntimeError(f"no content-sized window for pid={pid}")


def refresh_game_window_or_present(remote: Remote, pid: int,
                                   previous: dict, log_offset: int,
                                   sequence_floor: int, timeout: float,
                                   stage: str):
    """Follow a replacement CGWindow or prove the retained surface advances.

    UE replaces and temporarily retires its AppKit CGWindow while moving from
    the splash surface to the menu.  The fullscreen workspace consumes the
    WindowServer display stream and does not require that process-local
    catalog entry to remain published.  A missing catalog entry is therefore
    accepted only when the exact Stray PID is still live *and* its bounded
    CAMetalDrawable present counter has advanced since the preceding stage.
    """
    deadline = time.monotonic() + timeout
    last_sequence = sequence_floor
    while time.monotonic() < deadline:
        current = None
        try:
            current = largest_window(
                remote, pid, min(0.5, max(0.05, deadline - time.monotonic()))
            )
        except RuntimeError:
            pass
        suffix = log_suffix(remote, log_offset)
        fatal = ue_fatal_excerpt(suffix)
        if fatal:
            raise RuntimeError(
                "Stray reported a fatal error while waiting for its "
                f"{stage} surface: {fatal}"
            )
        samples = present_samples(suffix)
        if samples:
            last_sequence = max(last_sequence, samples[-1]["sequence"])
        if current is not None:
            process_live = root_pid_liveness(remote, pid)
            return current, {
                "stage": stage,
                **current,
                "catalog_visible": True,
                "process_live": process_live is True,
                "present_sequence": last_sequence,
            }
        process_live = root_pid_liveness(remote, pid)
        if process_live is False:
            raise RuntimeError(
                f"Stray exited while waiting for its {stage} surface"
            )
        if last_sequence > sequence_floor:
            return previous, {
                "stage": stage,
                **previous,
                "catalog_visible": False,
                "retained_last_window": True,
                "process_live": True,
                "present_sequence": last_sequence,
                "witness": "CAMetalDrawable present counter advanced",
            }
        time.sleep(0.2)
    raise RuntimeError(
        f"Stray pid={pid} remained live but published neither a {stage} "
        f"window nor a present after sequence={sequence_floor}"
    )


def steam_helper_pid(remote: Remote):
    for line in process_table(remote, "pid=,command="):
        fields = line.strip().split(None, 1)
        if (len(fields) == 2 and fields[0].isdigit() and
                "/Steam Helper.app/Contents/MacOS/Steam Helper" in fields[1]
                and "-launcher=0" in fields[1]):
            return int(fields[0])
    raise ProcessNotFound("Steam's main UI helper is not running")


def maybe_steam_helper_pid(remote: Remote):
    try:
        return steam_helper_pid(remote)
    except ProcessNotFound:
        return 0


def ensure_steam_ipctool(remote: Remote):
    """Restore the real Steam IPC Mach service after a GUI-stack restart."""
    status_command = (
        f"launchctl list {IPCTOOL_LABEL} 2>/dev/null || "
        f"launchctl print system/{IPCTOOL_LABEL} 2>/dev/null || "
        f"launchctl print user/501/{IPCTOOL_LABEL} 2>/dev/null || true"
    )
    status = remote.sudo(status_command, check=False)
    if re.search(r'\bpid"?\s*=\s*\d+', status, re.IGNORECASE):
        return "already-running"
    failures = []
    # Runtime-confirmed after cleanup_all: the first ipcserver instance can
    # consume stale SteamChrome ownership records, exit cleanly after its
    # cleanse pass, and leave no launchd job. Runtime-confirmed on 2026-08-21:
    # two consecutive generations retired the old 66966/76924 SteamChrome and
    # overlay ownership records; the third ordinary ipcserver generation then
    # remained live as PID 67245. Bound that production lifecycle to three
    # attempts; do not delete or fabricate any Steam IPC object in the runner.
    for attempt in range(1, 4):
        remote.sudo(
            f"test -f {shlex.quote(IPCTOOL_PLIST)} && "
            f"launchctl load {shlex.quote(IPCTOOL_PLIST)}",
        )
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            status = remote.sudo(status_command, check=False)
            if re.search(r'\bpid"?\s*=\s*\d+', status, re.IGNORECASE):
                return "loaded" if attempt == 1 else "loaded-after-cleanse"
            time.sleep(0.25)
        tail = remote.run(
            "/var/jb/usr/bin/tail -n 80 "
            "/var/jb/var/mobile/steam-ipcserver.log 2>/dev/null",
            check=False,
        )
        failures.append({"attempt": attempt, "tail": tail[-3000:]})
        remote.sudo(
            f"launchctl unload {shlex.quote(IPCTOOL_PLIST)} 2>/dev/null "
            "|| true", check=False,
        )
        time.sleep(0.5)
    raise RuntimeError(
        "Steam ipctool Mach service did not become live: " +
        json.dumps(failures, ensure_ascii=False)
    )


def ensure_steam_ui(remote: Remote, timeout: float, checkpoint=None,
                    applaunch_appid: int = 0):
    """Start one production Steam job and wait for its real library UI."""
    if applaunch_appid not in (0, 1332010):
        raise ValueError(f"unsupported Steam AppID: {applaunch_appid}")
    if remote.run(f"test -f {shlex.quote(STEAM_PLIST)}; echo $?", check=False
                  ).strip() != "0":
        raise RuntimeError("installed Steam runtime plist is missing")
    label_reader = '''
import plistlib, sys
with open(sys.argv[1], "rb") as stream:
    print(plistlib.load(stream).get("Label", ""))
'''
    installed_label = remote.sudo(
        f"/var/jb/usr/bin/python3 -c {shlex.quote(label_reader)} "
        f"{shlex.quote(STEAM_PLIST)}"
    ).strip()
    if installed_label != STEAM_JOB_LABEL:
        raise RuntimeError(
            "Steam job is not in launchd's application class: "
            f"expected={STEAM_JOB_LABEL} actual={installed_label}"
        )
    failures = []
    initially_running = maybe_steam_helper_pid(remote) > 1
    for attempt in range(1, 3):
        pid = maybe_steam_helper_pid(remote)
        if pid <= 1:
            # A prior non-KeepAlive Steam job can remain registered after its
            # process suite exits. Reload only this app job; never restart the
            # GUI stack. prepare_steam_runtime.sh consumes its validated AppID
            # marker exactly once, so an automatic retry must publish it again
            # before loading the replacement Steam generation.
            # Runtime-confirmed on 2026-08-24: generation 72328 included
            # `-applaunch 1332010`, while UI-timeout retry 72981 did not; the
            # runner then waited for a game it had never asked 72981 to launch.
            remote.sudo(
                f"launchctl unload {shlex.quote(STEAM_PLIST)} 2>/dev/null "
                "|| true",
                check=False,
            )
            if applaunch_appid:
                remote.sudo(
                    f"/var/jb/usr/bin/printf '%s\\n' "
                    f"{applaunch_appid:d} > "
                    f"{shlex.quote(STEAM_APPLAUNCH_MARKER)}"
                )
            remote.sudo(
                f"launchctl load {shlex.quote(STEAM_PLIST)}", timeout=20
            )
        deadline = time.monotonic() + timeout
        last_pid = pid
        while time.monotonic() < deadline:
            if checkpoint:
                checkpoint("steam-ui-start")
            last_pid = maybe_steam_helper_pid(remote)
            if last_pid > 1:
                try:
                    window = largest_window(
                        remote, last_pid,
                        min(3.0, max(0.2, deadline - time.monotonic())),
                        minimum_area=500000,
                    )
                    if initially_running and attempt == 1:
                        state = "already-running"
                    elif attempt == 1:
                        state = "loaded"
                    else:
                        state = "loaded-after-ui-retry"
                    return {"state": state, "pid": last_pid,
                            "window": window, "attempt": attempt,
                            "job_label": installed_label}
                except RuntimeError:
                    pass
            time.sleep(0.5)
        tail = remote.run(
            f"/var/jb/usr/bin/tail -n 80 {shlex.quote(RUNTIME_LOG)} 2>/dev/null",
            check=False,
        )
        failures.append({"attempt": attempt, "pid": last_pid,
                         "tail": tail[-4000:]})
        if attempt < 2:
            stop_steam_ui(remote)
            time.sleep(2.0)
    raise RuntimeError(
        "Steam did not publish a content-sized main UI after two bounded "
        "job attempts: " + json.dumps(failures, ensure_ascii=False)
    )


def request_running_steam_applaunch(remote: Remote, appid: int):
    """Ask the already-ready Steam owner to create its normal LaunchApp."""
    if appid != 1332010:
        raise ValueError(f"unsupported Steam AppID: {appid}")
    installed_bundle = (
        "/Users/root/Library/Application Support/Steam/"
        "Steam.AppBundle/Steam"
    )
    installed_client = installed_bundle + "/Contents/MacOS/steam_osx"
    request_log = "/var/mobile/Library/Logs/macws-steam-applaunch-request.log"
    command = (
        "( /var/jb/usr/bin/env "
        "HOME=/Users/root USER=mobile LOGNAME=mobile TMPDIR=/tmp "
        f"STEAM_APP_BUNDLE_PATH={shlex.quote(installed_bundle)} "
        "/var/jb/usr/macOS/bin/launchdchrootexec 501 501 "
        f"/var/mnt/rootfs {shlex.quote(installed_client)} "
        f"-applaunch {appid:d} >> {shlex.quote(request_log)} 2>&1; "
        f"/var/jb/usr/bin/printf 'request-exit=%s\\n' $? >> "
        f"{shlex.quote(request_log)} ) & "
        "/var/jb/usr/bin/printf 'request-pid=%s\\n' $!"
    )
    output = remote.sudo(command, timeout=10).strip()
    match = re.search(r"request-pid=(\d+)", output)
    if not match or int(match.group(1)) <= 1:
        raise RuntimeError(
            "could not start running-Steam AppID request: " + output
        )
    return {
        "mode": "running-steam-applaunch",
        "appid": appid,
        "request_pid": int(match.group(1)),
        "log": request_log,
        "game_launch_bypass": False,
    }


def stop_steam_ui(remote: Remote):
    remote.sudo(
        f"launchctl unload {shlex.quote(STEAM_PLIST)} 2>/dev/null || true; "
        "/var/jb/usr/bin/killall -TERM steam_osx 2>/dev/null || true; "
        "/var/jb/usr/bin/killall -TERM gameoverlayui 2>/dev/null || true; "
        "/var/jb/usr/bin/killall -TERM 'Steam Helper' 2>/dev/null || true; "
        "sleep 1; "
        "/var/jb/usr/bin/killall -9 gameoverlayui 2>/dev/null || true; "
        "/var/jb/usr/bin/killall -9 'Steam Helper' 2>/dev/null || true; "
        "/var/jb/usr/bin/killall -9 steam_osx 2>/dev/null || true",
        check=False,
    )
    rows = process_table(remote, "pid=,command=")
    return [
        row.strip() for row in rows
        if ("./steam_osx" in row or
            "/Steam Helper.app/Contents/MacOS/Steam Helper" in row or
            "/gameoverlayui" in row)
    ]


def steam_ui_process_pids(remote: Remote):
    """Return only Valve UI processes, excluding games and probe shells."""
    inventory = steam_ui_process_inventory(remote)
    return inventory["steam"] | inventory["helpers"]


def steam_ui_process_inventory(remote: Remote):
    """Resolve the exact Steam owner separately from its CEF helpers."""
    rows = process_table(remote, "pid=,command=")
    steam = set()
    helpers = set()
    for row in rows:
        fields = row.strip().split(None, 1)
        if len(fields) != 2 or not fields[0].isdigit():
            continue
        command = fields[1]
        if command.startswith("./steam_osx "):
            steam.add(int(fields[0]))
        elif "/Steam Helper.app/Contents/MacOS/Steam Helper" in command:
            helpers.add(int(fields[0]))
    return {"steam": steam, "helpers": helpers}


def signal_exact_pids(remote: Remote, pids: set[int], signal: str):
    if not pids:
        return []
    live = sorted(pids & steam_ui_process_pids(remote))
    if live:
        remote.sudo(
            f"kill -{signal} " + " ".join(str(pid) for pid in live),
            check=False,
        )
    return live


def render_path_process_inventory(remote: Remote):
    """Capture the exact display producers that may be paused while cooling.

    MacWSHost deliberately remains runnable so iPadOS keeps the fullscreen
    Scene and its last drawable visible.  WindowServer and macwsdisplayd are
    the two root-owned producers that keep generating/publishing desktop
    frames even when the desktop is visually static.
    """
    inventory = {}
    for row in process_table(remote, "pid=,state=,command="):
        fields = row.strip().split(None, 2)
        if len(fields) != 3 or not fields[0].isdigit():
            continue
        pid = int(fields[0])
        state = fields[1]
        command = fields[2]
        if "SkyLight.framework/Resources/WindowServer" in command:
            label = "WindowServer"
        elif command.endswith("/macwsdisplayd"):
            label = "macwsdisplayd"
        else:
            continue
        if label in inventory:
            raise RuntimeError(
                f"cooling requires one {label}, found multiple processes"
            )
        inventory[label] = {
            "pid": pid,
            "state": state,
            "command": command,
        }
    return inventory


def signal_exact_render_path(remote: Remote, captured: dict, signal: str):
    """Signal only still-live PIDs whose complete argv identity is unchanged."""
    if signal not in {"STOP", "CONT"}:
        raise ValueError("render-path cooling supports STOP/CONT only")
    if not captured:
        return []
    current = render_path_process_inventory(remote)
    targets = []
    for label, expected in captured.items():
        actual = current.get(label)
        if (actual is None or actual["pid"] != expected["pid"] or
                actual["command"] != expected["command"]):
            raise RuntimeError(
                f"render-path identity changed before SIG{signal}: "
                f"{label} expected={expected} actual={actual}"
            )
        targets.append(actual["pid"])
    if targets:
        remote.sudo(
            f"kill -{signal} " + " ".join(str(pid) for pid in targets),
            check=True,
        )
    return sorted(targets)


def signal_known_pids(remote: Remote, pids: set[int], signal: str):
    """Signal captured PIDs without a new process-table allocation.

    This is used at the Steam→game peak: the identities were recorded while
    the launcher was healthy, and asking ``ps`` to rediscover them after the
    game starts is exactly what can fail under memory pressure.
    """
    if not pids:
        return []
    marker = "__MACWS_SIGNAL_DONE__"
    commands = " ".join(
        f"if kill -{signal} {pid} 2>/dev/null; then echo signaled:{pid}; fi;"
        for pid in sorted(pids)
    ) + f" echo {marker}"
    output = remote.sudo(commands, check=False, timeout=15).splitlines()
    if not output or output[-1] != marker:
        raise RuntimeError(
            f"could not complete signal {signal} transaction for {sorted(pids)}"
        )
    return sorted(
        int(line.split(":", 1)[1]) for line in output
        if line.startswith("signaled:") and
        line.split(":", 1)[1].isdigit()
    )


def quiesce_known_steam_ui(agent: RemoteSignalAgent,
                           inventory: dict[str, set[int]]):
    """Bound one test-only Steam owner/CEF lifecycle transaction.

    The exact identities are captured before Play, when process enumeration is
    reliable.  Stop steam_osx only after its separate overlay process is bound
    to Stray, then retire its exact CEF helpers to return their resident memory.
    The caller must resume the captured steam_osx PIDs after the game exits.
    """
    steam = set(inventory.get("steam", set()))
    helpers = set(inventory.get("helpers", set()))
    if not steam or not helpers:
        raise RuntimeError(
            "Steam UI quiesce requires exact pre-Play owner and helper PIDs"
        )
    stopped = agent.signal(int(signal.SIGSTOP), steam)
    if set(stopped) != steam:
        raise RuntimeError(
            f"could not suspend every exact steam_osx PID: {stopped}"
        )
    terminated = agent.signal(int(signal.SIGTERM), helpers)
    time.sleep(0.5)
    killed = agent.signal(int(signal.SIGKILL), helpers)
    return {
        "steam_pids": sorted(steam),
        "helper_pids": sorted(helpers),
        "suspended": stopped,
        "term_signaled": terminated,
        "kill_signaled": killed,
    }


def configure_steam_overlay_test_environment(remote: Remote,
                                             requested: dict[str, str]):
    """Install one fresh-process overlay A/B environment deterministically."""
    before = {
        name: remote.sudo(
            f"launchctl getenv {shlex.quote(name)} 2>/dev/null || true",
            check=False,
        ).strip()
        for name in STEAM_OVERLAY_TEST_ENV
    }
    for name in STEAM_OVERLAY_TEST_ENV:
        remote.sudo(
            f"launchctl unsetenv {shlex.quote(name)} 2>/dev/null || true",
            check=False,
        )
    for name, value in requested.items():
        remote.sudo(
            f"launchctl setenv {shlex.quote(name)} {shlex.quote(value)}"
        )
    after = {
        name: remote.sudo(
            f"launchctl getenv {shlex.quote(name)} 2>/dev/null || true",
            check=False,
        ).strip()
        for name in STEAM_OVERLAY_TEST_ENV
    }
    if any(after[name] != requested.get(name, "")
           for name in STEAM_OVERLAY_TEST_ENV):
        raise RuntimeError(
            "Steam overlay test environment did not round-trip: " +
            json.dumps(after, ensure_ascii=False)
        )
    return {"before": before, "after": after}


def clear_steam_overlay_test_environment(remote: Remote):
    for name in STEAM_OVERLAY_TEST_ENV:
        remote.sudo(
            f"launchctl unsetenv {shlex.quote(name)} 2>/dev/null || true",
            check=False,
        )


def capture_steam_overlay_log(remote: Remote, pid: int,
                              destination: pathlib.Path):
    source = f"/var/mnt/rootfs/private/tmp/gameoverlayrenderer.{pid}.log"
    if remote.run(f"test -s {shlex.quote(source)}; echo $?", check=False
                  ).strip() != "0":
        return None
    remote.copy_from(source, destination)
    return {"path": str(destination), "bytes": destination.stat().st_size}


def background_cpu_snapshot(remote: Remote, process_rows=None):
    """Capture GUI contaminants that can steal CPU and create extra heat."""
    rows = []
    if process_rows is None:
        process_rows = process_table(
            remote, "pid=,uid=,pcpu=,command="
        )
    for line in process_rows:
        fields = line.strip().split(None, 3)
        if len(fields) != 4:
            continue
        try:
            pid = int(fields[0])
            uid = int(fields[1])
            cpu = float(fields[2])
        except ValueError:
            continue
        command = fields[3]
        # Both private macOS lsd jobs run as root in the chroot; the outer
        # iPadOS per-user lsd is uid 501. The exact argv distinguishes the two
        # private roles without two extra launchctl/SSH round trips per poll.
        if uid == 0 and command == "/usr/libexec/lsd":
            label = "lsd-session"
        elif uid == 0 and command == "/usr/libexec/lsd runAsRoot":
            label = "lsd-system"
        elif "/Finder.app/Contents/MacOS/Finder" in command:
            label = "Finder"
        elif "/Dock.app/Contents/MacOS/Dock" in command:
            label = "Dock"
        elif "SkyLight.framework/Resources/WindowServer" in command:
            label = "WindowServer"
        elif "MacWSHost.app/MacWSHost" in command:
            label = "MacWSHost"
        elif "/macwshostd" in command:
            label = "macwshostd"
        elif "/macwsdisplayd" in command:
            label = "macwsdisplayd"
        elif "/OSXvnc-server" in command:
            label = "OSXvnc"
        elif CONTROL_CENTER_COMMAND in command:
            label = "ControlCenter"
        elif command.startswith("/System/Library/CoreServices/ReportCrash"):
            # ReportCrash is normally an idle on-demand agent.  A crash storm
            # can instead pin it near one core and both heat the iPad and
            # invalidate a game comparison.  Observe it as a contaminant; do
            # not signal or disable the system crash reporter from the test.
            label = "ReportCrash"
        elif command.startswith("./steam_osx "):
            label = "Steam"
        elif "/gameoverlayui" in command:
            label = "Steam Overlay"
        elif "Steam Helper.app/Contents/MacOS/Steam Helper" in command:
            label = "Steam Helper"
        else:
            label = None
        if label:
            rows.append({"label": label, "pid": pid,
                         "cpu_percent": cpu, "command": command})
    return {"time": time.time(), "processes": rows,
            "maximum_cpu_percent": max(
                (row["cpu_percent"] for row in rows), default=0.0
            ),
            "total_cpu_percent": sum(
                row["cpu_percent"] for row in rows
            )}


def control_center_pids(remote: Remote):
    """Resolve only the real macOS ControlCenter executable."""
    pids = []
    for line in process_table(remote, "pid=,command="):
        fields = line.strip().split(None, 1)
        if len(fields) != 2 or CONTROL_CENTER_COMMAND not in fields[1]:
            continue
        try:
            pids.append(int(fields[0]))
        except ValueError:
            pass
    return sorted(set(pids))


def suspend_control_center_for_game(remote: Remote):
    """Temporarily remove a measured desktop heat source for the sample."""
    before = control_center_pids(remote)
    result = {"pids_before": before, "was_running": bool(before)}
    if not before:
        result["pids_after"] = []
        result["action"] = "already-stopped"
        return result
    remote.sudo(
        f"launchctl unload {shlex.quote(CONTROL_CENTER_PLIST)}",
        check=False,
    )
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        after = control_center_pids(remote)
        if not after:
            result["pids_after"] = []
            result["action"] = "suspended"
            return result
        time.sleep(0.25)
    result["pids_after"] = control_center_pids(remote)
    raise RuntimeError(
        "ControlCenter survived the bounded gaming-mode unload: " +
        json.dumps(result)
    )


def restore_control_center_after_game(remote: Remote):
    """Restore the Aqua workspace agent removed by gaming mode."""
    before = control_center_pids(remote)
    result = {"pids_before": before}
    if before:
        result["pids_after"] = before
        result["action"] = "already-running"
        return result
    remote.sudo(
        f"launchctl load {shlex.quote(CONTROL_CENTER_PLIST)}",
        check=False,
    )
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        after = control_center_pids(remote)
        if after:
            result["pids_after"] = after
            result["action"] = "restored"
            return result
        time.sleep(0.25)
    result["pids_after"] = control_center_pids(remote)
    raise RuntimeError(
        "ControlCenter did not return after the gaming-mode sample: " +
        json.dumps(result)
    )


def wait_for_idle_background(remote: Remote, ceiling: float,
                             total_ceiling: float, timeout: float,
                             labels: set[str] | None = None):
    deadline = time.monotonic() + timeout
    history = []
    consecutive_idle = 0
    while True:
        sample = background_cpu_snapshot(remote)
        considered = [
            row for row in sample["processes"]
            if labels is None or row["label"] in labels
        ]
        sample["considered_labels"] = (
            sorted(labels) if labels is not None else "all"
        )
        sample["considered_maximum_cpu_percent"] = max(
            (row["cpu_percent"] for row in considered), default=0.0
        )
        sample["considered_total_cpu_percent"] = sum(
            row["cpu_percent"] for row in considered
        )
        history.append(sample)
        if (sample["considered_maximum_cpu_percent"] <= ceiling and
                sample["considered_total_cpu_percent"] <= total_ceiling):
            consecutive_idle += 1
            if consecutive_idle >= 2:
                return sample, history
        else:
            consecutive_idle = 0
        if time.monotonic() >= deadline:
            raise RuntimeError(
                "background CPU preflight timed out: " +
                json.dumps(sample, ensure_ascii=False)
            )
        time.sleep(min(1.0, max(0.1, deadline - time.monotonic())))


def exact_window_geometry(remote: Remote, window: dict, purpose: str):
    capture_path = f"/private/tmp/macws_{purpose}.{os.getpid()}.png"
    command = (
        "export PATH=/usr/bin:/bin:/usr/sbin:/sbin; "
        f"/usr/sbin/screencapture -x -l {window['window']} "
        f"{capture_path} & capture_pid=$!; elapsed=0; "
        "while kill -0 $capture_pid 2>/dev/null; do "
        "if [ $elapsed -ge 8 ]; then kill -TERM $capture_pid 2>/dev/null; "
        "wait $capture_pid 2>/dev/null; rm -f " + capture_path +
        "; exit 124; fi; sleep 1; elapsed=$((elapsed+1)); done; "
        "wait $capture_pid || exit $?; "
        f"/usr/bin/sips -g pixelWidth -g pixelHeight {capture_path}; "
        f"rm -f {capture_path}"
    )
    try:
        inspect = remote.sudo(
            "bash /var/jb/usr/macOS/bin/run_bash.sh -c " +
            shlex.quote(command), timeout=15,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        # Steam input is normalized to the captured extent, so preserving the
        # window aspect ratio is sufficient for every fractional tap. The one
        # legacy absolute Play Y coordinate needs Retina units; 2x is the
        # runtime-confirmed backing scale of this desktop. A hung diagnostic
        # screenshot must never abort the launch pipeline.
        return (max(1, round(window["width"] * 2.0)),
                max(1, round(window["height"] * 2.0)))
    width_match = re.search(r"pixelWidth:\s*(\d+)", inspect)
    height_match = re.search(r"pixelHeight:\s*(\d+)", inspect)
    if not width_match or not height_match:
        raise RuntimeError(f"could not resolve {purpose} capture geometry")
    return int(width_match.group(1)), int(height_match.group(1))


def tap_window(remote: Remote, pid: int, window: dict, width: int,
               height: int, normalized_x: float, normalized_y: float,
               activate_first: bool = False, hold: float = 0.0):
    x = round(width * normalized_x)
    y = round(height * normalized_y)
    if remote.input_agent is not None:
        output = remote.input_agent.gesture(
            "tap", pid, window["window"], width, height, x, y,
            activate_first=activate_first, hold=hold,
        )
    else:
        output = remote.run(
            f"python3 {GESTURE_PROBE} tap --pid {pid} "
            f"--window {window['window']} --width {width} --height {height} "
            f"--x {x} --y {y}" +
            (" --activate-first" if activate_first else "") +
            (f" --hold {hold}" if hold else ""), timeout=15,
        ).strip()
    return {"x": x, "y": y, "probe": output}


def tap_window_point(remote: Remote, pid: int, window: dict, width: int,
                     height: int, x: int, y: int,
                     activate_first: bool = False):
    if not 0 <= x < width or not 0 <= y < height:
        raise RuntimeError(
            f"input point ({x},{y}) outside {width}x{height} Steam surface"
        )
    if remote.input_agent is not None:
        output = remote.input_agent.gesture(
            "tap", pid, window["window"], width, height, x, y,
            activate_first=activate_first,
        )
    else:
        output = remote.run(
            f"python3 {GESTURE_PROBE} tap --pid {pid} "
            f"--window {window['window']} --width {width} --height {height} "
            f"--x {x} --y {y}" +
            (" --activate-first" if activate_first else ""), timeout=15,
        ).strip()
    return {"x": x, "y": y, "probe": output}


def hover_window(remote: Remote, pid: int, window: dict, width: int,
                 height: int, normalized_x: float, normalized_y: float,
                 activate_first: bool = False):
    """Send one real button-free indirect-pointer update to an exact window."""
    x = round(width * normalized_x)
    y = round(height * normalized_y)
    if remote.input_agent is not None:
        output = remote.input_agent.gesture(
            "hover", pid, window["window"], width, height, x, y,
            activate_first=activate_first,
        )
    else:
        output = remote.run(
            f"python3 {GESTURE_PROBE} hover --pid {pid} "
            f"--window {window['window']} --width {width} --height {height} "
            f"--x {x} --y {y}" +
            (" --activate-first" if activate_first else ""), timeout=15,
        ).strip()
    return {"x": x, "y": y, "probe": output}


def send_steam_key(remote: Remote, pid: int, window: dict, *,
                   key: str | None = None, text: str | None = None,
                   command: bool = False):
    if (key is None) == (text is None):
        raise ValueError("send_steam_key requires exactly one of key or text")
    width = max(1, round(window["width"]))
    height = max(1, round(window["height"]))
    value = (f"--key {shlex.quote(key)}" if key is not None else
             f"--text {shlex.quote(text)}")
    if key is not None and remote.input_agent is not None:
        return remote.input_agent.key(
            pid, window["window"], width, height, key, command=command
        )
    return remote.run(
        f"python3 {KEY_PROBE} --pid {pid} --window {window['window']} "
        f"--width {width} --height {height} {value}" +
        (" --command" if command else ""), timeout=15,
    ).strip()


def select_stray_in_steam(remote: Remote):
    pid = steam_helper_pid(remote)
    # Steam first exposes a 700x440 login window (308k logical pixels).  It is
    # not the library surface and accepting it turns every normalized click
    # into a plausible but inert input record.  The real 1010x600 client is
    # >500k and remains stable across warm launches.
    window = largest_window(remote, pid, 45, minimum_area=500000)
    width, height = exact_window_geometry(remote, window, "steam_select")
    actions = []
    # Runtime-confirmed on 2026-09-21: the prearmed iOS LaunchServices agent
    # returned opened=false for Steam's *macOS* steam:// URL, although the
    # 1010x600 Steam client was visibly ready. Exact-window input at
    # (409,125) on its 2116x1324 Retina capture opened the LIBRARY menu;
    # (405,174) selected Home and the next iPadOS capture showed HOME/ALL
    # with Stray in the left list. Use that real UI route, and keep the
    # following visible HOME/ALL postcondition authoritative.
    actions.append({"action": "open-library-menu", **tap_window(
        remote, pid, window, width, height, 0.193, 0.094,
        activate_first=True)})
    time.sleep(0.35)
    actions.append({"action": "select-library-home", **tap_window(
        remote, pid, window, width, height, 0.191, 0.131)})
    actions.append({
        "action": "library-visible-postcondition",
        **wait_for_steam_library_surface(remote, pid, 60.0),
    })
    window = largest_window(remote, pid, 5, minimum_area=500000)
    width, height = exact_window_geometry(remote, window, "steam_search")
    actions.append({"action": "focus-search", **tap_window(
        remote, pid, window, width, height, 0.0887, 0.2558,
        activate_first=True)})
    time.sleep(0.4)
    actions.append({
        "action": "clear-search",
        "probe": send_steam_key(remote, pid, window, key="a", command=True),
    })
    actions.append({
        "action": "type-stray",
        "probe": send_steam_key(remote, pid, window, text="Stray"),
    })
    time.sleep(4.0)
    return {"pid": pid, "window": window, "capture_width": width,
            "capture_height": height, "actions": actions}


def wait_for_steam_library_surface(remote: Remote, pid: int,
                                   timeout: float):
    """Wait until Steam's Library SPA is visibly interactive."""
    deadline = time.monotonic() + timeout
    started = time.monotonic()
    attempts = []
    destination = pathlib.Path(tempfile.gettempdir()) / (
        f"macws-steam-library-ready.{os.getpid()}.png"
    )
    try:
        while time.monotonic() < deadline:
            window = largest_window(remote, pid, 5, minimum_area=500000)
            capture = capture_exact_window(
                remote, window["window"], destination
            )
            recognized = recognize_exact_window(capture)
            tokens = {
                re.sub(r"[^A-Z]+", "", line)
                for line in recognized.splitlines()
            }
            visible = "HOME" in tokens and "ALL" in tokens
            attempts.append({
                "elapsed_seconds": time.monotonic() - started,
                "visible": visible,
                "recognized_text": recognized[-1000:],
            })
            if visible:
                return {
                    "wait_seconds": time.monotonic() - started,
                    "attempts": attempts,
                    "witness": "visible HOME and ALL Library controls",
                }
            if "ERROR CODE: -324" in recognized:
                raise RuntimeError(
                    "Steam Library returned ERROR CODE: -324 instead of an "
                    "interactive surface: " +
                    json.dumps(attempts[-1], ensure_ascii=False)
                )
            time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
    finally:
        try:
            destination.unlink()
        except FileNotFoundError:
            pass
    raise RuntimeError(
        "Steam Library navigation did not expose visible HOME/ALL controls: " +
        json.dumps(attempts[-3:], ensure_ascii=False)
    )


def reuse_selected_stray_in_steam(remote: Remote):
    """Use the page deliberately left selected by a preceding sweep run."""
    pid = steam_helper_pid(remote)
    window = largest_window(remote, pid, 10, minimum_area=500000)
    width, height = exact_window_geometry(remote, window, "steam_reuse")
    if remote.input_agent is not None:
        activation = remote.input_agent.gesture(
            "activate", pid, window["window"], width, height
        )
    else:
        activation = remote.run(
            f"python3 {GESTURE_PROBE} activate --pid {pid} "
            f"--window {window['window']} --width {width} --height {height}",
            timeout=15,
        ).strip()
    # Command-H retires CEF's measured renderer load after every launch.  A
    # retained process therefore needs an activation-only record next time;
    # clicking Library to wake it navigates away from the selected Stray page.
    time.sleep(1.0)
    return {
        "pid": pid,
        "window": window,
        "capture_width": width,
        "capture_height": height,
        "actions": [{"action": "reuse-explicitly-requested",
                     "activation": activation}],
    }


def click_selected_steam_play(remote: Remote, selection: dict,
                              normalized_x: float,
                              normalized_y: float):
    pid = selection["pid"]
    window = largest_window(remote, pid, 5, minimum_area=500000)
    # Runtime-confirmed on the Retina Steam window: the AppKit catalog and
    # macws_window_metrics describe logical extents (currently 1194x750 and
    # 1010x600 respectively), while Host's exact-window input descriptor is
    # the 2480x1592 captured pixel surface.  Mixing those coordinate spaces
    # moved the nominal 32.3%/55.0% Play click away from the button.  Ask the
    # actual CGWindow capture for its pixel dimensions on every launch.
    width, height = exact_window_geometry(remote, window, "steam_play")
    # The broker descriptor point is 638x865 on both observed exact-window
    # geometries.  Its normalized Y differs because fullscreen adds pixels
    # below the logical 1010x600 Steam content.  A zero Y selects this
    # runtime-confirmed absolute point; an explicit nonzero override retains
    # the old normalized-coordinate diagnostic option.
    if normalized_y == 0:
        click = tap_window_point(
            remote, pid, window, width, height,
            round(width * normalized_x), 865, activate_first=True,
        )
    else:
        click = tap_window(remote, pid, window, width, height,
                           normalized_x, normalized_y, activate_first=True)
    return {"pid": pid, "window": window, "capture_width": width,
            "capture_height": height, "selection": selection, **click}


def click_steam_play(remote: Remote, normalized_x: float,
                     normalized_y: float):
    selection = select_stray_in_steam(remote)
    return click_selected_steam_play(
        remote, selection, normalized_x, normalized_y
    )


def send_return(remote: Remote, pid: int, window: dict):
    width = max(1, round(window["width"]))
    height = max(1, round(window["height"]))
    if remote.input_agent is not None:
        return remote.input_agent.key(
            pid, window["window"], width, height, "return", hold=0.12
        )
    return remote.run(
        f"python3 {KEY_PROBE} --pid {pid} --window {window['window']} "
        f"--width {width} --height {height} --key return --hold 0.12",
        timeout=15,
    ).strip()


def send_game_key(remote: Remote, pid: int, window: dict, key: str,
                  hold: float):
    width = max(1, round(window["width"]))
    height = max(1, round(window["height"]))
    if remote.input_agent is not None:
        return remote.input_agent.key(
            pid, window["window"], width, height, key, hold=hold
        )
    return remote.run(
        f"python3 {KEY_PROBE} --pid {pid} --window {window['window']} "
        f"--width {width} --height {height} --key {shlex.quote(key)} "
        f"--hold {hold:.3f}",
        timeout=max(15.0, hold + 10.0),
    ).strip()


def send_rfb_return(host: str, port: int, hold: float):
    """Send an independent VNC Return pair through the real OSXvnc path."""
    from vnc_capture import connect_rfb

    sock, width, height, _ = connect_rfb(host, port, 8.0)
    try:
        keysym = 0xff0d
        sock.sendall(struct.pack(">BBxxI", 4, 1, keysym))
        time.sleep(hold)
        sock.sendall(struct.pack(">BBxxI", 4, 0, keysym))
    finally:
        sock.close()
    return (f"rfb-key key=return keysym=0x{keysym:x} "
            f"framebuffer={width}x{height} hold={hold:.3f}s")


def apply_quality_profile(remote: Remote, quality: str, width: int, height: int,
                          screen_percentage: int, scaling_solution: str,
                          frame_rate_limit: int = 0,
                          uncapped: bool = False,
                          fullscreen_mode: int = 0):
    replacements = dict(QUALITY_PROFILES[quality])
    # UE's macOS native fullscreen mode (0) waits for the AppKit Space
    # transition delegate before CreateGameWindow may return.  Keep the mode
    # an explicit benchmark dimension so a Host fullscreen-canvas run can use
    # a real window (2) without its result being mislabeled native fullscreen.
    mode = str(fullscreen_mode)
    replacements["FullscreenMode"] = mode
    replacements["LastConfirmedFullscreenMode"] = mode
    replacements["PreferredFullscreenMode"] = mode
    replacements["SteamDeckScreenPercentage"] = str(screen_percentage)
    replacements["ScreenPercentage"] = str(screen_percentage)
    # Keep the project's custom fields and UE's native scalability resolution
    # scale in lockstep.  Earlier 85/90 A/B runs left sg.ResolutionQuality at
    # 100, so they did not establish that the effective renderer scale changed.
    # Output resolution remains native fullscreen. Non-resolution sg.* values
    # come from the explicitly selected quality preset.
    replacements["sg.ResolutionQuality"] = f"{screen_percentage:.6f}"
    replacements["ScalingSolution"] = scaling_solution
    # Stray applies its project-specific GameUserSettings FrameRateLimit after
    # Engine.ini's t.MaxFPS.  Runtime on 2026-08-24 showed an exact 49.79-
    # 49.99 FPS plateau with t.MaxFPS=54 while this key remained 50.  Keep the
    # two independently enforced UE caps in lockstep for a requested limit;
    # zero normally retains the user's current project setting, matching
    # --max-fps.  --uncapped is deliberately separate so a benchmark cannot
    # silently inherit a stale cap while claiming to be unlimited.
    if frame_rate_limit > 0:
        replacements["FrameRateLimit"] = f"{frame_rate_limit:.6f}"
    elif uncapped:
        replacements["FrameRateLimit"] = "0.000000"
    if width > 0 and height > 0:
        replacements.update({
            "ResolutionSizeX": str(width),
            "ResolutionSizeY": str(height),
            "LastUserConfirmedResolutionSizeX": str(width),
            "LastUserConfirmedResolutionSizeY": str(height),
            "DesiredScreenWidth": str(width),
            "DesiredScreenHeight": str(height),
            "LastUserConfirmedDesiredScreenWidth": str(width),
            "LastUserConfirmedDesiredScreenHeight": str(height),
        })
    # UE may remove individual keys after a launch. A replace-only sed pass
    # then leaves a partially configured profile and makes the next benchmark
    # fail before Steam starts. Update existing keys and insert missing keys in
    # their owning INI section in one atomic rewrite.
    profile_editor = r'''
import json
import os
import tempfile
import sys

path = sys.argv[1]
values = json.loads(sys.argv[2])
game_section = "[/Script/Hk_project.HKGameUserSettings]"
scalability_section = "[ScalabilityGroups]"
section_for = {
    key: scalability_section if key.startswith("sg.") else game_section
    for key in values
}
with open(path, "r", encoding="utf-8") as stream:
    lines = stream.readlines()

found = set()
for index, line in enumerate(lines):
    for key, value in values.items():
        if line.startswith(key + "="):
            lines[index] = f"{key}={value}\n"
            found.add(key)
            break

missing = {}
for key, value in values.items():
    if key not in found:
        missing.setdefault(section_for[key], []).append((key, value))

output = []
seen_sections = set()
for line in lines:
    output.append(line)
    section = line.rstrip("\r\n")
    if section in missing:
        seen_sections.add(section)
        output.extend(f"{key}={value}\n" for key, value in missing[section])
for section, entries in missing.items():
    if section in seen_sections:
        continue
    if output and output[-1].strip():
        output.append("\n")
    output.append(section + "\n")
    output.extend(f"{key}={value}\n" for key, value in entries)

descriptor, temporary = tempfile.mkstemp(
    prefix=".macws-profile-", dir=os.path.dirname(path)
)
try:
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.writelines(output)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temporary, os.stat(path).st_mode)
    os.replace(temporary, path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
'''
    remote.sudo(
        f"/var/jb/usr/bin/python3 -c {shlex.quote(profile_editor)} "
        f"{shlex.quote(GAME_CONFIG)} "
        f"{shlex.quote(json.dumps(replacements, separators=(',', ':')))}"
    )
    text = remote.sudo(
        f"/var/jb/usr/bin/grep -E {shlex.quote('^(' + '|'.join(re.escape(key) for key in replacements) + ')=')} "
        f"{shlex.quote(GAME_CONFIG)}",
    )
    observed = {}
    for line in text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            observed[key] = value
    mismatches = {
        key: {"expected": value, "actual": observed.get(key)}
        for key, value in replacements.items()
        if observed.get(key) != value
    }
    if mismatches:
        raise RuntimeError(f"profile verification failed: {mismatches}")
    return observed


def read_profile(remote: Remote):
    keys = list(dict.fromkeys(
        key for profile in QUALITY_PROFILES.values() for key in profile
    )) + [
        "FrameRateLimit",
        "ResolutionSizeX", "ResolutionSizeY",
        "LastUserConfirmedResolutionSizeX",
        "LastUserConfirmedResolutionSizeY",
        "DesiredScreenWidth", "DesiredScreenHeight",
        "LastUserConfirmedDesiredScreenWidth",
        "LastUserConfirmedDesiredScreenHeight",
    ]
    text = remote.sudo(
        f"/var/jb/usr/bin/grep -E {shlex.quote('^(' + '|'.join(re.escape(key) for key in keys) + ')=')} "
        f"{shlex.quote(GAME_CONFIG)}",
        check=False,
    )
    result = {}
    for line in text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            result[key] = value
    return result


def read_engine_system_settings(remote: Remote, keys: list[str]):
    reader = r'''
import json
import os
import sys

path = sys.argv[1]
keys = set(json.loads(sys.argv[2]))
result = {}
section = ""
if os.path.exists(path):
    with open(path, "r", encoding="utf-8") as stream:
        for raw in stream:
            line = raw.strip()
            if line.startswith("[") and line.endswith("]"):
                section = line
                continue
            if section != "[SystemSettings]" or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key in keys:
                result[key] = value
print(json.dumps(result, separators=(",", ":")))
'''
    text = remote.sudo(
        f"/var/jb/usr/bin/python3 -c {shlex.quote(reader)} "
        f"{shlex.quote(ENGINE_CONFIG)} "
        f"{shlex.quote(json.dumps(keys, separators=(',', ':')))}"
    ).strip()
    return json.loads(text or "{}")


def apply_engine_system_settings(remote: Remote, replacements: dict[str, str]):
    editor = r'''
import json
import os
import tempfile
import sys

path = sys.argv[1]
values = json.loads(sys.argv[2])
os.makedirs(os.path.dirname(path), exist_ok=True)
if os.path.exists(path):
    with open(path, "r", encoding="utf-8") as stream:
        lines = stream.readlines()
    mode = os.stat(path).st_mode
else:
    lines = []
    mode = 0o644

section = ""
found = set()
output = []
for raw in lines:
    stripped = raw.rstrip("\r\n")
    if stripped.startswith("[") and stripped.endswith("]"):
        section = stripped
    if section == "[SystemSettings]" and "=" in stripped:
        key = stripped.split("=", 1)[0]
        if key in values:
            if key not in found:
                output.append(f"{key}={values[key]}\n")
                found.add(key)
            continue
    output.append(raw)

missing = [(key, value) for key, value in values.items() if key not in found]
if missing:
    section_index = next(
        (index for index, line in enumerate(output)
         if line.rstrip("\r\n") == "[SystemSettings]"), None
    )
    if section_index is None:
        if output and output[-1].strip():
            output.append("\n")
        output.append("[SystemSettings]\n")
        output.extend(f"{key}={value}\n" for key, value in missing)
    else:
        insert = section_index + 1
        output[insert:insert] = [
            f"{key}={value}\n" for key, value in missing
        ]

descriptor, temporary = tempfile.mkstemp(
    prefix=".macws-engine-profile-", dir=os.path.dirname(path)
)
try:
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.writelines(output)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temporary, mode)
    os.replace(temporary, path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
'''
    remote.sudo(
        f"/var/jb/usr/bin/python3 -c {shlex.quote(editor)} "
        f"{shlex.quote(ENGINE_CONFIG)} "
        f"{shlex.quote(json.dumps(replacements, separators=(',', ':')))}"
    )
    observed = read_engine_system_settings(remote, list(replacements))
    mismatches = {
        key: {"expected": value, "actual": observed.get(key)}
        for key, value in replacements.items()
        if observed.get(key) != value
    }
    if mismatches:
        raise RuntimeError(
            f"engine SystemSettings verification failed: {mismatches}"
        )
    return observed


def remove_engine_system_settings(remote: Remote, keys: list[str]):
    """Atomically remove invalid/temporary keys from UE SystemSettings."""
    editor = r'''
import json
import os
import tempfile
import sys

path = sys.argv[1]
keys = set(json.loads(sys.argv[2]))
if not os.path.exists(path):
    print("[]")
    raise SystemExit(0)
with open(path, "r", encoding="utf-8") as stream:
    lines = stream.readlines()
mode = os.stat(path).st_mode

section = ""
removed = []
output = []
for raw in lines:
    stripped = raw.rstrip("\r\n")
    if stripped.startswith("[") and stripped.endswith("]"):
        section = stripped
    if section == "[SystemSettings]" and "=" in stripped:
        key = stripped.split("=", 1)[0]
        if key in keys:
            removed.append(key)
            continue
    output.append(raw)

descriptor, temporary = tempfile.mkstemp(
    prefix=".macws-engine-profile-", dir=os.path.dirname(path)
)
try:
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.writelines(output)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temporary, mode)
    os.replace(temporary, path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
print(json.dumps(sorted(set(removed)), separators=(",", ":")))
'''
    text = remote.sudo(
        f"/var/jb/usr/bin/python3 -c {shlex.quote(editor)} "
        f"{shlex.quote(ENGINE_CONFIG)} "
        f"{shlex.quote(json.dumps(keys, separators=(',', ':')))}"
    ).strip()
    observed = read_engine_system_settings(remote, keys)
    if observed:
        raise RuntimeError(
            f"engine SystemSettings removal verification failed: {observed}"
        )
    return json.loads(text or "[]")


def remote_file_size(remote: Remote, path: str):
    # Do not turn a transient fork/stat failure under game memory pressure
    # into a false log rotation.  That previously reset a current-run offset
    # to zero and admitted stale STRAY-PRESENT records from older processes.
    for _ in range(3):
        output = remote.run(
            f"/var/jb/usr/bin/stat -c %s {shlex.quote(path)} 2>/dev/null",
            check=False,
        ).strip()
        if output.isdigit():
            return int(output)
        time.sleep(0.05)
    exists = remote.run(
        f"test -e {shlex.quote(path)}; echo $?", check=False
    ).strip()
    if exists.endswith("1"):
        return 0
    raise RuntimeError(f"could not read current size of {path}")


def log_size(remote: Remote):
    return remote_file_size(remote, RUNTIME_LOG)


def file_suffix(remote: Remote, path: str, offset: int):
    # Steam truncates some logs on a clean job restart.  An
    # offset from the previous inode would otherwise make the failure capture
    # silently empty.
    if remote_file_size(remote, path) < offset:
        offset = 0
    return remote.run(
        f"/var/jb/usr/bin/tail -c +{offset + 1} {shlex.quote(path)} "
        "2>/dev/null",
        check=False, timeout=20,
    )


def active_gui_start_transaction(remote: Remote):
    """Return the exact live macos_gui start owner, never a stale PID file."""
    probe = remote.run(
        "transaction=/tmp/.macos_gui.transaction/pid; "
        "test -f \"$transaction\" || exit 0; "
        "owner=$(/var/jb/usr/bin/awk 'NR==1{print;exit}' \"$transaction\" "
        "2>/dev/null); "
        "case \"$owner\" in ''|*[!0-9]*) exit 0;; esac; "
        "command=$(ps -p \"$owner\" -o command= 2>/dev/null); "
        "case \"$command\" in "
        "*'/var/jb/usr/macOS/bin/macos_gui.sh start '*) "
        "printf '%s\\t%s\\n' \"$owner\" \"$command\";; esac",
        check=False,
    ).strip()
    if not probe or "\t" not in probe:
        return None
    owner, command = probe.split("\t", 1)
    return {"pid": int(owner), "command": command}


def reset_host_visible_fps(remote: Remote, target_pid: int,
                           timeout: float = 5.0):
    """Start an exact Host visible-frame interval for the current game PID."""
    offset = remote_file_size(remote, HOST_LOG)
    reset_url = f"macwshost://performance-reset?pid={target_pid}"
    remote.run(f"uiopen --url {shlex.quote(reset_url)}", check=False)
    deadline = time.monotonic() + timeout
    witness = ""
    while time.monotonic() < deadline:
        suffix = file_suffix(remote, HOST_LOG, offset)
        matches = re.findall(
            r"^.*performance-profile-target pid=(\d+) window=(\d+) "
            r"mode=(\d+).*$", suffix, re.MULTILINE,
        )
        if matches:
            observed_pid, window_id, mode = matches[-1]
            if int(observed_pid) != target_pid:
                raise RuntimeError(
                    "Host performance reset selected the wrong target: "
                    f"expected pid={target_pid}, observed pid={observed_pid}"
                )
            witness = next(
                line for line in reversed(suffix.splitlines())
                if "performance-profile-target" in line
            )
            return {
                "target_pid": target_pid,
                "window_id": int(window_id),
                "mode": int(mode),
                "witness": witness,
            }
        time.sleep(0.1)
    raise RuntimeError(
        "MacWSHost did not acknowledge the visible-FPS reset for "
        f"pid={target_pid}"
    )


def snapshot_host_visible_fps(remote: Remote, target_pid: int,
                              timeout: float = 8.0):
    """Export and validate the unique-direct-drawable Host FPS profile."""
    offset = remote_file_size(remote, HOST_LOG)
    remote.run("uiopen --url macwshost://performance-snapshot", check=False)
    deadline = time.monotonic() + timeout
    export_witness = ""
    while time.monotonic() < deadline:
        suffix = file_suffix(remote, HOST_LOG, offset)
        exported = [
            line for line in suffix.splitlines()
            if "performance-profile-export path=" in line
        ]
        if exported:
            export_witness = exported[-1]
            break
        time.sleep(0.1)
    if not export_witness:
        raise RuntimeError("MacWSHost did not export the visible-FPS profile")
    raw = remote.run(
        f"/var/jb/usr/bin/cat "
        f"{shlex.quote(HOST_PERFORMANCE_PROFILE)} 2>/dev/null",
        check=False,
    )
    try:
        profile = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            "MacWSHost visible-FPS profile is missing or invalid JSON"
        ) from error
    direct = profile.get("direct_drawable", {})
    target = direct.get("target")
    if not isinstance(target, dict):
        raise RuntimeError(
            "MacWSHost profile has no target direct-drawable source"
        )
    if int(target.get("owner_pid", 0)) != target_pid:
        raise RuntimeError(
            "MacWSHost visible-FPS profile belongs to the wrong process: "
            f"expected pid={target_pid}, observed={target.get('owner_pid')}"
        )
    frames = int(target.get("host_unique_frames_presented", 0))
    elapsed = float(target.get("host_visible_elapsed_s", 0.0))
    if frames < 2 or elapsed <= 0.0:
        raise RuntimeError(
            "MacWSHost did not present at least two unique game drawables "
            f"during the sample: frames={frames} elapsed={elapsed:.3f}s"
        )
    return {
        "profile": profile,
        "target": target,
        "export_witness": export_witness,
    }


def log_suffix(remote: Remote, offset: int):
    # ``offset`` is captured from this same Steam process immediately before
    # Play.  The file cannot rotate without that process exiting, so a second
    # ``stat`` child on every poll adds no correctness witness.  At Stray's
    # allocation peak the extra fork was runtime-confirmed to fail while the
    # game, overlay and append-only log were all still healthy.
    if remote.runtime_log_stream is not None:
        return remote.runtime_log_stream.snapshot_from(offset)
    return remote.run(
        f"/var/jb/usr/bin/tail -c +{offset + 1} "
        f"{shlex.quote(RUNTIME_LOG)} 2>/dev/null",
        check=False, timeout=20,
    )


def host_log_suffix(remote: Remote, offset: int):
    """Read current-run MacWSHost events without a peak-time remote fork."""
    if remote.host_log_stream is not None:
        return remote.host_log_stream.snapshot_from(offset)
    return file_suffix(remote, HOST_LOG, offset)


def ue_fatal_excerpt(text: str):
    """Preserve a bounded verbatim witness for UE's live-but-fatal spin."""
    # Prefer the originating fatal over the newest repeated "Spinning" line.
    # Runtime-confirmed by Stray PID 18629: selecting the last marker retained
    # only the one-second spin heartbeat and hid the preceding Metal error
    # `00000102` that actually explains the frozen live process.
    root_markers = tuple(
        marker for marker in UE_FATAL_MARKERS
        if marker != "Spinning after fatal error.."
    )
    position = max(
        (text.rfind(marker) for marker in root_markers), default=-1
    )
    if position < 0:
        position = text.rfind("Spinning after fatal error..")
    if position < 0:
        return None
    start = max(0, position - 700)
    end = min(len(text), position + 3000)
    return text[start:end]


def wait_for_present_floor(remote: Remote, log_offset: int, pid: int,
                           sequence_floor: int, timeout: float):
    """Require real drawable progress before changing launcher lifecycle."""
    deadline = time.monotonic() + timeout
    last_sequence = 0
    while time.monotonic() < deadline:
        suffix = log_suffix(remote, log_offset)
        fatal = ue_fatal_excerpt(suffix)
        if fatal:
            raise RuntimeError(
                "Stray reported a fatal error before the Steam UI "
                f"quiesce gate: {fatal}"
            )
        samples = present_samples(suffix)
        if samples:
            last_sequence = max(last_sequence, samples[-1]["sequence"])
        if last_sequence >= sequence_floor:
            return {
                "required_sequence": sequence_floor,
                "observed_sequence": last_sequence,
            }
        if root_pid_liveness(remote, pid) is False:
            raise RuntimeError(
                "Stray exited before the Steam UI quiesce gate"
            )
        time.sleep(0.25)
    raise RuntimeError(
        f"Stray did not reach present sequence {sequence_floor} before "
        f"Steam UI quiesce (last={last_sequence})"
    )


def wait_for_wait_trace_install(remote: Remote, offset: int,
                                timeout: float):
    """Require a current-process witness that the preserving hook landed."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        suffix = log_suffix(remote, offset)
        for line in suffix.splitlines():
            if "STRAY-WAIT missing" in line:
                raise RuntimeError(
                    "waitUntilCompleted trace hook could not be installed: " +
                    line
                )
            if "STRAY-WAIT installed" in line:
                return line
        time.sleep(0.25)
    raise RuntimeError(
        "no current-process STRAY-WAIT installation witness before timeout"
    )


def wait_trace_blocks(text: str):
    """Preserve complete begin/backtrace/end blocks from the runtime log."""
    blocks = []
    current = None
    for line in text.splitlines():
        begin = WAIT_TRACE_BEGIN_RE.search(line)
        if begin:
            current = {
                "sequence": int(begin.group("sequence")),
                "reported_frames": int(begin.group("frames")),
                "lines": [line],
                "complete": False,
            }
            continue
        if current is None:
            continue
        current["lines"].append(line)
        end = WAIT_TRACE_END_RE.search(line)
        if end and int(end.group("sequence")) == current["sequence"]:
            current["complete"] = True
            blocks.append(current)
            current = None
    if current is not None:
        blocks.append(current)
    return blocks


def submit_flag_samples(text: str):
    """Parse the exact submit flags captured beside the wait backtraces."""
    samples = []
    for match in SUBMIT_FLAGS_RE.finditer(text):
        samples.append({
            "sequence": int(match.group("sequence")),
            "flags": int(match.group("flags"), 0),
            "explicit_wait": match.group("explicit_wait") == "YES",
            "runtime_debug_level": int(match.group("debug_level")),
            "caller": match.group("caller"),
        })
    return samples


def surface_lock_blocks(text: str):
    """Preserve complete request-site stacks and measured lock latency."""
    blocks = []
    current = None
    for line in text.splitlines():
        begin = SURFACE_LOCK_BEGIN_RE.search(line)
        if begin:
            current = {
                "sequence": int(begin.group("sequence")),
                "reported_frames": int(begin.group("frames")),
                "lines": [line],
                "complete": False,
            }
            continue
        if current is None:
            continue
        current["lines"].append(line)
        end = SURFACE_LOCK_END_RE.search(line)
        if end and int(end.group("sequence")) == current["sequence"]:
            current["complete"] = True
            blocks.append(current)
            current = None
    if current is not None:
        blocks.append(current)
    return blocks


def app_input_consume_summary(lines):
    """Classify the preserving AppKit -> FMacApplication -> Slate trace."""
    text = "\n".join(lines)
    installed = "STRAY-INPUT-CONSUME installed" in text
    key_up_hook_installed = re.search(
        r"STRAY-INPUT-CONSUME installed .* slateUp=", text
    ) is not None
    appkit_down = re.search(r"APP-INPUT KEY-EVENT .* type=10\b", text) is not None
    appkit_up = re.search(r"APP-INPUT KEY-EVENT .* type=11\b", text) is not None
    mac_down = "STRAY-INPUT-CONSUME mac-key-down" in text
    slate_down = "STRAY-INPUT-CONSUME slate-key-down" in text
    slate_up = "STRAY-INPUT-CONSUME slate-key-up" in text
    slate_down_handled = re.search(
        r"STRAY-INPUT-CONSUME slate-key-down .* result=YES", text
    ) is not None
    slate_up_handled = re.search(
        r"STRAY-INPUT-CONSUME slate-key-up .* result=YES", text
    ) is not None
    if not installed:
        classification = "hook-not-installed"
    elif appkit_down and not mac_down:
        classification = "dropped-before-fmac-key-down"
    elif mac_down and not slate_down:
        classification = "dropped-between-fmac-and-slate-key-down"
    elif slate_down and not appkit_up:
        classification = "key-up-not-posted-by-appkit-bridge"
    elif appkit_up and key_up_hook_installed and not slate_up:
        classification = "key-up-dropped-before-slate"
    elif slate_up:
        classification = (
            "reached-slate-key-up-handled" if slate_up_handled else
            "reached-slate-key-up-unhandled"
        )
    elif slate_down and not key_up_hook_installed:
        classification = "key-up-observer-not-installed"
    elif slate_down:
        classification = "reached-slate-key-down-only"
    else:
        classification = "no-key-consumption-witness"
    return {
        "classification": classification,
        "hook_installed": installed,
        "key_up_hook_installed": key_up_hook_installed,
        "appkit_key_down": appkit_down,
        "appkit_key_up": appkit_up,
        "fmac_key_down": mac_down,
        "slate_key_down": slate_down,
        "slate_key_down_handled": slate_down_handled,
        "slate_key_up": slate_up,
        "slate_key_up_handled": slate_up_handled,
    }


def wait_for_steam_library_ready(remote: Remote, login_offset: int,
                                 webhelper_js_offset: int,
                                 webhelper_offset: int, timeout: float,
                                 checkpoint=None):
    """Require the current Steam login and CEF Library stores to finish."""
    deadline = time.monotonic() + timeout
    started = time.monotonic()
    login = ""
    webhelper_js = ""
    webhelper = ""
    witness = {}
    while time.monotonic() < deadline:
        if checkpoint:
            checkpoint("steam-library-ready")
        login = file_suffix(remote, STEAM_LOGIN_LOG, login_offset)
        webhelper_js = file_suffix(
            remote, STEAM_WEBHELPER_JS_LOG, webhelper_js_offset
        )
        webhelper = file_suffix(remote, STEAM_WEBHELPER_LOG, webhelper_offset)
        login_start = login.rfind("Starting login")
        login_success = login.rfind("SetLoginState: Success - OK")
        connected = webhelper_js.rfind(
            "WebUITransportStore: Connection status: connected"
        )
        disconnected = webhelper_js.rfind(
            "WebUITransportStore: Connection status: disconnected"
        )
        library_ui = webhelper_js.rfind("async LibraryUIStore")
        # Steam build 1785799196 no longer emits LibraryUIStore on every warm
        # or cold launch.  AppStore + FriendsUI is only the data-store
        # boundary: r14 runtime-confirmed those lines at 23:10:34 while the
        # window still showed only the spinner; the interactive 1194x594
        # SteamBrowser surface appeared at 23:11:52.  Require that later CEF
        # surface event as the actual input-readiness boundary.
        app_store = webhelper_js.rfind("SteamApp Init - AppStore")
        friends_ready = webhelper_js.rfind("FriendsUI ReadyToRender")
        main_browser_surface = webhelper.rfind(
            "SteamBrowser-'data:text/': WasHidden 0"
        )
        stores_ready = (
            library_ui > connected or
            (app_store > connected and friends_ready > app_store)
        )
        library_ui_ready = (
            connected >= 0 and
            stores_ready and main_browser_surface > connected
        )
        witness = {
            "login_started": login_start >= 0,
            "login_success_after_start": (
                login_start >= 0 and login_success > login_start
            ),
            "transport_currently_connected": (
                connected >= 0 and connected > disconnected
            ),
            "library_ui_after_connect": library_ui > connected >= 0,
            "app_store_after_connect": app_store > connected >= 0,
            "friends_ui_after_app_store": friends_ready > app_store >= 0,
            "main_browser_surface_after_connect": (
                main_browser_surface > connected >= 0
            ),
            "library_ready": library_ui_ready,
        }
        if (witness["login_started"] and
                witness["login_success_after_start"] and
                witness["transport_currently_connected"] and
                witness["library_ready"]):
            return {
                "wait_seconds": time.monotonic() - started,
                "witness": witness,
                "login_tail": login[-3000:],
                "webhelper_js_tail": webhelper_js[-5000:],
                "webhelper_tail": webhelper[-5000:],
            }
        time.sleep(0.5)
    raise RuntimeError(
        "Steam main window exists but its current login/Library stores are "
        "not ready: " + json.dumps({
            "witness": witness,
            "login_tail": login[-3000:],
            "webhelper_js_tail": webhelper_js[-5000:],
            "webhelper_tail": webhelper[-5000:],
        }, ensure_ascii=False)
    )


def wait_for_launch_outcome(remote: Remote, offset: int, timeout: float,
                            checkpoint=None, cloud_wait_floor: int = -1,
                            kicking_wait_floor: int = -1):
    deadline = time.monotonic() + timeout
    suffix = ""
    pid = 0
    last_launch_task = ""
    launch_task_since = time.monotonic()
    while time.monotonic() < deadline:
        if checkpoint:
            checkpoint("steam-play-launch")
        suffix = log_suffix(remote, offset)
        # The current-offset spawn witness names the exact child PID and is
        # emitted before the game allocation peak.  Prefer it over spawning a
        # fresh `ps`; retain the process scan only as a pre-witness fallback.
        spawned = list(re.finditer(
            r"\[MacWSSteamProcess\] NSWorkspace fallback executable=[^\n]*"
            r"spawn-error=0 pid=(\d+)\b",
            suffix,
        ))
        if spawned:
            pid = int(spawned[-1].group(1))
        elif pid <= 1:
            try:
                pid = game_pid(remote) or pid
            except RuntimeError:
                # A missing process-table allocation is not evidence that the
                # game exited. Keep following the already-open Steam log.
                pass
        # Steam 1785799196 uses two distinct reasons for the same visible,
        # blocking "Play anyway" dialog.  ``syncfailed`` is the ordinary
        # upload failure.  ``pendingcloudsessions`` is emitted when another
        # machine currently owns the account's game session.  Runtime-
        # confirmed by steam-launch-failure.png and console-linux.txt from
        # /tmp/macws-stray-ipad-r15-static-fair on 2026-08-25.  Both require
        # an explicit user-response gesture; neither is an unchanged cloud
        # task that can be repaired by replacing the Steam process.
        cloud_waits = list(re.finditer(
            r'LaunchApp waiting for user response to SynchronizingCloud '
            r'"(?:syncfailed|pendingcloudsessions)"',
            suffix,
        ))
        cloud_wait = cloud_waits[-1].start() if cloud_waits else -1
        cloud_advanced = max(
            suffix.rfind('LaunchApp continues with user response "IgnoreCloud"'),
            suffix.rfind("LaunchApp changed task to SynchronizingStats"),
            suffix.rfind("LaunchApp changed task to ShowInterstitials"),
            suffix.rfind("LaunchApp changed task to CreatingProcess"),
            suffix.rfind("LaunchApp changed task to Completed"),
            suffix.rfind("LaunchApp changed task to Failed"),
        )
        if (cloud_wait >= 0 and cloud_wait > cloud_advanced and
                cloud_wait > cloud_wait_floor):
            return {"outcome": "cloud_sync_failed", "pid": pid,
                    "log": suffix[-6000:]}
        # Runtime-confirmed by steam-launch-failure.png from the
        # 1440x900/High MetalFX diagnostic on 2026-08-27: after cloud sync,
        # Steam can separately stop at KickingOtherSession and display
        # "You are logged in on another computer already playing Stray" with
        # visible Continue/Cancel actions. This is not a cloud warning and the
        # required affirmative label is Continue, so expose a distinct state.
        kicking_waits = list(re.finditer(
            r'LaunchApp waiting for user response to KickingOtherSession '
            r'"[^"]*"',
            suffix,
        ))
        kicking_wait = kicking_waits[-1].start() if kicking_waits else -1
        kicking_advanced = max(
            suffix.rfind(
                'LaunchApp continues with user response "KickingOtherSession"'
            ),
            suffix.rfind("LaunchApp changed task to CreatingProcess"),
            suffix.rfind("LaunchApp changed task to Completed"),
            suffix.rfind("LaunchApp changed task to Failed"),
        )
        if (kicking_wait >= 0 and kicking_wait > kicking_advanced and
                kicking_wait > kicking_wait_floor):
            return {"outcome": "kicking_other_session", "pid": pid,
                    "log": suffix[-6000:]}
        # Runtime-confirmed via
        # /tmp/macws-stray-ipad-direct-r13-ws-idle/result.json: ActionID 1
        # remained at `changed task to SynchronizingCloud` for the full
        # 120-second launch window.  It emitted neither Steam's explicit
        # `syncfailed` user-response state nor any later LaunchApp task, and
        # the exact Steam window's CEF content was black.  Waiting longer is
        # not game startup.  Surface this distinct recoverable owner failure
        # after a bounded unchanged-task interval so the caller can replace
        # the stuck Steam generation without bypassing cloud or game launch.
        tasks = list(re.finditer(
            r"LaunchApp changed task to ([A-Za-z0-9_]+)", suffix
        ))
        current_launch_task = tasks[-1].group(1) if tasks else ""
        if current_launch_task != last_launch_task:
            last_launch_task = current_launch_task
            launch_task_since = time.monotonic()
        if (current_launch_task == "SynchronizingCloud" and
                time.monotonic() - launch_task_since >= 30.0):
            return {"outcome": "cloud_sync_stalled", "pid": pid,
                    "task": current_launch_task,
                    "unchanged_seconds": (
                        time.monotonic() - launch_task_since
                    ),
                    "log": suffix[-6000:]}
        if "LaunchApp failed with AppError_49" in suffix:
            return {"outcome": "app_error_49", "pid": 0,
                    "log": suffix[-6000:]}
        if "LaunchApp failed with AppError_46" in suffix:
            return {"outcome": "app_error_46", "pid": pid,
                    "log": suffix[-6000:]}
        if "LaunchApp changed task to Failed" in suffix:
            return {"outcome": "failed", "pid": pid,
                    "log": suffix[-6000:]}
        if (pid > 1 and
                "LaunchApp changed task to Completed" in suffix):
            return {"outcome": "started", "pid": pid,
                    "log": suffix[-6000:]}
        time.sleep(0.25)
    return {"outcome": "timeout", "pid": pid, "log": suffix[-6000:]}


def ensure_steam_fps_overlay_top_left(remote: Remote):
    editor = r'''
import os
import re
import sys
import tempfile

paths = sys.argv[1:]
keys = (
    "EnableGameOverlay",
    "InGameOverlayShowFPSCorner",
    "InGameOverlayShowFPSContrast",
    "overlay_fps_counter_corner",
)
changed = []
for path in paths:
    with open(path, "r", encoding="utf-8") as stream:
        text = stream.read()
    original = text
    counts = {}
    for key in keys:
        pattern = re.compile(r'("' + re.escape(key) + r'"\s+")[^"]*(")')
        text, counts[key] = pattern.subn(r'\g<1>1\2', text)
    if not counts["InGameOverlayShowFPSCorner"]:
        raise SystemExit("missing FPS corner setting in " + path)
    if text != original:
        descriptor, temporary = tempfile.mkstemp(
            prefix=".macws-steam-fps-", dir=os.path.dirname(path)
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, os.stat(path).st_mode)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        changed.append(path)
print("changed=" + str(len(changed)))
'''
    return remote.sudo(
        f"/var/jb/usr/bin/python3 -c {shlex.quote(editor)} "
        f"{shlex.quote(STEAM_CONFIG)} {shlex.quote(STEAM_LOCAL_CONFIG)}"
    ).strip()


def steam_fps_overlay_config(remote: Remote):
    values = {}
    for label, path in (("global", STEAM_CONFIG),
                        ("account", STEAM_LOCAL_CONFIG)):
        text = remote.run(
            f"/var/jb/usr/bin/grep -E "
            f"{shlex.quote('EnableGameOverlay|InGameOverlayShowFPSCorner|InGameOverlayShowFPSContrast')} "
            f"{shlex.quote(path)} 2>/dev/null",
            check=False,
        )
        values[label] = text.splitlines()
    combined = "\n".join(line for lines in values.values() for line in lines)
    corner_values = re.findall(
        r'"InGameOverlayShowFPSCorner"\s+"([^"]+)"', combined
    )
    required = {
        "overlay_enabled": re.search(
            r'"EnableGameOverlay"\s+"1"', combined
        ) is not None,
        "fps_corner_top_left": bool(corner_values) and
            set(corner_values) == {"1"},
        "fps_high_contrast": re.search(
            r'"InGameOverlayShowFPSContrast"\s+"1"', combined
        ) is not None,
    }
    if not all(required.values()):
        raise RuntimeError(
            "Steam FPS overlay config is incomplete: " +
            json.dumps({"required": required, "lines": values},
                       ensure_ascii=False)
        )
    return {
        "required": required,
        "corner_values": corner_values,
        "lines": values,
    }


def wait_for_steam_overlay(remote: Remote, game_process: int, log_offset: int,
                           timeout: float = 10.0,
                           allow_suppressed_ui: bool = False):
    deadline = time.monotonic() + timeout
    last_process = ""
    last_log = ""
    while time.monotonic() < deadline:
        last_log = log_suffix(remote, log_offset)
        started = re.search(
            rf"GameOverlay: started '(?P<path>[^']*gameoverlayui)' "
            rf"\(pid (?P<pid>\d+)\) for game process {game_process}\b",
            last_log,
        )
        if started:
            overlay_pid = int(started.group("pid"))
            last_process = f"{overlay_pid} {started.group('path')}"
            return {"pid": overlay_pid, "process": last_process,
                    "runtime_witness": started.group(0)}
        if allow_suppressed_ui:
            injected = re.search(
                r"\[MacWSSteamProcess\] NSWorkspace runtime launch "
                r"[^\n]*insert=[^\n]*"
                r"macws-gameoverlayrenderer-arm64\.dylib[^\n]*",
                last_log,
            )
            spawned = re.search(
                rf"\[MacWSSteamProcess\] NSWorkspace fallback "
                rf"[^\n]*spawn-error=0 pid={game_process}\b[^\n]*",
                last_log,
            )
            if injected and spawned and game_pid(remote) == game_process:
                return {
                    "pid": 0,
                    "process": "suppressed-by-SteamNoOverlayUIDrawing",
                    "runtime_witness": (
                        injected.group(0) + "\n" + spawned.group(0)
                    ),
                    "diagnostic_only": True,
                }
        time.sleep(0.25)
    raise RuntimeError(
        "Steam did not start gameoverlayui for this Stray process; "
        f"process={last_process or '(none)'} log=" + last_log[-3000:]
    )


def wait_for_native_steam_quiesce(remote: Remote, game_process: int,
                                  timeout: float = 8.0):
    """Require the fork-free watchdog's bounded Steam-UI transaction."""
    deadline = time.monotonic() + timeout
    last = ""
    pattern = re.compile(
        rf"steam-ui transaction=game-active game={game_process} "
        r"suspended=(\d+) retired=(\d+)"
    )
    while time.monotonic() < deadline:
        last = remote.run(
            f"/var/jb/usr/bin/tail -n 80 "
            f"{shlex.quote(STRAY_SAFETY_LOG)} 2>/dev/null",
            check=False,
        )
        matches = list(pattern.finditer(last))
        if matches:
            suspended = int(matches[-1].group(1))
            retired = int(matches[-1].group(2))
            if suspended < 1 and retired < 1:
                raise RuntimeError(
                    "native watchdog observed Stray/overlay but found no "
                    "Steam UI process to quiesce"
                )
            return {
                "suspended_count": suspended,
                "retired_count": retired,
                "runtime_witness": matches[-1].group(0),
                "fork_free": True,
            }
        time.sleep(0.20)
    raise RuntimeError(
        "native watchdog did not quiesce Steam UI after overlay attach: " +
        last[-2000:]
    )


def verify_no_overlay_injection(remote: Remote, game_process: int,
                                log_offset: int):
    """Prove the diagnostic game process did not map Valve's renderer."""
    time.sleep(1.0)
    suffix = log_suffix(remote, log_offset)
    diagnostic = re.search(
        r"\[MacWSSteamProcess\] DIAGNOSTIC Stray overlay injection "
        r"disabled[^\n]*", suffix,
    )
    launch = re.search(
        r"\[MacWSSteamProcess\] NSWorkspace runtime launch [^\n]*",
        suffix,
    )
    maps = remote.sudo(
        "/var/jb/usr/macOS/bin/run_bash.sh -c " + shlex.quote(
            f"/usr/bin/vmmap {game_process} 2>/dev/null | "
            "/usr/bin/grep -E 'gameoverlayrenderer|steamloader' || true"
        ),
        check=False, timeout=20,
    )
    overlay_mapped = "gameoverlayrenderer" in maps
    loader_mapped = "steamloader" in maps
    if not diagnostic or not launch or overlay_mapped or not loader_mapped:
        raise RuntimeError(
            "no-overlay diagnostic invariant failed: " +
            json.dumps({
                "diagnostic": diagnostic.group(0) if diagnostic else "",
                "launch": launch.group(0) if launch else "",
                "maps": maps[-3000:],
            }, ensure_ascii=False)
        )
    return {
        "diagnostic_only": True,
        "injection": "gameoverlayrenderer-disabled",
        "steamloader_mapped": loader_mapped,
        "gameoverlayrenderer_mapped": overlay_mapped,
        "runtime_witness": diagnostic.group(0) + "\n" + launch.group(0),
        "maps": maps.strip(),
    }


def ios_console_locked(remote: Remote):
    """Return SpringBoard's lock state, or unknown without its direct probe."""
    probe = remote.run(
        f"{LOCK_STATE_PROBE} 2>/dev/null", check=False, timeout=10
    )
    match = re.search(r"\blocked=([01])\b", probe)
    if match:
        return match.group(1) == "1", probe.strip()
    # IOConsoleLocked can remain Yes after SpringBoard unlocks in the dual-
    # WindowServer setup. It is only a diagnostic witness, not a lock verdict:
    # treating a stale Yes as authoritative blocked an unlocked iPad on
    # 2026-09-21. Let the real FrontBoard/Scene transaction decide instead.
    output = remote.run(
        "ioreg -l 2>/dev/null | "
        "awk '/\"IOConsoleLocked\" =/ {print; exit}'",
        check=False, timeout=10,
    )
    if "= Yes" in output:
        return None, "stale-prone-ioreg " + output.strip()
    if "= No" in output:
        return False, "fallback-ioreg " + output.strip()
    return None, "lock-state-unavailable " + output.strip()


def workspace_preflight(remote: Remote, timeout: float = 8.0,
                        startup_timeout: float = 600.0, checkpoint=None):
    locked, lock_witness = ios_console_locked(remote)
    if locked:
        raise RuntimeError(
            "iPadOS console is locked; unlock the iPad once before the run "
            "(FrontBoard otherwise creates only an ActivePrewarm Host): " +
            lock_witness
        )
    offset = remote_file_size(remote, HOST_LOG)
    request_time = time.time()
    processes = "\n".join(process_table(remote, "pid=,state=,command="))
    if "MacWSHost.app/MacWSHost" not in processes:
        # A bundle activation is the public cold-start transaction.  Follow it
        # with the URL so an already-live Scene and a fresh Scene use the same
        # workspace request path.
        remote.run(
            "uiopen --bundleid com.macwsguide.host", check=False
        )
    remote.run("uiopen --url macwshost://enter-workspace", check=False)
    deadline = time.monotonic() + timeout
    processes = ""
    while time.monotonic() < deadline:
        if checkpoint:
            checkpoint("workspace-host-activation")
        processes = "\n".join(
            process_table(remote, "pid=,state=,command=")
        )
        if "MacWSHost.app/MacWSHost" in processes:
            break
        time.sleep(0.25)
    def live_components(process_list: str):
        current_host_state = ""
        for process_line in process_list.splitlines():
            process_fields = process_line.strip().split(None, 2)
            if (len(process_fields) == 3 and
                    "MacWSHost.app/MacWSHost" in process_fields[2]):
                current_host_state = process_fields[1]
                break
        return {
            "MacWSHost": "MacWSHost.app/MacWSHost" in process_list,
            "MacWSHost-runnable": bool(current_host_state) and
                not current_host_state.startswith("T"),
            "WindowServer":
                "SkyLight.framework/Resources/WindowServer" in process_list,
            "macwsinputd": "/macwsinputd" in process_list,
            "input_socket": remote.run(
                "test -S /var/mnt/rootfs/private/tmp/macws_host_input.sock; "
                "echo $?", check=False,
            ).strip().endswith("0"),
        }

    required = live_components(processes)
    missing = [name for name, present in required.items() if not present]
    cold_start_requested = bool(missing)
    cold_start_started = time.monotonic()
    cold_start_trigger = "already-running"
    if missing:
        # Reuse an exact in-flight macos_gui start instead of queueing the
        # public start URL behind it. Runtime-confirmed on 2026-08-29: a Host
        # restoration start completed after 112 seconds; the runner had
        # already queued a second start, which immediately repeated cleanup,
        # trust and service initialization and then owned the GUI lease when
        # game launch was ready.  The transaction PID plus exact current
        # command is the serialization authority, not process uptime alone.
        existing_start = active_gui_start_transaction(remote)
        if existing_start:
            cold_start_trigger = "reuse-live-gui-transaction"
        else:
            # enter-workspace owns only UIKit presentation. After a deliberate
            # cleanup or a hostd restart, use the app's existing public start
            # transaction to restore WindowServer/inputd/displayd before
            # judging the fullscreen surface. This is the same operation as
            # the primary UI button, not a launchd/process bypass.
            remote.run("uiopen --url macwshost://start", check=False)
            cold_start_trigger = "public-start-url"
        startup_deadline = time.monotonic() + max(startup_timeout, timeout)
        next_failure_probe = time.monotonic()
        while time.monotonic() < startup_deadline:
            if checkpoint:
                checkpoint("workspace-cold-start")
            now = time.monotonic()
            if now >= next_failure_probe:
                # The public control transaction publishes a terminal failure
                # in MacWSHost.log.  Treat that as authoritative instead of
                # waiting the remainder of the ten-minute component timeout.
                # Runtime witness (2026-08-21):
                #   control-status ... busy=NO phase=操作失败
                #   error=GUI 启动脚本失败（退出码 1）
                host_suffix = file_suffix(remote, HOST_LOG, offset)
                for line in reversed(host_suffix.splitlines()):
                    try:
                        timestamp = float(line.split(None, 1)[0])
                    except (IndexError, ValueError):
                        continue
                    if timestamp < request_time - 0.5:
                        break
                    failure = re.search(
                        r"control-status .*\bbusy=NO\b.*"
                        r"\bphase=操作失败(?:\s+error=(.*))?",
                        line,
                    )
                    if failure:
                        detail = (failure.group(1) or "unspecified").strip()
                        raise RuntimeError(
                            "workspace public start transaction failed: " +
                            detail + " | runtime witness: " + line
                        )
                next_failure_probe = now + 1.0
            processes = "\n".join(
                process_table(remote, "pid=,state=,command=")
            )
            required = live_components(processes)
            missing = [
                name for name, present in required.items() if not present
            ]
            if not missing:
                break
            time.sleep(0.25)
    if missing:
        raise RuntimeError(
            "workspace preflight missing live components: " +
            ", ".join(missing)
        )
    # A cold start can finish after the first presentation request.  Reassert
    # once all producer endpoints exist so the following snapshot belongs to
    # the newly restored workspace, not the stopped stack's retained surface.
    remote.run("uiopen --url macwshost://enter-workspace", check=False)
    time.sleep(0.15)
    remote.run("uiopen --url macwshost://performance-snapshot", check=False)
    # Runtime-confirmed after cleanup_all on 2026-08-21: producer processes
    # existed about three seconds before DisplayStream published its first
    # IOSurface, after the old eight-second presentation deadline had already
    # expired.  Give only a genuine cold start a bounded 30-second first-frame
    # allowance; a retained-live workspace still has the strict short probe.
    presentation_timeout = (
        min(startup_timeout, max(30.0, timeout))
        if cold_start_requested else timeout
    )
    deadline = time.monotonic() + presentation_timeout
    next_snapshot = time.monotonic() + 0.75
    suffix = ""
    while time.monotonic() < deadline:
        if checkpoint:
            checkpoint("workspace-first-frame")
        if remote_file_size(remote, HOST_LOG) < offset:
            offset = 0
        suffix = remote.run(
            f"/var/jb/usr/bin/tail -c +{offset + 1} "
            f"{shlex.quote(HOST_LOG)} 2>/dev/null", check=False,
        )
        current_lines = []
        for line in suffix.splitlines():
            try:
                timestamp = float(line.split(None, 1)[0])
            except (IndexError, ValueError):
                continue
            if timestamp >= request_time - 0.5:
                current_lines.append(line)
        current = "\n".join(current_lines)
        reasserted = "scene-fullscreen foreground-reassert requested=YES" in current
        controls_hidden = (
            "workspace-mode recovery controls-hidden=YES" in current
        )
        fullscreen = re.search(
            r"scene-maximization UIKit-observation .*fills-screen=YES", current
        ) is not None
        # A snapshot line by itself is not a frame-readiness witness.  On a
        # cold Host activation the URL can race the DisplayStream's first
        # IOSurface and report base-stream/sequence/surface all zero; accepting
        # it left automation driving a visually blank desktop container.
        snapshot = re.search(
            r"display-performance-snapshot reason=url-control "
            r"base-stream=[1-9][0-9]* base-sequence=[1-9][0-9]* "
            r"base-surface=[1-9][0-9]*",
            current,
        ) is not None
        # A current-generation first-frame callback is itself a direct
        # IOSurface witness and includes the nonzero frame geometry.  During a
        # cold post-install start, MacWSHost can be busy servicing the user's
        # queued Catalyst path while performance-snapshot URL events wait on
        # its control transaction.  Runtime witness 2026-08-21:
        #   display-stream first-frame ... status=2388×1668 ...
        # Accept that actual callback, but never the earlier zero-valued
        # snapshot which motivated this guard.
        first_frame = re.search(
            r"display-stream first-frame revalidate-input .*"
            r"status=[1-9][0-9]*[×x][1-9][0-9]*",
            current,
        ) is not None
        if (reasserted and controls_hidden and fullscreen and
                (snapshot or first_frame)):
            return {
                "components": required,
                "cold_start_requested": cold_start_requested,
                "cold_start_trigger": cold_start_trigger,
                "cold_start_wait_seconds": round(
                    time.monotonic() - cold_start_started, 3
                ) if cold_start_requested else 0.0,
                "console_locked": locked,
                "console_lock_witness": lock_witness,
                "reasserted": True,
                "controls_hidden": True,
                "fills_screen": True,
                "performance_snapshot": snapshot,
                "first_frame_callback": first_frame,
                "log_witness": [
                    line for line in current_lines
                    if ("scene-fullscreen foreground-reassert" in line or
                        "workspace-mode recovery controls-hidden" in line or
                        "scene-maximization UIKit-observation" in line or
                        "display-performance-snapshot reason=url-control" in line)
                ][-3:],
            }
        if time.monotonic() >= next_snapshot:
            remote.run(
                "uiopen --url macwshost://performance-snapshot", check=False
            )
            next_snapshot = time.monotonic() + 0.75
        time.sleep(0.25)
    evidence = " | ".join(current_lines[-8:]) if suffix else "(empty log suffix)"
    raise RuntimeError(
        "workspace did not reassert fullscreen and a live frame snapshot: " +
        evidence
    )


def present_samples(text: str):
    result = []
    for match in PRESENT_RE.finditer(text):
        result.append({
            "sequence": int(match.group("sequence")),
            "total_seconds": float(match.group("total")),
            "average_fps": float(match.group("average")),
            "window_fps": float(match.group("window")),
            "texture_width": int(match.group("width")),
            "texture_height": int(match.group("height")),
            "pixel_format": (
                int(match.group("format")) if match.group("format") else None
            ),
        })
    return result


def metalfx_runtime_summary(text: str, requested_percentage: int):
    """Verify the scaler from real encode calls, not a transient INI key."""
    encodes = []
    for match in METALFX_ENCODE_RE.finditer(text):
        output_width = int(match.group("output_width"))
        output_height = int(match.group("output_height"))
        input_width = int(match.group("input_width"))
        input_height = int(match.group("input_height"))
        if output_width <= 0 or output_height <= 0:
            continue
        encodes.append({
            "sequence": int(match.group("sequence")),
            "class": match.group("class"),
            "input": [input_width, input_height],
            "output": [output_width, output_height],
            "width_percentage": input_width / output_width * 100.0,
            "height_percentage": input_height / output_height * 100.0,
        })
    matching = [
        item for item in encodes
        if item["class"].startswith("_MFX") and
        abs(item["width_percentage"] - requested_percentage) <= 2.0 and
        abs(item["height_percentage"] - requested_percentage) <= 2.0
    ]
    return {
        "requested_percentage": requested_percentage,
        "encode_count": len(encodes),
        "matching_encode_count": len(matching),
        "active": bool(matching),
        "first_matching_encode": matching[0] if matching else None,
        "witness": (
            "real _MFX* encode with requested input/output ratio"
            if matching else "no conforming MetalFX encode"
        ),
    }


def cumulative_timing_window(text: str, pattern: re.Pattern,
                             sequence_floor: int):
    """Remove startup history from a cumulative drawable timing counter."""
    records = []
    for match in pattern.finditer(text):
        record = {
            "count": int(match.group("count")),
            "average_ms": float(match.group("average")),
            "maximum_ms_cumulative": float(match.group("maximum")),
            "current_ms": float(match.group("current")),
        }
        if "slow8" in match.groupdict():
            record["slow8"] = int(match.group("slow8"))
            record["slow16"] = int(match.group("slow16"))
        records.append(record)
    baseline = next(
        (item for item in reversed(records)
         if item["count"] <= sequence_floor), None
    )
    final = records[-1] if records else None
    if not baseline or not final or final["count"] <= baseline["count"]:
        return {"baseline": baseline, "final": final, "interval_count": 0}
    interval_count = final["count"] - baseline["count"]
    interval_total = (
        final["average_ms"] * final["count"] -
        baseline["average_ms"] * baseline["count"]
    )
    result = {
        "baseline": baseline,
        "final": final,
        "interval_count": interval_count,
        "interval_average_ms": interval_total / interval_count,
    }
    if "slow8" in final:
        result["interval_slow8"] = final["slow8"] - baseline["slow8"]
        result["interval_slow16"] = final["slow16"] - baseline["slow16"]
    return result


def fps_summary(samples: list[dict]):
    if not samples:
        return {"count": 0}
    rates = [sample["window_fps"] for sample in samples]
    midpoint = max(1, len(rates) // 2)
    first_half = rates[:midpoint]
    second_half = rates[midpoint:] or rates[-1:]
    first_median = statistics.median(first_half)
    second_median = statistics.median(second_half)
    return {
        "count": len(rates),
        "mean_window_fps": statistics.fmean(rates),
        "median_window_fps": statistics.median(rates),
        "minimum_window_fps": min(rates),
        "maximum_window_fps": max(rates),
        "first_half_median_fps": first_median,
        "second_half_median_fps": second_median,
        "second_half_change_percent": (
            (second_median - first_median) / first_median * 100.0
            if first_median > 0 else None
        ),
        "last_cumulative_fps": samples[-1]["average_fps"],
        "texture": [samples[-1]["texture_width"],
                    samples[-1]["texture_height"]],
        "pixel_format": samples[-1]["pixel_format"],
    }


def throttle_summary(thermal_samples: list[dict], fps: dict,
                     perf_summary: dict):
    temperatures = [
        sample["temperature_c"] for sample in thermal_samples
        if sample["temperature_c"] is not None
    ]
    states = [sample["state"] for sample in thermal_samples]
    temperature_rise = (
        temperatures[-1] - temperatures[0] if len(temperatures) >= 2
        else None
    )
    fps_change = fps.get("second_half_change_percent")
    intervals = perf_summary.get("intervals", [])
    # Recount reports Performance and Efficiency as distinct counter domains.
    # Never compare the first interval from one domain with the last interval
    # from the other: that produced a repeatable but meaningless ~-30% value.
    # Prefer the Performance domain because Stray's busy render/game threads
    # are scheduled there in the captured runs; fall back only if unavailable.
    available_levels = {
        item["level"] for item in intervals
        if item["effective_cycle_rate_ghz"] > 0
    }
    cycle_rate_level = (
        "Performance" if "Performance" in available_levels else
        sorted(available_levels)[0] if available_levels else None
    )
    cycle_rates = [
        item["effective_cycle_rate_ghz"] for item in intervals
        if item["effective_cycle_rate_ghz"] > 0 and
        item["level"] == cycle_rate_level
    ]
    cycle_rate_change = None
    if len(cycle_rates) >= 2 and cycle_rates[0] > 0:
        cycle_rate_change = (
            (cycle_rates[-1] - cycle_rates[0]) / cycle_rates[0] * 100.0
        )
    pressure = any(state not in ("nominal", "unknown") for state in states)
    correlated_drop = (
        temperature_rise is not None and temperature_rise >= 2.0 and
        fps_change is not None and fps_change <= -10.0
    )
    if pressure:
        classification = "runtime-confirmed-thermal-pressure"
    elif correlated_drop:
        classification = "THEORY-temperature-correlated-fps-drop"
    else:
        classification = "not-observed-in-bounded-sample"
    return {
        "classification": classification,
        "thermal_states": states,
        "minimum_temperature_c": min(temperatures) if temperatures else None,
        "maximum_temperature_c": max(temperatures) if temperatures else None,
        "temperature_rise_c": temperature_rise,
        "fps_second_half_change_percent": fps_change,
        "cpu_cycle_rate_level": cycle_rate_level,
        "cpu_cycle_rate_change_percent": cycle_rate_change,
        "note": ("Only iPadOS non-nominal thermal state is direct throttle "
                 "pressure evidence; temperature/FPS correlation is labeled "
                 "THEORY and the CPU counter is not a GPU clock reading."),
    }


def cpu_snapshot(remote: Remote, game_process: int, process_rows=None):
    rows = []
    if process_rows is None:
        process_rows = process_table(
            remote, "pid=,uid=,pcpu=,command="
        )
    for line in process_rows:
        fields = line.strip().split(None, 3)
        if len(fields) != 4:
            continue
        try:
            pid = int(fields[0])
            percent = float(fields[2])
        except ValueError:
            continue
        command = fields[3]
        if not any(token in command for token in (
                GAME_NAME, "WindowServer", "MacWSHost.app/MacWSHost",
                "macwsdisplayd", "steam_osx",
                "Steam Helper.app/Contents/MacOS/Steam Helper")):
            continue
        label = ("Stray" if pid == game_process else
                 "WindowServer" if "WindowServer" in command else
                 "MacWSHost" if "MacWSHost.app/MacWSHost" in command else
                 "macwsdisplayd" if "macwsdisplayd" in command else
                 "Steam Helper" if "Steam Helper.app" in command else
                 "steam_osx")
        rows.append({"pid": pid, "cpu_percent": percent, "label": label})
    return rows


def perf_level_snapshot(remote: Remote, game_process: int):
    """Read cumulative XNU CPU counters without perturbing the game thread."""
    raw = remote.sudo(
        f"{PERF_LEVEL_PROBE} {game_process}", check=False, timeout=10
    ).strip()
    header = re.search(
        r"process_perf_levels pid=(\d+).*?read_threads=(\d+).*?"
        r"failed_threads=(\d+).*?levels=(\d+).*?"
        r"timebase_numer=(\d+).*?timebase_denom=(\d+)", raw,
    )
    levels = []
    for match in re.finditer(
        r"perf_level index=(\d+) name=(\S+) instructions=(\d+) "
        r"cycles=(\d+) user_time_mach=(\d+) system_time_mach=(\d+) "
        r"energy_nj=(\d+)", raw,
    ):
        levels.append({
            "index": int(match.group(1)),
            "name": match.group(2),
            "instructions": int(match.group(3)),
            "cycles": int(match.group(4)),
            "user_time_mach": int(match.group(5)),
            "system_time_mach": int(match.group(6)),
            "energy_nj": int(match.group(7)),
        })
    return {
        "time": time.time(),
        "available": bool(header and levels),
        "read_threads": int(header.group(2)) if header else 0,
        "failed_threads": int(header.group(3)) if header else 0,
        "timebase_numer": int(header.group(5)) if header else 0,
        "timebase_denom": int(header.group(6)) if header else 0,
        "levels": levels,
        "raw": raw,
    }


def perf_level_summary(snapshots: list[dict]):
    """Derive interval active-cycle rates, a CPU downclock witness proxy."""
    intervals = []
    for before, after in zip(snapshots, snapshots[1:]):
        if not before["available"] or not after["available"]:
            continue
        old = {level["index"]: level for level in before["levels"]}
        for level in after["levels"]:
            previous = old.get(level["index"])
            if not previous:
                continue
            cycles = level["cycles"] - previous["cycles"]
            active_ticks = (
                level["user_time_mach"] - previous["user_time_mach"] +
                level["system_time_mach"] - previous["system_time_mach"]
            )
            if cycles < 0 or active_ticks <= 0:
                continue
            # XNU recount time is in mach absolute ticks.  This is an average
            # active CPU cycle rate for the process, not a GPU-frequency
            # reading and not a claim about a particular core's requested P-
            # state.
            ghz = (cycles / active_ticks * after["timebase_denom"] /
                   after["timebase_numer"])
            intervals.append({
                "start": before["time"],
                "end": after["time"],
                "level": level["name"],
                "cycles": cycles,
                "active_time_mach": active_ticks,
                "effective_cycle_rate_ghz": ghz,
            })
    rates = [item["effective_cycle_rate_ghz"] for item in intervals]
    by_level = {}
    for item in intervals:
        by_level.setdefault(item["level"], []).append(
            item["effective_cycle_rate_ghz"]
        )
    level_summaries = {}
    for level, level_rates in sorted(by_level.items()):
        change = None
        if len(level_rates) >= 2 and level_rates[0] > 0:
            change = ((level_rates[-1] - level_rates[0]) /
                      level_rates[0] * 100.0)
        level_summaries[level] = {
            "count": len(level_rates),
            "minimum_effective_cycle_rate_ghz": min(level_rates),
            "maximum_effective_cycle_rate_ghz": max(level_rates),
            "first_to_last_change_percent": change,
        }
    return {
        "intervals": intervals,
        "levels": level_summaries,
        "minimum_effective_cycle_rate_ghz": min(rates) if rates else None,
        "maximum_effective_cycle_rate_ghz": max(rates) if rates else None,
        "note": ("process-wide XNU active-cycle-rate proxy; thermal state and "
                 "FPS drift remain the authoritative throttle witnesses"),
    }


def capture_exact_window(remote: Remote, window: int,
                         destination: pathlib.Path):
    chroot_path = f"/private/tmp/macws_stray_benchmark.{os.getpid()}.png"
    ios_path = "/var/mnt/rootfs" + chroot_path
    transfer_path = f"/var/jb/var/mobile/macws_stray_benchmark.{os.getpid()}.png"
    fallback_error = None
    try:
        command = (
            "export PATH=/usr/bin:/bin:/usr/sbin:/sbin; "
            f"/usr/sbin/screencapture -x -l {window} {chroot_path} "
            "& capture_pid=$!; elapsed=0; "
            "while kill -0 $capture_pid 2>/dev/null; do "
            "if [ $elapsed -ge 8 ]; then kill -TERM $capture_pid 2>/dev/null; "
            "wait $capture_pid 2>/dev/null; exit 124; fi; "
            "sleep 1; elapsed=$((elapsed+1)); done; wait $capture_pid"
        )
        try:
            remote.sudo(
                "bash /var/jb/usr/macOS/bin/run_bash.sh -c " +
                shlex.quote(command), timeout=15,
            )
        except (subprocess.CalledProcessError,
                subprocess.TimeoutExpired) as error:
            fallback_error = f"{type(error).__name__}: {error}"
        if fallback_error:
            capture = capture_host_ui(remote, destination)
            capture["source"] = "MacWSHost UIKit fallback"
            capture["exact_window_error"] = fallback_error
            return capture
        # screencapture creates a world-readable PNG. Copy it as the SSH user;
        # rootless iOS deliberately denies root/chown metadata operations in
        # the mobile-owned Procursus data directory.
        remote.run(
            f"cp {shlex.quote(ios_path)} {shlex.quote(transfer_path)}"
        )
        remote.copy_from(transfer_path, destination)
        data = destination.read_bytes()[:24]
        if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
            raise RuntimeError("exact-window capture is not a PNG")
        width, height = struct.unpack(">II", data[16:24])
        return {"path": str(destination), "width": width, "height": height}
    finally:
        remote.run(f"rm -f {shlex.quote(transfer_path)}", check=False)
        remote.sudo(f"rm -f {shlex.quote(ios_path)}", check=False)


def capture_host_ui(remote: Remote, destination: pathlib.Path):
    """Capture the exact screen MacWSHost is presenting on the iPad.

    Unlike ``screencapture`` this asks the already-running UIKit process to
    render its current hierarchy.  It therefore creates no chroot helper at
    the game's peak allocation point and is also a direct witness that the
    user's macPad view, rather than only WindowServer/VNC, is advancing.
    """
    path = "/var/mobile/Library/Logs/MacWSHost-automation.jpg"
    offset_error_text = ""
    host_stream = getattr(remote, "host_log_stream", None)
    stream_offset = getattr(remote, "host_log_offset", None)
    stream_prefix_length = None
    if host_stream is not None and stream_offset is not None:
        # Capture the local follower length before requesting the snapshot.
        # The subsequent acknowledgement can then be proven without asking
        # iPadOS to fork stat/dd/tail during Stray's allocation peak.
        stream_prefix_length = len(
            host_stream.snapshot_from(int(stream_offset))
        )
        offset = int(stream_offset)
    else:
        try:
            offset = remote_file_size(remote, HOST_LOG)
        except Exception as offset_error:
            # ``stat`` is diagnostic, not part of MacWSHost's screenshot
            # contract.  At Stray's allocation peak iPadOS can reject that
            # child while the already-running UIKit process remains healthy.
            offset = None
            offset_error_text = str(offset_error)
    input_agent = getattr(remote, "input_agent", None)
    if input_agent is not None:
        request_transport = input_agent.open_url(
            "macwshost://screenshot-automation"
        )
    else:
        remote.run(
            "uiopen --url macwshost://screenshot-automation", check=False
        )
        request_transport = "uiopen"
    witness = ""
    if offset is not None:
        deadline = time.monotonic() + 12.0
        while time.monotonic() < deadline:
            if host_stream is not None:
                suffix = host_stream.snapshot_from(offset)
                if stream_prefix_length is not None:
                    suffix = suffix[stream_prefix_length:]
            else:
                suffix = file_suffix(remote, HOST_LOG, offset)
            matches = [line for line in suffix.splitlines()
                       if "automation-snapshot written=" in line]
            if matches:
                witness = matches[-1]
                if "written=YES" not in witness:
                    raise RuntimeError(
                        "MacWSHost UI snapshot failed: " + witness
                    )
                break
            time.sleep(0.10)
        if not witness:
            raise RuntimeError(
                "MacWSHost did not acknowledge its UI snapshot"
            )
    else:
        # The URL request is asynchronous.  Give the existing Host process a
        # bounded completion window, then validate the actual JPEG below. Do
        # not launch another remote ``stat``/``tail`` in this fallback.
        time.sleep(1.0)
        witness = (
            "MacWSHost screenshot URL requested; log-offset unavailable: " +
            offset_error_text
        )
    if input_agent is not None:
        copy_transport = input_agent.copy_file(path, destination)
    else:
        copy_error = None
        for _ in range(5):
            try:
                remote.copy_from(path, destination)
                copy_error = None
                break
            except (OSError, subprocess.SubprocessError) as error:
                copy_error = error
                time.sleep(0.5)
        if copy_error is not None:
            raise RuntimeError(
                "could not copy MacWSHost UI snapshot after retries: " +
                str(copy_error)
            )
        copy_transport = "scp"
    data = destination.read_bytes()
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        raise RuntimeError("MacWSHost automation capture is not a JPEG")
    width = height = 0
    cursor = 2
    start_of_frame = {
        0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
        0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
    }
    while cursor + 4 <= len(data):
        if data[cursor] != 0xFF:
            cursor += 1
            continue
        marker = data[cursor + 1]
        cursor += 2
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            continue
        if cursor + 2 > len(data):
            break
        segment_length = struct.unpack(">H", data[cursor:cursor + 2])[0]
        if marker in start_of_frame and segment_length >= 7:
            height, width = struct.unpack(">HH", data[cursor + 3:cursor + 7])
            break
        if segment_length < 2:
            break
        cursor += segment_length
    if width <= 0 or height <= 0:
        raise RuntimeError("MacWSHost automation JPEG has no SOF dimensions")
    return {
        "path": str(destination),
        "width": width,
        "height": height,
        "format": "jpeg",
        "source": "MacWSHost UIKit hierarchy",
        "runtime_witness": witness,
        "request_transport": request_transport,
        "copy_transport": copy_transport,
    }


def isolated_dark_regions(capture: dict):
    """Find compact dark islands surrounded by a substantially bright ring.

    This is deliberately a host-side visual witness, not an assertion that a
    region is a rendering bug.  The retained 2026-08-24 Stray artifact is a
    124x94 dark island in a bright sky; screen bars, the FPS background and
    ordinary dark foliage either touch an edge or do not have a bright local
    ring.  Keeping the result geometric lets later render-target evidence
    confirm or reject each candidate without teaching the runtime a game-
    specific pixel workaround.
    """
    temporary = tempfile.NamedTemporaryFile(
        prefix="macws-dark-regions.", suffix=".bmp", delete=False
    )
    temporary.close()
    bmp_path = pathlib.Path(temporary.name)
    try:
        converted = subprocess.run(
            ["/usr/bin/sips", "-s", "format", "bmp", capture["path"],
             "--out", str(bmp_path)],
            capture_output=True, text=True, timeout=20,
        )
        if converted.returncode:
            raise RuntimeError(
                "sips dark-region conversion failed: " +
                converted.stderr.strip()
            )
        payload = bmp_path.read_bytes()
        if len(payload) < 138 or payload[:2] != b"BM":
            raise RuntimeError("dark-region conversion did not produce BMP")
        offset = struct.unpack_from("<I", payload, 10)[0]
        width = struct.unpack_from("<i", payload, 18)[0]
        signed_height = struct.unpack_from("<i", payload, 22)[0]
        bits_per_pixel = struct.unpack_from("<H", payload, 28)[0]
        height = abs(signed_height)
        if width <= 0 or height <= 0 or bits_per_pixel not in (24, 32):
            raise RuntimeError(
                "unexpected dark-region BMP geometry: " +
                f"{width}x{signed_height}/{bits_per_pixel}"
            )
        bytes_per_pixel = bits_per_pixel // 8
        bytes_per_row = ((width * bits_per_pixel + 31) // 32) * 4
        if offset + bytes_per_row * height > len(payload):
            raise RuntimeError("truncated dark-region BMP pixels")

        def brightness(x: int, y: int):
            source_y = height - 1 - y if signed_height > 0 else y
            pixel = offset + source_y * bytes_per_row + x * bytes_per_pixel
            blue, green, red = payload[pixel:pixel + 3]
            return (int(red) + int(green) + int(blue)) / 3.0

        step = 2
        sample_width = (width + step - 1) // step
        sample_height = (height + step - 1) // step
        dark = bytearray(sample_width * sample_height)
        for sample_y in range(sample_height):
            y = min(height - 1, sample_y * step)
            row = sample_y * sample_width
            for sample_x in range(sample_width):
                x = min(width - 1, sample_x * step)
                dark[row + sample_x] = brightness(x, y) < 80.0

        visited = bytearray(len(dark))
        regions = []
        for seed, is_dark in enumerate(dark):
            if not is_dark or visited[seed]:
                continue
            pending = [seed]
            visited[seed] = 1
            count = 0
            min_x = sample_width
            max_x = 0
            min_y = sample_height
            max_y = 0
            while pending:
                current = pending.pop()
                sample_y, sample_x = divmod(current, sample_width)
                count += 1
                min_x = min(min_x, sample_x)
                max_x = max(max_x, sample_x)
                min_y = min(min_y, sample_y)
                max_y = max(max_y, sample_y)
                for next_x, next_y in (
                        (sample_x - 1, sample_y),
                        (sample_x + 1, sample_y),
                        (sample_x, sample_y - 1),
                        (sample_x, sample_y + 1)):
                    if (0 <= next_x < sample_width and
                            0 <= next_y < sample_height):
                        neighbor = next_y * sample_width + next_x
                        if dark[neighbor] and not visited[neighbor]:
                            visited[neighbor] = 1
                            pending.append(neighbor)
            box_width = (max_x - min_x + 1) * step
            box_height = (max_y - min_y + 1) * step
            if (count < 80 or box_width < 12 or box_height < 12 or
                    min_x == 0 or min_y == 0 or
                    max_x == sample_width - 1 or
                    max_y == sample_height - 1 or
                    box_width * box_height > width * height * 0.03):
                continue
            ring = []
            ring_radius = 10
            for ring_y in range(
                    max(0, min_y - ring_radius),
                    min(sample_height, max_y + ring_radius + 1)):
                for ring_x in range(
                        max(0, min_x - ring_radius),
                        min(sample_width, max_x + ring_radius + 1)):
                    if (min_x <= ring_x <= max_x and
                            min_y <= ring_y <= max_y):
                        continue
                    ring.append(brightness(
                        min(width - 1, ring_x * step),
                        min(height - 1, ring_y * step),
                    ))
            if not ring:
                continue
            ring_mean = sum(ring) / len(ring)
            ring_bright_ratio = sum(value > 180.0 for value in ring) / len(ring)
            box_samples = (max_x - min_x + 1) * (max_y - min_y + 1)
            fill_ratio = count / box_samples
            if (ring_mean < 140.0 or ring_bright_ratio < 0.50 or
                    fill_ratio < 0.15):
                continue
            regions.append({
                "bounds_pixels": {
                    "x": min_x * step,
                    "y": min_y * step,
                    "width": min(width, (max_x + 1) * step) - min_x * step,
                    "height": min(height, (max_y + 1) * step) - min_y * step,
                },
                "sampled_dark_pixels": count,
                "estimated_dark_pixels": count * step * step,
                "dark_fill_ratio": fill_ratio,
                "ring_mean_brightness": ring_mean,
                "ring_bright_ratio": ring_bright_ratio,
            })
        regions.sort(
            key=lambda item: item["estimated_dark_pixels"], reverse=True
        )
        return {
            "classification": "diagnostic-candidates-not-root-cause",
            "dark_threshold": 80,
            "ring_mean_floor": 140,
            "ring_bright_ratio_floor": 0.50,
            "regions": regions,
        }
    finally:
        try:
            bmp_path.unlink()
        except FileNotFoundError:
            pass


def capture_host_layers(remote: Remote, destination: pathlib.Path):
    """Export the retained WindowServer base and per-window IOSurfaces."""
    destination.mkdir(parents=True, exist_ok=True)
    host_stream = getattr(remote, "host_log_stream", None)
    stream_offset = getattr(remote, "host_log_offset", None)
    if host_stream is None or stream_offset is None:
        raise RuntimeError(
            "prearmed MacWSHost log stream is required for layer capture"
        )
    prefix_length = len(host_stream.snapshot_from(int(stream_offset)))
    input_agent = getattr(remote, "input_agent", None)
    if input_agent is None:
        raise RuntimeError(
            "prearmed input/transfer agent is required for layer capture"
        )
    request_transport = input_agent.open_url(
        "macwshost://screenshot-layers"
    )
    deadline = time.monotonic() + 20.0
    witness_lines = []
    while time.monotonic() < deadline:
        suffix = host_stream.snapshot_from(int(stream_offset))[prefix_length:]
        witness_lines = [
            line for line in suffix.splitlines()
            if ("workspace-layer-snapshot " in line or
                "workspace-layer-snapshot-complete " in line)
        ]
        if any("workspace-layer-snapshot-complete " in line
               for line in witness_lines):
            break
        time.sleep(0.10)
    else:
        raise RuntimeError(
            "MacWSHost did not complete its per-layer snapshot"
        )
    listing = remote.run(
        "find " + shlex.quote(HOST_LAYER_DIRECTORY) +
        " -maxdepth 1 -type f -name '*.png' -print | sort",
        check=False,
    )
    copied = []
    for source in listing.splitlines():
        source = source.strip()
        if (not source.startswith(HOST_LAYER_DIRECTORY + "/") or
                pathlib.PurePosixPath(source).parent.as_posix() !=
                HOST_LAYER_DIRECTORY):
            continue
        target = destination / pathlib.PurePosixPath(source).name
        transport = input_agent.copy_file(source, target, timeout=30.0)
        header = target.read_bytes()[:24]
        if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
            raise RuntimeError("MacWSHost layer capture is not a PNG")
        width, height = struct.unpack(">II", header[16:24])
        capture = {
            "remote_path": source,
            "path": str(target),
            "width": width,
            "height": height,
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "copy_transport": transport,
        }
        capture["isolated_dark_regions"] = isolated_dark_regions(capture)
        copied.append(capture)
    return {
        "request_transport": request_transport,
        "runtime_witness": witness_lines,
        "files": copied,
    }


def collect_render_target_captures(remote: Remote,
                                   destination: pathlib.Path):
    """Copy only the bounded one-shot Stray RT/buffer diagnostic batch."""
    destination.mkdir(parents=True, exist_ok=True)
    listing = remote.run(
        "find /var/mnt/rootfs/private/tmp -maxdepth 1 -type f "
        "\\( -name 'macws_stray_rt_c*.raw' -o "
        "-name 'macws_stray_buf_c*.raw' -o "
        "-name 'macws_stray_stage_*.raw' -o "
        "-name 'macws_stray_shadow_stage_*.raw' -o "
        "-name 'macws_stray_compute_stage_*.raw' -o "
        "-name 'macws_stray_input_*.raw' \\) -print | sort",
        check=False,
    )
    input_agent = getattr(remote, "input_agent", None)
    copied = []
    for source in listing.splitlines():
        source = source.strip()
        if (not source.startswith("/var/mnt/rootfs/private/tmp/macws_stray_")
                or pathlib.PurePosixPath(source).parent.as_posix() !=
                "/var/mnt/rootfs/private/tmp"):
            continue
        target = destination / pathlib.PurePosixPath(source).name
        if input_agent is not None:
            transport = input_agent.copy_file(source, target, timeout=45.0)
        else:
            remote.copy_from(source, target)
            transport = "scp"
        with target.open("rb") as artifact:
            header = artifact.readline(1024).decode("ascii", "replace").strip()
        copied.append({
            "remote_path": source,
            "path": str(target),
            "bytes": target.stat().st_size,
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "header": header,
            "copy_transport": transport,
        })
    analysis = subprocess.run(
        [sys.executable, str(STRAY_RT_ANALYZER), str(destination),
         "--output", str(destination / "decoded")],
        capture_output=True, text=True, timeout=180,
    )
    if analysis.returncode:
        raise RuntimeError(
            "render-target analyzer failed: " + analysis.stderr.strip()
        )
    report_path = destination / "decoded" / "report.json"
    return {
        "files": copied,
        "bytes": sum(item["bytes"] for item in copied),
        "behavior": (
            "read-only copy of bounded six-command-buffer batch plus "
            "diagnostic-only per-render-pass stage snapshots"
        ),
        "analysis": json.loads(report_path.read_text()),
    }


def capture_game_view(remote: Remote, _window: int,
                      destination: pathlib.Path):
    # The test contract is what the user sees on the iPad.  Keeping the window
    # parameter makes call sites explicit about their intended owner while the
    # capture itself deliberately observes the final macPad presentation.
    return capture_host_ui(remote, destination)


def capture_process_sample(remote: Remote, pid: int, seconds: float,
                           destination: pathlib.Path):
    """Capture a bounded, preserving macOS `sample` profile for one PID."""
    sample_agent = getattr(remote, "sample_agent", None)
    if sample_agent is not None and not sample_agent.used:
        return sample_agent.capture(remote, pid, seconds, destination)
    duration = max(1, round(seconds))
    chroot_path = f"/private/tmp/macws_stray_sample.{os.getpid()}.txt"
    ios_path = "/var/mnt/rootfs" + chroot_path
    transfer_path = f"/var/jb/var/mobile/macws_stray_sample.{os.getpid()}.txt"
    try:
        output = remote.sudo(
            "bash /var/jb/usr/macOS/bin/run_bash.sh -c " + shlex.quote(
                "export PATH=/usr/bin:/bin:/usr/sbin:/sbin; "
                f"/usr/bin/sample {pid} {duration} 1 -file {chroot_path}"
            ),
            timeout=duration + 25,
        )
        remote.run(
            f"cp {shlex.quote(ios_path)} {shlex.quote(transfer_path)}"
        )
        remote.copy_from(transfer_path, destination)
        if destination.stat().st_size < 100:
            raise RuntimeError("macOS process sample is unexpectedly short")
        return {
            "path": str(destination),
            "duration_seconds": duration,
            "size": destination.stat().st_size,
            "tool_output": output.strip(),
        }
    finally:
        remote.run(f"rm -f {shlex.quote(transfer_path)}", check=False)
        remote.sudo(f"rm -f {shlex.quote(ios_path)}", check=False)


def collect_gpu_command_error_artifacts(remote: Remote, pid: int,
                                        destination: pathlib.Path):
    """Copy the exact bounded submit dumps emitted for one Stray PID.

    The recorder writes into the chroot's /private/tmp, whose iOS-side path is
    below /var/mnt/rootfs.  Restrict both directory and file discovery to the
    exact captured PID; this never removes a dump or reads another process's
    diagnostic directory.
    """
    if pid <= 1:
        return {"directories": [], "files": [], "bytes": 0}
    remote_root = "/var/mnt/rootfs/private/tmp"
    names = (
        f"macws_fast_submit_error_{pid}_*",
        f"macws_submit_error_{pid}_*",
    )
    find_command = (
        f"find {shlex.quote(remote_root)} -maxdepth 1 -type d "
        "\\( " + " -o ".join(
            f"-name {shlex.quote(name)}" for name in names
        ) + " \\) -print"
    )
    remote_directories = sorted(
        line.strip() for line in
        remote.run(find_command, check=False).splitlines()
        if line.strip()
    )
    destination.mkdir(parents=True, exist_ok=True)
    copied = []
    for remote_directory in remote_directories:
        prefix = remote_root + "/"
        basename = pathlib.PurePosixPath(remote_directory).name
        if (not remote_directory.startswith(prefix) or
                not re.fullmatch(
                    rf"macws_(?:fast_)?submit_error_{pid}_[0-9]+",
                    basename)):
            raise RuntimeError(
                f"unsafe GPU error artifact directory: {remote_directory}"
            )
        local_directory = destination / basename
        local_directory.mkdir(exist_ok=True)
        remote_files = remote.run(
            f"find {shlex.quote(remote_directory)} -maxdepth 1 -type f -print",
            check=False,
        ).splitlines()
        for remote_file in sorted(path.strip() for path in remote_files
                                  if path.strip()):
            file_name = pathlib.PurePosixPath(remote_file).name
            if (not remote_file.startswith(remote_directory + "/") or
                    "/" in file_name or file_name in {"", ".", ".."}):
                raise RuntimeError(
                    f"unsafe GPU error artifact file: {remote_file}"
                )
            local_file = local_directory / file_name
            remote.copy_from(remote_file, local_file)
            data = local_file.read_bytes()
            copied.append({
                "remote": remote_file,
                "path": str(local_file),
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            })
    return {
        "directories": remote_directories,
        "files": copied,
        "bytes": sum(item["bytes"] for item in copied),
        "behavior": "read-only exact-PID archive; remote evidence retained",
    }


def vision_ocr_executable():
    """Compile the host Vision helper once instead of interpreting per frame."""
    if (VISION_OCR_BINARY.is_file() and
            VISION_OCR_BINARY.stat().st_mtime_ns >=
            VISION_OCR.stat().st_mtime_ns):
        return str(VISION_OCR_BINARY)
    VISION_OCR_BINARY.parent.mkdir(parents=True, exist_ok=True)
    temporary = VISION_OCR_BINARY.with_name(
        f"{VISION_OCR_BINARY.name}.{os.getpid()}.tmp"
    )
    try:
        completed = subprocess.run(
            ["/usr/bin/xcrun", "swiftc", str(VISION_OCR),
             "-o", str(temporary)],
            capture_output=True, text=True, timeout=120,
        )
        if completed.returncode:
            raise RuntimeError(
                "Vision OCR helper compile failed: " +
                completed.stderr.strip()
            )
        os.replace(temporary, VISION_OCR_BINARY)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return str(VISION_OCR_BINARY)


def recognize_exact_window(capture: dict):
    """Run host-side Vision OCR without adding work to the iPad."""
    completed = subprocess.run(
        [vision_ocr_executable(), capture["path"]],
        capture_output=True, text=True, timeout=20,
    )
    capture["recognized_text"] = completed.stdout.strip()
    if completed.returncode:
        capture["ocr_error"] = completed.stderr.strip()
    return capture["recognized_text"].upper()


def recognize_exact_window_boxes(capture: dict):
    """Return Vision text plus normalized boxes for resilient UI actions."""
    completed = subprocess.run(
        [vision_ocr_executable(), "--json", capture["path"]],
        capture_output=True, text=True, timeout=20,
    )
    if completed.returncode:
        raise RuntimeError(
            "Vision boxed OCR failed: " + completed.stderr.strip()
        )
    observations = json.loads(completed.stdout)
    if not isinstance(observations, list):
        raise RuntimeError("Vision boxed OCR returned a non-list payload")
    return observations


def exact_image_stats(capture: dict):
    """Measure visible pixels without adding a third-party image package."""
    temporary = tempfile.NamedTemporaryFile(
        prefix="macws-image-stats.", suffix=".bmp", delete=False
    )
    temporary.close()
    bmp_path = pathlib.Path(temporary.name)
    try:
        converted = subprocess.run(
            ["/usr/bin/sips", "-s", "format", "bmp", capture["path"],
             "--out", str(bmp_path)],
            capture_output=True, text=True, timeout=20,
        )
        if converted.returncode:
            raise RuntimeError(
                "sips image-stat conversion failed: " +
                converted.stderr.strip()
            )
        payload = bmp_path.read_bytes()
        if len(payload) < 138 or payload[:2] != b"BM":
            raise RuntimeError("image-stat conversion did not produce BMP")
        offset = struct.unpack_from("<I", payload, 10)[0]
        width = struct.unpack_from("<i", payload, 18)[0]
        signed_height = struct.unpack_from("<i", payload, 22)[0]
        bits_per_pixel = struct.unpack_from("<H", payload, 28)[0]
        if (width <= 0 or signed_height == 0 or
                bits_per_pixel not in (24, 32)):
            raise RuntimeError(
                "unexpected image-stat BMP geometry: " +
                f"{width}x{signed_height}/{bits_per_pixel}"
            )
        height = abs(signed_height)
        bytes_per_pixel = bits_per_pixel // 8
        bytes_per_row = ((width * bits_per_pixel + 31) // 32) * 4
        end = offset + bytes_per_row * height
        if end > len(payload):
            raise RuntimeError("truncated image-stat BMP pixels")
        # Sampling one pixel in four is sufficient to distinguish the actual
        # scene from retained loading black while keeping host-side analysis
        # cheap.  The green FPS glyph by itself measured only 0.06-0.10%
        # nonblack; the first real scene measured 67.63%.
        count = nonblack = 0
        brightness_sum = brightness_squared_sum = 0.0
        for y in range(height):
            row = offset + y * bytes_per_row
            for x in range(0, width, 4):
                pixel = row + x * bytes_per_pixel
                blue, green, red = payload[pixel:pixel + 3]
                brightness = (int(red) + int(green) + int(blue)) / 3.0
                count += 1
                nonblack += max(red, green, blue) > 8
                brightness_sum += brightness
                brightness_squared_sum += brightness * brightness
        mean = brightness_sum / count
        return {
            "sample_count": count,
            "mean_brightness": mean,
            "brightness_variance":
                brightness_squared_sum / count - mean * mean,
            "nonblack_ratio": nonblack / count,
        }
    finally:
        try:
            bmp_path.unlink()
        except FileNotFoundError:
            pass


def click_steam_text(remote: Remote, pid: int, window: dict,
                     capture: dict, requested_text: str):
    """Click the center of a runtime-observed Steam text label."""
    observations = recognize_exact_window_boxes(capture)
    needle = requested_text.upper()
    matches = [
        item for item in observations
        if needle in str(item.get("text", "")).upper()
    ]
    if not matches:
        raise RuntimeError(
            f"Steam dialog did not expose OCR label {requested_text!r}: " +
            json.dumps(observations, ensure_ascii=False)
        )
    # Vision's normalized origin is bottom-left; Host input uses top-left
    # exact-window pixels.  Prefer the largest matching label when OCR emits
    # a duplicate from a dimmed background layer.
    observed = max(
        matches,
        key=lambda item: float(item["width"]) * float(item["height"]),
    )
    width = int(capture["width"])
    height = int(capture["height"])
    x = round((float(observed["x"]) +
               float(observed["width"]) / 2.0) * width)
    # Vision's lower edge for this CEF text landed at the bottom border of the
    # blue control when converted through the Retina screenshot.  Aim at the
    # upper glyph edge, which is still inside the padded button.  The exact
    # 2026-08-21 witness was (1453,935) on 2480x1560.
    y = round((1.0 - float(observed["y"]) -
               float(observed["height"])) * height)
    if remote.input_agent is not None:
        # This reconciliation runs after all long-lived launch observers are
        # already armed.  Reuse that input transport just like the PLAY and
        # keyboard paths do: spawning a one-shot SSH/Python gesture process
        # here runtime-timed-out (rc=124) while Steam displayed the real Exit
        # confirmation, aborting the whole GPU diagnostic before launch.
        first = remote.input_agent.gesture(
            "tap", pid, window["window"], width, height, x, y,
            activate_first=True,
        )
        time.sleep(0.08)
        second = remote.input_agent.gesture(
            "tap", pid, window["window"], width, height, x, y,
        )
        output = first + "; " + second
    else:
        output = remote.run(
            f"python3 {GESTURE_PROBE} double-tap --pid {pid} "
            f"--window {window['window']} --width {width} --height {height} "
            f"--x {x} --y {y} --activate-first",
            timeout=15,
        ).strip()
    return {
        "requested_text": requested_text,
        "observation": observed,
        "observations": observations,
        "x": x,
        "y": y,
        "gesture": "double-tap",
        "probe": output,
    }


def find_steam_text_window(remote: Remote, requested_text: str,
                           destination: pathlib.Path,
                           timeout: float = 8.0):
    """Find a Steam modal by its visible text across all Helper owners.

    Steam's LaunchApp owner and its native Cloud/session modal are not
    necessarily the same Steam Helper process.  Selecting the largest window
    of the main ``-launcher=0`` helper captured the blank Store shell while a
    newly spawned helper owned the real "Play anyway" prompt.  Resolve the
    modal from current per-process AppInput metrics and require OCR of the
    requested action before returning an input destination.
    """
    deadline = time.monotonic() + timeout
    attempts = []
    observed_windows = set()
    needle = requested_text.upper()
    while time.monotonic() < deadline:
        inventory = steam_ui_process_inventory(remote)
        for pid in sorted(inventory["helpers"]):
            payload = remote.run(
                f"dd if={shlex.quote(f'{METRICS_PREFIX}.{pid}.bin')} "
                "bs=1048576 2>/dev/null",
                check=False, binary=True,
            )
            windows = [
                item for item in parse_metrics(payload)
                if item["width"] * item["height"] >= 20000
            ]
            windows.sort(key=lambda item: (
                bool(item["flags"] & (1 << 6)),
                bool(item["flags"] & 1),
                item["width"] * item["height"], item["window"],
            ), reverse=True)
            for window in windows[:4]:
                identity = (pid, int(window["window"]))
                if identity in observed_windows:
                    continue
                observed_windows.add(identity)
                candidate_path = destination.with_name(
                    f"{destination.stem}.{pid}.{window['window']}"
                    f"{destination.suffix}"
                )
                try:
                    capture = capture_exact_window(
                        remote, int(window["window"]), candidate_path
                    )
                    recognized = recognize_exact_window(capture)
                except Exception as error:
                    attempts.append({
                        "pid": pid, "window": int(window["window"]),
                        "error": str(error),
                    })
                    continue
                attempt = {
                    "pid": pid,
                    "window": int(window["window"]),
                    "flags": int(window["flags"]),
                    "recognized_text": recognized[-2000:],
                    "path": str(candidate_path),
                }
                attempts.append(attempt)
                if needle not in recognized:
                    continue
                os.replace(candidate_path, destination)
                capture["path"] = str(destination)
                capture["recognized_text"] = recognized
                return {
                    "pid": pid,
                    "window": window,
                    "capture": capture,
                    "attempts": attempts,
                    "witness": f"visible OCR action {requested_text!r}",
                }
        time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))
    raise RuntimeError(
        f"Steam did not expose OCR action {requested_text!r} in any current "
        "Helper window: " +
        json.dumps(attempts[-12:], ensure_ascii=False)
    )


def steam_primary_action_state(recognized_text: str):
    """Classify Steam's Stray action without matching PLAY TIME text."""
    text = recognized_text.upper()
    tokens = {
        re.sub(r"[^A-Z]+", "", line)
        for line in text.splitlines()
    }
    if "EXIT GAME?" in text and "CONFIRM" in tokens:
        return "exit-confirmation"
    if tokens.intersection({"STOP", "XSTOP"}):
        return "stop"
    if "PLAY" in tokens:
        return "play"
    return "unrecognized"


def prepare_steam_play_action(remote: Remote, selection: dict,
                              output: pathlib.Path, timeout: float,
                              checkpoint=None):
    """Require Steam's real PLAY state after a frozen launcher cooldown."""
    deadline = time.monotonic() + timeout
    attempts = []
    store_home_recovery_attempted = False
    for index in range(1, 13):
        if checkpoint:
            checkpoint("steam-play-state")
        window = largest_window(
            remote, selection["pid"], 5, minimum_area=500000
        )
        capture = capture_exact_window(
            remote, window["window"],
            output / f"steam-play-state-{index}.png",
        )
        recognized = recognize_exact_window(capture)
        state = steam_primary_action_state(recognized)
        attempt = {
            "state": state,
            "recognized_text": recognized,
            "window": window,
            "capture": capture,
        }
        attempts.append(attempt)
        if state == "play":
            return {
                "state": "play",
                "attempts": attempts,
                "selection": {**selection, "window": window},
            }
        if (not store_home_recovery_attempted and
                "STORE.STEAMPOWERED.COM" in recognized):
            # A cold Steam CEF generation can render its Store shell before
            # it accepts the first navigation event.  r12 retained that exact
            # URL through all 12 old polling attempts.  Retry the complete,
            # idempotent Library -> Home -> search sequence once as soon as
            # the visible postcondition disproves the first attempt.
            store_home_recovery_attempted = True
            selection = select_stray_in_steam(remote)
            attempt["action"] = {
                "action": "store-home-library-recovery",
                "selection": selection,
            }
            continue
        live_game = game_pid(remote)
        if live_game:
            raise RuntimeError(
                "Steam exposes a non-PLAY action while live Stray PID " +
                str(live_game) + " remains"
            )
        if state == "stop":
            # Runtime-confirmed 2026-08-21: Steam was SIGSTOP'd while the old
            # game exited and briefly retained "Stray - Running / STOP" after
            # SIGCONT.  Use Steam's visible STOP action to enter its normal
            # Exit confirmation instead of clicking it as if it were PLAY.
            attempt["action"] = click_selected_steam_play(
                remote, {**selection, "window": window}, 0.2573, 0.0
            )
        elif state == "exit-confirmation":
            # The process is already absent, so confirmation only reconciles
            # Steam's stale application lifecycle state.
            attempt["action"] = click_steam_text(
                remote, selection["pid"], window, capture, "Confirm"
            )
        if time.monotonic() >= deadline:
            break
        time.sleep(0.5)
    raise RuntimeError(
        "Steam did not converge to a visible PLAY action: " +
        json.dumps(attempts[-3:], ensure_ascii=False)
    )


def stray_visual_state(recognized_text: str):
    """Classify only UI states supported by retained screenshot evidence."""
    recognized_text = recognized_text.upper()
    if ("AUTOSAVE FEATURE" in recognized_text or
            ("AUTOSAVE" in recognized_text and
             "DO NOT TURN OFF" in recognized_text)):
        return "autosave-notice"
    if ("SELECT SAVE" in recognized_text or
            ("SLOT 1" in recognized_text and "SLOT 2" in recognized_text)):
        return "save-select"
    if ("START GAME" in recognized_text and
            "SETTINGS" in recognized_text):
        return "main-menu"
    if ("PRESS" in recognized_text and
            ("BUTTON" in recognized_text or "KEY" in recognized_text)):
        return "startup-prompt"
    return "non-menu-or-unrecognized"


def capture_vnc(host: str, port: int, destination: pathlib.Path):
    from vnc_capture import capture, save_capture

    result = capture(host, port, 8.0)
    save_capture(str(destination), None, result, prefix="benchmark: ")
    return {"path": str(destination), "width": result[0],
            "height": result[1], "nonblack_pixels": result[4]}


def arm_game_launch_observers(remote: Remote, args, result: dict):
    """Pre-arm every observer that must exist before Steam creates Stray."""
    offset = log_size(remote)
    result["runtime_log_offset"] = offset
    remote.runtime_log_offset = offset
    runtime_log_stream = RemoteRuntimeLogStream(remote, RUNTIME_LOG, offset)
    remote.runtime_log_stream = runtime_log_stream
    result["runtime_log_stream"] = {
        "armed_before_game": True,
        "transport": "dedicated-non-multiplexed-ssh",
        "device_process": "tail -f",
        "starting_offset": offset,
        "maximum_retained_bytes": runtime_log_stream.MAX_BYTES,
    }

    host_offset = remote_file_size(remote, HOST_LOG)
    result["host_log_offset"] = host_offset
    remote.host_log_offset = host_offset
    host_log_stream = RemoteRuntimeLogStream(remote, HOST_LOG, host_offset)
    remote.host_log_stream = host_log_stream
    result["host_log_stream"] = {
        "armed_before_game": True,
        "transport": "dedicated-non-multiplexed-ssh",
        "device_process": "tail -f",
        "starting_offset": host_offset,
        "purpose": "current-run fullscreen catalog fallback",
        "maximum_retained_bytes": host_log_stream.MAX_BYTES,
    }

    input_agent = RemoteInputAgent(remote)
    remote.input_agent = input_agent
    result["input_agent"] = {
        "armed_before_game": True,
        "transport": "dedicated-non-multiplexed-ssh",
        "device_process": "persistent Python Host-ABI sender",
        "process_control": "none",
    }
    sample_agent = None
    if (args.process_sample_seconds or args.windowserver_sample_seconds or
            args.gameplay_hover_probe or
            args.gameplay_host_hover_probe or
            args.gameplay_host_long_drag_probe or
            args.gameplay_walk_probe):
        sample_agent = RemoteSampleAgent(remote)
        remote.sample_agent = sample_agent
        result["sample_agent"] = {
            "armed_before_game": True,
            "transport": "dedicated-non-multiplexed-ssh",
            "device_process": "waiting chroot shell -> exec sample",
            "process_creation_at_capture": "none",
            "process_control": "none",
        }
    return {
        "offset": offset,
        "runtime_log_stream": runtime_log_stream,
        "host_log_stream": host_log_stream,
        "input_agent": input_agent,
        "sample_agent": sample_agent,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", default="mobile")
    parser.add_argument("--ssh-port", type=int, default=22)
    parser.add_argument("--vnc-port", type=int, default=5900)
    parser.add_argument(
        "--sudo-password", default=os.environ.get("MACWS_DEVICE_SUDO_PASSWORD")
    )
    parser.add_argument("--output", type=pathlib.Path,
                        default=pathlib.Path("/tmp/macws-stray-perf"))
    parser.add_argument("--cooldown-timeout", type=float, default=600)
    parser.add_argument(
        "--temperature-ceiling", type=float, default=35.5,
        help=("legacy compatibility value retained for old invocations; "
              "recorded sensor temperature is not a gate"),
    )
    parser.add_argument(
        "--start-temperature-ceiling", type=float, default=34.0,
        help=("legacy compatibility value; numeric temperatures are recorded "
              "but only iPadOS thermal-state=nominal is enforced"),
    )
    parser.add_argument("--cool-stable-samples", type=int, default=2)
    parser.add_argument(
        "--allow-fair-functional", action="store_true",
        help=("admit a short functional-only run at thermal-state=fair; "
              "non-nominal samples still invalidate every performance "
              "result and this does not alter the production thermal policy"),
    )
    parser.add_argument(
        "--allow-serious-functional", action="store_true",
        help=("admit serious thermal pressure only for a bounded <=5-second "
              "non-scored functional run; critical is still rejected, the "
              "result is forced to THERMAL_PRESSURE, and Stray is cleaned up "
              "at the normal bounded-test endpoint"),
    )
    parser.add_argument("--launch-timeout", type=float, default=40)
    parser.add_argument("--steam-start-timeout", type=float, default=90)
    parser.add_argument("--steam-ready-timeout", type=float, default=75)
    parser.add_argument("--catalog-timeout", type=float, default=20)
    parser.add_argument("--workspace-timeout", type=float, default=8)
    parser.add_argument(
        "--workspace-start-timeout", type=float, default=600,
        help=("maximum wait for the app's public cold-start transaction; "
              "the first start after a boot may restore the trust closure"),
    )
    parser.add_argument("--background-idle-timeout", type=float, default=20)
    parser.add_argument("--background-cpu-ceiling", type=float, default=15.0)
    parser.add_argument("--background-total-cpu-ceiling", type=float,
                        default=25.0)
    parser.add_argument(
        "--keep-control-center-during-sample", action="store_true",
        help=("do not temporarily unload the real macOS ControlCenter; the "
              "default gaming mode restores it after every bounded run"),
    )
    parser.add_argument("--skip-workspace-preflight", action="store_true")
    parser.add_argument(
        "--preflight-only", action="store_true",
        help="restore/verify the live fullscreen workspace without Steam/game",
    )
    parser.add_argument(
        "--steam-preflight-only", action="store_true",
        help=("start and hide the application-class Steam UI, then retain it "
              "as an idle warm launcher for fast bounded game iterations"),
    )
    parser.add_argument("--menu-delay", type=float, default=10)
    parser.add_argument("--enter-count", type=int, default=1)
    parser.add_argument("--enter-interval", type=float, default=2)
    parser.add_argument(
        "--progress-to-gameplay", action="store_true",
        help=("use exact-window screenshots and host-side Vision OCR to "
              "advance startup/main-menu/save-slot states, then benchmark "
              "only after two consecutive non-menu witnesses"),
    )
    parser.add_argument(
        "--gameplay-timeout", type=float, default=300.0,
        help=("maximum visual-state-machine time after the initial menu "
              "delay; loading black and intro cinematics are observed "
              "without repeated input"),
    )
    parser.add_argument(
        "--gameplay-load-delay", type=float, default=0.0,
        help=("after selecting a save slot, observe thermal state without "
              "capturing/OCRing frames for this many seconds before "
              "classifying a visible scene as playable"),
    )
    parser.add_argument(
        "--cool-game-before-sample", action="store_true",
        help=("benchmark preconditioning only: after visually verified "
              "gameplay, SIGSTOP the exact Stray PID, wait for stable "
              "iPadOS thermal-state=nominal, then SIGCONT before input and "
              "FPS scoring; this never exits the game and is not a "
              "production thermal policy"),
    )
    parser.add_argument(
        "--cool-render-path", action="store_true",
        help=("benchmark preconditioning only: while waiting for nominal, "
              "temporarily SIGSTOP the exact WindowServer and macwsdisplayd "
              "PIDs while MacWSHost keeps the last fullscreen frame visible; "
              "resume and verify the same PIDs before launch or scoring"),
    )
    parser.add_argument(
        "--gameplay-hover-probe", action="store_true",
        help=("after the exact-window gameplay witness, send two real "
              "button-free indirect-pointer moves and prove that presentation "
              "continues; on failure capture a preserving process sample"),
    )
    parser.add_argument(
        "--gameplay-host-hover-probe", action="store_true",
        help=("after verified gameplay, ask the running MacWSHost Scene to "
              "emit its 120 Hz fullscreen hover scenario; this exercises the "
              "same Host -> inputd -> Dock/WindowServer global-pointer route "
              "as a physical Magic Keyboard pointer"),
    )
    parser.add_argument(
        "--gameplay-host-long-drag-probe", action="store_true",
        help=("after verified gameplay, ask the running MacWSHost Scene to "
              "hold for 420 ms and emit a 120-sample 120 Hz primary drag; "
              "require the game to keep presenting without a UE fatal"),
    )
    parser.add_argument(
        "--hover-observation-seconds", type=float, default=12.0,
        help=("bounded observation after gameplay hover; 12 seconds covers "
              "one 120-present trace interval even near 11 FPS; the Host "
              "eight-traversal scenario has an enforced 10-second minimum"),
    )
    parser.add_argument(
        "--long-drag-observation-seconds", type=float, default=15.0,
        help=("bounded fatal/presentation observation after the Host's "
              "420-ms hold plus 120 Hz drag"),
    )
    parser.add_argument(
        "--gameplay-walk-probe", action="store_true",
        help=("after reaching verified gameplay, hold one real Magic "
              "Keyboard movement key and require presentation to continue"),
    )
    parser.add_argument(
        "--walk-key", choices=("w", "a", "s", "d"), default="w",
    )
    parser.add_argument(
        "--walk-hold", type=float, default=5.0,
        help="seconds to hold the gameplay movement key",
    )
    parser.add_argument(
        "--walk-observation-seconds", type=float, default=15.0,
        help="bounded presentation/fatal observation after releasing the key",
    )
    parser.add_argument(
        "--rfb-return-count", type=int, default=0,
        help=("diagnostic A/B: send this many Return pairs through OSXvnc "
              "after the Host-ABI inputs"),
    )
    parser.add_argument("--rfb-key-hold", type=float, default=0.12)
    parser.add_argument(
        "--tap-game-start", action="store_true",
        help=("activate Stray and tap the calibrated Start Game button before "
              "sending the requested Return keys"),
    )
    parser.add_argument("--game-start-x", type=float, default=0.5)
    parser.add_argument("--game-start-y", type=float, default=0.782)
    parser.add_argument(
        "--game-start-hold", type=float, default=0.0,
        help=("diagnostic A/B: separate the real Start Game mouse down/up "
              "by this many seconds; zero retains the atomic UIKit tap"),
    )
    parser.add_argument("--warmup", type=float, default=8)
    parser.add_argument("--sample-seconds", type=float, default=20)
    parser.add_argument(
        "--process-sample-seconds", type=float, default=0.0,
        help=("diagnostic-only: capture a bounded macOS sample profile after "
              "the visible gameplay witness and before FPS measurement"),
    )
    parser.add_argument(
        "--windowserver-sample-seconds", type=float, default=0.0,
        help=("diagnostic-only: after the gameplay witness, capture a "
              "bounded preserving sample of the live WindowServer before "
              "FPS measurement"),
    )
    parser.add_argument("--thermal-interval", type=float, default=2)
    parser.add_argument("--steam-play-x", type=float, default=0.2573)
    parser.add_argument(
        "--steam-play-y", type=float, default=0.0,
        help="normalized override; zero uses the calibrated absolute Y=865",
    )
    parser.add_argument("--resolution-width", type=int, default=0,
                        help="zero preserves the game's current default")
    parser.add_argument("--resolution-height", type=int, default=0,
                        help="zero preserves the game's current default")
    parser.add_argument("--screen-percentage", type=int, default=100,
                        help="internal render percentage; output stays native")
    parser.add_argument(
        "--quality", choices=tuple(QUALITY_PROFILES), default="high",
        help="Stray/UE scalability preset; defaults to the historical high profile",
    )
    parser.add_argument(
        "--ue-fullscreen-mode", type=int, choices=(0, 1, 2), default=0,
        help=("UE window mode written to all three GameUserSettings mode "
              "keys: 0=native fullscreen, 1=borderless, 2=windowed; the "
              "MacWSHost fullscreen canvas remains an independent property"),
    )
    parser.add_argument(
        "--max-fps", type=int, default=0,
        help=("UE t.MaxFPS cap; zero preserves the current Engine.ini value"),
    )
    parser.add_argument(
        "--uncapped", action="store_true",
        help=("explicitly set both UE t.MaxFPS and Stray FrameRateLimit to "
              "zero; mutually exclusive with --max-fps"),
    )
    parser.add_argument("--scaling-solution", choices=("BuiltIn", "MetalFX"),
                        default="BuiltIn")
    parser.add_argument(
        "--pre-exposure", choices=("preserve", "on", "off"),
        default="preserve",
        help=("set r.UsePreExposure for a controlled eye-adaptation A/B; "
              "preserve leaves Engine.ini unchanged"),
    )
    parser.add_argument(
        "--eye-adaptation-method",
        choices=("preserve", "histogram", "basic", "manual"),
        default="preserve",
        help=("set r.EyeAdaptation.MethodOverride for a controlled A/B; "
              "preserve leaves Engine.ini unchanged"),
    )
    parser.add_argument(
        "--eye-adaptation-quality",
        choices=("preserve", "off", "low", "normal", "high"),
        default="preserve",
        help=("set r.EyeAdaptationQuality for a controlled A/B; preserve "
              "leaves Engine.ini unchanged"),
    )
    parser.add_argument(
        "--occlusion-queries", choices=("preserve", "on", "off"),
        default="preserve",
        help=("controlled A/B for UE hardware occlusion queries; off uses "
              "the engine's r.AllowOcclusionQueries=0 setting"),
    )
    parser.add_argument(
        "--upscale-quality",
        choices=("preserve", "default", "nearest", "bilinear"),
        default="preserve",
        help=("controlled A/B for the full-resolution UE spatial upscale; "
              "default removes the override, nearest/bilinear set "
              "r.Upscale.Quality to 0/1, and the requested fullscreen "
              "output resolution remains unchanged"),
    )
    parser.add_argument(
        "--rhi-thread", choices=("preserve", "on", "off"),
        default="preserve",
        help=("controlled A/B using Stray's RE-confirmed -rhithread/"
              "-norhithread early launch option; never writes the similarly "
              "named UE console command into Engine.ini"),
    )
    parser.add_argument(
        "--steam-overlay-disable-wait-for-cef-frame", action="store_true",
        help=("run a fresh Steam process with Valve's documented-in-binary "
              "CEF-frame wait disabled for one bounded A/B run"),
    )
    parser.add_argument(
        "--steam-applaunch", action="store_true",
        help=("start a fresh Steam owner with its supported one-shot "
              "-applaunch 1332010 argument; Steam and its overlay retain "
              "game ownership without CEF Library-page navigation"),
    )
    parser.add_argument(
        "--stray-overlay-event-wait-diagnostic", action="store_true",
        help=("bounded fresh-Steam A/B: for the exact current overlay UUID, "
              "replace its 10 ms sem_trywait polling interval with a real "
              "brokered token-or-timeout event wait"),
    )
    parser.add_argument(
        "--stray-crash-trace", action="store_true",
        help=("bounded fresh-Steam diagnostic: enable libmachook's existing "
              "fatal-signal register dump and abort backtrace only in the "
              "exact Stray-Mac-Shipping child"),
    )
    parser.add_argument(
        "--steam-process-diagnostics", action="store_true",
        help=("bounded fresh-Steam diagnostic: record the existing Steam "
              "launch adapter and exact Stray UE4 init/RequestExit/_Exit "
              "boundaries without changing their return values"),
    )
    parser.add_argument(
        "--steam-overlay-frame-time-logging", action="store_true",
        help=("enable Valve's overlay frame-time log for this run and copy "
              "the exact game-PID log into the output directory"),
    )
    parser.add_argument(
        "--steam-semaphore-timing-diagnostics", action="store_true",
        help=("observe production Steam named-semaphore operation counts and "
              "round-trip time without changing wait/token semantics"),
    )
    parser.add_argument(
        "--steam-overlay-no-ui-drawing", action="store_true",
        help=("diagnostic A/B using Valve's SteamNoOverlayUIDrawing switch; "
              "this intentionally removes the FPS HUD and is never a valid "
              "production profile"),
    )
    parser.add_argument(
        "--stray-completion-fps-diagnostic",
        choices=("default", "30", "50", "60", "120"),
        default="default",
        help=("bounded fresh-Steam A/B: leave UE's configured cap unchanged "
              "but publish virtual-display render activity at 30, 50, 60, "
              "or 120 Hz instead of the production 54 Hz; default leaves the "
              "production child environment unchanged"),
    )
    parser.add_argument(
        "--drawable-timing", action="store_true",
        help=("diagnostic-only: time CAMetalLayer nextDrawable and "
              "present-to-command-buffer-completion without bypassing either"),
    )
    parser.add_argument(
        "--direct-drawable-lease", action="store_true",
        help=("bounded A/B: publish completed Stray CAMetalDrawable "
              "IOSurfaces to Host with a versioned cross-process use-count "
              "lease; the normal DisplayStream remains active"),
    )
    parser.add_argument(
        "--direct-drawable-after-gameplay", action="store_true",
        help=("bounded A/B: install the completed-drawable publisher before "
              "launch, but activate it only after the runner visually "
              "confirms real gameplay"),
    )
    parser.add_argument(
        "--wait-trace", action="store_true",
        help=("diagnostic-only: capture up to 24 native callers of the real "
              "MTLCommandBuffer waitUntilCompleted during the bounded sample"),
    )
    parser.add_argument(
        "--disable-display-sync", action="store_true",
        help=("diagnostic A/B: set Stray's public CAMetalLayer "
              "displaySyncEnabled property to NO before nextDrawable"),
    )
    parser.add_argument(
        "--disable-overlay-injection", action="store_true",
        help=("diagnostic A/B: keep the Steam launch and steamloader but do "
              "not inject gameoverlayrenderer; the FPS HUD is intentionally "
              "absent, so this is never a production-profile result"),
    )
    parser.add_argument(
        "--app-input-diagnostics", action="store_true",
        help=("diagnostic-only: record Stray's preserving AppKit input route "
              "for the bounded launch and copy its exact witnesses"),
    )
    parser.add_argument(
        "--capture-metal-libraries", action="store_true",
        help=("diagnostic-only: capture the first 512 byte-exact "
              "newLibraryWithData inputs for offline target conversion"),
    )
    parser.add_argument(
        "--pipeline-diagnostics", action="store_true",
        help=("diagnostic-only: record the real Metal render/compute "
              "pipeline results without enabling render-target capture"),
    )
    parser.add_argument(
        "--render-trace", action="store_true",
        help=("diagnostic-only: map Stray render encoders and pipeline binds "
              "to their real command buffers without exporting textures or "
              "inserting capture blits"),
    )
    parser.add_argument(
        "--gpu-command-error-diagnostics", action="store_true",
        help=("diagnostic-only: retain a bounded 48-entry AGX submit flight "
              "recorder and read-only IOGPU/error-getter witnesses so a "
              "00000102/00000103 failure can be joined to exact bytes"),
    )
    parser.add_argument(
        "--queue-qos-diagnostics", action="store_true",
        help=("diagnostic-only: capture the exact IOGPU command-queue "
              "creation QoS/priority payloads and later priority updates; "
              "no queue setting or submission order is changed"),
    )
    parser.add_argument(
        "--submit-timing-diagnostics", action="store_true",
        help=("diagnostic-only: time the real IOGPU submit external-method "
              "boundary and record its queue scalar/thread; no command, "
              "argument, queue property or submission order is changed"),
    )
    parser.add_argument(
        "--submit-forward-progress-bridge", action="store_true",
        help=("diagnostic A/B: after a selector-0x1a call has entered the "
              "iOS kernel and exceeded the normal 25 ms acceptance window, "
              "allow its later peer event submission to enter concurrently"),
    )
    parser.add_argument(
        "--capture-render-targets", action="store_true",
        help=("diagnostic-only: after verified gameplay, export the macPad "
              "final image, WindowServer base/per-window IOSurfaces and one "
              "bounded six-command-buffer Stray intermediate-target batch"),
    )
    parser.add_argument(
        "--forward-stray-nil-textures", action="store_true",
        help=("diagnostic A/B: forward Stray's real nil texture bindings to "
              "the original AGX encoder instead of the legacy global "
              "nil-binding skip; requires --capture-render-targets"),
    )
    parser.add_argument(
        "--reuse-steam-selection", action="store_true",
        help=("skip Library/search navigation after a preceding run has "
              "intentionally left Stray selected"),
    )
    parser.add_argument("--leave-running", action="store_true")
    parser.add_argument(
        "--user-handoff", action="store_true",
        help=("prepare Steam on Stray's verified PLAY page and return all "
              "input/process control to the user without launching or "
              "instrumenting the game"),
    )
    parser.add_argument(
        "--keep-steam-running", action="store_true",
        help=("stop Stray after the sample but retain a verified production "
              "Steam UI for faster subsequent non-environmental runs"),
    )
    parser.add_argument(
        "--quiesce-steam-ui-during-game", action="store_true",
        help=("bounded lifecycle A/B: after gameplay is visually verified, "
              "suspend the exact steam_osx owner and retire only its exact "
              "pre-Play CEF helpers; resume steam_osx after game cleanup"),
    )
    parser.add_argument(
        "--verbose-json", action="store_true",
        help="also print the complete evidence JSON already written to disk",
    )
    args = parser.parse_args()
    if not args.sudo_password:
        parser.error("provide --sudo-password or MACWS_DEVICE_SUDO_PASSWORD")
    if args.direct_drawable_lease and args.direct_drawable_after_gameplay:
        parser.error(
            "--direct-drawable-lease and "
            "--direct-drawable-after-gameplay are mutually exclusive"
        )
    if args.direct_drawable_after_gameplay and not args.progress_to_gameplay:
        parser.error(
            "--direct-drawable-after-gameplay requires "
            "--progress-to-gameplay"
        )
    if args.cool_game_before_sample and not args.progress_to_gameplay:
        parser.error(
            "--cool-game-before-sample requires --progress-to-gameplay"
        )
    if args.user_handoff:
        args.leave_running = True
        forbidden_handoff = {
            "--gameplay-hover-probe": args.gameplay_hover_probe,
            "--gameplay-host-hover-probe": args.gameplay_host_hover_probe,
            "--gameplay-host-long-drag-probe":
                args.gameplay_host_long_drag_probe,
            "--gameplay-walk-probe": args.gameplay_walk_probe,
            "--cool-game-before-sample": args.cool_game_before_sample,
            "--process-sample-seconds": args.process_sample_seconds > 0,
            "--windowserver-sample-seconds":
                args.windowserver_sample_seconds > 0,
            "--drawable-timing": args.drawable_timing,
            "--direct-drawable-lease": args.direct_drawable_lease,
            "--direct-drawable-after-gameplay":
                args.direct_drawable_after_gameplay,
            "--wait-trace": args.wait_trace,
            "--capture-metal-libraries": args.capture_metal_libraries,
            "--pipeline-diagnostics": args.pipeline_diagnostics,
            "--render-trace": args.render_trace,
            "--gpu-command-error-diagnostics":
                args.gpu_command_error_diagnostics,
            "--submit-timing-diagnostics":
                args.submit_timing_diagnostics,
            "--submit-forward-progress-bridge":
                args.submit_forward_progress_bridge,
            "--capture-render-targets": args.capture_render_targets,
            "--forward-stray-nil-textures":
                args.forward_stray_nil_textures,
            "--disable-display-sync": args.disable_display_sync,
            "--disable-overlay-injection": args.disable_overlay_injection,
            "--steam-overlay-disable-wait-for-cef-frame":
                args.steam_overlay_disable_wait_for_cef_frame,
            "--steam-applaunch": args.steam_applaunch,
            "--stray-overlay-event-wait-diagnostic":
                args.stray_overlay_event_wait_diagnostic,
            "--stray-crash-trace": args.stray_crash_trace,
            "--steam-process-diagnostics":
                args.steam_process_diagnostics,
            "--steam-overlay-frame-time-logging":
                args.steam_overlay_frame_time_logging,
            "--steam-semaphore-timing-diagnostics":
                args.steam_semaphore_timing_diagnostics,
            "--steam-overlay-no-ui-drawing":
                args.steam_overlay_no_ui_drawing,
            "--stray-completion-fps-diagnostic":
                args.stray_completion_fps_diagnostic != "default",
            "--quiesce-steam-ui-during-game":
                args.quiesce_steam_ui_during_game,
        }
        enabled_handoff_diagnostics = [
            name for name, enabled in forbidden_handoff.items() if enabled
        ]
        if enabled_handoff_diagnostics:
            parser.error(
                "--user-handoff forbids test controls/diagnostics: " +
                ", ".join(enabled_handoff_diagnostics)
            )
    if ((args.resolution_width == 0) != (args.resolution_height == 0)):
        parser.error("set both resolution dimensions or neither")
    if args.preflight_only and args.steam_preflight_only:
        parser.error("choose only one preflight-only mode")
    if args.steam_applaunch and args.reuse_steam_selection:
        parser.error(
            "--steam-applaunch does not use a retained CEF selection"
        )
    if args.steam_applaunch and (
            args.preflight_only or args.steam_preflight_only):
        parser.error(
            "--steam-applaunch launches a game and is not a preflight mode"
        )
    if args.leave_running and args.quiesce_steam_ui_during_game:
        parser.error(
            "--leave-running cannot leave the Steam owner suspended"
        )
    if args.leave_running and args.capture_metal_libraries:
        parser.error(
            "--capture-metal-libraries is bounded and cannot be combined "
            "with --leave-running"
        )
    if args.capture_render_targets and not args.progress_to_gameplay:
        parser.error(
            "--capture-render-targets requires --progress-to-gameplay"
        )
    if args.forward_stray_nil_textures and not args.capture_render_targets:
        parser.error(
            "--forward-stray-nil-textures requires "
            "--capture-render-targets"
        )
    if (args.temperature_ceiling <= 0 or
            args.start_temperature_ceiling <= 0 or
            args.sample_seconds <= 0 or
            not 0 <= args.process_sample_seconds <= 5 or
            not 0 <= args.windowserver_sample_seconds <= 5 or
            args.cool_stable_samples < 1 or
            args.steam_start_timeout <= 0 or
            args.steam_ready_timeout <= 0 or
            args.background_idle_timeout <= 0 or
            args.background_cpu_ceiling <= 0 or
            args.background_total_cpu_ceiling <= 0 or
            args.workspace_timeout <= 0 or
            args.workspace_start_timeout <= 0 or
            args.gameplay_timeout <= 0 or args.gameplay_load_delay < 0 or
            args.hover_observation_seconds <= 0 or
            args.thermal_interval <= 0 or args.enter_count < 0 or
            args.enter_interval <= 0 or
            args.walk_hold <= 0 or args.walk_observation_seconds <= 0 or
            args.rfb_return_count < 0 or args.rfb_key_hold < 0 or
            not 0 < args.steam_play_x < 1 or
            not 0 <= args.steam_play_y < 1 or
            not 0 < args.game_start_x < 1 or
            not 0 < args.game_start_y < 1 or
            args.game_start_hold < 0 or
            (args.max_fps != 0 and not 30 <= args.max_fps <= 120) or
            (args.uncapped and args.max_fps != 0) or
            not 10 <= args.screen_percentage <= 100):
        parser.error("invalid thermal, timing, input, or coordinate setting")
    if args.allow_serious_functional and (
            args.sample_seconds > 5 or args.leave_running or
            args.user_handoff):
        parser.error(
            "--allow-serious-functional requires sample-seconds <= 5 and a "
            "bounded run (no --leave-running/--user-handoff)"
        )

    args.output.mkdir(parents=True, exist_ok=True)
    result_path = args.output / "result.json"
    screenshot_path = args.output / "frame.png"
    window_screenshot_path = args.output / "stray-window.png"
    remote = Remote(args.host, args.user, args.ssh_port, args.sudo_password)
    result = {
        "result": "INCOMPLETE",
        "lifecycle_policy": (
            "user-owned-after-verified-Steam-PLAY-page"
            if args.user_handoff else
            ("leave-game-running" if args.leave_running else "bounded-test")
        ),
        "thermal_valid": True,
        "thermal_policy": {
            "prelaunch_admission_state": (
                "nominal-fair-or-serious-functional-only"
                if args.allow_serious_functional else
                ("nominal-or-fair-functional-only"
                 if args.allow_fair_functional else "nominal")
            ),
            "runtime_state": "observe-only",
            "process_control": "none",
            "numeric_temperature_enforced": False,
            "temperature_c": "observed-only",
            "poll_interval_seconds": args.thermal_interval,
            "charging": "observed-only",
            "legacy_cli_temperature_values": {
                "temperature_ceiling_c": args.temperature_ceiling,
                "start_temperature_ceiling_c":
                    args.start_temperature_ceiling,
            },
        },
        "thermal": [],
    }

    last_thermal_checkpoint = {"time": 0.0}

    def thermal_checkpoint(stage: str, force: bool = False):
        """Rate-limited observer usable inside non-sleep startup poll loops."""
        now = time.monotonic()
        if (not force and now - last_thermal_checkpoint["time"] <
                args.thermal_interval):
            return None
        sample = {**thermal_snapshot(remote), "stage": stage}
        last_thermal_checkpoint["time"] = time.monotonic()
        result["thermal"].append(sample)
        result.setdefault("thermal_startup", {}).setdefault(
            stage, []
        ).append(sample)
        if sample["state"] == "unknown" or not thermally_safe(
                sample, args.temperature_ceiling):
            result["thermal_valid"] = False
            result.setdefault("thermal_pressure_observed", []).append(sample)
        # The explicit fair-state mode exists only to unblock a short
        # functional preflight. If pressure escalates before Steam has even
        # produced a Stray PID, continuing an expensive desktop cold start
        # cannot yield a usable game witness. Abort that pre-game transaction
        # promptly. Once Stray exists, thermal telemetry remains observation-
        # only as required; this path never signals or exits the game.
        if (args.allow_fair_functional and
                not args.allow_serious_functional and pid <= 1 and
                sample["state"] in ("serious", "critical")):
            raise RuntimeError(
                "functional-only prelaunch thermal escalation: " +
                sample["raw"]
            )
        if (args.allow_serious_functional and pid <= 1 and
                sample["state"] == "critical"):
            raise RuntimeError(
                "serious-only functional prelaunch reached critical: " +
                sample["raw"]
            )
        return sample

    def thermally_guarded_pause(stage: str, duration: float):
        """Wait without leaving a startup/load heat soak unobserved."""
        started = time.monotonic()
        deadline = time.monotonic() + max(0.0, duration)
        samples = []
        last_report = float("-inf")
        while True:
            sample = thermal_checkpoint(stage, force=True)
            samples.append(sample)
            elapsed = time.monotonic() - started
            if elapsed - last_report >= 30.0:
                print(
                    f"[stray-perf] stage={stage} elapsed={elapsed:.1f}s "
                    f"state={sample['state']}", flush=True,
                )
                last_report = elapsed
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(args.thermal_interval, remaining))
        return sample
    overlay_test_environment = {}
    if args.rhi_thread != "preserve":
        overlay_test_environment["MACWS_STRAY_RHI_THREAD_SELECTOR"] = \
            args.rhi_thread
    if args.steam_overlay_disable_wait_for_cef_frame:
        overlay_test_environment[
            "MACWS_STRAY_OVERLAY_DISABLE_CEF_WAIT_DIAGNOSTIC"
        ] = "1"
    if args.stray_overlay_event_wait_diagnostic:
        overlay_test_environment[
            "MACWS_STRAY_OVERLAY_EVENT_WAIT_DIAGNOSTIC_SELECTOR"
        ] = "1"
    if args.stray_crash_trace:
        overlay_test_environment["MACWS_STRAY_CRASH_TRACE_SELECTOR"] = "1"
    if args.steam_process_diagnostics:
        overlay_test_environment["MACWS_STEAM_PROCESS_DIAGNOSTICS"] = "1"
    if args.steam_overlay_frame_time_logging:
        overlay_test_environment["STEAM_OVERLAY_FRAME_TIME_LOGGING"] = "1"
        overlay_test_environment["STEAM_OVERLAY_LOGGING_FLUSH"] = "1"
    if args.steam_semaphore_timing_diagnostics:
        overlay_test_environment["MACWS_STEAM_SEM_TIMING_DIAGNOSTICS"] = "1"
    if args.steam_overlay_no_ui_drawing:
        overlay_test_environment["SteamNoOverlayUIDrawing"] = "1"
    if args.stray_completion_fps_diagnostic != "default":
        overlay_test_environment[
            "MACWS_STRAY_COMPLETION_FPS_DIAGNOSTIC"
        ] = args.stray_completion_fps_diagnostic
    if args.keep_steam_running and overlay_test_environment:
        parser.error(
            "--keep-steam-running cannot retain a diagnostic overlay "
            "environment"
        )
    result["steam_overlay_environment_requested"] = overlay_test_environment
    pid = 0
    steam_was_running = False
    steam_started_by_pipeline = False
    overlay_environment_configured = False
    markers_to_touch = []
    compiler_pids_before = set()
    retain_steam_preflight = False
    restore_control_center = False
    cooldown_suspended_steam = set()
    cooldown_steam_resumed = False
    steam_ui_pids_before_play = set()
    steam_ui_inventory_before_play = {"steam": set(), "helpers": set()}
    steam_game_quiesced_pids = set()
    steam_signal_agent = None
    thermal_stream = None
    gpu_power_stream = None
    gpu_power_sample_mark = None
    runtime_log_stream = None
    host_log_stream = None
    input_agent = None
    sample_agent = None
    gui_transaction_lease_held = False
    diagnostic_markers_armed = False
    device_safety_armed = False
    game_precondition_stopped = False
    cooling_render_path = {}
    cooling_render_path_stopped = False
    try:
        thermal_stream = RemoteThermalStream(remote, args.thermal_interval)
        remote.thermal_stream = thermal_stream
        result["thermal_stream"] = {
            "armed_before_game": True,
            "transport": "dedicated-non-multiplexed-ssh",
            "device_process": "macwsthermal stream",
            "process_control": "none",
        }
        if (not args.preflight_only and not args.steam_preflight_only and
                not args.leave_running):
            # Disarm watchdogs left by older builds. Thermal pressure remains
            # in the result as telemetry and never changes the game process.
            result["device_safety"] = arm_stray_safety(remote)
            device_safety_armed = result["device_safety"]["armed"]
        if args.preflight_only:
            if args.skip_workspace_preflight:
                raise RuntimeError(
                    "--preflight-only conflicts with --skip-workspace-preflight"
                )
            result["workspace_preflight"] = workspace_preflight(
                remote, args.workspace_timeout, args.workspace_start_timeout,
                thermal_checkpoint,
            )
            idle, idle_history = wait_for_idle_background(
                remote, args.background_cpu_ceiling,
                args.background_total_cpu_ceiling,
                args.background_idle_timeout,
                labels=QUIET_DESKTOP_LABELS,
            )
            result["background_cpu_preflight"] = idle
            result["background_cpu_history"] = idle_history
            result["result"] = "OK"
            return

        result["power_preflight"] = power_snapshot(remote)

        if args.steam_preflight_only:
            # Match the normal benchmark's thermal setup.  Runtime on
            # 2026-08-21 measured the otherwise-idle macOS ControlCenter at
            # roughly 4-7% CPU; leaving it alive made the reusable-Steam
            # preflight retain avoidable CPU load before the stable-nominal
            # admission samples.
            if not args.keep_control_center_during_sample:
                result["control_center_cooldown_mode"] = \
                    suspend_control_center_for_game(remote)
                restore_control_center = result[
                    "control_center_cooldown_mode"
                ]["was_running"]
            initial_cool, initial_history = wait_for_cool_device(
                remote, args.start_temperature_ceiling,
                args.cooldown_timeout,
                args.thermal_interval, args.cool_stable_samples,
                progress_label="steam-preflight-initial-cooldown",
            )
            result["thermal"].extend(initial_history)
            result["thermal_initial_preflight"] = initial_cool
            if not args.skip_workspace_preflight:
                result["workspace_preflight"] = workspace_preflight(
                    remote, args.workspace_timeout,
                    args.workspace_start_timeout,
                    thermal_checkpoint,
                )
            result["steam_ipctool"] = ensure_steam_ipctool(remote)
            steam_was_running = maybe_steam_helper_pid(remote) > 1
            steam_started_by_pipeline = not steam_was_running
            result["steam_was_running"] = steam_was_running
            login_size = remote_file_size(remote, STEAM_LOGIN_LOG)
            webhelper_js_size = remote_file_size(
                remote, STEAM_WEBHELPER_JS_LOG
            )
            webhelper_size = remote_file_size(remote, STEAM_WEBHELPER_LOG)
            login_offset = (max(0, login_size - 2_000_000)
                            if steam_was_running else login_size)
            webhelper_js_offset = (
                max(0, webhelper_js_size - 2_000_000)
                if steam_was_running else webhelper_js_size
            )
            webhelper_offset = (max(0, webhelper_size - 2_000_000)
                                if steam_was_running else webhelper_size)
            result["steam_fps_overlay_prepare"] = \
                ensure_steam_fps_overlay_top_left(remote)
            result["steam_preflight"] = ensure_steam_ui(
                remote, args.steam_start_timeout, thermal_checkpoint
            )
            result["steam_library_ready"] = wait_for_steam_library_ready(
                remote, login_offset, webhelper_js_offset, webhelper_offset,
                args.steam_ready_timeout, thermal_checkpoint,
            )
            result["steam_ready_capture"] = capture_exact_window(
                remote, result["steam_preflight"]["window"]["window"],
                args.output / "steam-ready.png",
            )
            result["steam_fps_overlay_config"] = \
                steam_fps_overlay_config(remote)
            result["steam_hide"] = send_steam_key(
                remote, result["steam_preflight"]["pid"],
                result["steam_preflight"]["window"], key="h", command=True,
            )
            steam_idle, steam_idle_history = wait_for_idle_background(
                remote, args.background_cpu_ceiling,
                args.background_total_cpu_ceiling,
                args.background_idle_timeout,
                labels={"Steam", "Steam Helper"},
            )
            result["steam_background_after_hide"] = steam_idle
            result["steam_background_after_hide_history"] = \
                steam_idle_history
            # The retained CEF/UI processes are useful for the next run but
            # still consume measurable CPU while we wait for the iPad to cool.
            # Freeze only the exact PIDs observed here, then resume the same
            # set before returning the warm launcher to the caller.
            cooldown_suspended_steam = steam_ui_process_pids(remote)
            stopped = signal_exact_pids(
                remote, cooldown_suspended_steam, "STOP"
            )
            if stopped:
                result["steam_cooldown_suspend"] = {
                    "pids": stopped, "action": "SIGSTOP"
                }
            cooled, cooldown = wait_for_cool_device(
                remote, args.start_temperature_ceiling,
                args.cooldown_timeout,
                args.thermal_interval, args.cool_stable_samples,
                progress_label="steam-preflight-retained-cooldown",
            )
            result["thermal"].extend(cooldown)
            result["thermal_before"] = cooled
            if cooldown_suspended_steam:
                resumed = signal_exact_pids(
                    remote, cooldown_suspended_steam, "CONT"
                )
                result["steam_cooldown_resume"] = {
                    "pids": resumed, "action": "SIGCONT"
                }
                cooldown_steam_resumed = True
            retain_steam_preflight = True
            result["result"] = "OK"
            return

        # A benchmark invocation owns replacement of an earlier Stray run.
        # Retire that exact executable before the nominal-temperature gate:
        # waiting first is a self-deadlock because the stale game continues
        # submitting at full rate and is itself the dominant heat source.
        # User handoff remains strictly observation-only and refuses to touch
        # an existing game before changing any other session state.
        stale_pid = game_pid(remote)
        if args.user_handoff:
            if stale_pid > 1:
                raise RuntimeError(
                    "--user-handoff found an already-running Stray process; "
                    "refusing to signal or replace a user-owned game"
                )
            result["stale_game_cleanup"] = {
                "pid": 0,
                "signal": None,
                "confirmed_exited": True,
                "policy": "observation-only",
            }
        else:
            result["stale_game_cleanup"] = terminate_exact_game(
                remote, stale_pid
            )
            if game_pid(remote):
                raise RuntimeError("a stale Stray process survived cleanup")

        # ControlCenter was runtime-observed at roughly 4-7% CPU on the idle
        # chroot desktop.  Remove that measured heat source before the initial
        # temperature gate, not only after it: otherwise the runner can spend
        # longer waiting for a stable nominal state while keeping the process
        # that adds avoidable load alive.  The finally path restores it iff this run
        # actually found the workspace agent running.
        if not args.keep_control_center_during_sample:
            result["control_center_cooldown_mode"] = \
                suspend_control_center_for_game(remote)
            restore_control_center = result[
                "control_center_cooldown_mode"
            ]["was_running"]

        # A retained, hidden Steam saves the expensive login/CEF cold start,
        # but its idle helpers still consumed about 6% CPU in the measured
        # process snapshot.  Freeze only those exact Valve UI PIDs while the
        # device and workspace pass their cold gates; resume the same PIDs
        # before any Steam contract check.  The finally path also resumes them
        # after every exceptional exit.
        if not overlay_test_environment:
            cooldown_suspended_steam = steam_ui_process_pids(remote)
            stopped = signal_exact_pids(
                remote, cooldown_suspended_steam, "STOP"
            )
            if stopped:
                result["steam_cooldown_suspend"] = {
                    "pids": stopped, "action": "SIGSTOP"
                }
        if args.cool_render_path:
            cooling_render_path = render_path_process_inventory(remote)
            required_render_labels = {"WindowServer", "macwsdisplayd"}
            if not cooling_render_path:
                # A deliberate cleanup/cold start is already the strongest
                # initial render-path cooldown.  Do not require processes to
                # exist merely so they can be stopped.  The later gameplay
                # preconditioning phase still requires and validates the
                # complete live pair before signaling either PID.
                result["initial_render_path_cooling_suspend"] = {
                    "inventory": {},
                    "pids": [],
                    "action": "none-already-stopped",
                    "game_exit": False,
                    "MacWSHost": "not-running-before-cold-start",
                }
            elif set(cooling_render_path) != required_render_labels:
                raise RuntimeError(
                    "render-path cooling requires a live fullscreen stack: "
                    f"expected={sorted(required_render_labels)} "
                    f"actual={sorted(cooling_render_path)}"
                )
            else:
                stopped = signal_exact_render_path(
                    remote, cooling_render_path, "STOP"
                )
                cooling_render_path_stopped = True
                result["initial_render_path_cooling_suspend"] = {
                    "inventory": cooling_render_path,
                    "pids": stopped,
                    "action": "SIGSTOP",
                    "game_exit": False,
                    "MacWSHost": "runnable-last-drawable-visible",
                }
        try:
            initial_cool, initial_history = wait_for_cool_device(
                remote, args.start_temperature_ceiling,
                args.cooldown_timeout,
                args.thermal_interval, args.cool_stable_samples,
                (("nominal", "fair", "serious")
                 if args.allow_serious_functional else
                 (("nominal", "fair") if args.allow_fair_functional
                  else ("nominal",))),
                progress_label="initial-prelaunch-cooldown",
            )
        finally:
            if cooling_render_path_stopped:
                resumed = signal_exact_render_path(
                    remote, cooling_render_path, "CONT"
                )
                cooling_render_path_stopped = False
                time.sleep(0.25)
                resumed_inventory = render_path_process_inventory(remote)
                result["initial_render_path_cooling_resume"] = {
                    "pids": resumed,
                    "action": "SIGCONT",
                    "inventory": resumed_inventory,
                }
                for label, expected in cooling_render_path.items():
                    actual = resumed_inventory.get(label)
                    if (actual is None or actual["pid"] != expected["pid"] or
                            actual["state"].startswith("T")):
                        raise RuntimeError(
                            "render path did not resume after thermal "
                            f"preflight: {label} actual={actual}"
                        )
                cooling_render_path = {}
        result["thermal"].extend(initial_history)
        result["thermal_initial_preflight"] = initial_cool
        compiler_pids_before = mtl_compiler_service_pids(remote)
        result["mtl_compiler_services_before"] = sorted(
            compiler_pids_before
        )
        if overlay_test_environment:
            # Environment is inherited when the registered Steam job starts;
            # an existing Steam process cannot observe launchctl's new values.
            # Restart only Steam for explicit diagnostic A/B runs.
            steam_stop_survivors = stop_steam_ui(remote)
            time.sleep(2.0)
            if steam_stop_survivors:
                raise RuntimeError(
                    "Steam processes survived the requested fresh-process "
                    "A/B setup: " + "; ".join(steam_stop_survivors)
                )
        result["steam_overlay_environment"] = \
            configure_steam_overlay_test_environment(
                remote, overlay_test_environment
            )
        overlay_environment_configured = True
        if not args.skip_workspace_preflight:
            result["workspace_preflight"] = workspace_preflight(
                remote, args.workspace_timeout, args.workspace_start_timeout,
                thermal_checkpoint,
            )
        if not args.keep_control_center_during_sample:
            result["control_center_gaming_mode"] = \
                suspend_control_center_for_game(remote)
            restore_control_center = (
                restore_control_center or result[
                    "control_center_gaming_mode"
                ]["was_running"]
            )
        # Gate the compositor/desktop before starting Steam.  Its CPU-only CEF
        # renderer can legitimately redraw the visible Library page at more
        # than one core while automation searches for Stray; waiting for that
        # renderer to become idle before clicking Play creates a deadlock and
        # turns a short launcher phase into an avoidable heat soak.
        idle, idle_history = wait_for_idle_background(
            remote, args.background_cpu_ceiling,
            args.background_total_cpu_ceiling,
            args.background_idle_timeout,
            labels=QUIET_DESKTOP_LABELS,
        )
        result["background_cpu_preflight"] = idle
        result["background_cpu_history"] = idle_history
        # The full stable-nominal dwell already completed immediately above.
        # Revalidate after the short desktop-idle check, but do not repeat a
        # multi-minute dwell from zero; that doubled every deliberately long
        # cooldown without adding a distinct thermal invariant.
        thermal_recheck_samples = min(args.cool_stable_samples, 2)
        preflight, cooldown = wait_for_cool_device(
            remote, args.start_temperature_ceiling,
            args.cooldown_timeout,
            args.thermal_interval, thermal_recheck_samples,
            (("nominal", "fair", "serious")
             if args.allow_serious_functional else
             (("nominal", "fair") if args.allow_fair_functional
              else ("nominal",))),
            progress_label="workspace-prelaunch-cooldown",
        )
        result["thermal_recheck_samples"] = thermal_recheck_samples
        result["thermal"].extend(cooldown)
        result["thermal_before"] = preflight
        if not args.user_handoff:
            try:
                # workspace_preflight has now made autosignd and the chroot
                # execution path available.  Arm before Steam/Stray so the
                # peak sample requires no new privileged process creation.
                gpu_power_stream = RemoteGPUPowerStream(remote)
                result["gpu_power_stream"] = {
                    "armed_before_game": True,
                    "transport": "dedicated-non-multiplexed-ssh-pty",
                    "device_process": (
                        "chroot powermetrics --samplers gpu_power,thermal"
                    ),
                    "process_control": "none",
                }
            except Exception as gpu_power_stream_error:
                # This observer is attribution evidence, not a launch
                # precondition.  Preserve a concrete error and continue the
                # otherwise valid benchmark.
                result["gpu_power_stream_error"] = str(
                    gpu_power_stream_error
                )
        result["quality_requested"] = args.quality
        result["profile_requested"] = apply_quality_profile(
            remote, args.quality,
            args.resolution_width, args.resolution_height,
            args.screen_percentage, args.scaling_solution, args.max_fps,
            args.uncapped, args.ue_fullscreen_mode,
        )
        # Runtime log from Stray PID 43046 proves r.RHIThread.Enable is a
        # console command, not a SystemSettings CVar: putting it in Engine.ini
        # makes UE abort on a conflicting object type.  Clean up the one stale
        # diagnostic key before every launch; RHI threading A/B is selected by
        # the engine's early -rhithread/-norhithread argument instead.
        result["invalid_engine_system_settings_removed"] = \
            remove_engine_system_settings(remote, ["r.RHIThread.Enable"])
        engine_settings = {}
        if args.pre_exposure != "preserve":
            engine_settings["r.UsePreExposure"] = \
                "1" if args.pre_exposure == "on" else "0"
        if args.eye_adaptation_method != "preserve":
            engine_settings["r.EyeAdaptation.MethodOverride"] = {
                "histogram": "1", "basic": "2", "manual": "3",
            }[args.eye_adaptation_method]
        if args.eye_adaptation_quality != "preserve":
            engine_settings["r.EyeAdaptationQuality"] = {
                "off": "0", "low": "1", "normal": "2", "high": "3",
            }[args.eye_adaptation_quality]
        if args.occlusion_queries != "preserve":
            engine_settings["r.AllowOcclusionQueries"] = (
                "1" if args.occlusion_queries == "on" else "0"
            )
        if args.upscale_quality == "default":
            result["upscale_engine_system_settings_removed"] = \
                remove_engine_system_settings(remote, ["r.Upscale.Quality"])
        elif args.upscale_quality != "preserve":
            engine_settings["r.Upscale.Quality"] = {
                "nearest": "0", "bilinear": "1",
            }[args.upscale_quality]
        if args.uncapped:
            engine_settings["t.MaxFPS"] = "0"
        elif args.max_fps:
            engine_settings["t.MaxFPS"] = str(args.max_fps)
        if engine_settings:
            result["engine_system_settings_requested"] = \
                apply_engine_system_settings(
                    remote, engine_settings,
                )
        else:
            result["engine_system_settings_requested"] = \
                read_engine_system_settings(
                    remote, [
                        "r.UsePreExposure",
                        "r.EyeAdaptation.MethodOverride",
                        "r.EyeAdaptationQuality",
                        "r.AllowOcclusionQueries",
                        "r.Upscale.Quality",
                        "t.MaxFPS",
                    ]
                )
        result["profile_timeline"] = [{
            "stage": "after-apply",
            "time": time.time(),
            "values": result["profile_requested"],
        }]
        # The broad diagnostics marker changes the workload.  Keep this run to
        # the bounded present counter only.
        markers_to_touch = [] if args.user_handoff else [PRESENT_MARKER]
        if args.drawable_timing:
            markers_to_touch.append(DRAWABLE_TIMING_MARKER)
        if (args.direct_drawable_lease or
                args.direct_drawable_after_gameplay):
            markers_to_touch.append(DIRECT_DRAWABLE_LEASE_MARKER)
        if args.direct_drawable_lease:
            markers_to_touch.append(DIRECT_DRAWABLE_ACTIVE_MARKER)
        if args.wait_trace:
            markers_to_touch.append(WAIT_TRACE_INSTALL_MARKER)
        if args.disable_display_sync:
            markers_to_touch.append(DISABLE_DISPLAY_SYNC_MARKER)
        if args.disable_overlay_injection:
            markers_to_touch.append(NO_OVERLAY_INJECTION_MARKER)
        if args.app_input_diagnostics:
            markers_to_touch.append(APP_INPUT_DIAGNOSTICS_MARKER)
        if args.capture_metal_libraries:
            markers_to_touch.append(MTL_DATA_DIAGNOSTIC_MARKER)
        if args.pipeline_diagnostics:
            markers_to_touch.append(PIPELINE_DIAGNOSTIC_MARKER)
        if args.render_trace:
            markers_to_touch.append(RENDER_TRACE_MARKER)
        if args.capture_render_targets:
            markers_to_touch.extend((
                RENDER_TRACE_MARKER,
                PIPELINE_DIAGNOSTIC_MARKER,
            ))
        if args.forward_stray_nil_textures:
            markers_to_touch.append(FORWARD_NIL_TEXTURE_MARKER)
        if args.gpu_command_error_diagnostics:
            markers_to_touch.extend((
                IOGPU_ERROR_DIAGNOSTIC_MARKER,
                COMMAND_ERROR_DIAGNOSTIC_MARKER,
                SUBMIT_FAST_RING_MARKER,
            ))
        if args.queue_qos_diagnostics:
            markers_to_touch.append(QUEUE_QOS_DIAGNOSTIC_MARKER)
        if args.submit_timing_diagnostics:
            markers_to_touch.append(SUBMIT_TIMING_DIAGNOSTIC_MARKER)
        if args.submit_forward_progress_bridge:
            markers_to_touch.append(SUBMIT_FORWARD_PROGRESS_BRIDGE_MARKER)
        remote.sudo(
            "rm -f " + " ".join(
                shlex.quote(path) for path in DIAGNOSTIC_MARKERS
            )
        )
        remaining_diagnostics = remote.run(
            "for marker in " + " ".join(
                shlex.quote(path) for path in DIAGNOSTIC_MARKERS
            ) + "; do test ! -e \"$marker\" || echo \"$marker\"; done",
            check=False,
        ).splitlines()
        if remaining_diagnostics:
            raise RuntimeError(
                "diagnostic markers survived cleanup: " +
                ", ".join(remaining_diagnostics)
            )
        if args.capture_metal_libraries:
            stale_captures = remote.sudo(
                "find /var/mnt/rootfs/private/tmp -maxdepth 1 -type f "
                "-name 'macws_mtl_data_*.bin' -print",
                check=False,
            ).splitlines()
            if stale_captures:
                raise RuntimeError(
                    "archive existing Metal captures before a new bounded "
                    "capture: " + ", ".join(stale_captures[:8])
                )
        if args.capture_render_targets:
            # These two locations contain only artifacts owned by this exact
            # bounded diagnostic.  Clear them before Stray reaches its peak
            # allocation point so later collection never needs to guess
            # whether a file belongs to the current PID/generation.
            result["render_target_stale_cleanup"] = {
                "raw": remote.sudo(
                    "find /var/mnt/rootfs/private/tmp -maxdepth 1 -type f "
                    "\\( -name 'macws_stray_rt_c*.raw' -o "
                    "-name 'macws_stray_buf_c*.raw' -o "
                    "-name 'macws_stray_stage_*.raw' -o "
                    "-name 'macws_stray_shadow_stage_*.raw' -o "
                    "-name 'macws_stray_compute_stage_*.raw' -o "
                    "-name 'macws_stray_input_*.raw' \\) -delete -print",
                    check=False,
                ).splitlines(),
                "layers": remote.run(
                    "find " + shlex.quote(HOST_LAYER_DIRECTORY) +
                    " -maxdepth 1 -type f -name '*.png' -delete -print",
                    check=False,
                ).splitlines(),
            }
        result["diagnostic_markers_active"] = markers_to_touch[1:]
        if cooldown_suspended_steam:
            resumed = signal_exact_pids(
                remote, cooldown_suspended_steam, "CONT"
            )
            result["steam_cooldown_resume"] = {
                "pids": resumed, "action": "SIGCONT"
            }
            cooldown_steam_resumed = True
        result["steam_ipctool"] = ensure_steam_ipctool(remote)
        steam_was_running = maybe_steam_helper_pid(remote) > 1
        steam_started_by_pipeline = not steam_was_running
        result["steam_was_running"] = steam_was_running
        if args.steam_applaunch:
            if steam_was_running:
                raise RuntimeError(
                    "--steam-applaunch requires a fresh Steam owner; stop "
                    "Steam first or combine it with a fresh-process A/B"
                )
            observers = arm_game_launch_observers(remote, args, result)
            offset = observers["offset"]
            runtime_log_stream = observers["runtime_log_stream"]
            host_log_stream = observers["host_log_stream"]
            input_agent = observers["input_agent"]
            sample_agent = observers["sample_agent"]
            result["gui_transaction_lease"] = \
                input_agent.acquire_gui_lease()
            gui_transaction_lease_held = True
            if markers_to_touch:
                result["diagnostic_marker_arm"] = \
                    input_agent.set_diagnostic_markers(
                        markers_to_touch, True
                    )
                diagnostic_markers_armed = True
            # ensure_steam_ui publishes this validated decimal AppID before
            # every bounded job attempt. prepare_steam_runtime.sh consumes it
            # exactly once and passes -applaunch to the launchd-owned Steam
            # process. Steam still performs its normal LaunchApp, cloud,
            # overlay and NSWorkspace transactions.
            result["steam_applaunch_request"] = {
                "appid": 1332010,
                "transport": "launchd-owned Steam one-shot argument",
                "bypasses_steam_game_launch": False,
                "bypasses_cef_library_navigation": True,
            }
        # A fresh process must produce fresh login/CEF readiness witnesses.
        # For an explicitly reused process, retain a bounded tail and verify
        # that the latest state in that process is ready rather than accepting
        # a historical success followed by a disconnect.
        login_size = remote_file_size(remote, STEAM_LOGIN_LOG)
        webhelper_js_size = remote_file_size(remote, STEAM_WEBHELPER_JS_LOG)
        webhelper_size = remote_file_size(remote, STEAM_WEBHELPER_LOG)
        login_offset = (max(0, login_size - 2_000_000)
                        if steam_was_running else login_size)
        webhelper_js_offset = (
            max(0, webhelper_js_size - 2_000_000)
            if steam_was_running else webhelper_js_size
        )
        webhelper_offset = (max(0, webhelper_size - 2_000_000)
                            if steam_was_running else webhelper_size)
        result["steam_fps_overlay_prepare"] = \
            ensure_steam_fps_overlay_top_left(remote)
        launch_by_applaunch = args.steam_applaunch
        result["steam_preflight"] = ensure_steam_ui(
            remote, args.steam_start_timeout, thermal_checkpoint,
            applaunch_appid=1332010 if args.steam_applaunch else 0,
        )
        if args.steam_applaunch:
            result["steam_library_ready"] = {
                "skipped": "CEF Library page is not part of -applaunch",
            }
        elif steam_was_running:
            # A retained owner has no new `Starting login` line after the
            # runner's offset.  Requiring one made r15 poll unchanged history
            # forever even though the exact current window visibly exposed
            # Steam's interactive top navigation.  Validate that live surface
            # directly; the following Library postcondition still decides
            # whether UI Play or the normal running-client LaunchApp is used.
            retained_capture = capture_exact_window(
                remote, result["steam_preflight"]["window"]["window"],
                args.output / "steam-ready.png",
            )
            retained_text = recognize_exact_window(retained_capture)
            retained_ready = (
                "STORE" in retained_text and
                "LIBRARY" in retained_text
            )
            if not retained_ready:
                raise RuntimeError(
                    "retained Steam owner has no visible STORE/LIBRARY "
                    "surface: " + retained_text[-2000:]
                )
            result["steam_library_ready"] = {
                "reused_process": True,
                "witness": "visible STORE and LIBRARY top navigation",
                "recognized_text": retained_text[-2000:],
            }
            result["steam_ready_capture"] = retained_capture
        else:
            result["steam_library_ready"] = wait_for_steam_library_ready(
                remote, login_offset, webhelper_js_offset, webhelper_offset,
                args.steam_ready_timeout, thermal_checkpoint,
            )
        if args.steam_applaunch:
            preflight_window = result["steam_preflight"]["window"]
            result["steam_ready_capture"] = {
                "skipped": (
                    "one-shot LaunchApp is already in flight; do not race "
                    "a valueless CEF spinner capture against game creation"
                ),
                "width": max(1, round(preflight_window["width"])),
                "height": max(1, round(preflight_window["height"])),
            }
        elif "steam_ready_capture" not in result:
            result["steam_ready_capture"] = capture_exact_window(
                remote, result["steam_preflight"]["window"]["window"],
                args.output / "steam-ready.png",
            )
        result["steam_fps_overlay_config"] = steam_fps_overlay_config(remote)
        if input_agent is None:
            observers = arm_game_launch_observers(remote, args, result)
            offset = observers["offset"]
            runtime_log_stream = observers["runtime_log_stream"]
            host_log_stream = observers["host_log_stream"]
            input_agent = observers["input_agent"]
            sample_agent = observers["sample_agent"]
        if args.steam_applaunch:
            selection = {
                "pid": result["steam_preflight"]["pid"],
                "window": result["steam_preflight"]["window"],
                "capture_width": result["steam_ready_capture"]["width"],
                "capture_height": result["steam_ready_capture"]["height"],
                "actions": [{
                    "action": "launchd-owned-steam-applaunch",
                    "appid": 1332010,
                }],
            }
            result["steam_selection_capture"] = \
                result["steam_ready_capture"]
            result["steam_play_state"] = {
                "state": "applaunch-in-flight",
                "attempts": [],
                "selection": selection,
            }
        else:
            try:
                if args.reuse_steam_selection:
                    selection = reuse_selected_stray_in_steam(remote)
                else:
                    selection = select_stray_in_steam(remote)
                result["steam_selection_capture"] = capture_exact_window(
                    remote, selection["window"]["window"],
                    args.output / "steam-selection.png",
                )
                result["steam_play_state"] = prepare_steam_play_action(
                    remote, selection, args.output,
                    min(30.0, args.steam_ready_timeout), thermal_checkpoint,
                )
            except RuntimeError as selection_error:
                if not args.user_handoff and not args.reuse_steam_selection:
                    # The current client has already passed the fresh login,
                    # Store/Library-data and main-browser-surface gates above.
                    # Runtime-confirmed failures after that boundary include
                    # Chromium -324, a Host tap that remained on Store for 60
                    # seconds, and iOS LaunchServices rejecting Steam's macOS
                    # URL scheme. Preserve Steam ownership, cloud checks and
                    # overlay by asking that already-ready owner to create its
                    # ordinary LaunchApp after diagnostic markers are armed.
                    launch_by_applaunch = True
                    window = largest_window(
                        remote, result["steam_preflight"]["pid"], 5,
                        minimum_area=500000,
                    )
                    selection = {
                        "pid": result["steam_preflight"]["pid"],
                        "window": window,
                        "capture_width": max(1, round(window["width"])),
                        "capture_height": max(1, round(window["height"])),
                        "actions": [{
                            "action": "ready-client-applaunch-fallback",
                            "error": str(selection_error),
                            "game_launch_bypass": False,
                        }],
                    }
                    result["steam_library_launch_fallback"] = \
                        selection["actions"][0]
                    result["steam_selection_capture"] = \
                        result["steam_ready_capture"]
                    result["steam_play_state"] = {
                        "state": "running-client-applaunch-pending",
                        "attempts": [],
                        "selection": selection,
                    }
                elif (not args.reuse_steam_selection or
                      not str(selection_error).startswith(
                          "Steam did not converge to a visible PLAY "
                          "action:")):
                    raise
                else:
                    # A retained process normally remains on Stray after
                    # Command-H, but a real cold start can restore Store/Home.
                    # Fall back once to a real Library search and preserve the
                    # same OCR-proven PLAY postcondition.
                    result["steam_reuse_fallback_reason"] = str(
                        selection_error
                    )
                    selection = select_stray_in_steam(remote)
                    result["steam_reuse_fallback_capture"] = \
                        capture_exact_window(
                            remote, selection["window"]["window"],
                            args.output / "steam-selection-fallback.png",
                        )
                    result["steam_play_state"] = prepare_steam_play_action(
                        remote, selection, args.output,
                        min(30.0, args.steam_ready_timeout),
                        thermal_checkpoint,
                    )
        selection = result["steam_play_state"]["selection"]
        if args.user_handoff:
            # This is an explicit ownership boundary.  The verified Steam
            # PLAY page remains visible; no Stray process, trace hook, game
            # input, signal agent, or automatic cleanup is created after it.
            result["user_handoff"] = {
                "steam_pid": selection["pid"],
                "steam_window": selection["window"],
                "state": result["steam_play_state"]["state"],
                "next_action_owner": "user",
                "game_launched": False,
                "game_input_after_handoff": "none",
                "game_process_control": "none",
                "diagnostic_markers": [],
                "instruction": "Click Steam PLAY to launch Stray",
            }
            result["result"] = "READY_FOR_USER"
            return
        # Capture launcher identities before the game allocation peak.  The
        # same exact set can be stopped without rediscovering it if iPadOS can
        # no longer fork a fresh `ps` once Stray starts.
        steam_ui_inventory_before_play = steam_ui_process_inventory(remote)
        steam_ui_pids_before_play = (
            steam_ui_inventory_before_play["steam"] |
            steam_ui_inventory_before_play["helpers"]
        )
        result["steam_ui_pids_before_play"] = sorted(
            steam_ui_pids_before_play
        )
        result["steam_ui_inventory_before_play"] = {
            name: sorted(processes) for name, processes in
            steam_ui_inventory_before_play.items()
        }
        if not args.leave_running or args.quiesce_steam_ui_during_game:
            # Arm while Steam is the only large application.  The persistent
            # native Python process can issue exact cached-PID signals later
            # without asking iPadOS to fork sudo/bash at the game peak.  Its
            # game cleanup path independently validates proc_pidpath before
            # every signal; temperature telemetry cannot invoke it.
            # Reuse the already-active input/control channel.  A separate
            # idle SSH signal-agent connection runtime-exited during a
            # 2026-08-25 Stray load spike while this channel continued to
            # publish markers and release its GUI lease successfully.  One
            # shared prearmed device process removes that failure mode and an
            # otherwise redundant SSH connection; exact PID/path validation
            # remains inside the device process before every signal.
            steam_signal_agent = input_agent
            remote.signal_agent = steam_signal_agent
            result["steam_signal_agent"] = {
                "armed_before_play": True,
                "transport": "shared-prearmed-input-control-agent",
                "process_control_scope": (
                    "exact Stray cleanup and optional Steam UI quiesce"
                ),
                "stray_control": "test-finally cleanup only",
                "thermal_control": False,
                "identity_validation": "libproc proc_pidpath basename",
            }
        # The launch diagnostics and the desktop control plane share one
        # transaction. Runtime-confirmed via MacWSStartup.log on 2026-08-23:
        # an earlier runner left these markers present while Steam selection
        # was still underway; Repair Desktop stopped the old WindowServer,
        # then production preflight rejected the replacement generation on
        # the three surviving marker paths. Acquire the same atomic lease used
        # by macos_gui.sh before publishing any marker, and use the already
        # armed device process for marker I/O so no peak-time SSH fork is
        # introduced.
        if not gui_transaction_lease_held:
            result["gui_transaction_lease"] = \
                input_agent.acquire_gui_lease()
            gui_transaction_lease_held = True
        if markers_to_touch and not diagnostic_markers_armed:
            result["diagnostic_marker_arm"] = \
                input_agent.set_diagnostic_markers(
                    markers_to_touch, True
                )
            diagnostic_markers_armed = True
        if launch_by_applaunch:
            if args.steam_applaunch:
                result["steam_play"] = {
                    "mode": "launchd-owned-steam-applaunch",
                    "appid": 1332010,
                    "pid": selection["pid"],
                    "window": selection["window"],
                    "selection": selection,
                }
            else:
                result["steam_play"] = {
                    **request_running_steam_applaunch(remote, 1332010),
                    "pid": selection["pid"],
                    "window": selection["window"],
                    "selection": selection,
                }
        else:
            result["steam_play"] = click_selected_steam_play(
                remote, selection, args.steam_play_x, args.steam_play_y
            )
        # Hide the expensive Library renderer while Steam is still below the
        # game allocation peak.  The launch state machine and overlay service
        # continue in steam_osx; only the visible CEF page is retired.
        if not launch_by_applaunch:
            result["steam_hide"] = send_steam_key(
                remote, selection["pid"], selection["window"],
                key="h", command=True,
            )
        result["profile_timeline"].append({
            "stage": ("after-steam-applaunch" if launch_by_applaunch
                      else "after-steam-play"),
            "time": time.time(),
            "values": read_profile(remote),
        })
        first_outcome = wait_for_launch_outcome(
            remote, offset, args.launch_timeout, thermal_checkpoint
        )
        result["steam_launch_attempts"] = [first_outcome]
        pid = first_outcome["pid"]
        if (args.steam_applaunch and
                first_outcome["outcome"] == "timeout" and
                "LaunchApp " not in first_outcome.get("log", "")):
            # A launchd-owned Steam generation can publish its real Library
            # window while consuming -applaunch before the client is ready to
            # create a LaunchApp task.  Runtime-confirmed by R22 and R25: the
            # bounded log suffix contained Steam's completed startup witness
            # but no LaunchApp line at all.  Waiting longer cannot advance a
            # task that does not exist.  Replace only that Steam generation
            # once and republish the same validated one-shot AppID marker;
            # Steam still owns the normal cloud/overlay/NSWorkspace launch.
            result["steam_applaunch_missing_task_recovery"] = {
                "trigger": "startup-complete-without-LaunchApp-task",
                "first_generation_pid": selection["pid"],
                "action": "replace-Steam-owner-once",
                "game_launch_bypass": False,
            }
            stop_steam_ui(remote)
            retry_offset = log_size(remote)
            retry_preflight = ensure_steam_ui(
                remote, args.steam_start_timeout, thermal_checkpoint,
                applaunch_appid=1332010,
            )
            result["steam_applaunch_missing_task_retry_preflight"] = \
                retry_preflight
            retry_window = retry_preflight["window"]
            selection = {
                "pid": retry_preflight["pid"],
                "window": retry_window,
                "capture_width": max(1, round(retry_window["width"])),
                "capture_height": max(1, round(retry_window["height"])),
                "actions": [{
                    "action": "launchd-owned-steam-applaunch-retry",
                    "appid": 1332010,
                }],
            }
            result["steam_applaunch_missing_task_recovery"][
                "replacement_generation_pid"
            ] = selection["pid"]
            retry_outcome = wait_for_launch_outcome(
                remote, retry_offset, args.launch_timeout,
                thermal_checkpoint,
            )
            result["steam_launch_attempts"].append(retry_outcome)
            pid = retry_outcome["pid"]
        if (args.steam_applaunch and
                result["steam_launch_attempts"][-1]["outcome"] ==
                    "cloud_sync_stalled"):
            # Replace only the Steam owner that runtime-confirmed itself
            # stuck before CreatingProcess.  prepare_steam_runtime consumes
            # the AppID marker once, so ensure_steam_ui republishes the same
            # validated 1332010 marker for the replacement generation.  The
            # retry still executes Steam's normal cloud, LaunchApp, overlay,
            # and NSWorkspace path; no user data or cloud setting is changed.
            result["steam_applaunch_stall_recovery"] = {
                "trigger": "unchanged-SynchronizingCloud",
                "first_generation_pid": selection["pid"],
                "action": "replace-Steam-owner-once",
                "cloud_bypass": False,
                "game_launch_bypass": False,
            }
            stop_steam_ui(remote)
            retry_offset = log_size(remote)
            retry_preflight = ensure_steam_ui(
                remote, args.steam_start_timeout, thermal_checkpoint,
                applaunch_appid=1332010,
            )
            result["steam_applaunch_retry_preflight"] = retry_preflight
            retry_window = retry_preflight["window"]
            selection = {
                "pid": retry_preflight["pid"],
                "window": retry_window,
                "capture_width": max(1, round(retry_window["width"])),
                "capture_height": max(1, round(retry_window["height"])),
                "actions": [{
                    "action": "launchd-owned-steam-applaunch-retry",
                    "appid": 1332010,
                }],
            }
            result["steam_applaunch_stall_recovery"][
                "replacement_generation_pid"
            ] = selection["pid"]
            retry_outcome = wait_for_launch_outcome(
                remote, retry_offset, args.launch_timeout,
                thermal_checkpoint,
            )
            result["steam_launch_attempts"].append(retry_outcome)
            pid = retry_outcome["pid"]
        if (result["steam_launch_attempts"][-1]["outcome"] ==
                "cloud_sync_failed"):
            # Runtime-confirmed by console log and exact-window capture on
            # 2026-08-21: Steam can pause this ActionID at the normal
            # "Unable to Sync" prompt.  Resolve the visible, OCR-located
            # "Play anyway" action and continue observing the same launch;
            # do not suppress or rewrite Steam Cloud state.
            cloud_dialog = find_steam_text_window(
                remote, "Play anyway",
                args.output / "steam-cloud-sync-warning.png", 10.0,
            )
            steam_window = cloud_dialog["window"]
            cloud_capture = cloud_dialog["capture"]
            result["steam_cloud_sync_warning"] = cloud_capture
            result["steam_cloud_sync_window_selection"] = {
                key: value for key, value in cloud_dialog.items()
                if key != "capture"
            }
            result["steam_cloud_sync_confirm"] = click_steam_text(
                remote, cloud_dialog["pid"], steam_window,
                cloud_capture, "Play anyway",
            )
            # The confirmation gesture is asynchronous with respect to CEF's
            # launch state task.  The previous implementation immediately
            # re-read the same still-current "syncfailed" line and classified
            # it as a second failure even though Steam advanced and spawned
            # Stray moments later.  Ignore only the exact warning already
            # acknowledged; a newly appended warning remains a real failure.
            acknowledged_text = log_suffix(remote, offset)
            acknowledged_waits = list(re.finditer(
                r'LaunchApp waiting for user response to SynchronizingCloud '
                r'"(?:syncfailed|pendingcloudsessions)"',
                acknowledged_text,
            ))
            acknowledged_cloud_wait = (
                acknowledged_waits[-1].start()
                if acknowledged_waits else -1
            )
            continued = wait_for_launch_outcome(
                remote, offset, args.launch_timeout, thermal_checkpoint,
                cloud_wait_floor=acknowledged_cloud_wait,
            )
            result["steam_launch_attempts"].append(continued)
            pid = continued["pid"]
        if (result["steam_launch_attempts"][-1]["outcome"] ==
                "kicking_other_session"):
            # This is Steam's normal account-session handoff dialog. Confirm
            # the visible Continue action and keep observing the same launch
            # task; do not kill a remote process or bypass Steam ownership.
            handoff_dialog = find_steam_text_window(
                remote, "Continue",
                args.output / "steam-other-session-warning.png", 10.0,
            )
            steam_window = handoff_dialog["window"]
            handoff_capture = handoff_dialog["capture"]
            result["steam_other_session_warning"] = handoff_capture
            result["steam_other_session_window_selection"] = {
                key: value for key, value in handoff_dialog.items()
                if key != "capture"
            }
            result["steam_other_session_confirm"] = click_steam_text(
                remote, handoff_dialog["pid"], steam_window,
                handoff_capture, "Continue",
            )
            acknowledged_text = log_suffix(remote, offset)
            acknowledged_waits = list(re.finditer(
                r'LaunchApp waiting for user response to KickingOtherSession '
                r'"[^"]*"',
                acknowledged_text,
            ))
            acknowledged_kicking_wait = (
                acknowledged_waits[-1].start()
                if acknowledged_waits else -1
            )
            continued = wait_for_launch_outcome(
                remote, offset, args.launch_timeout, thermal_checkpoint,
                kicking_wait_floor=acknowledged_kicking_wait,
            )
            result["steam_launch_attempts"].append(continued)
            pid = continued["pid"]
        if (not args.steam_applaunch and
                result["steam_launch_attempts"][-1]["outcome"] ==
                "app_error_49"):
            # Runtime-confirmed with client 1785799196: a fresh Steam session
            # can reject ActionID 1 before invoking any NSWorkspace selector,
            # while the next action enters the verified-depot -> runtime-app
            # adapter and completes.  This is a bounded UI retry, not a
            # signature-check bypass; preserve both action logs as evidence.
            selection = result["steam_play"]["selection"]
            result["steam_error_dismiss"] = send_steam_key(
                remote, selection["pid"], selection["window"], key="escape"
            )
            time.sleep(1.0)
            retry_offset = log_size(remote)
            retry_click = click_selected_steam_play(
                remote, selection, args.steam_play_x, args.steam_play_y
            )
            result["steam_retry_play"] = retry_click
            retry_outcome = wait_for_launch_outcome(
                remote, retry_offset, args.launch_timeout,
                thermal_checkpoint,
            )
            result["steam_launch_attempts"].append(retry_outcome)
            pid = retry_outcome["pid"]
        if (result["steam_launch_attempts"][-1]["outcome"] != "started" or
                pid <= 1):
            # Preserve the exact Steam state that made launch selection fail;
            # without this, a coordinate/UI regression and a real Steam
            # CreatingProcess failure are indistinguishable after cleanup.
            try:
                steam_window = largest_window(
                    remote, result["steam_play"]["pid"], 5,
                    minimum_area=500000,
                )
                result["steam_failure_capture"] = capture_exact_window(
                    remote, steam_window["window"],
                    args.output / "steam-launch-failure.png",
                )
            except Exception as capture_error:
                result["steam_failure_capture_error"] = str(capture_error)
            result["steam_failure_log"] = log_suffix(remote, offset)[-12000:]
            raise RuntimeError(
                "Steam Play did not complete a Stray launch: " +
                result["steam_launch_attempts"][-1]["outcome"]
            )
        result["pid"] = pid
        if launch_by_applaunch:
            # Keep the one-shot LaunchApp owner active until a real Stray PID
            # exists. Then hide only the costly CEF surface; steam_osx and the
            # FPS overlay remain runnable for the production game contract.
            result["steam_hide"] = send_steam_key(
                remote, selection["pid"], selection["window"],
                key="h", command=True,
            )
        if args.wait_trace:
            result["wait_trace_install_witness"] = \
                wait_for_wait_trace_install(
                    remote, offset, args.launch_timeout
                )
            # Installation is process-start-only; once witnessed, keeping this
            # sentinel cannot add information.  Capture has its own narrowly
            # timed marker below.
            remote.sudo(
                f"rm -f {shlex.quote(WAIT_TRACE_INSTALL_MARKER)}",
                check=False,
            )
        if args.disable_overlay_injection:
            result["steam_overlay"] = verify_no_overlay_injection(
                remote, pid, offset
            )
        else:
            result["steam_overlay"] = wait_for_steam_overlay(
                remote, pid, offset, timeout=args.launch_timeout,
                allow_suppressed_ui=args.steam_overlay_no_ui_drawing,
            )
        window = largest_window(remote, pid, args.catalog_timeout)
        result["window"] = window
        initial_samples = present_samples(log_suffix(remote, offset))
        observed_sequence = max(
            (sample["sequence"] for sample in initial_samples), default=0
        )
        result["window_history"] = [{
            "stage": "initial", **window,
            "catalog_visible": True,
            "process_live": root_pid_liveness(remote, pid) is True,
            "present_sequence": observed_sequence,
        }]
        retain_runtime_markers = (
            args.capture_metal_libraries or
            args.pipeline_diagnostics or
            args.render_trace or
            args.gpu_command_error_diagnostics or
            args.submit_timing_diagnostics
        )
        if not retain_runtime_markers:
            # Present/drawable/display-sync flags are cached in the Stray
            # process, overlay selection has already happened in Steam's
            # child-environment builder, and optional wait/input hooks have
            # been installed before this real window/present witness. Retire
            # the launch sentinels before releasing the GUI transaction so a
            # later user repair can never observe a half-launch diagnostic
            # state.
            result["diagnostic_marker_retirement"] = \
                input_agent.set_diagnostic_markers(
                    markers_to_touch, False
                )
            diagnostic_markers_armed = False
            result["gui_transaction_lease_release"] = \
                input_agent.release_gui_lease()
            gui_transaction_lease_held = False
        else:
            result["gui_transaction_lease_retained"] = {
                "reason": "runtime diagnostic markers remain observable",
                "until": "bounded-run cleanup",
            }
        # A workspace restart can recreate the fullscreen Scene with its
        # UIKit control panel visible.  The macOS/game pixels remain live
        # behind it, so frame counters alone do not detect the obstruction;
        # run direct2hz-sustained60 captured the panel over the entire game
        # and partially covered Steam's FPS label.  Match normal fullscreen
        # play before visual classification and scoring.  hide-controls also
        # restores hardware-keyboard focus to MacWSMetalView on the next main
        # turn, so this does not steal input from Stray.
        result["host_controls_hide_before_gameplay"] = \
            input_agent.open_url("macwshost://hide-controls")
        thermally_guarded_pause("host-controls-hide-settle", 0.5)
        if args.quiesce_steam_ui_during_game:
            result["steam_game_quiesce"] = {
                "pre_play_pids": sorted(steam_ui_pids_before_play),
                "action": "deferred-until-verified-gameplay",
                "scope": "Steam remains runnable throughout startup",
            }
        else:
            result["steam_game_quiesce"] = {
                "pre_play_pids": sorted(steam_ui_pids_before_play),
                "action": "none",
                "scope": "observation-only; Steam and helpers remain runnable",
            }
        # The hidden Steam Library is not a prerequisite for the already
        # launched game.  Runtime on 2026-08-22 showed Stray publishing
        # 2,160 presents while this former idle gate retried ``ps`` at the
        # allocation peak and aborted the otherwise healthy run.  Preserve a
        # best-effort observation without delaying or controlling Steam.
        try:
            result["steam_background_after_hide"] = \
                background_cpu_snapshot(remote)
        except Exception as steam_background_error:
            result["steam_background_after_hide_error"] = str(
                steam_background_error
            )
        result["profile_timeline"].append({
            "stage": "after-game-window",
            "time": time.time(),
            "values": read_profile(remote),
        })
        # MacWSHost's fullscreen catalog already selects a newly frontmost
        # Stray layer.  Asking for a second Scene here races iPadOS URL routing
        # and can move a correctly selected game into a windowed Scene.
        thermally_guarded_pause("menu-delay", args.menu_delay)
        window, window_evidence = refresh_game_window_or_present(
            remote, pid, window, offset, observed_sequence,
            max(8.0, args.catalog_timeout), "before-input",
        )
        observed_sequence = window_evidence["present_sequence"]
        result["window_history"].append(window_evidence)
        result["return_inputs"] = []
        result["rfb_return_inputs"] = []
        if args.tap_game_start:
            result["game_start_tap"] = tap_window(
                remote, pid, window,
                max(1, round(window["width"])),
                max(1, round(window["height"])),
                args.game_start_x, args.game_start_y,
                activate_first=True,
                hold=args.game_start_hold,
            )
            time.sleep(args.enter_interval)
        effective_enter_count = args.enter_count
        gameplay_prompt_steps = max(
            16, math.ceil(args.gameplay_timeout / args.enter_interval)
        )
        result["input_plan"] = {
            "target": ("gameplay-visual-state-machine"
                       if args.progress_to_gameplay else "menu"),
            "maximum_prompt_steps": (
                gameplay_prompt_steps if args.progress_to_gameplay else 0
            ),
            "gameplay_timeout_seconds": (
                args.gameplay_timeout if args.progress_to_gameplay else 0
            ),
            "post_save_capture_quiet_seconds": (
                args.gameplay_load_delay
                if args.progress_to_gameplay else 0
            ),
            "host_return_count": (
                "state-dependent" if args.progress_to_gameplay
                else effective_enter_count
            ),
            "rfb_return_count": args.rfb_return_count,
            "interval_seconds": args.enter_interval,
        }
        result["input_stage_captures"] = []
        if args.progress_to_gameplay:
            save_flow_started = False
            consecutive_non_menu = 0
            gameplay_visual_reached = False
            # Stray's animated cat-logo landing screen contains no readable
            # prompt in the target build: Vision sees only Steam's FPS HUD.
            # Treating it as an arbitrary scene made the runner capture and
            # OCR the same animation until the full gameplay timeout without
            # ever issuing the one input that the screen is waiting for.  One
            # visible unknown startup frame is sufficient to advance it.  The
            # transaction is deliberately one-shot; later unknown frames are
            # observed, never button-mashed through cinematics or gameplay.
            unknown_startup_advanced = False
            gameplay_observation_not_before = 0.0
            advanced_singleton_states = set()
            gameplay_deadline = time.monotonic() + args.gameplay_timeout
            for prompt_index in range(gameplay_prompt_steps):
                if time.monotonic() >= gameplay_deadline:
                    break
                stage = f"prompt-{prompt_index + 1}"
                stage_suffix = log_suffix(remote, offset)
                fatal = ue_fatal_excerpt(stage_suffix)
                if fatal:
                    raise RuntimeError(
                        "Stray reported a fatal error during gameplay "
                        f"progression: {fatal}"
                    )
                if (steam_signal_agent is not None and
                        steam_signal_agent.exact_status(
                            pid, GAME_NAME) == "missing"):
                    raise RuntimeError(
                        "Stray exited during gameplay progression; "
                        "runtime tail=" + stage_suffix[-2500:]
                    )
                if (save_flow_started and
                        time.monotonic() <
                        gameplay_observation_not_before):
                    thermally_guarded_pause(
                        "gameplay-load-settle",
                        min(gameplay_observation_not_before -
                            time.monotonic(),
                            max(0.0, gameplay_deadline -
                                time.monotonic())),
                    )
                    if time.monotonic() >= gameplay_deadline:
                        break
                previous_window_id = int(window["window"])
                try:
                    window = largest_window(
                        remote, pid, min(1.0, args.catalog_timeout)
                    )
                except RuntimeError:
                    # A fullscreen transition can temporarily retire every
                    # catalog-visible window while CAMetalDrawable presents
                    # continue.  Retaining the last focused endpoint is the
                    # same bounded fallback used by
                    # refresh_game_window_or_present().
                    pass
                if int(window["window"]) != previous_window_id:
                    result["window_history"].append({
                        "stage": f"{stage}-focused-window-refresh",
                        **window,
                        "previous_window": previous_window_id,
                    })
                stage_path = args.output / f"{stage}.png"
                capture = {
                    "stage": stage,
                    **capture_game_view(
                        remote, int(window["window"]), stage_path
                    ),
                }
                recognized = recognize_exact_window(capture)
                visual_state = stray_visual_state(recognized)
                capture["visual_state"] = visual_state
                capture["image_stats"] = exact_image_stats(capture)
                result["input_stage_captures"].append(capture)

                visibly_nonblack = (
                    capture["image_stats"]["nonblack_ratio"] >= 0.05 and
                    capture["image_stats"][
                        "brightness_variance"] >= 100.0
                )
                # The requested production presentation includes Steam's
                # top-left FPS counter.  It is also the only screen-level
                # identity witness that survives Stray retiring its AppKit /
                # SkyLight catalog layer in favour of the direct drawable.
                # Two arbitrary non-black Host frames are insufficient: run
                # 20260829 captured the Terminal desktop twice after the
                # semantic target had incorrectly changed to Dock and the old
                # classifier called that "gameplay".  Require the visible FPS
                # label as well as scene pixels before admitting gameplay.
                steam_fps_visible = bool(re.search(
                    r"(?:^|\s)FPS(?:\s|$)", recognized.upper()
                ))
                capture["steam_fps_visible"] = steam_fps_visible
                if visual_state == "non-menu-or-unrecognized":
                    if (save_flow_started and visibly_nonblack and
                            steam_fps_visible):
                        consecutive_non_menu += 1
                        capture["action"] = "observe-visible-scene"
                        if consecutive_non_menu >= 2:
                            gameplay_visual_reached = True
                            break
                    elif visibly_nonblack and not unknown_startup_advanced:
                        advance = send_return(remote, pid, window)
                        capture["action"] = \
                            "advance-unrecognized-startup-once"
                        capture["input"] = advance
                        result["return_inputs"].append(advance)
                        unknown_startup_advanced = True
                        consecutive_non_menu = 0
                    else:
                        consecutive_non_menu = 0
                        capture["action"] = (
                            "wait-non-menu-scene" if visibly_nonblack
                            else "wait-loading-black"
                        )
                else:
                    consecutive_non_menu = 0
                    if visual_state == "save-select":
                        if not save_flow_started:
                            select_slot = tap_window(
                                remote, pid, window,
                                max(1, round(window["width"])),
                                max(1, round(window["height"])),
                                0.167, 0.47, activate_first=True,
                            )
                            time.sleep(0.25)
                            confirm_slot = send_return(remote, pid, window)
                            capture["action"] = "select-slot-1"
                            capture["input"] = (
                                f"{select_slot}; {confirm_slot}"
                            )
                            result["return_inputs"].append(confirm_slot)
                            save_flow_started = True
                        else:
                            start = send_return(remote, pid, window)
                            capture["action"] = "activate-selected-save"
                            capture["input"] = start
                            result["return_inputs"].append(start)
                            # The first confirmation changes NEW GAME into
                            # START GAME but does not begin the cinematic.
                            # Waiting the full load delay before this second
                            # confirmation burned 150 seconds in a static menu
                            # on every run.  Start first, then give the real
                            # animation/load path its bounded quiet interval.
                            gameplay_observation_not_before = (
                                time.monotonic() +
                                args.gameplay_load_delay
                            )
                    elif visual_state in {
                            "autosave-notice", "startup-prompt",
                            "main-menu"}:
                        if visual_state in advanced_singleton_states:
                            capture["action"] = (
                                "wait-repeated-" + visual_state
                            )
                        else:
                            advance = send_return(remote, pid, window)
                            capture["action"] = "advance-" + visual_state
                            capture["input"] = advance
                            result["return_inputs"].append(advance)
                            advanced_singleton_states.add(visual_state)
                    else:
                        capture["action"] = "wait-unhandled-state"
                stage_pause = args.enter_interval
                if capture["action"] == "wait-loading-black":
                    stage_pause = max(stage_pause, 8.0)
                thermally_guarded_pause(
                    stage,
                    min(stage_pause, max(
                        0.0, gameplay_deadline - time.monotonic()
                    )),
                )
            result["gameplay_visual_reached"] = gameplay_visual_reached
            result["save_flow_started"] = save_flow_started
        else:
            for input_index in range(effective_enter_count):
                result["return_inputs"].append(
                    send_return(remote, pid, window)
                )
                thermally_guarded_pause(
                    f"host-return-{input_index + 1}", args.enter_interval
                )
        if args.quiesce_steam_ui_during_game:
            if (args.progress_to_gameplay and
                    not result.get("gameplay_visual_reached")):
                raise RuntimeError(
                    "Steam UI quiescing requires a verified gameplay visual"
                )
            # Runtime-confirmed by /tmp/macws-stray-shadowfix-score-qsteam:
            # suspending steam_osx immediately after the first 120 presents
            # left Stray publishing an unchanged black frame through sequence
            # 1800.  A present count proves liveness, not completed startup.
            # Keep Steam runnable until the visible gameplay postcondition has
            # passed, then bound this A/B to the steady-state interval.
            result["steam_game_quiesce_gate"] = wait_for_present_floor(
                remote, offset, pid, 120, 30.0
            )
            observed_sequence = max(
                observed_sequence,
                result["steam_game_quiesce_gate"]["observed_sequence"],
            )
            steam_game_quiesced_pids = set(
                steam_ui_inventory_before_play["steam"]
            )
            result["steam_game_quiesce"] = quiesce_known_steam_ui(
                steam_signal_agent, steam_ui_inventory_before_play
            )
            result["steam_game_quiesce"]["action"] = \
                "suspend-owner-retire-cef-after-verified-gameplay"
        if args.direct_drawable_after_gameplay:
            if not result.get("gameplay_visual_reached"):
                raise RuntimeError(
                    "deferred direct drawable activation requires the "
                    "verified gameplay visual"
                )
            markers_to_touch.append(DIRECT_DRAWABLE_ACTIVE_MARKER)
            activation_lease = input_agent.acquire_gui_lease()
            try:
                activation = input_agent.set_diagnostic_markers(
                    [DIRECT_DRAWABLE_ACTIVE_MARKER], True
                )
            finally:
                activation_release = input_agent.release_gui_lease()
            result["direct_drawable_gameplay_activation"] = {
                "capability_marker": DIRECT_DRAWABLE_LEASE_MARKER,
                "activation_marker": DIRECT_DRAWABLE_ACTIVE_MARKER,
                "gui_transaction_lease": activation_lease,
                "marker_result": activation,
                "gui_transaction_lease_release": activation_release,
                "stage": "after-verified-gameplay-before-input-and-score",
            }
            # The publisher probes the activation marker once per 30 game
            # presents.  One bounded second covers that interval at every
            # previously observed playable rate without putting filesystem
            # work on the steady-state present path.
            thermally_guarded_pause("direct-drawable-activation", 1.0)
        if args.cool_game_before_sample:
            if not result.get("gameplay_visual_reached"):
                raise RuntimeError(
                    "game cooling requires a verified gameplay visual"
                )
            if steam_signal_agent is None:
                raise RuntimeError(
                    "game cooling requires the prearmed exact-PID agent"
                )
            identity_before = steam_signal_agent.exact_status(pid, GAME_NAME)
            stop_state = steam_signal_agent.signal_exact(
                int(signal.SIGSTOP), pid, GAME_NAME
            )
            if identity_before != "match" or stop_state != "signaled":
                raise RuntimeError(
                    "could not suspend the exact Stray process for "
                    f"preconditioning: identity={identity_before} "
                    f"stop={stop_state}"
                )
            game_precondition_stopped = True
            cooling_started = time.time()
            try:
                if args.cool_render_path:
                    cooling_render_path = \
                        render_path_process_inventory(remote)
                    required_render_labels = {
                        "WindowServer", "macwsdisplayd"
                    }
                    if set(cooling_render_path) != required_render_labels:
                        raise RuntimeError(
                            "game cooling requires the live render path: "
                            f"expected={sorted(required_render_labels)} "
                            f"actual={sorted(cooling_render_path)}"
                        )
                    stopped_render_pids = signal_exact_render_path(
                        remote, cooling_render_path, "STOP"
                    )
                    cooling_render_path_stopped = True
                    result[
                        "gameplay_render_path_cooling_suspend"
                    ] = {
                        "inventory": cooling_render_path,
                        "pids": stopped_render_pids,
                        "action": "SIGSTOP",
                        "game_exit": False,
                        "MacWSHost": "runnable-last-drawable-visible",
                    }
                cooled, cooling_history = wait_for_cool_device(
                    remote, args.start_temperature_ceiling,
                    args.cooldown_timeout, args.thermal_interval,
                    args.cool_stable_samples,
                    progress_label="gameplay-presample-cooldown",
                )
                result["thermal"].extend(cooling_history)
            finally:
                if cooling_render_path_stopped:
                    resumed_render_pids = signal_exact_render_path(
                        remote, cooling_render_path, "CONT"
                    )
                    cooling_render_path_stopped = False
                    time.sleep(0.25)
                    resumed_inventory = \
                        render_path_process_inventory(remote)
                    result[
                        "gameplay_render_path_cooling_resume"
                    ] = {
                        "pids": resumed_render_pids,
                        "action": "SIGCONT",
                        "inventory": resumed_inventory,
                    }
                    for label, expected in cooling_render_path.items():
                        actual = resumed_inventory.get(label)
                        if (actual is None or
                                actual["pid"] != expected["pid"] or
                                actual["state"].startswith("T")):
                            raise RuntimeError(
                                "render path did not resume after game "
                                f"cooling: {label} actual={actual}"
                            )
                    cooling_render_path = {}
                resume_state = steam_signal_agent.signal_exact(
                    int(signal.SIGCONT), pid, GAME_NAME
                )
                game_precondition_stopped = False
            if resume_state != "signaled":
                raise RuntimeError(
                    "could not resume the exact Stray process after "
                    f"preconditioning: {resume_state}"
                )
            result["gameplay_thermal_preconditioning"] = {
                "policy": "explicit-benchmark-only",
                "process": f"{GAME_NAME}/{pid}",
                "process_control": ["SIGSTOP", "SIGCONT"],
                "game_exit": False,
                "started": cooling_started,
                "duration_seconds": time.time() - cooling_started,
                "stable_samples_required": args.cool_stable_samples,
                "final": cooled,
                "history": cooling_history,
            }
            # Require presentation to restart before any functional input or
            # scored sample.  This is a pixel/counter witness, not an uptime
            # check; refresh_game_window_or_present fails if the resumed game
            # does not advance its real present sequence.
            window, resume_evidence = refresh_game_window_or_present(
                remote, pid, window, offset, observed_sequence,
                max(8.0, args.catalog_timeout),
                "after-thermal-preconditioning",
            )
            observed_sequence = resume_evidence["present_sequence"]
            result["window_history"].append(resume_evidence)
        for input_index in range(args.rfb_return_count):
            result["rfb_return_inputs"].append(send_rfb_return(
                args.host, args.vnc_port, args.rfb_key_hold,
            ))
            thermally_guarded_pause(
                f"rfb-return-{input_index + 1}", args.enter_interval
            )
        gameplay_probe_requested = (
            args.gameplay_hover_probe or
            args.gameplay_host_hover_probe or
            args.gameplay_host_long_drag_probe or
            args.gameplay_walk_probe
        )
        if (args.progress_to_gameplay and gameplay_probe_requested and
                not result.get("gameplay_visual_reached")):
            progression_suffix = log_suffix(remote, offset)
            progression_samples = present_samples(progression_suffix)
            last_sequence = max(
                (sample["sequence"] for sample in progression_samples),
                default=0,
            )
            process_state = (
                steam_signal_agent.exact_status(pid, GAME_NAME)
                if steam_signal_agent is not None else "unknown"
            )
            progression_failure = {
                "classification": (
                    "runtime-confirmed-live-presentation-stall"
                    if process_state == "match" and
                    last_sequence <= observed_sequence else
                    "gameplay-visual-not-reached"
                ),
                "process_identity": process_state,
                "initial_sequence": observed_sequence,
                "last_sequence": last_sequence,
                "fatal_error": ue_fatal_excerpt(progression_suffix),
                "capture_count": len(result["input_stage_captures"]),
            }
            if (process_state == "match" and sample_agent is not None and
                    not sample_agent.used):
                progression_failure["process_sample"] = \
                    capture_process_sample(
                        remote, pid, 3.0,
                        args.output /
                        "stray-gameplay-progression-stall.sample.txt",
                    )
            result["gameplay_progression_failure"] = progression_failure
            result["result"] = "GAMEPLAY_PROGRESS_STALL"
            raise RuntimeError(
                "verified gameplay visual was not reached before the "
                "bounded progression deadline"
            )
        if args.gameplay_hover_probe:
            if not args.progress_to_gameplay or not result.get(
                    "gameplay_visual_reached"):
                raise RuntimeError(
                    "gameplay hover probe requires a verified gameplay visual"
                )
            hover_before_path = args.output / "gameplay-hover-before.png"
            hover_after_path = args.output / "gameplay-hover-after.png"
            hover_before = capture_game_view(
                remote, int(window["window"]), hover_before_path
            )
            hover_before["image_stats"] = exact_image_stats(hover_before)
            hover_before["sha256"] = hashlib.sha256(
                hover_before_path.read_bytes()
            ).hexdigest()
            before_hover_samples = present_samples(log_suffix(remote, offset))
            hover_sequence_floor = max(
                (sample["sequence"] for sample in before_hover_samples),
                default=0,
            )
            hover_width = max(1, round(window["width"]))
            hover_height = max(1, round(window["height"]))
            hover_inputs = [hover_window(
                remote, pid, window, hover_width, hover_height,
                0.48, 0.50, activate_first=True,
            )]
            time.sleep(0.10)
            hover_inputs.append(hover_window(
                remote, pid, window, hover_width, hover_height,
                0.52, 0.50,
            ))
            thermally_guarded_pause(
                "gameplay-hover-observation", args.hover_observation_seconds
            )
            hover_suffix = log_suffix(remote, offset)
            hover_fatal = ue_fatal_excerpt(hover_suffix)
            after_hover_samples = present_samples(hover_suffix)
            hover_last_sequence = max(
                (sample["sequence"] for sample in after_hover_samples),
                default=0,
            )
            hover_after = capture_game_view(
                remote, int(window["window"]), hover_after_path
            )
            hover_after["image_stats"] = exact_image_stats(hover_after)
            hover_after["sha256"] = hashlib.sha256(
                hover_after_path.read_bytes()
            ).hexdigest()
            hover_process_live = root_pid_liveness(remote, pid)
            hover_advanced = hover_last_sequence > hover_sequence_floor
            result["gameplay_hover_probe"] = {
                "inputs": hover_inputs,
                "observation_seconds": args.hover_observation_seconds,
                "sequence_floor": hover_sequence_floor,
                "last_sequence": hover_last_sequence,
                "presentation_advanced": hover_advanced,
                "process_live": hover_process_live is True,
                "fatal_error": hover_fatal,
                "before": hover_before,
                "after": hover_after,
                "same_frame_sha256": (
                    hover_before["sha256"] == hover_after["sha256"]
                ),
            }
            if (not hover_advanced and hover_process_live is True and
                    not hover_fatal):
                result["gameplay_hover_probe"]["classification"] = \
                    "runtime-confirmed-live-process-presentation-stall"
                result["gameplay_hover_probe"]["process_sample"] = \
                    capture_process_sample(
                        remote, pid, 2.0,
                        args.output / "stray-hover-stall.sample.txt",
                    )
                result["result"] = "INPUT_PRESENTATION_STALL"
                raise RuntimeError(
                    "Stray stayed live but stopped publishing presents after "
                    "the exact gameplay hover input"
                )
            result["gameplay_hover_probe"]["classification"] = (
                "presentation-continued-after-input" if hover_advanced else
                "game-exited-or-fatal-after-input"
            )
        if args.gameplay_host_hover_probe:
            if not args.progress_to_gameplay or not result.get(
                    "gameplay_visual_reached"):
                raise RuntimeError(
                    "gameplay Host hover probe requires a verified gameplay "
                    "visual"
                )
            if remote.input_agent is None or remote.host_log_offset is None:
                raise RuntimeError(
                    "gameplay Host hover probe requires the prearmed Host "
                    "URL/input and log transports"
                )
            host_hover_before_path = (
                args.output / "gameplay-host-hover-before.png"
            )
            host_hover_after_path = (
                args.output / "gameplay-host-hover-after.png"
            )
            host_hover_before = capture_game_view(
                remote, int(window["window"]), host_hover_before_path
            )
            host_hover_before["image_stats"] = exact_image_stats(
                host_hover_before
            )
            host_hover_before["sha256"] = hashlib.sha256(
                host_hover_before_path.read_bytes()
            ).hexdigest()
            before_host_hover_samples = present_samples(
                log_suffix(remote, offset)
            )
            host_hover_sequence_floor = max(
                (sample["sequence"] for sample in before_host_hover_samples),
                default=0,
            )
            host_text_before = host_log_suffix(
                remote, int(remote.host_log_offset)
            )
            request_transport = remote.input_agent.open_url(
                "macwshost://performance-gesture-hover"
            )
            # The Host emits 961 samples over just over eight seconds and
            # deliberately waits another 400 ms before logging completion.
            # A caller-supplied observation shorter than that can report a
            # false route failure even though the Host finishes normally.
            host_hover_observation_seconds = max(
                args.hover_observation_seconds,
                HOST_HOVER_SCENARIO_SECONDS,
            )
            thermally_guarded_pause(
                "gameplay-host-hover-observation",
                host_hover_observation_seconds,
            )
            host_text_after = host_log_suffix(
                remote, int(remote.host_log_offset)
            )
            if not host_text_after.startswith(host_text_before):
                raise RuntimeError(
                    "MacWSHost log stream was not append-only during the "
                    "120 Hz hover probe"
                )
            host_hover_log = host_text_after[len(host_text_before):]
            host_hover_end = [
                line for line in host_hover_log.splitlines()
                if "performance-gesture-end scenario=hover success=YES" in line
            ]
            host_hover_result = [
                line for line in host_hover_log.splitlines()
                if ("performance-url-gesture scenario=hover success=YES" in
                    line)
            ]
            if not host_hover_end or not host_hover_result:
                raise RuntimeError(
                    "MacWSHost did not acknowledge completion of its 120 Hz "
                    "hover route: " + host_hover_log[-1000:]
                )
            host_hover_suffix = log_suffix(remote, offset)
            host_hover_fatal = ue_fatal_excerpt(host_hover_suffix)
            after_host_hover_samples = present_samples(host_hover_suffix)
            host_hover_last_sequence = max(
                (sample["sequence"] for sample in after_host_hover_samples),
                default=0,
            )
            host_hover_after = capture_game_view(
                remote, int(window["window"]), host_hover_after_path
            )
            host_hover_after["image_stats"] = exact_image_stats(
                host_hover_after
            )
            host_hover_after["sha256"] = hashlib.sha256(
                host_hover_after_path.read_bytes()
            ).hexdigest()
            host_hover_process_live = root_pid_liveness(remote, pid)
            host_hover_advanced = (
                host_hover_last_sequence > host_hover_sequence_floor
            )
            result["gameplay_host_hover_probe"] = {
                "route": (
                    "MacWSHost 120-Hz hover -> macwsinputd -> "
                    "Dock/WindowServer global pointer"
                ),
                "request_transport": request_transport,
                "requested_observation_seconds":
                    args.hover_observation_seconds,
                "observation_seconds": host_hover_observation_seconds,
                "sequence_floor": host_hover_sequence_floor,
                "last_sequence": host_hover_last_sequence,
                "presentation_advanced": host_hover_advanced,
                "process_live": host_hover_process_live is True,
                "fatal_error": host_hover_fatal,
                "host_completion_witnesses": [
                    host_hover_end[-1], host_hover_result[-1],
                ],
                "before": host_hover_before,
                "after": host_hover_after,
                "same_frame_sha256": (
                    host_hover_before["sha256"] ==
                    host_hover_after["sha256"]
                ),
            }
            if (not host_hover_advanced and
                    host_hover_process_live is True and
                    not host_hover_fatal):
                result["gameplay_host_hover_probe"]["classification"] = \
                    "runtime-confirmed-live-process-presentation-stall"
                result["gameplay_host_hover_probe"]["process_sample"] = \
                    capture_process_sample(
                        remote, pid, 2.0,
                        args.output / "stray-host-hover-stall.sample.txt",
                    )
                result["result"] = "HOST_INPUT_PRESENTATION_STALL"
                raise RuntimeError(
                    "Stray stayed live but stopped publishing presents after "
                    "MacWSHost's 120 Hz fullscreen hover input"
                )
            result["gameplay_host_hover_probe"]["classification"] = (
                "presentation-continued-after-host-input"
                if host_hover_advanced else
                "game-exited-or-fatal-after-host-input"
            )
        if args.gameplay_host_long_drag_probe:
            if not args.progress_to_gameplay or not result.get(
                    "gameplay_visual_reached"):
                raise RuntimeError(
                    "gameplay Host long-drag probe requires a verified "
                    "gameplay visual"
                )
            if remote.input_agent is None or remote.host_log_offset is None:
                raise RuntimeError(
                    "gameplay Host long-drag probe requires the prearmed "
                    "Host URL/input and log transports"
                )
            drag_before_path = args.output / "gameplay-host-long-drag-before.png"
            drag_after_path = args.output / "gameplay-host-long-drag-after.png"
            drag_before = capture_game_view(
                remote, int(window["window"]), drag_before_path
            )
            drag_before["image_stats"] = exact_image_stats(drag_before)
            drag_before["sha256"] = hashlib.sha256(
                drag_before_path.read_bytes()
            ).hexdigest()
            before_drag_samples = present_samples(log_suffix(remote, offset))
            drag_sequence_floor = max(
                (sample["sequence"] for sample in before_drag_samples),
                default=0,
            )
            host_text_before = host_log_suffix(
                remote, int(remote.host_log_offset)
            )
            request_transport = remote.input_agent.open_url(
                "macwshost://performance-gesture-long-drag"
            )
            thermally_guarded_pause(
                "gameplay-host-long-drag-observation",
                args.long_drag_observation_seconds,
            )
            host_text_after = host_log_suffix(
                remote, int(remote.host_log_offset)
            )
            if not host_text_after.startswith(host_text_before):
                raise RuntimeError(
                    "MacWSHost log stream was not append-only during the "
                    "120 Hz long-drag probe"
                )
            drag_host_log = host_text_after[len(host_text_before):]
            drag_host_end = [
                line for line in drag_host_log.splitlines()
                if ("performance-gesture-end scenario=long-drag "
                    "success=YES") in line
            ]
            drag_host_result = [
                line for line in drag_host_log.splitlines()
                if ("performance-url-gesture scenario=long-drag "
                    "success=YES") in line
            ]
            if not drag_host_end or not drag_host_result:
                raise RuntimeError(
                    "MacWSHost did not acknowledge completion of its "
                    "420-ms hold plus 120 Hz drag route: " +
                    drag_host_log[-1000:]
                )
            drag_suffix = log_suffix(remote, offset)
            drag_fatal = ue_fatal_excerpt(drag_suffix)
            after_drag_samples = present_samples(drag_suffix)
            drag_last_sequence = max(
                (sample["sequence"] for sample in after_drag_samples),
                default=0,
            )
            drag_after = capture_game_view(
                remote, int(window["window"]), drag_after_path
            )
            drag_after["image_stats"] = exact_image_stats(drag_after)
            drag_after["sha256"] = hashlib.sha256(
                drag_after_path.read_bytes()
            ).hexdigest()
            drag_process_live = root_pid_liveness(remote, pid)
            drag_advanced = drag_last_sequence > drag_sequence_floor
            result["gameplay_host_long_drag_probe"] = {
                "route": (
                    "MacWSHost 420-ms hold + 120-sample 120-Hz drag -> "
                    "macwsinputd -> Dock/WindowServer primary pointer"
                ),
                "request_transport": request_transport,
                "observation_seconds": args.long_drag_observation_seconds,
                "sequence_floor": drag_sequence_floor,
                "last_sequence": drag_last_sequence,
                "presentation_advanced": drag_advanced,
                "process_live": drag_process_live is True,
                "fatal_error": drag_fatal,
                "host_completion_witnesses": [
                    drag_host_end[-1], drag_host_result[-1],
                ],
                "before": drag_before,
                "after": drag_after,
                "same_frame_sha256": (
                    drag_before["sha256"] == drag_after["sha256"]
                ),
            }
            if drag_fatal:
                result["gameplay_host_long_drag_probe"]["classification"] = \
                    "runtime-confirmed-fatal-after-host-long-drag"
                result["result"] = "GAMEPLAY_HOST_LONG_DRAG_FATAL"
                raise RuntimeError(
                    "Stray reported a UE fatal after MacWSHost's bounded "
                    "long-drag input"
                )
            if not drag_advanced or drag_process_live is not True:
                result["gameplay_host_long_drag_probe"]["classification"] = \
                    "runtime-confirmed-no-presentation-after-host-long-drag"
                if drag_process_live is True:
                    result["gameplay_host_long_drag_probe"][
                        "process_sample"] = capture_process_sample(
                            remote, pid, 2.0,
                            args.output /
                            "stray-host-long-drag-stall.sample.txt",
                        )
                result["result"] = "GAMEPLAY_HOST_LONG_DRAG_STALL"
                raise RuntimeError(
                    "Stray stopped publishing presents after MacWSHost's "
                    "bounded long-drag input"
                )
            result["gameplay_host_long_drag_probe"]["classification"] = \
                "presentation-continued-after-host-long-drag"
        if args.gameplay_walk_probe:
            if not args.progress_to_gameplay or not result.get(
                    "gameplay_visual_reached"):
                raise RuntimeError(
                    "gameplay walk probe requires a verified gameplay visual"
                )
            previous_window_id = int(window["window"])
            try:
                window = largest_window(
                    remote, pid, min(1.0, args.catalog_timeout)
                )
            except RuntimeError:
                pass
            if int(window["window"]) != previous_window_id:
                result["window_history"].append({
                    "stage": "gameplay-walk-focused-window-refresh",
                    **window,
                    "previous_window": previous_window_id,
                })
            walk_before_path = args.output / "gameplay-walk-before.png"
            walk_after_path = args.output / "gameplay-walk-after.png"
            walk_before = capture_game_view(
                remote, int(window["window"]), walk_before_path
            )
            walk_before["image_stats"] = exact_image_stats(walk_before)
            walk_before["sha256"] = hashlib.sha256(
                walk_before_path.read_bytes()
            ).hexdigest()
            before_walk_samples = present_samples(log_suffix(remote, offset))
            walk_sequence_floor = max(
                (sample["sequence"] for sample in before_walk_samples),
                default=0,
            )
            walk_input = send_game_key(
                remote, pid, window, args.walk_key, args.walk_hold
            )
            thermally_guarded_pause(
                "gameplay-walk-observation", args.walk_observation_seconds
            )
            walk_suffix = log_suffix(remote, offset)
            walk_fatal = ue_fatal_excerpt(walk_suffix)
            after_walk_samples = present_samples(walk_suffix)
            walk_last_sequence = max(
                (sample["sequence"] for sample in after_walk_samples),
                default=0,
            )
            walk_after = capture_game_view(
                remote, int(window["window"]), walk_after_path
            )
            walk_after["image_stats"] = exact_image_stats(walk_after)
            walk_after["sha256"] = hashlib.sha256(
                walk_after_path.read_bytes()
            ).hexdigest()
            walk_process_live = root_pid_liveness(remote, pid)
            walk_advanced = walk_last_sequence > walk_sequence_floor
            result["gameplay_walk_probe"] = {
                "key": args.walk_key,
                "hold_seconds": args.walk_hold,
                "input": walk_input,
                "observation_seconds": args.walk_observation_seconds,
                "sequence_floor": walk_sequence_floor,
                "last_sequence": walk_last_sequence,
                "presentation_advanced": walk_advanced,
                "process_live": walk_process_live is True,
                "fatal_error": walk_fatal,
                "before": walk_before,
                "after": walk_after,
                "same_frame_sha256": (
                    walk_before["sha256"] == walk_after["sha256"]
                ),
            }
            if walk_fatal:
                result["gameplay_walk_probe"]["classification"] = \
                    "runtime-confirmed-fatal-after-movement"
                result["result"] = "GAMEPLAY_WALK_FATAL"
                raise RuntimeError(
                    "Stray reported a UE fatal after the bounded gameplay "
                    "movement probe"
                )
            if not walk_advanced or walk_process_live is not True:
                result["gameplay_walk_probe"]["classification"] = \
                    "runtime-confirmed-no-presentation-after-movement"
                if walk_process_live is True:
                    result["gameplay_walk_probe"]["process_sample"] = \
                        capture_process_sample(
                            remote, pid, 2.0,
                            args.output / "stray-walk-stall.sample.txt",
                        )
                result["result"] = "GAMEPLAY_WALK_STALL"
                raise RuntimeError(
                    "Stray stopped publishing presents during the bounded "
                    "gameplay movement probe"
                )
            result["gameplay_walk_probe"]["classification"] = \
                "presentation-continued-after-movement"
        if args.capture_render_targets:
            if (not args.progress_to_gameplay or not result.get(
                    "gameplay_visual_reached")):
                raise RuntimeError(
                    "render-target capture requires a verified gameplay visual"
                )
            visual_capture = {}
            marker_was_armed = False
            try:
                visual_capture["marker_arm"] = \
                    input_agent.set_diagnostic_markers(
                        [RENDER_TARGET_CAPTURE_MARKER], True
                    )
                marker_was_armed = True
                # Arm before the UIKit witness.  The black rectangle is
                # transient; taking the screenshot first allowed it to vanish
                # before the first internal target was copied, making two
                # individually valid captures describe different frames.
                # The render-target batch now overlaps this exact visible
                # observation window.
                before_path = \
                    args.output / "render-target-final-before.png"
                before = capture_game_view(
                    remote, int(window["window"]), before_path
                )
                before["image_stats"] = exact_image_stats(before)
                before["isolated_dark_regions"] = \
                    isolated_dark_regions(before)
                visual_capture["final_before"] = before
                # Six command buffers cover the tail of the current UE frame
                # and the following deferred/postprocess frame.  One second
                # is intentionally diagnostic and is not scored as FPS.
                thermally_guarded_pause("render-target-batch", 1.0)
            finally:
                if marker_was_armed:
                    visual_capture["marker_retire"] = \
                        input_agent.set_diagnostic_markers(
                            [RENDER_TARGET_CAPTURE_MARKER], False
                        )
            visual_capture["windowserver_layers"] = capture_host_layers(
                remote, args.output / "windowserver-layers"
            )
            after_path = args.output / "render-target-final-after.png"
            after = capture_game_view(
                remote, int(window["window"]), after_path
            )
            after["image_stats"] = exact_image_stats(after)
            after["isolated_dark_regions"] = isolated_dark_regions(after)
            visual_capture["final_after"] = after
            visual_capture["render_targets"] = \
                collect_render_target_captures(
                    remote, args.output / "render-targets"
                )
            capture_suffix = log_suffix(remote, offset)
            visual_capture["runtime_witness"] = [
                line for line in capture_suffix.splitlines()
                if ("STRAY-RT-CAPTURE" in line or
                    "STRAY-NAN-" in line or
                    "STRAY-NIL-TEXTURE" in line or
                    "STRAY-FRAGMENT-BIND" in line or
                    "STRAY-STAGE-" in line or
                    "RENDER-PIPELINE #" in line or
                    "STRAY-MRT " in line or
                    "STRAY-DEPTH-PASS " in line)
            ][-1200:]
            result["render_target_capture"] = visual_capture
        if args.process_sample_seconds:
            thermal_checkpoint("process-sample-before", force=True)
            result["process_sample"] = capture_process_sample(
                remote, pid, args.process_sample_seconds,
                args.output / "stray-process.sample.txt",
            )
            thermal_checkpoint("process-sample-after", force=True)
        if args.windowserver_sample_seconds:
            windowserver_pids = []
            for row in process_table(remote, "pid=,command="):
                fields = row.strip().split(None, 1)
                if len(fields) != 2:
                    continue
                candidate, command = fields
                if (candidate.isdigit() and
                        "SkyLight.framework/Resources/WindowServer" in
                        command):
                    windowserver_pids.append(int(candidate))
            if len(windowserver_pids) != 1:
                raise RuntimeError(
                    "expected exactly one live WindowServer for sampling; "
                    f"observed={windowserver_pids}"
                )
            thermal_checkpoint("windowserver-sample-before", force=True)
            result["windowserver_sample"] = capture_process_sample(
                remote, windowserver_pids[0],
                args.windowserver_sample_seconds,
                args.output / "windowserver-process.sample.txt",
            )
            thermal_checkpoint("windowserver-sample-after", force=True)
        thermally_guarded_pause("game-warmup", args.warmup)
        window, window_evidence = refresh_game_window_or_present(
            remote, pid, window, offset, observed_sequence,
            max(8.0, args.catalog_timeout), "before-sample",
        )
        observed_sequence = window_evidence["present_sequence"]
        result["window_history"].append(window_evidence)
        result["profile_timeline"].append({
            "stage": "before-sample",
            "time": time.time(),
            "values": read_profile(remote),
        })
        sample_preflight = thermal_snapshot(remote)
        result["thermal"].append(sample_preflight)
        result["thermal_pre_sample"] = sample_preflight
        if (sample_preflight["state"] == "unknown" or
                not thermally_safe(sample_preflight,
                                   args.temperature_ceiling)):
            result["thermal_valid"] = False
            result.setdefault("thermal_pressure_observed", []).append(
                {**sample_preflight, "stage": "before-sample"}
            )
        result["gpu_before_sample"] = gpu_snapshot(remote)
        if gpu_power_stream is not None:
            gpu_power_sample_mark = gpu_power_stream.mark()
        # Begin the scored interval at the actual iPad presentation boundary.
        # The legacy STRAY-PRESENT trace remains a producer-call liveness
        # diagnostic below.  It has no iPad CAMetalDrawable presentation
        # callback and is therefore never the visible-FPS authority.
        result["host_visible_fps_reset"] = reset_host_visible_fps(
            remote, pid
        )
        before_sample = present_samples(log_suffix(remote, offset))
        sequence_floor = max(
            (sample["sequence"] for sample in before_sample), default=0
        )
        result["sequence_floor"] = sequence_floor
        deadline = time.monotonic() + args.sample_seconds
        result["cpu_perf_levels"] = []
        result["thermal_during_sample"] = []
        result["background_during_sample"] = []
        if args.wait_trace:
            remote.sudo(f"touch {shlex.quote(WAIT_TRACE_CAPTURE_MARKER)}")
            result["wait_trace_capture_started"] = time.time()
        # These are independent read-only witnesses.  Collect them in one
        # wall-clock interval so SSH latency does not stretch a nominal
        # 20-second benchmark into a minute-long heat soak.
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                while time.monotonic() < deadline:
                    thermal_future = pool.submit(thermal_snapshot, remote)
                    perf_future = pool.submit(perf_level_snapshot, remote, pid)
                    runtime_future = pool.submit(log_suffix, remote, offset)
                    process_future = pool.submit(
                        process_table, remote,
                        "pid=,uid=,pcpu=,command="
                    )
                    sample = thermal_future.result()
                    process_rows = process_future.result()
                    result["thermal"].append(sample)
                    result["thermal_during_sample"].append(sample)
                    result.setdefault("cpu", []).append({
                        "time": time.time(),
                        "processes": cpu_snapshot(
                            remote, pid, process_rows
                        ),
                    })
                    result["cpu_perf_levels"].append(perf_future.result())
                    result["background_during_sample"].append(
                        background_cpu_snapshot(remote, process_rows)
                    )
                    process_live = root_pid_liveness(remote, pid)
                    if (sample["state"] == "unknown" or
                            not thermally_safe(
                                sample, args.temperature_ceiling)):
                        result["thermal_valid"] = False
                        result.setdefault(
                            "thermal_pressure_observed", []
                        ).append({**sample, "stage": "sample"})
                    if process_live is False:
                        result["result"] = "GAME_EXITED"
                        break
                    fatal = ue_fatal_excerpt(runtime_future.result())
                    if fatal:
                        result["result"] = "GAME_FATAL"
                        result["fatal_error"] = fatal
                        break
                    time.sleep(min(args.thermal_interval,
                                   max(0.0, deadline - time.monotonic())))
        finally:
            if args.wait_trace:
                remote.sudo(
                    f"rm -f {shlex.quote(WAIT_TRACE_CAPTURE_MARKER)}",
                    check=False,
                )
                result["wait_trace_capture_stopped"] = time.time()
        suffix = log_suffix(remote, offset)
        result["gpu_after_sample"] = gpu_snapshot(remote)
        if (gpu_power_stream is not None and
                gpu_power_sample_mark is not None):
            try:
                result["gpu_power_during_sample"] = \
                    gpu_power_stream.samples_since(gpu_power_sample_mark)
                result["gpu_power_summary"] = gpu_power_summary(
                    result["gpu_power_during_sample"]
                )
            except Exception as gpu_power_sample_error:
                result["gpu_power_sample_error"] = str(
                    gpu_power_sample_error
                )
        if args.app_input_diagnostics:
            result["app_input_log"] = [
                line for line in suffix.splitlines()
                if ("APP-INPUT" in line or
                    "STRAY-INPUT-CONSUME" in line)
            ][-400:]
            result["app_input_consume_summary"] = \
                app_input_consume_summary(result["app_input_log"])
        if args.wait_trace:
            result["wait_trace_blocks"] = wait_trace_blocks(suffix)
            result["wait_trace_count"] = len(result["wait_trace_blocks"])
            result["submit_flag_samples"] = submit_flag_samples(suffix)
            result["surface_lock_blocks"] = surface_lock_blocks(suffix)
            result["surface_lock_count"] = len(
                result["surface_lock_blocks"])
        if args.drawable_timing:
            result["drawable_timing_log"] = [
                line for line in suffix.splitlines()
                if ("STRAY-DRAWABLE-TIMING" in line or
                    "STRAY-DRAWABLE-PRESENTED" in line or
                    "STRAY-APP-STATE" in line or
                    "STRAY-DISPLAY-SYNC" in line or
                    "STRAY-PRESENT-COMPLETE" in line or
                    "STRAY-COMMIT-COMPLETE" in line)
            ]
            result["drawable_timing_summary"] = {
                "next_drawable": cumulative_timing_window(
                    suffix, DRAWABLE_TIMING_RE, sequence_floor
                ),
                "present_to_presented": cumulative_timing_window(
                    suffix, DRAWABLE_PRESENTED_RE, sequence_floor
                ),
                "note": (
                    "interval averages subtract cumulative startup history; "
                    "timing mode is diagnostic-only"
                ),
            }
        result["display_sync_log"] = [
            line for line in suffix.splitlines()
            if "STRAY-DISPLAY-SYNC" in line
        ][-20:]
        all_samples = present_samples(suffix)
        measured = [sample for sample in all_samples
                    if sample["sequence"] > sequence_floor]
        result["present_samples"] = measured
        result["legacy_present_trace_fps"] = fps_summary(measured)
        if result["result"] == "INCOMPLETE":
            host_visible = snapshot_host_visible_fps(remote, pid)
            target_visible = host_visible["target"]
            result["host_visible_fps"] = host_visible
            result["fps"] = {
                "source": "host-unique-direct-drawable-presented",
                "count": int(target_visible[
                    "host_unique_frames_presented"
                ]),
                "mean_visible_fps": float(target_visible[
                    "host_visible_average_fps"
                ]),
                "one_percent_low_fps": float(target_visible[
                    "host_visible_one_percent_low_fps"
                ]),
                "visible_elapsed_s": float(target_visible[
                    "host_visible_elapsed_s"
                ]),
                "producer_delivered_average_fps": float(target_visible[
                    "producer_delivered_average_fps"
                ]),
                "producer_sequence_average_fps": float(target_visible[
                    "producer_sequence_average_fps"
                ]),
                "missing_transport_sequences": int(target_visible[
                    "missing_sequences"
                ]),
                "frame_interval": target_visible[
                    "host_visible_frame_interval"
                ],
            }
        else:
            result["fps"] = {"source": "unavailable", "count": 0}
        result["cpu_perf_level_summary"] = perf_level_summary(
            result["cpu_perf_levels"]
        )
        result["throttle"] = throttle_summary(
            result["thermal_during_sample"], result["fps"],
            result["cpu_perf_level_summary"],
        )
        result["profile_after"] = read_profile(remote)
        result["engine_system_settings_after"] = \
            read_engine_system_settings(
                remote,
                list(result["engine_system_settings_requested"]),
            )
        result["engine_system_settings_drift"] = any(
            result["engine_system_settings_after"].get(key) != value
            for key, value in
            result["engine_system_settings_requested"].items()
        )
        result["metalfx_runtime"] = metalfx_runtime_summary(
            suffix, args.screen_percentage
        )
        profile_drift_fields = {}
        for key, value in result["profile_requested"].items():
            actual = result["profile_after"].get(key)
            if actual == value:
                continue
            # Stray consumes and then removes its non-stock ScalingSolution
            # key before the first game window.  Accepting absence by itself
            # hid a failed scaler; requiring the real MetalFX encode class and
            # the requested input/output ratio proves the stronger runtime
            # postcondition.  BuiltIn historically has no persisted key.
            scaling_runtime_satisfied = (
                key == "ScalingSolution" and actual is None and (
                    value == "BuiltIn" or
                    (value == "MetalFX" and
                     result["metalfx_runtime"]["active"])
                )
            )
            if not scaling_runtime_satisfied:
                profile_drift_fields[key] = {
                    "requested": value, "actual": actual
                }
        result["profile_drift_fields"] = profile_drift_fields
        result["profile_drift"] = bool(profile_drift_fields) or \
            result["engine_system_settings_drift"]
        try:
            window = largest_window(remote, pid, 5)
            result["window_history"].append({"stage": "capture", **window})
            result["window_capture"] = capture_game_view(
                remote, window["window"], window_screenshot_path
            )
            result["window_capture_visual_state"] = stray_visual_state(
                recognize_exact_window(result["window_capture"])
            )
            result["window_capture_image_stats"] = exact_image_stats(
                result["window_capture"]
            )
        except Exception as error:
            result["window_capture_error"] = str(error)
        try:
            result["capture"] = capture_vnc(
                args.host, args.vnc_port, screenshot_path
            )
        except Exception as error:  # keep the performance evidence on RFB loss
            result["capture_error"] = str(error)
        if result["result"] == "INCOMPLETE":
            if result["profile_drift"]:
                result["result"] = "PROFILE_DRIFT"
            elif (args.disable_display_sync and
                  not result.get("display_sync_log")):
                result["result"] = "DISPLAY_SYNC_NOT_APPLIED"
            elif not measured:
                result["result"] = "NO_PRESENT_SAMPLES"
            elif (args.progress_to_gameplay and (
                    not result.get("gameplay_visual_reached") or
                    result.get("window_capture_visual_state") !=
                    "non-menu-or-unrecognized" or
                    result.get("window_capture_image_stats", {}).get(
                        "nonblack_ratio", 0.0
                    ) < 0.05)):
                result["result"] = "NOT_GAMEPLAY"
            else:
                result["result"] = "OK"
    except ThermalPreflightInterrupted as error:
        result["thermal"].extend(error.history)
        result["interruption"] = str(error)
        result["result"] = "INTERRUPTED"
    except ThermalPreflightError as error:
        result["thermal"].extend(error.history)
        result["error"] = str(error)
        if result["result"] == "INCOMPLETE":
            result["result"] = "ERROR"
    except Exception as error:
        result["error"] = str(error)
        if result["result"] == "INCOMPLETE":
            result["result"] = "ERROR"
    finally:
        if cooling_render_path_stopped:
            try:
                result["render_path_cooling_emergency_resume"] = {
                    "pids": signal_exact_render_path(
                        remote, cooling_render_path, "CONT"
                    ),
                    "action": "SIGCONT",
                }
                cooling_render_path_stopped = False
                cooling_render_path = {}
            except Exception as render_resume_error:
                result[
                    "render_path_cooling_emergency_resume_error"
                ] = str(render_resume_error)
        if game_precondition_stopped and steam_signal_agent is not None and \
                pid > 1:
            try:
                result["gameplay_thermal_preconditioning_emergency_resume"] = \
                    steam_signal_agent.signal_exact(
                        int(signal.SIGCONT), pid, GAME_NAME
                    )
                game_precondition_stopped = False
            except Exception as game_resume_error:
                result[
                    "gameplay_thermal_preconditioning_emergency_resume_error"
                ] = str(game_resume_error)
        if cooldown_suspended_steam and not cooldown_steam_resumed:
            try:
                result["steam_cooldown_emergency_resume"] = {
                    "pids": signal_exact_pids(
                        remote, cooldown_suspended_steam, "CONT"
                    ),
                    "action": "SIGCONT",
                }
                cooldown_steam_resumed = True
            except Exception as resume_error:
                result["steam_cooldown_emergency_resume_error"] = str(
                    resume_error
                )
        # Marker absence is the production-start precondition, so clear every
        # launch/runtime diagnostic before releasing the shared GUI lease.
        # Do this even for --leave-running: the bounded observation is over,
        # and a surviving sentinel must not make Repair Desktop unable to
        # rebuild a later WindowServer generation.
        if markers_to_touch:
            try:
                if input_agent is None:
                    raise RuntimeError("prearmed marker agent unavailable")
                result["diagnostic_marker_cleanup"] = \
                    input_agent.set_diagnostic_markers(
                        markers_to_touch, False
                    )
                diagnostic_markers_armed = False
            except Exception as marker_cleanup_error:
                result["diagnostic_marker_cleanup_agent_error"] = str(
                    marker_cleanup_error
                )
                try:
                    remote.sudo(
                        "rm -f " + " ".join(
                            shlex.quote(path) for path in markers_to_touch
                        )
                    )
                    diagnostic_markers_armed = False
                    result["diagnostic_marker_cleanup_fallback"] = \
                        "sudo-exact-paths"
                except Exception as marker_cleanup_fallback_error:
                    result["diagnostic_marker_cleanup_fallback_error"] = str(
                        marker_cleanup_fallback_error
                    )
        if args.steam_applaunch:
            # Normally consumed before steam_osx is exec'd. Remove an
            # unconsumed request after any preflight failure so a later manual
            # Steam start can never launch Stray unexpectedly.
            try:
                remote.sudo(
                    f"rm -f {shlex.quote(STEAM_APPLAUNCH_MARKER)}",
                    check=False,
                )
                result["steam_applaunch_marker_cleanup"] = True
            except Exception as applaunch_cleanup_error:
                result["steam_applaunch_marker_cleanup_error"] = str(
                    applaunch_cleanup_error
                )
        if gui_transaction_lease_held and input_agent is not None:
            try:
                result["gui_transaction_lease_cleanup"] = \
                    input_agent.release_gui_lease()
                gui_transaction_lease_held = False
            except Exception as gui_lease_cleanup_error:
                result["gui_transaction_lease_cleanup_error"] = str(
                    gui_lease_cleanup_error
                )
        if args.wait_trace:
            # These diagnostics must never outlive the bounded run, including
            # --leave-running: the installed hook preserves behavior, while a
            # stale capture marker would keep paying backtrace cost.
            remote.sudo(
                "rm -f " + shlex.quote(WAIT_TRACE_INSTALL_MARKER) + " " +
                shlex.quote(WAIT_TRACE_CAPTURE_MARKER),
                check=False,
            )
        if pid > 1 and args.steam_overlay_frame_time_logging:
            try:
                result["steam_overlay_frame_time_log"] = \
                    capture_steam_overlay_log(
                        remote, pid,
                        args.output / f"gameoverlayrenderer.{pid}.log",
                    )
            except Exception as capture_error:
                result["steam_overlay_frame_time_log_error"] = str(
                    capture_error
                )
        # A lazy UE Metal pipeline can be created after the FPS deadline but
        # before the exact game process is terminated.  The sample-loop poll
        # alone therefore has a small blind window: PID 64135 produced its
        # first "Target OS is incompatible" / UE fatal while the final window
        # capture was being collected, and the old runner incorrectly wrote
        # OK.  Read from this run's pre-launch byte offset immediately before
        # cleanup and make that concrete fatal authoritative.  This remains a
        # read-only witness; it neither retries the pipeline nor suppresses
        # Unreal's failure path.
        if pid > 1 and "runtime_log_offset" in result:
            try:
                final_runtime_suffix = log_suffix(
                    remote, int(result["runtime_log_offset"])
                )
                final_runtime_fatal = ue_fatal_excerpt(final_runtime_suffix)
                result["final_runtime_fatal_check"] = {
                    "checked_immediately_before_game_cleanup": True,
                    "fatal_error": final_runtime_fatal,
                }
                if final_runtime_fatal:
                    result["fatal_error"] = final_runtime_fatal
                    if result.get("result") not in {
                            "GAME_FATAL", "GAMEPLAY_HOVER_FATAL",
                            "GAMEPLAY_HOST_HOVER_FATAL",
                            "GAMEPLAY_LONG_DRAG_FATAL",
                            "GAMEPLAY_WALK_FATAL"}:
                        result["result"] = "GAME_FATAL_LATE"
            except Exception as final_fatal_check_error:
                result["final_runtime_fatal_check_error"] = str(
                    final_fatal_check_error
                )
        if args.gpu_command_error_diagnostics and pid > 1:
            try:
                result["gpu_command_error_artifacts"] = \
                    collect_gpu_command_error_artifacts(
                        remote, pid,
                        args.output / "gpu-command-error-artifacts",
                    )
            except Exception as artifact_error:
                result["gpu_command_error_artifacts_error"] = str(
                    artifact_error
                )
        if not args.leave_running:
            # Resolve by exact executable basename again in case Steam created
            # the process just after a launch timeout.  This keeps a pipeline
            # bookkeeping error from leaving an unmonitored thermal load.
            cleanup_pid = pid if pid > 1 else 0
            if cleanup_pid <= 1:
                try:
                    cleanup_pid = game_pid(remote)
                except Exception as cleanup_probe_error:
                    result["game_cleanup_probe_error"] = str(
                        cleanup_probe_error
                    )
                    cleanup_pid = 0
            try:
                if steam_signal_agent is not None and cleanup_pid > 1:
                    result["game_cleanup"] = \
                        terminate_exact_game_with_agent(
                            steam_signal_agent, cleanup_pid
                        )
                else:
                    result["game_cleanup"] = terminate_exact_game(
                        remote, cleanup_pid
                    )
            except Exception as game_cleanup_error:
                result["game_cleanup"] = {
                    "pid": cleanup_pid,
                    "confirmed_exited": False,
                    "error": str(game_cleanup_error),
                }
            if not result["game_cleanup"].get("confirmed_exited"):
                result["result"] = "CLEANUP_INCOMPLETE"
            try:
                compiler_pids_after = mtl_compiler_service_pids(remote)
                owned_compilers = sorted(
                    compiler_pids_after - compiler_pids_before
                )
                cleanup = {
                    "owned_after_game": owned_compilers,
                    "survivors_after_term": [],
                    "survivors_after_kill": [],
                }
                if owned_compilers:
                    targets = " ".join(
                        str(process) for process in owned_compilers
                    )
                    remote.sudo(
                        f"kill -TERM {targets} 2>/dev/null || true",
                        check=False,
                    )
                    time.sleep(0.5)
                    current = mtl_compiler_service_pids(remote)
                    survivors = sorted(set(owned_compilers) & current)
                    cleanup["survivors_after_term"] = survivors
                    if survivors:
                        targets = " ".join(
                            str(process) for process in survivors
                        )
                        remote.sudo(
                            f"kill -KILL {targets} 2>/dev/null || true",
                            check=False,
                        )
                        time.sleep(0.25)
                    cleanup["survivors_after_kill"] = sorted(
                        set(owned_compilers) &
                        mtl_compiler_service_pids(remote)
                    )
                result["mtl_compiler_services_cleanup"] = cleanup
            except Exception as compiler_cleanup_error:
                result["mtl_compiler_services_cleanup_error"] = str(
                    compiler_cleanup_error
                )
        else:
            # ``--leave-running`` is commonly used while inspecting a fatal
            # frame.  At Stray's allocation peak iPadOS may temporarily be
            # unable to fork ``ps``; do not let that optional observation
            # replace the real test error or prevent result.json from being
            # written.
            try:
                current_compilers = sorted(
                    mtl_compiler_service_pids(remote)
                )
            except Exception as compiler_probe_error:
                current_compilers = []
                result["mtl_compiler_services_probe_error"] = str(
                    compiler_probe_error
                )
            result["mtl_compiler_services_cleanup"] = {
                "skipped": "Stray was intentionally left running",
                "current": current_compilers,
            }
        if steam_game_quiesced_pids:
            try:
                result["steam_game_quiesce_resume"] = {
                    "pids": steam_signal_agent.signal(
                        int(signal.SIGCONT), steam_game_quiesced_pids
                    ),
                    "action": "SIGCONT",
                }
                steam_game_quiesced_pids = set()
            except Exception as steam_resume_error:
                result["steam_game_quiesce_resume_error"] = str(
                    steam_resume_error
                )
        if steam_signal_agent is not None:
            try:
                if steam_signal_agent is input_agent:
                    # Keep the shared channel alive until the prearmed sample
                    # agent has copied/removed its artifact and the ordinary
                    # input-agent teardown runs below.
                    result["steam_signal_agent_close_deferred"] = \
                        "shared-input-agent-finalizer"
                else:
                    steam_signal_agent.close()
                remote.signal_agent = None
                result["steam_signal_agent_closed"] = True
            except Exception as signal_agent_close_error:
                result["steam_signal_agent_close_error"] = str(
                    signal_agent_close_error
                )
        cleanup_confirmed = (
            args.leave_running or
            result.get("game_cleanup", {}).get("confirmed_exited") is True
        )
        if device_safety_armed and cleanup_confirmed:
            result["device_safety_cleanup"] = disarm_stray_safety(remote)
            device_safety_armed = False
        elif device_safety_armed:
            # This branch is retained for result-schema compatibility only;
            # current observe-only builds never arm a process-control job.
            result["device_safety_cleanup"] = {
                "armed": True,
                "reason": "game exit could not be runtime-confirmed",
            }
        if args.capture_metal_libraries:
            try:
                report_script = (
                    "import glob,os; "
                    "p=glob.glob('/var/mnt/rootfs/private/tmp/"
                    "macws_mtl_data_*.bin'); "
                    "print(len(p),sum(os.path.getsize(x) for x in p))"
                )
                capture_report = remote.sudo(
                    "/var/jb/usr/bin/python3 -c " +
                    shlex.quote(report_script),
                    check=False,
                ).strip().split()
                result["metal_library_capture"] = {
                    "count": int(capture_report[-2]),
                    "bytes": int(capture_report[-1]),
                    "limit": 512,
                    "behavior": "observer-only byte-exact input capture",
                }
            except Exception as capture_report_error:
                result["metal_library_capture_error"] = str(
                    capture_report_error
                )
        if (not args.leave_running and not args.keep_steam_running and
                not retain_steam_preflight and
                steam_started_by_pipeline):
            try:
                steam_stop_survivors = stop_steam_ui(remote)
                result["pipeline_started_steam_stop_survivors"] = \
                    steam_stop_survivors
                result["pipeline_started_steam_stopped"] = not \
                    steam_stop_survivors
            except Exception as steam_cleanup_error:
                steam_stop_survivors = ["process-table-unavailable"]
                result["pipeline_started_steam_stop_error"] = str(
                    steam_cleanup_error
                )
                result["pipeline_started_steam_stopped"] = False
            if steam_stop_survivors and result["result"] == "OK":
                result["result"] = "CLEANUP_INCOMPLETE"
        elif (not args.leave_running and args.keep_steam_running and
              steam_started_by_pipeline):
            result["pipeline_started_steam_stopped"] = False
            result["pipeline_started_steam_retained_for_reuse"] = True
        elif retain_steam_preflight:
            result["pipeline_started_steam_stopped"] = False
            result["pipeline_started_steam_retained_for_reuse"] = True
        if overlay_environment_configured:
            clear_steam_overlay_test_environment(remote)
        if restore_control_center:
            try:
                result["control_center_restore"] = \
                    restore_control_center_after_game(remote)
            except Exception as restore_error:
                result["control_center_restore_error"] = str(restore_error)
                if result["result"] == "OK":
                    result["result"] = "CLEANUP_INCOMPLETE"
        try:
            result["power_after"] = power_snapshot(remote)
        except Exception as power_error:
            result["power_after_error"] = str(power_error)
        try:
            result["thermal_after"] = thermal_snapshot(remote)
        except Exception as thermal_probe_error:
            result["thermal_after"] = {
                "state": "unknown",
                "observation_error": str(thermal_probe_error),
            }
        if (result["thermal_after"]["state"] == "unknown" or
                not thermally_safe(result["thermal_after"],
                                   args.temperature_ceiling)):
            result["thermal_valid"] = False
            result.setdefault("thermal_pressure_observed", []).append({
                **result["thermal_after"], "stage": "post-cleanup",
            })
        if args.cool_game_before_sample:
            scored_thermal_samples = []
            if result.get("thermal_pre_sample"):
                scored_thermal_samples.append(result["thermal_pre_sample"])
            scored_thermal_samples.extend(
                result.get("thermal_during_sample", [])
            )
            result["thermal_valid_for_scored_interval"] = bool(
                scored_thermal_samples
            ) and all(
                sample.get("state") == "nominal"
                for sample in scored_thermal_samples
            )
        else:
            result["thermal_valid_for_scored_interval"] = result.get(
                "thermal_valid", True
            )
        # Thermal telemetry is observe-only and must never control Stray, but
        # a functional pass recorded under iPadOS pressure is not a valid
        # no-throttling performance result.  Preserve the functional outcome
        # separately and make the top-level classification unambiguous.  This
        # runs after exact-PID bounded cleanup; it sends no signal and is also
        # safe for --leave-running user handoff.
        if not result.get("thermal_valid_for_scored_interval", True) and \
                result.get("result") == "OK":
            result["functional_result"] = "OK"
            result["result"] = "THERMAL_PRESSURE"
        if thermal_stream is not None:
            try:
                thermal_stream.close()
                result["thermal_stream_closed"] = True
            except Exception as thermal_stream_close_error:
                result["thermal_stream_close_error"] = str(
                    thermal_stream_close_error
                )
            remote.thermal_stream = None
        if gpu_power_stream is not None:
            try:
                gpu_power_stream.close()
                result["gpu_power_stream_closed"] = True
            except Exception as gpu_power_stream_close_error:
                result["gpu_power_stream_close_error"] = str(
                    gpu_power_stream_close_error
                )
        if runtime_log_stream is not None:
            try:
                runtime_log_stream.close()
                result["runtime_log_stream_closed"] = True
            except Exception as runtime_log_stream_close_error:
                result["runtime_log_stream_close_error"] = str(
                    runtime_log_stream_close_error
                )
            remote.runtime_log_stream = None
        if host_log_stream is not None:
            try:
                host_log_stream.close()
                result["host_log_stream_closed"] = True
            except Exception as host_log_stream_close_error:
                result["host_log_stream_close_error"] = str(
                    host_log_stream_close_error
                )
            remote.host_log_stream = None
        if sample_agent is not None:
            try:
                sample_agent.close()
                result["sample_agent_closed"] = True
            except Exception as sample_agent_close_error:
                result["sample_agent_close_error"] = str(
                    sample_agent_close_error
                )
            remote.sample_agent = None
        if input_agent is not None:
            try:
                input_agent.close()
                result["input_agent_closed"] = True
            except Exception as input_agent_close_error:
                result["input_agent_close_error"] = str(
                    input_agent_close_error
                )
            remote.input_agent = None
        result_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        )
        remote.close()
        output = result if args.verbose_json else {
            "result": result.get("result"),
            "pid": result.get("pid"),
            "fps": result.get("fps"),
            "throttle": result.get("throttle"),
            "thermal_after": result.get("thermal_after"),
            "user_handoff": result.get("user_handoff"),
            "error": result.get("error"),
            "evidence": str(result_path),
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

"""Prepare and measure native Stray on the development Mac.

The scored interval records the visible Steam FPS counter, Valve's optional
overlay frame-time log, AGX utilization, pmset pressure warnings, and the
GPU driver's IOReport throttler-counter delta.  It never stops the game.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import pathlib
import re
import statistics
import subprocess
import tempfile
import time


HOME = pathlib.Path.home()
STEAM = HOME / "Library/Application Support/Steam"
MANIFEST = STEAM / "steamapps/appmanifest_1332010.acf"
GAME = STEAM / "steamapps/common/Stray/Stray.app/Contents/MacOS/Stray-Mac-Shipping"
CONFIG_DIR = HOME / "Library/Preferences/Stray/MacNoEditor"
GAME_CONFIG = CONFIG_DIR / "GameUserSettings.ini"
ENGINE_CONFIG = CONFIG_DIR / "Engine.ini"
REPO = pathlib.Path(__file__).resolve().parent.parent
BUILD = pathlib.Path("/tmp/macws-local-bench")
THROTTLE_PROBE = BUILD / "macos_gpu_throttle_probe"
INPUT_PROBE = BUILD / "macbook_input_motion"
OCR_PROBE = BUILD / "vision_ocr"
STEAM_CONFIG = STEAM / "config/config.vdf"
STEAM_LOCAL_CONFIG = STEAM / "userdata/296809210/config/localconfig.vdf"

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
    "ScalingSolution": "BuiltIn",
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

QUALITY_PROFILES = {"high": HIGH_PROFILE, "medium": MEDIUM_PROFILE}


def run(command: list[str], *, check: bool = True, timeout: float = 30.0):
    return subprocess.run(command, check=check, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          timeout=timeout).stdout


def install_status():
    text = MANIFEST.read_text(errors="replace") if MANIFEST.exists() else ""
    def value(key):
        match = re.search(rf'"{re.escape(key)}"\s+"([^"]*)"', text)
        return match.group(1) if match else None
    return {
        "manifest": str(MANIFEST),
        "state_flags": value("StateFlags"),
        "build_id": value("buildid"),
        "size_on_disk": value("SizeOnDisk"),
        "executable": str(GAME),
        "executable_ready": GAME.is_file() and os.access(GAME, os.X_OK),
    }


def build_helpers():
    BUILD.mkdir(parents=True, exist_ok=True)
    commands = [
        ["xcrun", "clang", "-fblocks", "-Wall", "-Wextra", "-Werror",
         "-framework", "CoreFoundation",
         str(REPO / "misc/macos_gpu_throttle_probe.m"),
         "-o", str(THROTTLE_PROBE)],
        ["xcrun", "clang", "-fobjc-arc", "-Wall", "-Wextra", "-Werror",
         "-framework", "AppKit", "-framework", "ApplicationServices",
         str(REPO / "misc/macbook_input_motion.m"),
         "-o", str(INPUT_PROBE)],
        ["xcrun", "swiftc", str(REPO / "misc/vision_ocr.swift"),
         "-o", str(OCR_PROBE)],
    ]
    for command in commands:
        run(command, timeout=120)
    return {
        "throttle_probe": str(THROTTLE_PROBE),
        "input_probe": str(INPUT_PROBE),
        "ocr_probe": str(OCR_PROBE),
        "accessibility": json.loads(run(
            [str(INPUT_PROBE), "--preflight"]))["accessibility_preflight"],
    }


def atomic_ini_update(path: pathlib.Path, values: dict[str, str],
                      section_for: dict[str, str], remove: set[str] = set()):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(errors="replace").splitlines(keepends=True) \
        if path.exists() else []
    found = set()
    output = []
    for line in lines:
        stripped = line.rstrip("\r\n")
        key = stripped.split("=", 1)[0] if "=" in stripped else None
        if key in remove:
            continue
        if key in values:
            output.append(f"{key}={values[key]}\n")
            found.add(key)
        else:
            output.append(line)
    missing: dict[str, list[tuple[str, str]]] = {}
    for key, value in values.items():
        if key not in found:
            missing.setdefault(section_for[key], []).append((key, value))
    final = []
    emitted = set()
    for line in output:
        final.append(line)
        section = line.rstrip("\r\n")
        if section in missing:
            final.extend(f"{key}={value}\n" for key, value in missing[section])
            emitted.add(section)
    for section, entries in missing.items():
        if section in emitted:
            continue
        if final and final[-1].strip():
            final.append("\n")
        final.append(section + "\n")
        final.extend(f"{key}={value}\n" for key, value in entries)
    descriptor, temporary = tempfile.mkstemp(prefix=".macws-profile-",
                                              dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.writelines(final)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            os.chmod(temporary, path.stat().st_mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def configure(max_fps: int, eye_adaptation_quality: str, quality: str,
              width: int, height: int, screen_percentage: int,
              scaling_solution: str):
    if max_fps < 0 or max_fps > 240:
        raise SystemExit("--max-fps must be between 0 and 240")
    if (width <= 0) != (height <= 0):
        raise SystemExit("--resolution-width and --resolution-height must be set together")
    if not 25 <= screen_percentage <= 100:
        raise SystemExit("--screen-percentage must be between 25 and 100")
    game_values = dict(QUALITY_PROFILES[quality])
    game_values.update({
        "SteamDeckScreenPercentage": str(screen_percentage),
        "ScreenPercentage": str(screen_percentage),
        "sg.ResolutionQuality": f"{screen_percentage:.6f}",
        "ScalingSolution": scaling_solution,
    })
    if width > 0:
        game_values.update({
            "ResolutionSizeX": str(width),
            "ResolutionSizeY": str(height),
            "LastUserConfirmedResolutionSizeX": str(width),
            "LastUserConfirmedResolutionSizeY": str(height),
            "DesiredScreenWidth": str(width),
            "DesiredScreenHeight": str(height),
            "LastUserConfirmedDesiredScreenWidth": str(width),
            "LastUserConfirmedDesiredScreenHeight": str(height),
        })
    game_values["FrameRateLimit"] = f"{max_fps:.6f}"
    game_section = "[/Script/Hk_project.HKGameUserSettings]"
    scalability = "[ScalabilityGroups]"
    atomic_ini_update(
        GAME_CONFIG, game_values,
        {key: scalability if key.startswith("sg.") else game_section
         for key in game_values},
    )
    engine_values = {
        "t.MaxFPS": str(max_fps),
        "r.UsePreExposure": "1",
        # Runtime evidence retained in
        # docs/evidence/stray-steam-performance-20260821.md: histogram +
        # quality 0 + pre-exposure removes the synchronous adaptation
        # readback while retaining the intended scene brightness.
        "r.EyeAdaptation.MethodOverride": "1",
        "r.EyeAdaptationQuality": {
            "off": "0", "low": "1", "normal": "2", "high": "3",
        }[eye_adaptation_quality],
        "r.AllowOcclusionQueries": "1",
    }
    atomic_ini_update(
        ENGINE_CONFIG, engine_values,
        {key: "[SystemSettings]" for key in engine_values},
        {"r.Upscale.Quality", "r.RHIThread.Enable"},
    )
    observed = {}
    for path in (GAME_CONFIG, ENGINE_CONFIG):
        for line in path.read_text(errors="replace").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                if key in game_values or key in engine_values:
                    observed[key] = value
    expected = {**game_values, **engine_values}
    mismatch = {key: {"expected": value, "actual": observed.get(key)}
                for key, value in expected.items()
                if observed.get(key) != value}
    if mismatch:
        raise RuntimeError("profile verification failed: " +
                           json.dumps(mismatch, sort_keys=True))
    return {"max_fps": max_fps, "quality": quality,
            "resolution": [width, height] if width > 0 else None,
            "screen_percentage": screen_percentage,
            "scaling_solution": scaling_solution,
            "settings": observed,
            "game_config": str(GAME_CONFIG),
            "engine_config": str(ENGINE_CONFIG)}


def atomic_text_write(path: pathlib.Path, text: str):
    descriptor, temporary = tempfile.mkstemp(prefix=".macws-steam-",
                                              dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, path.stat().st_mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def steam_running():
    return bool(run(["pgrep", "-x", "steam_osx"], check=False).strip())


def update_vdf_keys(text: str, anchor: str,
                    values: dict[str, str]):
    for key, value in values.items():
        pattern = re.compile(rf'("{re.escape(key)}"\s+)"[^"]*"')
        text = pattern.sub(rf'\g<1>"{value}"', text)
    missing = [item for item in values if not re.search(
        rf'"{re.escape(item)}"\s+"[^"]*"', text)]
    if missing:
        match = re.search(anchor, text)
        if not match:
            raise RuntimeError("Steam VDF anchor not found")
        indentation = re.search(r"(?m)^(\s*)\S", match.group(0)).group(1)
        insertion = "".join(
            f'{indentation}\t"{key}"\t\t"{values[key]}"\n'
            for key in missing)
        text = text[:match.end()] + insertion + text[match.end():]
    return text


def prepare_steam_overlay():
    if steam_running():
        raise SystemExit("quit Steam before prepare-steam-overlay")
    global_values = {
        "EnableGameOverlay": "1",
        "InGameOverlayShowFPSCorner": "1",
        "InGameOverlayShowFPSContrast": "1",
        "overlay_fps_counter_corner": "1",
        "overlay_fps_counter_detail_level": "1",
    }
    account_values = {
        **global_values,
        "InGameOverlayShowFPSScaling": "1.300000",
        "InGameOverlayShowFPSDetailLevel": "2",
    }
    for path, values in ((STEAM_CONFIG, global_values),
                         (STEAM_LOCAL_CONFIG, account_values)):
        text = path.read_text(errors="replace")
        if path == STEAM_CONFIG:
            anchor = r'(?m)^\s*"Steam"\s*\n\s*\{\s*\n'
        else:
            # Updating every already-present key is intentional; missing
            # values live in UserLocalConfigStore/system, the first stable
            # account-level section read by the client.
            anchor = r'(?m)^\s*"system"\s*\n\s*\{\s*\n'
        atomic_text_write(path, update_vdf_keys(text, anchor, values))
    run(["launchctl", "setenv", "STEAM_OVERLAY_FRAME_TIME_LOGGING", "1"])
    run(["launchctl", "setenv", "STEAM_OVERLAY_LOGGING_FLUSH", "1"])
    return {
        "global_config": str(STEAM_CONFIG),
        "account_config": str(STEAM_LOCAL_CONFIG),
        "fps_corner": "top-left",
        "frame_time_logging": True,
    }


def clear_steam_overlay_environment():
    run(["launchctl", "unsetenv", "STEAM_OVERLAY_FRAME_TIME_LOGGING"],
        check=False)
    run(["launchctl", "unsetenv", "STEAM_OVERLAY_LOGGING_FLUSH"],
        check=False)
    return {"frame_time_logging_environment": "cleared"}


def thermal_snapshot():
    raw = run(["pmset", "-g", "therm"], check=False)
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    clean = len(lines) >= 3 and all(line.startswith("Note: No ")
                                    for line in lines)
    return {"time": time.time(), "unthrottled": clean,
            "raw": raw.strip()}


def gpu_snapshot():
    raw = run(["ioreg", "-r", "-c", "AGXAccelerator", "-d", "1", "-l"],
              check=False)
    def integer(name):
        match = re.search(rf'"{re.escape(name)}"\s*=\s*(-?\d+)', raw)
        return int(match.group(1)) if match else None
    return {
        "time": time.time(),
        "tiler_percent": integer("Tiler Utilization %"),
        "renderer_percent": integer("Renderer Utilization %"),
        "device_percent": integer("Device Utilization %"),
        "recovery_count": integer("recoveryCount"),
        "last_recovery_time": integer("lastRecoveryTime"),
    }


def game_pid():
    raw = run(["pgrep", "-x", "Stray-Mac-Shipping"], check=False)
    pids = [int(item) for item in raw.split() if item.isdigit()]
    return pids[-1] if pids else 0


def ocr_fps(image: pathlib.Path):
    try:
        items = json.loads(run([str(OCR_PROBE), "--json", str(image)],
                               timeout=20))
    except (subprocess.SubprocessError, json.JSONDecodeError):
        return None, []
    candidates = []
    for item in items:
        text = item.get("text", "").strip()
        explicit = re.search(
            r"(?i)(?:\bFPS\s*(\d{1,3}(?:\.\d+)?)\b|"
            r"\b(\d{1,3}(?:\.\d+)?)\s*FPS\b)", text)
        bare = re.fullmatch(r"\s*(\d{1,3}(?:\.\d+)?)\s*", text)
        match = explicit or bare
        number = (next((group for group in match.groups() if group), None)
                  if match else None)
        if number and 1 <= float(number) <= 300:
            candidates.append((bool(explicit), float(number), text))
    candidates.sort(reverse=True)
    return (candidates[0][1] if candidates else None,
            [item.get("text", "") for item in items])


def sample(pid: int, duration: float, output: pathlib.Path, input_mode: str):
    if pid <= 1:
        pid = game_pid()
    if pid <= 1:
        raise SystemExit("Stray-Mac-Shipping is not running")
    build = build_helpers()
    preflight = thermal_snapshot()
    if not preflight["unthrottled"]:
        raise SystemExit("MacBook has a thermal/performance warning: " +
                         preflight["raw"])
    output.mkdir(parents=True, exist_ok=True)
    throttle = subprocess.Popen(
        [str(THROTTLE_PROBE), f"{duration:.3f}"], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    def inject():
        time.sleep(min(3.0, duration / 4.0))
        if input_mode == "w":
            return run([str(INPUT_PROBE), "--key-hold", str(pid), "13",
                        f"{min(5.0, duration / 3.0):.3f}"],
                       timeout=duration + 10)
        if input_mode == "mouse":
            return run([str(INPUT_PROBE), "--mouse-move", str(pid),
                        f"{min(5.0, duration / 3.0):.3f}", "60"],
                       timeout=duration + 10)
        return ""

    samples = []
    started = time.monotonic()
    next_sample = started
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        input_future = executor.submit(inject)
        index = 0
        while time.monotonic() - started < duration:
            now = time.monotonic()
            if now < next_sample:
                time.sleep(next_sample - now)
            image = output / f"fps-{index:03d}.png"
            capture = run(["screencapture", "-x", "-R0,0,260,120",
                           str(image)], check=False)
            fps, text = ocr_fps(image) if image.exists() else (None, [])
            try:
                cpu = float(run(["ps", "-p", str(pid), "-o", "%cpu="],
                                check=False).strip() or "nan")
            except ValueError:
                cpu = None
            samples.append({
                "time_from_start": time.monotonic() - started,
                "fps": fps,
                "ocr_text": text,
                "capture_error": capture.strip(),
                "cpu_percent": cpu,
                "gpu": gpu_snapshot(),
                "thermal": thermal_snapshot(),
            })
            index += 1
            next_sample += 1.0
        input_result = input_future.result()
    throttle_text = throttle.communicate(timeout=15)[0].strip()
    try:
        throttle_result = json.loads(throttle_text)
    except json.JSONDecodeError:
        throttle_result = {"error": throttle_text}
    fps_values = [item["fps"] for item in samples if item["fps"] is not None]
    overlay_log = pathlib.Path(f"/tmp/gameoverlayrenderer.{pid}.log")
    result = {
        "pid": pid,
        "duration_seconds": duration,
        "input_mode": input_mode,
        "input_result": input_result.strip(),
        "helpers": build,
        "preflight": preflight,
        "postflight": thermal_snapshot(),
        "gpu_throttler_delta": throttle_result,
        "overlay_log": str(overlay_log) if overlay_log.exists() else None,
        "overlay_log_bytes": overlay_log.stat().st_size
            if overlay_log.exists() else 0,
        "visible_fps": ({
            "count": len(fps_values),
            "mean": statistics.mean(fps_values),
            "median": statistics.median(fps_values),
            "minimum": min(fps_values),
            "maximum": max(fps_values),
        } if fps_values else {"count": 0}),
        "samples": samples,
    }
    (output / "result.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("prepare-steam-overlay")
    sub.add_parser("clear-steam-overlay-environment")
    prepare = sub.add_parser("configure")
    # Cross-device comparison is uncapped.  A production/user-facing cap is
    # a separate policy and must never silently enter the benchmark profile.
    prepare.add_argument("--max-fps", type=int, default=0)
    prepare.add_argument(
        "--eye-adaptation-quality",
        choices=("off", "low", "normal", "high"), default="low",
    )
    prepare.add_argument("--quality", choices=tuple(QUALITY_PROFILES),
                         default="high")
    prepare.add_argument("--resolution-width", type=int, default=1400)
    prepare.add_argument("--resolution-height", type=int, default=900)
    prepare.add_argument("--screen-percentage", type=int, default=40)
    prepare.add_argument("--scaling-solution",
                         choices=("BuiltIn", "MetalFX"), default="BuiltIn")
    measure = sub.add_parser("sample")
    measure.add_argument("--pid", type=int, default=0)
    measure.add_argument("--duration", type=float, default=30.0)
    measure.add_argument("--output", type=pathlib.Path, required=True)
    measure.add_argument("--input", choices=("none", "w", "mouse"),
                         default="w")
    args = parser.parse_args()
    if args.command == "status":
        result = {"install": install_status(),
                  "thermal": thermal_snapshot(),
                  "game_pid": game_pid()}
    elif args.command == "prepare-steam-overlay":
        result = prepare_steam_overlay()
    elif args.command == "clear-steam-overlay-environment":
        result = clear_steam_overlay_environment()
    elif args.command == "configure":
        result = configure(
            args.max_fps, args.eye_adaptation_quality, args.quality,
            args.resolution_width, args.resolution_height,
            args.screen_percentage, args.scaling_solution,
        )
    else:
        result = sample(args.pid, args.duration, args.output, args.input)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

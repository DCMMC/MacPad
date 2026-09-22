"""Run a thermally-gated Stray render-scale sweep with one-command cleanup.

Each point uses ``stray_perf_loop.py`` as the authoritative worker.  The first
point navigates Steam's Library; later points reuse the selected Stray page,
which removes roughly twelve seconds of UI settling from every iteration.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time


def parse_percentages(value: str):
    result = []
    for item in value.split(","):
        percentage = int(item.strip())
        if not 25 <= percentage <= 100:
            raise argparse.ArgumentTypeError(
                "screen percentages must be between 25 and 100"
            )
        result.append(percentage)
    if not result:
        raise argparse.ArgumentTypeError("provide at least one percentage")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--output", type=pathlib.Path,
                        default=pathlib.Path("/tmp/macws-stray-sweep"))
    parser.add_argument("--screen-percentages", type=parse_percentages,
                        default=parse_percentages("100,70,50,40"))
    parser.add_argument("--target-fps", type=float, default=58.0)
    parser.add_argument("--resolution-width", type=int, default=0)
    parser.add_argument("--resolution-height", type=int, default=0)
    parser.add_argument("--sample-seconds", type=float, default=12.0)
    parser.add_argument("--warmup", type=float, default=6.0)
    parser.add_argument("--menu-delay", type=float, default=8.0)
    parser.add_argument("--temperature-ceiling", type=float, default=39.0)
    args = parser.parse_args()
    if not os.environ.get("MACWS_DEVICE_SUDO_PASSWORD"):
        parser.error("set MACWS_DEVICE_SUDO_PASSWORD")
    if ((args.resolution_width == 0) != (args.resolution_height == 0)):
        parser.error("set both resolution dimensions or neither")

    worker = pathlib.Path(__file__).with_name("stray_perf_loop.py")
    args.output.mkdir(parents=True, exist_ok=True)
    summary = {
        "started": time.time(),
        "target_fps": args.target_fps,
        "points": [],
        "selected": None,
    }
    for index, percentage in enumerate(args.screen_percentages):
        point_dir = args.output / f"high-{percentage:03d}"
        command = [
            sys.executable, str(worker), "--host", args.host,
            "--output", str(point_dir),
            "--screen-percentage", str(percentage),
            "--sample-seconds", str(args.sample_seconds),
            "--warmup", str(args.warmup),
            "--menu-delay", str(args.menu_delay),
            "--temperature-ceiling", str(args.temperature_ceiling),
            "--resolution-width", str(args.resolution_width),
            "--resolution-height", str(args.resolution_height),
        ]
        if index:
            command.append("--reuse-steam-selection")
        completed = subprocess.run(command, stdout=subprocess.DEVNULL)
        result_path = point_dir / "result.json"
        result = json.loads(result_path.read_text()) if result_path.exists() else {
            "result": "WORKER_DID_NOT_WRITE_RESULT",
            "returncode": completed.returncode,
        }
        fps = result.get("fps", {})
        point = {
            "screen_percentage": percentage,
            "result": result.get("result"),
            "median_fps": fps.get("median_window_fps"),
            "minimum_fps": fps.get("minimum_window_fps"),
            "maximum_fps": fps.get("maximum_window_fps"),
            "throttle": result.get("throttle"),
            "artifact": str(result_path),
        }
        summary["points"].append(point)
        print(json.dumps(point, ensure_ascii=False), flush=True)
        median = point["median_fps"]
        if (point["result"] == "OK" and median is not None and
                median >= args.target_fps and summary["selected"] is None):
            summary["selected"] = point
        if point["result"] in {
                "ERROR", "GAME_EXITED",
                "WORKER_DID_NOT_WRITE_RESULT"}:
            break

    summary["ended"] = time.time()
    summary_path = args.output / "summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

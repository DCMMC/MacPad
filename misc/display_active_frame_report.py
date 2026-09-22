"""Score allocation-free macwsdisplayd active-frame burst records.

Examples:

    ssh mobile@ipad 'tail -n 500 /var/jb/var/mobile/macwsdisplayd.err' | \
      python3 misc/display_active_frame_report.py --owner Dock

The parser intentionally rejects the legacy lifetime ``fps=`` field because
that number includes static time between native enter/exit animations.
"""

import argparse
import json
import pathlib
import re
import sys


PATTERN = re.compile(
    r"active-frame-burst layer=(?P<layer>\d+) owner=(?P<owner>.*?) "
    r"reason=(?P<reason>\S+) intervals=(?P<intervals>\d+) "
    r"average-fps=(?P<average>[0-9.]+) p50-ms=(?P<p50>[0-9.]+) "
    r"p99-ms=(?P<p99>[0-9.]+) "
    r"one-percent-low-fps=(?P<one_low>[0-9.]+)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", type=pathlib.Path,
                        help="macwsdisplayd log; stdin when omitted")
    parser.add_argument("--owner", default="Dock")
    parser.add_argument("--average-fps", type=float, default=60.0)
    parser.add_argument("--one-percent-low-fps", type=float, default=45.0)
    parser.add_argument("--minimum-intervals", type=int, default=30)
    args = parser.parse_args()
    text = args.path.read_text(errors="replace") if args.path else sys.stdin.read()
    bursts = []
    for match in PATTERN.finditer(text):
        if match.group("owner") != args.owner:
            continue
        item = {
            "layer": int(match.group("layer")),
            "owner": match.group("owner"),
            "reason": match.group("reason"),
            "intervals": int(match.group("intervals")),
            "average_fps": float(match.group("average")),
            "p50_ms": float(match.group("p50")),
            "p99_ms": float(match.group("p99")),
            "one_percent_low_fps": float(match.group("one_low")),
        }
        if item["intervals"] >= args.minimum_intervals:
            bursts.append(item)
    if not bursts:
        result = {
            "result": "NO_VALID_BURST",
            "owner": args.owner,
            "minimum_intervals": args.minimum_intervals,
            "bursts": [],
        }
    else:
        selected = bursts[-1]
        average_ok = selected["average_fps"] >= args.average_fps
        one_low_ok = (
            selected["one_percent_low_fps"] >= args.one_percent_low_fps)
        result = {
            "result": "PASS" if average_ok and one_low_ok else "FAIL",
            "owner": args.owner,
            "thresholds": {
                "average_fps": args.average_fps,
                "one_percent_low_fps": args.one_percent_low_fps,
            },
            "selected": selected,
            "gates": {
                "average_fps": average_ok,
                "one_percent_low_fps": one_low_ok,
            },
            "bursts": bursts,
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["result"] != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()

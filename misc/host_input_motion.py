"""Measure native Host touch-drag delivery without an RFB connection."""

import argparse
import json
import math
import os
import socket
import struct
import time

from host_input_matrix import (
    SOURCE_FINGER, SOURCE_PENCIL, TOUCH_DOWN, TOUCH_MOVE, TOUCH_UP, record,
    resolve_window,
)


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1,
                       max(0, math.ceil(len(ordered) * fraction) - 1))]


def read_events(path):
    events = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except FileNotFoundError:
        pass
    return events


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--window", type=int, default=0)
    parser.add_argument("--width", type=int, default=1728)
    parser.add_argument("--height", type=int, default=1312)
    parser.add_argument("--duration", type=float, default=3.0)
    parser.add_argument("--hz", type=float, default=60.0)
    parser.add_argument("--source", choices=("finger", "pencil"),
                        default="finger")
    parser.add_argument("--pressure", type=float, default=0.72,
                        help="Pencil pressure for move records (0...1)")
    parser.add_argument("--tilt-x", type=float, default=0.18)
    parser.add_argument("--tilt-y", type=float, default=-0.12)
    parser.add_argument("--socket",
                        default="/var/mnt/rootfs/private/tmp/macws_host_input.sock")
    parser.add_argument("--log",
                        default="/var/mnt/rootfs/private/tmp/macws_inputlab_events.jsonl")
    args = parser.parse_args()
    if (args.pid <= 1 or args.duration <= 0 or args.hz <= 0 or
            not 0.0 <= args.pressure <= 1.0 or
            not -1.0 <= args.tilt_x <= 1.0 or
            not -1.0 <= args.tilt_y <= 1.0):
        parser.error("pid/duration/hz must be positive")
    window = resolve_window(args.pid, args.window)
    baseline = len(read_events(args.log))

    local_path = f"/tmp/macws_host_motion.{os.getpid()}.sock"
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    sock.bind(local_path)
    contact = 0x4D4F544E  # "MOTN"
    sequence = 1

    input_source = SOURCE_PENCIL if args.source == "pencil" else SOURCE_FINGER

    def send(kind, x, y, pressure):
        nonlocal sequence
        sock.sendto(record(kind, sequence, args.pid, window,
                           args.width, args.height, x, y,
                           pressure=pressure, contact=contact,
                           source=input_source, altitude=0.9, azimuth=0.35,
                           tilt_x=args.tilt_x, tilt_y=args.tilt_y), args.socket)
        sequence += 1

    sample_count = max(2, round(args.duration * args.hz))
    started = time.time()
    active_pressure = args.pressure if input_source == SOURCE_PENCIL else 1.0
    send(TOUCH_DOWN, 520, 520, active_pressure)
    deadline = time.perf_counter()
    for index in range(sample_count):
        # Three smooth traversals within InputLab's canvas.  The endpoint
        # remains in content, so this measures a real AppKit drag rather than
        # title-bar window management.
        phase = index / max(1, sample_count - 1) * 3.0
        fraction = phase % 1.0
        if int(phase) & 1:
            fraction = 1.0 - fraction
        x = 520.0 + fraction * 660.0
        y = 520.0 + 70.0 * math.sin(index * 2.0 * math.pi / 60.0)
        send(TOUCH_MOVE, x, y, active_pressure)
        deadline += 1.0 / args.hz
        delay = deadline - time.perf_counter()
        if delay > 0:
            time.sleep(delay)
    send(TOUCH_UP, 520, 520, 0.0)
    sent_finished = time.time()

    wait_deadline = time.time() + 2.0
    captured = []
    while time.time() < wait_deadline:
        captured = read_events(args.log)[baseline:]
        names = [event.get("event") for event in captured]
        if "left_up" in names:
            break
        time.sleep(0.02)

    names = [event.get("event") for event in captured]
    drags = [event for event in captured if event.get("event") == "left_drag"]
    latencies = [event["latency_ms"] for event in captured
                 if event.get("latency_valid") and
                 event.get("event") in ("left_down", "left_drag", "left_up")]
    elapsed = max(args.duration, sent_finished - started)
    result = {
        "result": "PASS" if ("left_down" in names and
                               "left_up" in names and drags) else "FAIL",
        "transport": "MacWSInputRecord-v4 (no RFB)",
        "source": args.source,
        "pid": args.pid,
        "window": window,
        "requested_hz": args.hz,
        "duration_s": elapsed,
        "move_records_sent": sample_count,
        "drag_events_received": len(drags),
        "drag_delivery_hz": len(drags) / elapsed,
        "coalesced_or_missing_moves": sample_count - len(drags),
        "latency_ms": {
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "maximum": max(latencies) if latencies else None,
        },
        "ordered_boundaries": [name for name in names
                               if name in ("left_down", "left_up")],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sock.close()
    os.unlink(local_path)
    if result["result"] != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()

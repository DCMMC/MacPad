"""Trace one live CGWindow's committed bounds at a bounded sampling rate."""

import json
import sys
import time

import cg_window_list as catalog


def read_bounds(owner_pid, window_id):
    array = catalog.cg.CGWindowListCopyWindowInfo(0, 0)
    if not array:
        return None
    try:
        for index in range(catalog.cf.CFArrayGetCount(array)):
            dictionary = catalog.cf.CFArrayGetValueAtIndex(array, index)
            if catalog.number(dictionary, "kCGWindowOwnerPID") != owner_pid:
                continue
            if catalog.number(dictionary, "kCGWindowNumber") != window_id:
                continue
            return catalog.bounds(dictionary)
    finally:
        catalog.cf.CFRelease(array)
    return None


def main():
    if len(sys.argv) < 3:
        raise SystemExit("usage: cg_window_trace.py PID WINDOW [SECONDS] [HZ]")
    owner_pid = int(sys.argv[1])
    window_id = int(sys.argv[2])
    duration = float(sys.argv[3]) if len(sys.argv) > 3 else 2.0
    frequency = float(sys.argv[4]) if len(sys.argv) > 4 else 60.0
    if duration <= 0.0 or frequency <= 0.0 or frequency > 120.0:
        raise SystemExit("SECONDS and HZ must be within safe positive bounds")
    start = time.monotonic()
    deadline = start + duration
    interval = 1.0 / frequency
    last = object()
    while time.monotonic() <= deadline:
        bounds = read_bounds(owner_pid, window_id)
        if bounds != last:
            print(json.dumps({
                "elapsed_ms": round((time.monotonic() - start) * 1000.0, 3),
                "bounds": bounds,
            }))
            sys.stdout.flush()
            last = bounds
        next_sample = start + (int((time.monotonic() - start) / interval) + 1) * interval
        time.sleep(max(0.0, min(interval, next_sample - time.monotonic())))


if __name__ == "__main__":
    main()

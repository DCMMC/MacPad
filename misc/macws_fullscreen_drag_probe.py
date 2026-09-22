"""Bounded real fullscreen pointer test (iOS-side Python; explicit --perform).

This exercises inputd -> OSXvnc -> WindowServer, not UIKit recognition. Pair
the result with full iPadOS screenshots; focused metrics alone do not prove
that the window visibly moved. Coordinates must identify the current native
title bar in source-frame pixels. No file contents or application data change.
"""

import argparse
import json
import math
import socket
import time

from host_input_matrix import (record, ACTIVATE_TARGET, TOUCH_DOWN, TOUCH_MOVE,
                              TOUCH_UP, GLOBAL_SYSTEM_SURFACE)
from macws_window_metrics_dump import read_metrics
from macws_menu_probe import require_live_target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid', type=int, required=True)
    parser.add_argument('--window', type=int, required=True)
    parser.add_argument('--dock', type=int, required=True)
    parser.add_argument('--frame', type=int, nargs=2, required=True)
    parser.add_argument('--start', type=float, nargs=2, required=True)
    parser.add_argument('--end', type=float, nargs=2, required=True)
    parser.add_argument('--duration', type=float, default=1.0)
    parser.add_argument('--perform', action='store_true')
    args = parser.parse_args()
    if (min(args.pid, args.dock) <= 1 or args.window <= 0 or
        any(not 0 < extent <= 8192 for extent in args.frame) or
        not math.isfinite(args.duration) or not 0.2 <= args.duration <= 1.5 or
        any(not math.isfinite(value) or not 0 <= value < args.frame[axis]
            for point in (args.start, args.end) for axis, value in enumerate(point))):
        parser.error('invalid target, coordinates, frame or duration')
    if not args.perform:
        print(json.dumps({'action': 'none', 'requires': '--perform', 'arguments': vars(args)}))
        return
    path = f'/var/mnt/rootfs/private/tmp/macws_window_metrics.{args.pid}.bin'
    require_live_target(args.pid)
    require_live_target(args.dock)
    before = read_metrics(path)
    if not any(w['window_id'] == args.window for w in before['windows']):
        parser.error('requested window does not exist in the exact owner metrics')
    observations = []
    def observe(phase):
        try:
            require_live_target(args.pid)
        except RuntimeError as error:
            observations.append({'phase': phase, 'time': time.time(),
                                 'focused': [], 'error': str(error)})
            return False
        metrics = read_metrics(path)
        focused = [w['window_id'] for w in metrics['windows'] if 'focused' in w['flags']]
        observations.append({'phase': phase, 'time': time.time(),
                             'generation': metrics['generation'], 'focused': focused})
        return focused == [args.window]
    destination = '/var/mnt/rootfs/private/tmp/macws_host_input.sock'
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as transport:
        transport.sendto(record(ACTIVATE_TARGET, 1, args.pid, args.window,
            *args.frame, args.frame[0]/2, args.frame[1]/2), destination)
        time.sleep(0.8)
        if not observe('before'):
            print(json.dumps({'result': 'FAIL', 'reason': 'initial-focus-not-established',
                              'observations': observations}))
            raise SystemExit(1)
        contact = (time.time_ns() & 0xffffffff) or 1
        point = args.start
        try:
            transport.sendto(record(TOUCH_DOWN, 2, args.dock, 0, *args.frame,
                *point, pressure=1, contact=contact, flags=GLOBAL_SYSTEM_SURFACE), destination)
            for step in range(1, 21):
                time.sleep(args.duration/20)
                point = [a+(b-a)*step/20 for a,b in zip(args.start, args.end)]
                transport.sendto(record(TOUCH_MOVE, step+2, args.dock, 0,
                    *args.frame, *point, pressure=1, contact=contact,
                    flags=GLOBAL_SYSTEM_SURFACE), destination)
                if step in (5, 10, 15, 20) and not observe('during'):
                    break  # Release immediately; don't keep dragging a wrong/dead target.
        finally:
            transport.sendto(record(TOUCH_UP, 23, args.dock, 0,
                *args.frame, *point, contact=contact, flags=GLOBAL_SYSTEM_SURFACE), destination)
        time.sleep(0.4)
        observe('after')
    passed = all(row['focused'] == [args.window] for row in observations)
    print(json.dumps({'result': 'PASS' if passed else 'FAIL',
        'scope': 'native-fullscreen-pointer-and-AppKit-focus-not-UIKit-or-pixels',
        'arguments': vars(args), 'observations': observations}))
    raise SystemExit(0 if passed else 1)


if __name__ == '__main__':
    main()

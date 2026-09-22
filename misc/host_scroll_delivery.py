"""Send one bounded phased scroll through the production Host input socket.

This is a transport regression helper, not an input simulator inside the
target application. It emits the same MacWSInputRecord-v4 sequence as the
UIKit recognizer; a separate app-native or CDP observer must verify delivery.
"""

import argparse
import os
import socket
import struct
import time

from host_input_matrix import (
    GESTURE_BEGAN, GESTURE_CHANGED, GESTURE_ENDED, SCROLL, SOURCE_FINGER,
    record, resolve_window,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--window", type=int, default=0)
    parser.add_argument("--width", type=int, default=2388)
    parser.add_argument("--height", type=int, default=1668)
    parser.add_argument("--x", type=float, default=1200.0)
    parser.add_argument("--y", type=float, default=800.0)
    parser.add_argument("--dx", type=float, default=0.0)
    parser.add_argument("--dy", type=float, default=-10.0)
    parser.add_argument("--count", type=int, default=6)
    parser.add_argument("--hz", type=float, default=120.0)
    parser.add_argument("--socket", default=
        "/var/mnt/rootfs/private/tmp/macws_host_input.sock")
    args = parser.parse_args()
    if args.pid <= 1 or args.count < 1 or args.hz <= 0:
        parser.error("pid/count/hz must be positive")
    window = resolve_window(args.pid, args.window)
    local_path = f"/tmp/macws_scroll_delivery.{os.getpid()}.sock"
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    sock.bind(local_path)
    sequence = 1

    def send(flags, dx=0.0, dy=0.0):
        nonlocal sequence
        horizontal = struct.unpack("<I", struct.pack("<f", dx))[0]
        payload = record(
            SCROLL, sequence, args.pid, window, args.width, args.height,
            args.x, args.y, pressure=dy, contact=horizontal,
            source=SOURCE_FINGER, flags=flags)
        sock.sendto(payload, args.socket)
        sequence += 1

    send(GESTURE_BEGAN)
    deadline = time.perf_counter()
    for _ in range(args.count):
        send(GESTURE_CHANGED, args.dx, args.dy)
        deadline += 1.0 / args.hz
        delay = deadline - time.perf_counter()
        if delay > 0:
            time.sleep(delay)
    send(GESTURE_ENDED)
    print(f"pid={args.pid} window={window} samples={args.count} "
          f"delta=({args.dx},{args.dy}) unit=logical-pixel")
    sock.close()
    os.unlink(local_path)


if __name__ == "__main__":
    main()

"""Send one bounded native Dock gesture through the MacWS input broker.

Run with the iOS Python interpreter (do not add a shebang; AMFI rejects
script exec on this device):

    python3 misc/system_gesture_probe.py --target-pid <Dock PID> --cancel

The default is a short upward three-finger gesture.  This is a protocol and
runtime diagnostic, not a substitute for physical Host gesture testing.
"""

import argparse
import math
import socket
import struct
import time


INPUT_MAGIC = 0x4D574556
INPUT_VERSION = 5
INPUT_KIND_SYSTEM_GESTURE = 21
INPUT_SOURCE_FINGER = 1
FLAG_GLOBAL_SYSTEM_SURFACE = 1 << 6
FLAG_GESTURE_BEGAN = 1 << 8
FLAG_GESTURE_CHANGED = 1 << 9
FLAG_GESTURE_ENDED = 1 << 10
FLAG_GESTURE_CANCELLED = 1 << 11
AXIS_HORIZONTAL = 1
AXIS_VERTICAL = 2
RECORD = struct.Struct("<IHHQdfffIIIiHHIffffII")


def record(target_pid, axis, progress, velocity, phase, sequence):
    return RECORD.pack(
        INPUT_MAGIC,
        INPUT_VERSION,
        INPUT_KIND_SYSTEM_GESTURE,
        0x33464750524F4245,  # "3FGPROBE"
        time.monotonic(),
        1194.0,
        834.0,
        progress,
        0x33464750,  # stable "3FGP" contact
        2388,
        1668,
        target_pid,
        INPUT_SOURCE_FINGER,
        FLAG_GLOBAL_SYSTEM_SURFACE | phase,
        axis,
        velocity,
        0.0,
        0.0,
        0.0,
        sequence,
        0,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-pid", type=int, required=True)
    parser.add_argument("--socket", default=
                        "/var/mnt/rootfs/private/tmp/macws_host_input.sock")
    parser.add_argument("--axis", choices=("horizontal", "vertical"),
                        default="vertical")
    # Dock's Ventura navigation convention is signed in screen coordinates:
    # an upward finger translation is negative.  Keeping the sign here makes
    # the diagnostic match the Host transport instead of naming a semantic
    # action itself.
    parser.add_argument("--progress", type=float, default=-0.08)
    parser.add_argument("--duration", type=float, default=0.10)
    parser.add_argument("--rate", type=float, default=60.0)
    parser.add_argument("--hold", type=float, default=0.0,
                        help="seconds to hold the final changed phase")
    parser.add_argument("--cancel", action="store_true")
    args = parser.parse_args()
    if args.target_pid <= 1 or not (0.01 <= abs(args.progress) <= 1.5):
        parser.error("invalid target PID or progress")
    if not (0.03 <= args.duration <= 2.0) or not (10.0 <= args.rate <= 120.0):
        parser.error("duration/rate outside bounded diagnostic range")
    if not (0.0 <= args.hold <= 2.0):
        parser.error("hold outside bounded diagnostic range")

    axis = AXIS_HORIZONTAL if args.axis == "horizontal" else AXIS_VERTICAL
    sample_count = max(2, int(math.ceil(args.duration * args.rate)))
    velocity = args.progress / args.duration
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    sequence = 1
    initial = args.progress / sample_count
    sock.sendto(record(args.target_pid, axis, initial, velocity,
                       FLAG_GESTURE_BEGAN, sequence), args.socket)
    for index in range(2, sample_count + 1):
        deadline = time.monotonic() + 1.0 / args.rate
        sequence += 1
        value = args.progress * index / sample_count
        sock.sendto(record(args.target_pid, axis, value, velocity,
                           FLAG_GESTURE_CHANGED, sequence), args.socket)
        time.sleep(max(0.0, deadline - time.monotonic()))
    if args.hold:
        time.sleep(args.hold)
    sequence += 1
    terminal = (FLAG_GESTURE_CANCELLED if args.cancel
                else FLAG_GESTURE_ENDED)
    sock.sendto(record(args.target_pid, axis, args.progress, velocity,
                       terminal, sequence), args.socket)
    print("sent", sequence, "records", "bytes=", RECORD.size,
          "terminal=", "cancel" if args.cancel else "end")


if __name__ == "__main__":
    main()

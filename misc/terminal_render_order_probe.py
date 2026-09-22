"""Capture Host-presented frames while typing into a Terminal window.

Run this with the iPadOS Python.  The probe sends the same version-5 key
records as MacWSHost and asks the already-running Host to snapshot its final
Metal drawable; it does not start or restart the GUI stack.
"""

import argparse
import os
import shutil
import socket
import struct
import subprocess
import time


INPUT_MAGIC = 0x4D574556
INPUT_VERSION = 5
KEY_DOWN = 11
KEY_UP = 12
SOURCE_HARDWARE_KEYBOARD = 4
WINDOW_SCENE_FLAG = 0x80000000
RECORD = struct.Struct("<IHHQdfffIIIiHHIffffII")

KEYS = {
    "a": (0, ord("a")), "b": (11, ord("b")), "c": (8, ord("c")),
    "d": (2, ord("d")), "e": (14, ord("e")), "f": (3, ord("f")),
    "g": (5, ord("g")), "h": (4, ord("h")), "i": (34, ord("i")),
    "j": (38, ord("j")), "k": (40, ord("k")), "l": (37, ord("l")),
    "m": (46, ord("m")), "n": (45, ord("n")), "o": (31, ord("o")),
    "p": (35, ord("p")), "q": (12, ord("q")), "r": (15, ord("r")),
    "s": (1, ord("s")), "t": (17, ord("t")), "u": (32, ord("u")),
    "v": (9, ord("v")), "w": (13, ord("w")), "x": (7, ord("x")),
    "y": (16, ord("y")), "z": (6, ord("z")),
    "backspace": (51, 0xFF08),
    "enter": (36, 0xFF0D),
}


def uptime():
    return time.clock_gettime(time.CLOCK_UPTIME_RAW)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--window", type=int, required=True)
    parser.add_argument("--text", default="testme")
    parser.add_argument("--width", type=int, default=2388)
    parser.add_argument("--height", type=int, default=1668)
    parser.add_argument("--interval", type=float, default=0.045)
    parser.add_argument("--duration", type=float, default=2.2)
    parser.add_argument("--clear", type=int, default=12)
    parser.add_argument(
        "--erase", type=int, default=0,
        help="send this many backspaces after the text burst")
    parser.add_argument("--submit", action="store_true")
    parser.add_argument(
        "--input-only", action="store_true",
        help="send the complete key burst without asking Host for snapshots")
    parser.add_argument(
        "--output", default="/var/jb/var/mobile/macws-terminal-order-probe")
    parser.add_argument(
        "--socket",
        default="/var/mnt/rootfs/private/tmp/macws_host_input.sock")
    args = parser.parse_args()
    if args.pid <= 1 or args.window <= 0 or args.width <= 0 or args.height <= 0:
        parser.error("pid, window and frame dimensions must be positive")
    if args.clear < 0 or args.erase < 0:
        parser.error("clear and erase must be non-negative")
    unknown = sorted(set(args.text) - set(KEYS))
    if unknown:
        parser.error(f"unsupported probe characters: {unknown}")

    os.makedirs(args.output, exist_ok=True)
    for name in os.listdir(args.output):
        candidate = os.path.join(args.output, name)
        if os.path.isfile(candidate) or os.path.islink(candidate):
            os.unlink(candidate)

    sender_path = f"/tmp/macws_terminal_order_probe.{os.getpid()}.sock"
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    sock.bind(sender_path)
    sequence = 0

    def send_key(name):
        nonlocal sequence
        key_code, key_sym = KEYS[name]
        for kind in (KEY_DOWN, KEY_UP):
            sequence += 1
            payload = RECORD.pack(
                INPUT_MAGIC, INPUT_VERSION, kind,
                (args.window << 32) | WINDOW_SCENE_FLAG,
                uptime(), args.width / 2, args.height / 2,
                float(key_code), key_sym, args.width, args.height, args.pid,
                SOURCE_HARDWARE_KEYBOARD, 0, 0,
                0.0, 0.0, 0.0, 0.0, sequence, 0)
            sock.sendto(payload, args.socket)
            time.sleep(0.003)

    # Establish an empty prompt before the measured character burst.
    for _ in range(args.clear):
        send_key("backspace")
        time.sleep(0.012)
    time.sleep(0.15)

    if args.input_only:
        for character in args.text:
            send_key(character)
            time.sleep(args.interval)
        for _ in range(args.erase):
            send_key("backspace")
            time.sleep(args.interval)
        if args.submit:
            send_key("enter")
        time.sleep(args.duration)
        sock.close()
        os.unlink(sender_path)
        print(f"frames=0 records={sequence} text={args.text!r}")
        return

    source = "/var/mobile/Library/Logs/MacWSHost-rendered.png"
    started = time.monotonic()
    frame_index = 0
    text_index = 0
    next_key = started
    while time.monotonic() - started < args.duration:
        now = time.monotonic()
        if text_index < len(args.text) and now >= next_key:
            send_key(args.text[text_index])
            text_index += 1
            next_key = now + args.interval
        subprocess.run(
            ["/var/jb/usr/bin/uiopen", "--url",
             "macwshost://screenshot-rendered"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False)
        time.sleep(0.022)
        if os.path.isfile(source):
            shutil.copy2(
                source, os.path.join(args.output, f"{frame_index:03d}.png"))
            frame_index += 1
        time.sleep(0.008)

    if args.submit:
        send_key("enter")
        time.sleep(0.4)
    for _ in range(args.erase):
        send_key("backspace")
        time.sleep(args.interval)

    sock.close()
    os.unlink(sender_path)
    print(f"frames={frame_index} records={sequence} text={args.text!r}")


if __name__ == "__main__":
    main()

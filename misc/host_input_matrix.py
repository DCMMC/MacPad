"""Exercise the native MacWS Host input ABI without using RFB.

Run this on the iPadOS side.  It sends the same version-5 records emitted by
MacWSHost's UIKit recognizers to macwsinputd, then validates the resulting real
AppKit events recorded by InputLab.  The frame coordinates are relative to one
window DisplayStream IOSurface, not the fullscreen VNC desktop.
"""

import argparse
import json
import os
import socket
import struct
import time


INPUT_MAGIC = 0x4D574556
INPUT_VERSION = 5
WINDOW_SCENE_FLAG = 0x80000000

TOUCH_DOWN = 1
TOUCH_MOVE = 2
TOUCH_UP = 3
HOVER = 5
TAP = 6
ACTIVATE_TARGET = 8
KEY_DOWN = 11
KEY_UP = 12
SECONDARY_TAP = 13
SCROLL = 14
MAGNIFY = 19
DESKTOP_COMMAND = 20
ROTATE = 22

DOUBLE_CLICK = 1 << 5
GLOBAL_SYSTEM_SURFACE = 1 << 6

GESTURE_BEGAN = 1 << 8
GESTURE_CHANGED = 1 << 9
GESTURE_ENDED = 1 << 10
LATENCY_DIAGNOSTIC = 1 << 15
SCROLL_MOMENTUM = 1 << 12
SCROLL_WILL_MOMENTUM = 1 << 7

SOURCE_FINGER = 1
SOURCE_PENCIL = 2
SOURCE_HARDWARE_KEYBOARD = 4

MOD_CAPS_LOCK = 1 << 16
MOD_SHIFT = 1 << 17
MOD_CONTROL = 1 << 18
MOD_COMMAND = 1 << 20

RECORD = struct.Struct("<IHHQdfffIIIiHHIffffII")


def uptime():
    # NSProcessInfo.systemUptime/CACurrentMediaTime use the uptime clock on
    # iOS.  CLOCK_MONOTONIC includes suspended time on this device, while the
    # Procursus Python monotonic clock is process-relative.
    return time.clock_gettime(time.CLOCK_UPTIME_RAW)


def scene_for_window(window_id, modifiers=0):
    return ((window_id & 0xFFFFFFFF) << 32) | WINDOW_SCENE_FLAG | \
        (modifiers & 0x7FFFFFFF)


def record(kind, sequence, pid, window_id, width, height, x, y,
           pressure=0.0, contact=0, source=SOURCE_FINGER, modifiers=0,
           flags=0, altitude=0.0, azimuth=0.0, tilt_x=0.0, tilt_y=0.0):
    return RECORD.pack(
        INPUT_MAGIC, INPUT_VERSION, kind,
        scene_for_window(window_id, modifiers), uptime(),
        float(x), float(y), float(pressure), contact & 0xFFFFFFFF,
        width, height, pid, source, flags, 0,
        float(altitude), float(azimuth), float(tilt_x), float(tilt_y),
        sequence, 0)


def load_events(path):
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


def resolve_window(pid, requested, timeout=5.0):
    if requested > 0:
        return requested
    path = f"/var/mnt/rootfs/private/tmp/macws_window_metrics.{pid}.bin"
    deadline = time.time() + timeout
    payload = b""
    while time.time() < deadline:
        try:
            with open(path, "rb") as handle:
                payload = handle.read()
        except FileNotFoundError:
            payload = b""
        if len(payload) >= 40:
            break
        time.sleep(0.05)
    if len(payload) < 44:
        raise RuntimeError(f"no window metrics entry for pid {pid}")
    magic, version, header_size, entry_size, entry_count, generation = \
        struct.unpack_from("<IHHIIQ", payload)
    expected_size = header_size + entry_count * entry_size
    supported_layout = ((version == 2 and entry_size == 20) or
                        (version == 3 and entry_size == 56))
    if (magic != 0x4D57474D or not supported_layout or header_size != 24 or
            entry_count < 1 or generation == 0 or
            len(payload) != expected_size):
        raise RuntimeError(f"invalid window metrics for pid {pid}")
    return struct.unpack_from("<I", payload, header_size)[0]


def wait_for(sock, expected, log_path, deadline):
    while time.time() < deadline:
        events = load_events(log_path)
        names = [event.get("event") for event in events]
        cursor = 0
        for name in names:
            if cursor < len(expected) and name == expected[cursor]:
                cursor += 1
        if cursor == len(expected):
            return events
        time.sleep(0.02)
    raise RuntimeError(
        f"missing ordered events {expected}; got "
        f"{[event.get('event') for event in load_events(log_path)]}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--window", type=int, default=0,
                        help="AppKit window number; 0 discovers it from metrics")
    parser.add_argument("--width", type=int, default=1728)
    parser.add_argument("--height", type=int, default=1312)
    parser.add_argument(
        "--global-route", action="store_true",
        help=("send fullscreen targetPID=0/window=0 records so macwsinputd "
              "must hit-test and latch the native gesture owner"))
    parser.add_argument("--socket",
                        default="/var/mnt/rootfs/private/tmp/macws_host_input.sock")
    parser.add_argument("--log",
                        default="/var/mnt/rootfs/private/tmp/macws_inputlab_events.jsonl")
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()
    if args.pid <= 1 or args.window < 0 or args.width <= 0 or args.height <= 0:
        parser.error("pid/geometry must be positive and window nonnegative")
    args.window = resolve_window(args.pid, args.window)
    route_pid = 0 if args.global_route else args.pid
    route_window = 0 if args.global_route else args.window

    # Every assertion below describes this invocation only.  Keeping stale
    # InputLab events makes an ordered-name check pass before a new record has
    # traversed the bridge and also corrupts the exact keyboard/magnify value
    # comparisons.
    with open(args.log, "w", encoding="utf-8"):
        pass

    try:
        os.unlink("/tmp/macws_host_input_matrix.sock")
    except FileNotFoundError:
        pass
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    sock.bind("/tmp/macws_host_input_matrix.sock")
    sequence = 0

    def send(kind, x, y, **kwargs):
        nonlocal sequence
        sequence += 1
        sock.sendto(record(kind, sequence, route_pid, route_window,
                           args.width, args.height, x, y, **kwargs),
                    args.socket)

    # InputLab's canvas occupies the upper central area of its 1728x1312
    # window stream.  Use distinct contacts to catch accidental gesture-state
    # sharing between single-finger, drag, and two-finger semantics.
    send(TAP, 900, 500, pressure=1.0, contact=0x1001)
    wait_for(sock, ["left_down", "left_up"], args.log,
             time.time() + args.timeout)

    send(HOVER, 940, 520, contact=0x1005)
    wait_for(sock, ["move"], args.log, time.time() + args.timeout)

    send(TOUCH_DOWN, 700, 500, pressure=1.0, contact=0x1002)
    for step in range(1, 9):
        send(TOUCH_MOVE, 700 + step * 35, 500 + step * 8,
             pressure=1.0, contact=0x1002)
        time.sleep(1.0 / 60.0)
    send(TOUCH_UP, 980, 564, contact=0x1002)
    wait_for(sock, ["left_down", "left_drag", "left_up"], args.log,
             time.time() + args.timeout)

    send(SECONDARY_TAP, 900, 500, contact=0x2001)
    wait_for(sock, ["right_down", "right_up"], args.log,
             time.time() + args.timeout)

    # Exercise a real phased gesture.  In fullscreen mode the begin must
    # resolve the window under the finger once; every changed/end record must
    # stay latched to that same AppKit owner without another SkyLight query.
    send(SCROLL, 900, 500, flags=GESTURE_BEGAN)
    horizontal_bits = struct.unpack("<I", struct.pack("<f", 1.5))[0]
    for _ in range(8):
        send(SCROLL, 900, 500, pressure=-4.0,
             contact=horizontal_bits, flags=GESTURE_CHANGED)
        time.sleep(1.0 / 120.0)
    send(SCROLL, 900, 500, flags=GESTURE_ENDED)
    deadline = time.time() + args.timeout
    scroll_events = []
    while time.time() < deadline:
        scroll_events = load_events(args.log)
        if any(event.get("event") == "scroll" and
               event.get("phase") == 8 for event in scroll_events):
            break
        time.sleep(0.02)
    scroll_phases = [event.get("phase") for event in scroll_events
                     if event.get("event") == "scroll"]
    if not scroll_phases or scroll_phases[0] != 1 or \
            scroll_phases[-1] != 8 or 4 not in scroll_phases:
        raise RuntimeError(f"scroll phase mismatch values={scroll_phases}")

    # Preserve AppKit's two linked phase machines across finger release and
    # inertial continuation. The finger stream advertises WillMomentum before
    # ending; the following records must arrive with phase=0 and native
    # momentum Begin/Continue/End rather than starting a new scroll target.
    event_offset = len(load_events(args.log))
    send(SCROLL, 900, 500, flags=GESTURE_BEGAN)
    send(SCROLL, 900, 500, pressure=-6.0,
         flags=GESTURE_CHANGED)
    send(SCROLL, 900, 500,
         flags=GESTURE_ENDED | SCROLL_WILL_MOMENTUM)
    send(SCROLL, 900, 500, pressure=-5.0,
         flags=GESTURE_BEGAN | SCROLL_MOMENTUM)
    send(SCROLL, 900, 500, pressure=-3.0,
         flags=GESTURE_CHANGED | SCROLL_MOMENTUM)
    send(SCROLL, 900, 500,
         flags=GESTURE_ENDED | SCROLL_MOMENTUM)
    deadline = time.time() + args.timeout
    momentum_events = []
    while time.time() < deadline:
        momentum_events = [
            event for event in load_events(args.log)[event_offset:]
            if event.get("event") == "scroll"
        ]
        momentum_phases = [event.get("momentum_phase")
                           for event in momentum_events]
        if momentum_phases and momentum_phases[-1] == 8:
            break
        time.sleep(0.02)
    momentum_phases = [event.get("momentum_phase")
                       for event in momentum_events]
    if momentum_phases[-3:] != [1, 4, 8]:
        raise RuntimeError(
            f"momentum phase mismatch values={momentum_phases}")

    gesture_contact = 0x50494E43  # "PINC"
    send(MAGNIFY, 900, 500, contact=gesture_contact,
         flags=GESTURE_BEGAN)
    send(MAGNIFY, 900, 500, pressure=0.125, contact=gesture_contact,
         flags=GESTURE_CHANGED)
    wait_for(sock, ["magnify", "magnify"], args.log,
             time.time() + args.timeout)
    send(MAGNIFY, 900, 500, pressure=-0.0625, contact=gesture_contact,
         flags=GESTURE_CHANGED)
    send(MAGNIFY, 900, 500, contact=gesture_contact,
         flags=GESTURE_ENDED)
    magnify_events = wait_for(
        sock, ["magnify", "magnify", "magnify", "magnify"],
        args.log, time.time() + args.timeout)
    magnifications = [event.get("magnification") for event in magnify_events
                      if event.get("event") == "magnify"]
    expected_magnifications = [0.0, 0.125, -0.0625, 0.0]
    if (len(magnifications) != len(expected_magnifications) or
            any(abs(actual - expected) > 1e-6
                for actual, expected in zip(
                    magnifications, expected_magnifications))):
        raise RuntimeError(
            f"magnify semantic mismatch values={magnifications}")
    magnify_phases = [event.get("phase") for event in magnify_events
                      if event.get("event") == "magnify"]
    if magnify_phases != [1, 4, 4, 8]:
        raise RuntimeError(
            f"magnify phase mismatch values={magnify_phases}")

    keys = [
        (0, ord("a"), 0),
        (0, ord("A"), MOD_SHIFT),
        (11, ord("B"), MOD_CAPS_LOCK),
        (8, ord("c"), MOD_CONTROL),
        (46, ord("m"), MOD_COMMAND),
        (48, 0xFF09, 0),
        (51, 0xFF08, 0),
        (36, 0xFF0D, 0),
        (53, 0xFF1B, 0),
    ]
    for key_code, key_sym, modifiers in keys:
        send(KEY_DOWN, 900, 500, pressure=key_code, contact=key_sym,
             source=SOURCE_HARDWARE_KEYBOARD, modifiers=modifiers)
        send(KEY_UP, 900, 500, pressure=key_code, contact=key_sym,
             source=SOURCE_HARDWARE_KEYBOARD, modifiers=modifiers)
    # AppKit consumes the Command-modified key-up before it reaches a custom
    # first responder.  InputLab's scoped NSApplication witness runtime-
    # confirmed that the bridge still delivered that event to sendEvent:.
    # Validate responder-visible semantics here: four complete pairs,
    # Command+M down, then four complete pairs.
    responder_keys = ["key_down", "key_up"] * 4 + ["key_down"] + \
        ["key_down", "key_up"] * 4
    events = wait_for(sock, responder_keys, args.log,
                      time.time() + args.timeout)

    key_downs = [event for event in events
                 if event.get("event") == "key_down"]
    expected_characters = ["a", "A", "B", "c", "m", "\t", "\x7f",
                           "\r", "\x1b"]
    expected_modifiers = [0, MOD_SHIFT, MOD_CAPS_LOCK, MOD_CONTROL,
                          MOD_COMMAND, 0, 0, 0, 0]
    actual_characters = [event.get("characters") for event in key_downs]
    actual_modifiers = [event.get("modifiers") for event in key_downs]
    if (actual_characters != expected_characters or
            actual_modifiers != expected_modifiers):
        raise RuntimeError(
            f"keyboard semantic mismatch characters={actual_characters} "
            f"modifiers={actual_modifiers}")

    relevant = [event for event in events if event.get("event") != "ready"]
    latencies = [event["latency_ms"] for event in relevant
                 if event.get("latency_valid")]
    result = {
        "result": "PASS",
        "transport": "MacWSInputRecord-v4 (no RFB)",
        "pid": args.pid,
        "window": args.window,
        "route": "fullscreen-global-hit-test" if args.global_route
                 else "exact-window",
        "frame": [args.width, args.height],
        "records_sent": sequence,
        "events_received": len(relevant),
        "events": [event.get("event") for event in relevant],
        "keyboard_characters": actual_characters,
        "keyboard_modifiers": actual_modifiers,
        "magnifications": magnifications,
        "magnify_phases": magnify_phases,
        "scroll_phases": scroll_phases,
        "momentum_phases": momentum_phases,
        "command_key_up": "delivered to NSApplication; consumed before responder",
        "latency_ms": {
            "minimum": min(latencies) if latencies else None,
            "maximum": max(latencies) if latencies else None,
            "average": sum(latencies) / len(latencies) if latencies else None,
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sock.close()
    os.unlink("/tmp/macws_host_input_matrix.sock")


if __name__ == "__main__":
    main()

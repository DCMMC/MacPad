"""Send a deterministic RFB input matrix and verify it in MacWS InputLab.

Unlike ``vnc_native_input_test.py``, this test never treats a changed pixel as
proof that an input event ran.  InputLab records the actual NSEvents consumed
by the target AppKit process, including their monotonic timestamps.  Frame
publication is measured by a separate test so a stalled DisplayStream cannot
misdiagnose a working click as an input failure.

The InputLab window must be at its default position and OSXvnc must advertise
the standard 2388x1668 Retina desktop used by the iPad13,6 test device.
"""

import argparse
import json
import socket
import struct
import subprocess
import time

import vnc_capture


XK_BACKSPACE = 0xFF08
XK_TAB = 0xFF09
XK_RETURN = 0xFF0D
XK_ESCAPE = 0xFF1B
XK_SHIFT_L = 0xFFE1
XK_CONTROL_L = 0xFFE3
XK_CAPS_LOCK = 0xFFE5
XK_ALT_L = 0xFFE9

MODIFIER_CAPS_LOCK = 1 << 16
MODIFIER_SHIFT = 1 << 17
MODIFIER_CONTROL = 1 << 18
MODIFIER_COMMAND = 1 << 20


def rfb_pointer(sock, mask, x, y):
    sock.sendall(struct.pack(">BBHH", 5, mask, x, y))


def rfb_key(sock, down, keysym):
    sock.sendall(struct.pack(">BBxxI", 4, int(down), keysym))


def click(sock, x, y, mask=1, hold=0.025):
    rfb_pointer(sock, mask, x, y)
    time.sleep(hold)
    rfb_pointer(sock, 0, x, y)


def drag(sock, start, end, steps=12, delay=0.008):
    rfb_pointer(sock, 1, *start)
    for step in range(1, steps + 1):
        fraction = step / steps
        point = tuple(round(a + (b - a) * fraction)
                      for a, b in zip(start, end))
        time.sleep(delay)
        rfb_pointer(sock, 1, *point)
    time.sleep(delay)
    rfb_pointer(sock, 0, *end)


def key_pair(sock, keysym, delay=0.012):
    rfb_key(sock, True, keysym)
    time.sleep(delay)
    rfb_key(sock, False, keysym)


def chord(sock, modifier, key, delay=0.012):
    rfb_key(sock, True, modifier)
    time.sleep(delay)
    rfb_key(sock, True, key)
    time.sleep(delay)
    rfb_key(sock, False, key)
    time.sleep(delay)
    rfb_key(sock, False, modifier)


def read_events(ssh_target, path):
    result = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=no",
         ssh_target, "cat", path], check=True, capture_output=True, text=True)
    events = []
    for line in result.stdout.splitlines():
        if line.strip():
            events.append(json.loads(line))
    return events


def event_summary(events):
    counts = {}
    latencies = []
    for event in events:
        name = event["event"]
        counts[name] = counts.get(name, 0) + 1
        if event.get("latency_valid", False):
            latencies.append(float(event.get("latency_ms", 0)))
    latency = {
        "samples": len(latencies),
        "min_ms": min(latencies) if latencies else None,
        "median_ms": (sorted(latencies)[len(latencies) // 2]
                      if latencies else None),
        "max_ms": max(latencies) if latencies else None,
    }
    return counts, latency


def require(events, name, minimum=1):
    actual = sum(event["event"] == name for event in events)
    return {
        "name": name,
        "minimum": minimum,
        "actual": actual,
        "pass": actual >= minimum,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("--port", type=int, default=5900)
    parser.add_argument("--ssh-target")
    parser.add_argument("--event-log",
                        default="/var/mnt/rootfs/tmp/macws_inputlab_events.jsonl")
    parser.add_argument("--output")
    parser.add_argument("--settle", type=float, default=0.8)
    args = parser.parse_args()
    if args.ssh_target is None:
        args.ssh_target = f"mobile@{args.host}"

    before = read_events(args.ssh_target, args.event_log)
    baseline = max((int(event.get("sequence", 0)) for event in before),
                   default=0)

    sock, width, height, name = vnc_capture.connect_rfb(
        args.host, args.port, 8.0)
    sock.settimeout(8.0)
    try:
        # The default InputLab geometry deliberately keeps every point away
        # from title-bar and window edges. Scale the known 1194x834 logical
        # geometry to the RFB advertisement so the same test covers both the
        # fallback desktop and production 2388x1668 Retina sharing.
        scale_x = width / 1194.0
        scale_y = height / 834.0
        canvas_a = (round(420 * scale_x), round(300 * scale_y))
        canvas_b = (round(700 * scale_x), round(430 * scale_y))
        for point in (canvas_a, canvas_b):
            if not (0 <= point[0] < width and 0 <= point[1] < height):
                raise RuntimeError(f"InputLab point {point} outside {width}x{height}")

        # Establish the same hover/focus state a human client creates.
        rfb_pointer(sock, 0, *canvas_a)
        time.sleep(0.05)

        click(sock, *canvas_a)
        # Do not let the focus click become click 1 of the double-click test.
        time.sleep(1.1)

        # A real double click should produce click_count=1 then 2.
        click(sock, *canvas_a)
        time.sleep(0.07)
        click(sock, *canvas_a)
        time.sleep(0.10)

        drag(sock, canvas_a, canvas_b)
        time.sleep(0.10)

        click(sock, *canvas_a, mask=4)
        time.sleep(0.10)

        # RFB wheel buttons: 4/5 are vertical, 6/7 horizontal.  Each wheel
        # notch is one press/release pair at the current cursor point.
        for mask in (8, 16, 32, 64):
            click(sock, *canvas_a, mask=mask, hold=0.008)
            time.sleep(0.04)

        # Plain, shifted, Caps Lock, Control, Command, navigation and editing.
        key_pair(sock, ord("a"))
        chord(sock, XK_SHIFT_L, ord("A"))
        key_pair(sock, XK_CAPS_LOCK)
        key_pair(sock, ord("b"))
        key_pair(sock, XK_CAPS_LOCK)
        chord(sock, XK_CONTROL_L, ord("c"))
        # The installed OSXvnc key table maps XK_Alt_L to macOS Command
        # (keyCode 55 / modifier 0x100000); Meta/Super map to Option.
        chord(sock, XK_ALT_L, ord("m"))
        for keysym in (XK_TAB, XK_BACKSPACE, XK_RETURN, XK_ESCAPE):
            key_pair(sock, keysym)
    finally:
        sock.close()

    time.sleep(args.settle)
    after = read_events(args.ssh_target, args.event_log)
    events = [event for event in after
              if int(event.get("sequence", 0)) > baseline]
    counts, latency = event_summary(events)
    checks = [
        require(events, "left_down", 3),
        require(events, "left_up", 3),
        require(events, "left_drag", 1),
        require(events, "right_down", 1),
        require(events, "right_up", 1),
        require(events, "scroll", 4),
        require(events, "key_down", 8),
        require(events, "key_up", 8),
        require(events, "flags_changed", 4),
    ]
    click_counts = [event.get("click_count") for event in events
                    if event["event"] == "left_down"]
    checks.append({
        "name": "double_click_count",
        "expected": 2,
        "actual": max(click_counts, default=0),
        "pass": 2 in click_counts,
    })
    key_downs = [event for event in events if event["event"] == "key_down"]
    def semantic_key_check(name, character, modifier):
        matches = [event for event in key_downs
                   if event.get("characters") == character and
                   int(event.get("modifiers", 0)) & modifier]
        return {"name": name, "actual": len(matches), "pass": bool(matches)}
    checks.extend([
        semantic_key_check("shift_uppercase", "A", MODIFIER_SHIFT),
        semantic_key_check("caps_uppercase", "B", MODIFIER_CAPS_LOCK),
        semantic_key_check("control_chord", "c", MODIFIER_CONTROL),
        semantic_key_check("command_chord", "m", MODIFIER_COMMAND),
    ])
    result = {
        "rfb": {"name": name, "width": width, "height": height},
        "baseline_sequence": baseline,
        "event_count": len(events),
        "counts": counts,
        "delivery_latency": latency,
        "checks": checks,
        "pass": all(check["pass"] for check in checks),
        "events": events,
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(rendered + "\n")
    raise SystemExit(0 if result["pass"] else 1)


if __name__ == "__main__":
    main()

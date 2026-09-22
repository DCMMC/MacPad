"""Measure VNC framebuffer delivery during continuous pointer motion.

Unlike the discrete usability benchmark, this keeps a 60-Hz pointer stream
and one incremental framebuffer request outstanding at the same time.  It is
designed to catch capture debounce starvation: a menu can react perfectly
after the pointer stops while publishing no intermediate hover frames during
the trajectory.

Coordinates and regions are physical RFB pixels.  The retained framebuffer is
32-bit BGRX, matching the other VNC diagnostics in this repository.
"""

import argparse
import collections
import hashlib
import json
import math
import os
import select
import socket
import statistics
import struct
import threading
import time

import vnc_capture
import vnc_live_click


def check_rect(width, height, rect):
    x, y, rect_width, rect_height = rect
    if (x < 0 or y < 0 or rect_width < 1 or rect_height < 1 or
            x + rect_width > width or y + rect_height > height):
        raise ValueError(
            f"ROI {rect} outside framebuffer {width}x{height}")


def pointer_message(mask, point):
    return struct.pack(">BBHH", 5, mask, point[0], point[1])


def key_message(down, keysym):
    return struct.pack(">BBxxI", 4, 1 if down else 0, keysym)


def update_message(width, height, incremental):
    return struct.pack(
        ">BBHHHH", 3, 1 if incremental else 0, 0, 0, width, height)


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * fraction)]


def region_changed_pixels(before, after, width, rect, masked_points,
                          mask_radius):
    """Count changed pixels after excluding recently occupied cursor boxes."""
    x, y, rect_width, rect_height = rect
    changed = 0
    for row in range(y, y + rect_height):
        for column in range(x, x + rect_width):
            if any(abs(column - point_x) <= mask_radius and
                   abs(row - point_y) <= mask_radius
                   for point_x, point_y in masked_points):
                continue
            offset = (row * width + column) * 4
            if before[offset:offset + 4] != after[offset:offset + 4]:
                changed += 1
    return changed


def region_digest(framebuffer, width, rect, masked_points, mask_radius):
    x, y, rect_width, rect_height = rect
    value = hashlib.sha256()
    for row in range(y, y + rect_height):
        start = (row * width + x) * 4
        line = bytearray(framebuffer[start:start + rect_width * 4])
        for point_x, point_y in masked_points:
            if point_y - mask_radius <= row <= point_y + mask_radius:
                left = max(x, point_x - mask_radius)
                right = min(x + rect_width, point_x + mask_radius + 1)
                if right > left:
                    relative_left = (left - x) * 4
                    relative_right = (right - x) * 4
                    line[relative_left:relative_right] = bytes(
                        relative_right - relative_left)
        value.update(line)
    return value.hexdigest()


def trajectory_point(start, end, progress):
    """Ping-pong line interpolation: 0→1→0 over each unit interval."""
    cycle = progress % 2.0
    fraction = cycle if cycle <= 1.0 else 2.0 - cycle
    return (
        round(start[0] + (end[0] - start[0]) * fraction),
        round(start[1] + (end[1] - start[1]) * fraction),
    )


def rectangle_pixels_in_roi(rectangles, roi):
    """Cheap wire-level damage area for latency tests.

    This intentionally measures transmitted rectangle coverage rather than
    comparing every framebuffer pixel.  The latter takes ~1.5 seconds for the
    1500x1500 test ROI in CPython and, if performed before the next incremental
    request, turns the test client itself into the RFB frame-rate limiter.
    """
    roi_x, roi_y, roi_width, roi_height = roi
    roi_x2 = roi_x + roi_width
    roi_y2 = roi_y + roi_height
    pixels = 0
    for x, y, width, height, encoding in rectangles:
        if encoding in (-223, -224):
            continue
        x1 = max(x, roi_x)
        y1 = max(y, roi_y)
        x2 = min(x + width, roi_x2)
        y2 = min(y + height, roi_y2)
        if x2 > x1 and y2 > y1:
            pixels += (x2 - x1) * (y2 - y1)
    return pixels


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("--port", type=int, default=5900)
    parser.add_argument("--encoding", choices=("raw", "hextile", "zlib"),
                        default="hextile")
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--duration", type=float, default=3.0)
    parser.add_argument("--settle-seconds", type=float, default=0.75)
    parser.add_argument("--hz", type=float, default=60.0)
    parser.add_argument("--cycles", type=float, default=3.0,
                        help="one-way traversals during duration (default: 3)")
    parser.add_argument("--trajectory", nargs=4, type=int, required=True,
                        metavar=("X1", "Y1", "X2", "Y2"))
    parser.add_argument("--button-mask", type=int, default=0,
                        help="RFB button mask held during motion")
    parser.add_argument("--pre-click", nargs=2, type=int,
                        metavar=("X", "Y"),
                        help="left click before measurement, e.g. a menu bar")
    parser.add_argument("--roi", nargs=4, type=int, required=True,
                        metavar=("X", "Y", "WIDTH", "HEIGHT"))
    parser.add_argument("--cursor-mask-radius", type=int, default=40)
    parser.add_argument("--meaningful-pixels", type=int, default=2000,
                        help="non-cursor changed pixels required per frame")
    parser.add_argument("--pipeline-requests", action="store_true",
                        help="send an incremental request with each motion "
                             "event instead of waiting for client decode")
    parser.add_argument("--fast-wire", action="store_true",
                        help="classify meaningful updates by transmitted ROI "
                             "area, avoiding a per-pixel CPython scan in the "
                             "RFB request hot path")
    parser.add_argument("--snapshot")
    parser.add_argument("--json")
    parser.add_argument("--escape-at-end", action="store_true")
    args = parser.parse_args()

    if (args.timeout <= 0 or args.duration <= 0 or args.hz <= 0 or
            args.cycles <= 0 or args.settle_seconds < 0 or
            args.cursor_mask_radius < 0 or args.meaningful_pixels < 1):
        parser.error("timings/cycles/Hz/pixel threshold must be positive")

    start = tuple(args.trajectory[:2])
    end = tuple(args.trajectory[2:])
    roi = tuple(args.roi)
    sock, width, height, name = vnc_capture.connect_rfb(
        args.host, args.port, args.timeout)
    check_rect(width, height, roi)
    for point in (start, end, tuple(args.pre_click) if args.pre_click else None):
        if point is not None and not (0 <= point[0] < width and
                                      0 <= point[1] < height):
            raise ValueError(f"point {point} outside {width}x{height}")

    framebuffer = bytearray(width * height * 4)
    send_lock = threading.Lock()
    state_lock = threading.Lock()
    recent_points = collections.deque(maxlen=8)
    sender_errors = []
    sent_events = 0

    def send(payload):
        with send_lock:
            sock.sendall(payload)

    try:
        vnc_live_click.configure_encoding(sock, args.encoding)
        send(update_message(width, height, False))
        initial_rectangles = vnc_live_click.receive_update(
            sock, width, framebuffer)

        if args.pre_click:
            pre_click = tuple(args.pre_click)
            send(update_message(width, height, True))
            send(pointer_message(1, pre_click))
            time.sleep(0.04)
            send(pointer_message(0, pre_click))
            # Let the menu's nested tracker and its first composite settle,
            # then establish an exact full-frame measurement baseline.
            time.sleep(0.45)
            send(update_message(width, height, False))
            vnc_live_click.receive_update(sock, width, framebuffer)

        baseline = bytes(framebuffer)
        send(update_message(width, height, True))
        measurement_started = time.monotonic()
        motion_deadline = measurement_started + args.duration

        def sender():
            nonlocal sent_events
            interval = 1.0 / args.hz
            next_event = measurement_started
            try:
                while True:
                    now = time.monotonic()
                    if now >= motion_deadline:
                        break
                    if now < next_event:
                        time.sleep(next_event - now)
                        now = time.monotonic()
                    elapsed_fraction = min(
                        1.0, (now - measurement_started) / args.duration)
                    point = trajectory_point(
                        start, end, elapsed_fraction * args.cycles)
                    with state_lock:
                        recent_points.append(point)
                    send(pointer_message(args.button_mask, point))
                    if args.pipeline_requests:
                        send(update_message(width, height, True))
                    sent_events += 1
                    next_event += interval
                with state_lock:
                    recent_points.append(end)
                send(pointer_message(0, end))
            except (OSError, RuntimeError) as error:
                sender_errors.append(str(error))

        thread = threading.Thread(target=sender, name="rfb-motion-sender")
        thread.start()

        update_times = []
        meaningful_times = []
        changed_pixel_counts = []
        digests = set()
        rectangles_seen = 0
        receive_seconds = 0.0
        prior_frame = bytes(framebuffer)
        receive_deadline = motion_deadline + args.settle_seconds
        while time.monotonic() < receive_deadline:
            remaining = receive_deadline - time.monotonic()
            readable, _, _ = select.select([sock], [], [], remaining)
            if not readable:
                break
            receive_started = time.monotonic()
            rectangles = vnc_live_click.receive_update(
                sock, width, framebuffer)
            receive_seconds += time.monotonic() - receive_started
            received = time.monotonic()
            update_times.append(received)
            rectangles_seen += len(rectangles)
            # Keep one incremental request outstanding before doing any local
            # classification. Runtime client-message tracing on 2026-07-29
            # showed that the old order delayed type-3 requests by ~1.49 s:
            # region_changed_pixels scanned 2.25 million pixels in Python
            # while the server correctly waited for the next RFB request.
            if time.monotonic() < receive_deadline:
                send(update_message(width, height, True))
            with state_lock:
                masks = tuple(recent_points)
            changed_pixels = (rectangle_pixels_in_roi(rectangles, roi)
                              if args.fast_wire else region_changed_pixels(
                                  prior_frame, framebuffer, width, roi, masks,
                                  args.cursor_mask_radius))
            changed_pixel_counts.append(changed_pixels)
            if not args.fast_wire:
                digest = region_digest(
                    framebuffer, width, roi, masks, args.cursor_mask_radius)
                digests.add(digest)
            if changed_pixels >= args.meaningful_pixels:
                meaningful_times.append(received)
            if not args.fast_wire:
                prior_frame = bytes(framebuffer)

        thread.join(timeout=max(1.0, args.timeout))
        if thread.is_alive():
            raise RuntimeError("pointer sender did not stop")
        if sender_errors:
            raise RuntimeError("; ".join(sender_errors))

        if args.escape_at_end:
            send(key_message(True, 0xFF1B))
            send(key_message(False, 0xFF1B))

        meaningful_intervals = [
            right - left
            for left, right in zip(meaningful_times, meaningful_times[1:])]
        observation_seconds = args.duration + args.settle_seconds
        summary = {
            "name": name,
            "width": width,
            "height": height,
            "encoding": args.encoding,
            "trajectory": [*start, *end],
            "roi": list(roi),
            "duration_seconds": args.duration,
            "settle_seconds": args.settle_seconds,
            "requested_hz": args.hz,
            "pipelined_requests": args.pipeline_requests,
            "classification": ("transmitted_roi_area" if args.fast_wire
                               else "changed_framebuffer_pixels"),
            "sent_events": sent_events,
            "framebuffer_updates": len(update_times),
            "update_fps": len(update_times) / observation_seconds,
            "meaningful_updates": len(meaningful_times),
            "meaningful_fps": len(meaningful_times) / observation_seconds,
            "unique_masked_roi_frames": (None if args.fast_wire
                                         else len(digests)),
            "rectangles_seen": rectangles_seen,
            "receive_seconds": receive_seconds,
            "first_update_seconds": (update_times[0] - measurement_started)
            if update_times else None,
            "first_meaningful_seconds": (
                meaningful_times[0] - measurement_started)
            if meaningful_times else None,
            "meaningful_interval_p50_seconds": percentile(
                meaningful_intervals, 0.50),
            "meaningful_interval_p95_seconds": percentile(
                meaningful_intervals, 0.95),
            "meaningful_interval_max_seconds": max(meaningful_intervals)
            if meaningful_intervals else None,
            "changed_pixels_mean": statistics.mean(changed_pixel_counts)
            if changed_pixel_counts else 0,
            "changed_pixels_max": max(changed_pixel_counts)
            if changed_pixel_counts else 0,
            "changed_pixels_from_baseline": (None if args.fast_wire else
                region_changed_pixels(
                    baseline, framebuffer, width, roi, (), 0)),
            "initial_rectangles": initial_rectangles,
        }
        print(
            "MOTION "
            f"sent={sent_events} updates={len(update_times)} "
            f"update_fps={summary['update_fps']:.2f} "
            f"meaningful={len(meaningful_times)} "
            f"meaningful_fps={summary['meaningful_fps']:.2f} "
            f"p50={summary['meaningful_interval_p50_seconds']} "
            f"p95={summary['meaningful_interval_p95_seconds']} "
            f"max_gap={summary['meaningful_interval_max_seconds']} "
            f"receive={receive_seconds:.3f}s",
            flush=True)
        if args.snapshot:
            parent = os.path.dirname(args.snapshot)
            if parent:
                os.makedirs(parent, exist_ok=True)
            vnc_live_click.save_frame(
                args.snapshot, width, height, framebuffer, "motion-final")
        if args.json:
            parent = os.path.dirname(args.json)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(args.json, "w", encoding="utf-8") as output:
                json.dump(summary, output, indent=2)
                output.write("\n")
        if not meaningful_times:
            raise SystemExit(2)
    except (EOFError, OSError, RuntimeError, socket.timeout) as error:
        print(f"MOTION FAIL error={error}", flush=True)
        raise SystemExit(2) from error
    finally:
        sock.close()


if __name__ == "__main__":
    main()

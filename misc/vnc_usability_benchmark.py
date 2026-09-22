"""Measure system-menu, context-menu, and title-drag VNC responsiveness.

The benchmark keeps one Retina RFB connection and one retained framebuffer.
For pointer motion it hashes a fixed region while masking both the old and new
cursor positions; a cursor tile by itself therefore cannot satisfy a test.

The defaults target Terminal on the iPad13,6 2388x1668 desktop used by this
project. Coordinates are physical RFB pixels, not AppKit points. Unless an
explicit --title-drag is supplied, the drag starts from the first wide light
title bar found in the normalized framebuffer instead of a stale fixed point.
"""

import argparse
import hashlib
import json
import os
import select
import socket
import statistics
import struct
import time

import vnc_capture
import vnc_live_click
import vnc_native_input_test

CURSOR_MASK_RADIUS = 32
MIN_CHANGED_PIXELS = 2048


def check_rect(width, height, rect, label):
    x, y, rect_width, rect_height = rect
    if (x < 0 or y < 0 or rect_width < 1 or rect_height < 1 or
            x + rect_width > width or y + rect_height > height):
        raise ValueError(
            f"{label} {rect} outside framebuffer {width}x{height}")


def check_point(width, height, x, y):
    if x < 0 or y < 0 or x >= width or y >= height:
        raise ValueError(f"point {(x, y)} outside framebuffer {width}x{height}")


def longest_light_run(framebuffer, width, y, threshold=232):
    """Return the longest nearly-white BGRX run on one framebuffer row."""
    best_start = None
    best_length = 0
    run_start = None
    for x in range(width):
        offset = (y * width + x) * 4
        blue, green, red = framebuffer[offset:offset + 3]
        light = (blue >= threshold and green >= threshold and
                 red >= threshold and
                 max(blue, green, red) - min(blue, green, red) <= 20)
        if light and run_start is None:
            run_start = x
        if not light and run_start is not None:
            length = x - run_start
            if length > best_length:
                best_start, best_length = run_start, length
            run_start = None
    if run_start is not None and width - run_start > best_length:
        best_start, best_length = run_start, width - run_start
    return best_start, best_length


def detect_light_title_drag(framebuffer, width, height):
    """Find Terminal's actual title-bar top edge and a safe drag trajectory.

    This is deliberately a visual test precondition, not an input workaround.
    It requires a wide nearly-white run preceded by several rows without one,
    so a white document body cannot be mistaken for its own title bar.
    """
    minimum_run = max(320, width // 5)
    first_row = max(80, height // 20)
    recent_lengths = []
    for y in range(first_row, height - 120):
        run_start, run_length = longest_light_run(framebuffer, width, y)
        if (run_start is not None and run_length >= minimum_run and
                len(recent_lengths) >= 4 and
                max(recent_lengths[-4:]) < minimum_run):
            # Confirm that this is a horizontal surface, not one bright row.
            confirmations = []
            for confirm_y in range(y + 1, min(y + 9, height)):
                confirm_start, confirm_length = longest_light_run(
                    framebuffer, width, confirm_y)
                confirmations.append(
                    confirm_start is not None and
                    confirm_length >= minimum_run and
                    confirm_start < run_start + run_length and
                    confirm_start + confirm_length > run_start)
            if sum(confirmations) < 6:
                recent_lengths.append(run_length)
                continue
            start_x = run_start + (run_length * 2) // 3
            start_y = y + 20
            end_x = min(width - 2, start_x + 220)
            end_y = min(height - 2, start_y + 100)
            return {
                "source": "auto-light-titlebar",
                "light_run": [run_start, y, run_length],
                "start": [start_x, start_y],
                "end": [end_x, end_y],
            }
        recent_lengths.append(run_length)
    raise RuntimeError(
        "no wide light title bar found; pass --title-drag X1 Y1 X2 Y2 "
        "or --skip-drag")


def region_digest(framebuffer, width, rect, masked_points=(), radius=None):
    """Hash a rectangle after zeroing cursor-sized boxes around points."""
    if radius is None:
        radius = CURSOR_MASK_RADIUS
    x, y, rect_width, rect_height = rect
    hasher = hashlib.sha256()
    for row in range(y, y + rect_height):
        start = (row * width + x) * 4
        line = bytearray(framebuffer[start:start + rect_width * 4])
        for point_x, point_y in masked_points:
            if point_y - radius <= row <= point_y + radius:
                left = max(x, point_x - radius)
                right = min(x + rect_width, point_x + radius + 1)
                if right > left:
                    relative_left = (left - x) * 4
                    relative_right = (right - x) * 4
                    line[relative_left:relative_right] = bytes(
                        relative_right - relative_left)
        hasher.update(line)
    return hasher.hexdigest()


def region_changed_pixels(before, after, width, rect, masked_points=(),
                          radius=None, stop_after=None):
    """Count changed pixels, skipping equal row spans in C.

    The former per-pixel `any()` loop examined the complete ROI even when the
    first framebuffer update was only a cursor tile.  A 1200x1100 context-menu
    ROI took about 1.2 seconds on the benchmark Mac and that local work was
    incorrectly included in the device latency.  Compare unmasked row spans as
    byte strings first, then inspect pixels only inside spans that differ.  A
    threshold caller can stop as soon as it has enough evidence.
    """
    if radius is None:
        radius = CURSOR_MASK_RADIUS
    x, y, rect_width, rect_height = rect
    changed = 0
    for row in range(y, y + rect_height):
        masked_intervals = []
        for point_x, point_y in masked_points:
            if abs(row - point_y) > radius:
                continue
            left = max(x, point_x - radius)
            right = min(x + rect_width, point_x + radius + 1)
            if left < right:
                masked_intervals.append((left, right))
        masked_intervals.sort()
        spans = []
        cursor = x
        for left, right in masked_intervals:
            if left > cursor:
                spans.append((cursor, left))
            if right > cursor:
                cursor = right
        if cursor < x + rect_width:
            spans.append((cursor, x + rect_width))

        for left, right in spans:
            byte_start = (row * width + left) * 4
            byte_end = (row * width + right) * 4
            before_span = before[byte_start:byte_end]
            after_span = after[byte_start:byte_end]
            if before_span == after_span:
                continue
            for offset in range(0, len(before_span), 4):
                if before_span[offset:offset + 4] != \
                        after_span[offset:offset + 4]:
                    changed += 1
                    if stop_after is not None and changed >= stop_after:
                        return changed
    return changed


def pointer(sock, mask, point):
    sock.sendall(struct.pack(">BBHH", 5, mask, point[0], point[1]))


def key(sock, keysym):
    sock.sendall(struct.pack(">BBxxI", 4, 1, keysym))
    sock.sendall(struct.pack(">BBxxI", 4, 0, keysym))


def wait_for_region(sock, width, height, framebuffer, rect, masks,
                    baseline, previous_digest, minimum_changed_pixels,
                    started, timeout, max_updates):
    rectangles_seen = []
    deadline = started + timeout
    first_readable_seconds = None
    receive_seconds = 0.0
    for update_index in range(1, max_updates + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        readable, _, _ = select.select([sock], [], [], remaining)
        if not readable:
            break
        if first_readable_seconds is None:
            first_readable_seconds = time.monotonic() - started
        sock.settimeout(remaining)
        receive_started = time.monotonic()
        rectangles = vnc_live_click.receive_update(
            sock, width, framebuffer)
        receive_seconds += time.monotonic() - receive_started
        rectangles_seen.extend(rectangles)
        current_digest = region_digest(
            framebuffer, width, rect, masks)
        if current_digest != previous_digest:
            changed_pixels = region_changed_pixels(
                baseline, framebuffer, width, rect, masks,
                stop_after=minimum_changed_pixels)
            if changed_pixels >= minimum_changed_pixels:
                return {
                    "passed": True,
                    "latency_seconds": time.monotonic() - started,
                    "updates": update_index,
                    "rectangles": rectangles_seen,
                    "digest": current_digest,
                    "first_readable_seconds": first_readable_seconds,
                    "receive_seconds": receive_seconds,
                    "first_valid_changed_pixels": changed_pixels,
                }
        if time.monotonic() < deadline:
            vnc_live_click.request_update(sock, width, height, True)
    return {
        "passed": False,
        "latency_seconds": None,
        "updates": len(rectangles_seen),
        "rectangles": rectangles_seen,
        "digest": previous_digest,
        "first_readable_seconds": first_readable_seconds,
        "receive_seconds": receive_seconds,
        "first_valid_changed_pixels": None,
    }


def measured_action(sock, width, height, framebuffer, label, rect, masks,
                    action, timeout, max_updates, settle_seconds):
    baseline = bytes(framebuffer)
    before = region_digest(framebuffer, width, rect, masks)
    vnc_live_click.request_update(sock, width, height, True)
    started = time.monotonic()
    action()
    result = wait_for_region(
        sock, width, height, framebuffer, rect, masks, baseline, before,
        MIN_CHANGED_PIXELS, started, timeout, max_updates)
    result["operation"] = label
    settled_updates = 0
    settled_changes = 0
    if result["passed"] and settle_seconds > 0:
        # The first changed rectangle can be cursor/traffic-light feedback
        # that precedes the actual menu composite. Keep the RFB request stream
        # alive for a short bounded interval and retain the newest complete
        # frame. This mirrors a real VNC viewer, which immediately asks for
        # the next incremental update instead of stopping after one tile.
        settle_deadline = time.monotonic() + settle_seconds
        settled_digest = result["digest"]
        vnc_live_click.request_update(sock, width, height, True)
        while True:
            remaining = settle_deadline - time.monotonic()
            if remaining <= 0:
                break
            readable, _, _ = select.select([sock], [], [], remaining)
            if not readable:
                break
            rectangles = vnc_live_click.receive_update(
                sock, width, framebuffer)
            result["rectangles"].extend(rectangles)
            settled_updates += 1
            current_digest = region_digest(framebuffer, width, rect, masks)
            if current_digest != settled_digest:
                settled_changes += 1
                settled_digest = current_digest
            if time.monotonic() < settle_deadline:
                vnc_live_click.request_update(sock, width, height, True)
        result["digest"] = settled_digest
    result["settled_updates"] = settled_updates
    result["settled_changes"] = settled_changes
    result["changed_pixels_from_action_baseline"] = region_changed_pixels(
        baseline, framebuffer, width, rect, masks)
    # A digest mismatch alone is too weak for this benchmark. Runtime A/Bs on
    # 2026-07-29 showed a failed contextual click change only 492 pixels from
    # cursor/caret activity while a real menu/hover/close changed 9k-264k and
    # a real title drag changed 788k. Reject small incidental refreshes after
    # the settle window so they cannot be reported as usable UI state.
    if (result["passed"] and
            result["changed_pixels_from_action_baseline"] <
            MIN_CHANGED_PIXELS):
        result["passed"] = False
        result["rejected_small_change"] = True
        result["latency_seconds"] = None
    else:
        result["rejected_small_change"] = False
    latency = result["latency_seconds"]
    latency_label = "MISS" if latency is None else f"{latency:.3f}s"
    print(
        f"USABILITY {'PASS' if result['passed'] else 'MISS'} "
        f"operation={label} latency={latency_label} "
        f"readable={result['first_readable_seconds']} "
        f"receive={result['receive_seconds']:.3f}s "
        f"updates={result['updates']} "
        f"changed={result['changed_pixels_from_action_baseline']}",
        flush=True)
    return result


def save_snapshot(directory, name, width, height, framebuffer):
    if not directory:
        return
    path = os.path.join(directory, f"{name}.png")
    vnc_live_click.save_frame(path, width, height, framebuffer, name)


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    index = round((len(ordered) - 1) * fraction)
    return ordered[index]


def main():
    global CURSOR_MASK_RADIUS, MIN_CHANGED_PIXELS
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("--port", type=int, default=5900)
    parser.add_argument("--encoding", choices=("raw", "hextile", "zlib"),
                        default="zlib")
    parser.add_argument("--timeout", type=float, default=6.0)
    parser.add_argument("--max-updates", type=int, default=16)
    parser.add_argument("--settle-seconds", type=float, default=0.35,
                        help="retain post-action incremental frames before "
                             "the next operation (default: 0.35)")
    parser.add_argument("--cursor-mask-radius", type=int, default=32)
    parser.add_argument("--min-changed-pixels", type=int, default=2048,
                        help="minimum non-cursor ROI pixels required for a "
                             "PASS after settling (default: 2048)")
    parser.add_argument("--menu-click", nargs=2, type=int, default=(235, 20),
                        metavar=("X", "Y"))
    parser.add_argument("--menu-roi", nargs=4, type=int,
                        default=(40, 30, 520, 410),
                        metavar=("X", "Y", "WIDTH", "HEIGHT"))
    parser.add_argument("--hover", nargs=2, type=int, action="append",
                        metavar=("X", "Y"),
                        help="menu hover point; repeat for a trajectory")
    parser.add_argument("--menu-select", nargs=2, type=int,
                        metavar=("X", "Y"),
                        help="click this menu item after the hover trajectory "
                             "instead of closing with Escape")
    parser.add_argument("--context-click", nargs=2, type=int,
                        default=(1000, 700), metavar=("X", "Y"))
    parser.add_argument("--context-roi", nargs=4, type=int,
                        default=(750, 450, 900, 900),
                        metavar=("X", "Y", "WIDTH", "HEIGHT"))
    parser.add_argument("--context-hover", nargs=2, type=int,
                        action="append", metavar=("X", "Y"),
                        help="context-menu hover point; repeat for a "
                             "trajectory before Escape closes the menu")
    parser.add_argument("--context-select", nargs=2, type=int,
                        metavar=("X", "Y"),
                        help="click this context-menu item after the hover "
                             "trajectory instead of closing with Escape")
    parser.add_argument("--context-hold-seconds", type=float, default=0.04,
                        help="client-side right-button hold before release "
                             "(default: 0.04)")
    parser.add_argument("--context-button-mask", type=int, choices=(2, 4),
                        default=4,
                        help="RFB button mask for contextual click; 4 is the "
                             "protocol secondary button, 2 diagnoses legacy "
                             "OSXvnc button swapping")
    parser.add_argument("--title-drag", nargs=4, type=int,
                        default=None,
                        metavar=("X1", "Y1", "X2", "Y2"))
    parser.add_argument("--drag-roi", nargs=4, type=int,
                        default=(80, 100, 1600, 1200),
                        metavar=("X", "Y", "WIDTH", "HEIGHT"))
    parser.add_argument("--snapshots")
    parser.add_argument("--json")
    parser.add_argument("--skip-menu", action="store_true")
    parser.add_argument("--skip-context", action="store_true")
    parser.add_argument("--skip-drag", action="store_true")
    args = parser.parse_args()
    CURSOR_MASK_RADIUS = args.cursor_mask_radius
    MIN_CHANGED_PIXELS = args.min_changed_pixels

    if (args.timeout <= 0 or args.max_updates < 1 or
            args.settle_seconds < 0 or
            args.context_hold_seconds < 0 or
            args.cursor_mask_radius < 0 or
            args.min_changed_pixels < 1):
        parser.error("timeout/max-updates must be positive and mask nonnegative")
    hover_points = args.hover or [
        (180, 65), (180, 115), (180, 150), (180, 200),
        (180, 255), (180, 290), (180, 325), (180, 375),
    ]

    sock, width, height, name = vnc_capture.connect_rfb(
        args.host, args.port, args.timeout)
    framebuffer = bytearray(width * height * 4)
    results = []
    try:
        menu_rect = tuple(args.menu_roi)
        context_rect = tuple(args.context_roi)
        drag_rect = tuple(args.drag_roi)
        for rect, label in ((menu_rect, "menu ROI"),
                            (context_rect, "context ROI"),
                            (drag_rect, "drag ROI")):
            check_rect(width, height, rect, label)
        if args.snapshots:
            os.makedirs(args.snapshots, exist_ok=True)

        if args.encoding == "hextile":
            vnc_live_click.configure_hextile(sock)
        elif args.encoding == "zlib":
            vnc_live_click.configure_zlib(sock)
        else:
            vnc_live_click.configure_raw(sock)
        vnc_live_click.request_update(sock, width, height, False)
        initial_rectangles = vnc_live_click.receive_update(
            sock, width, framebuffer)
        print(
            f"USABILITY start name={name!r} size={width}x{height} "
            f"initial={initial_rectangles}", flush=True)
        save_snapshot(args.snapshots, "00-initial", width, height,
                      framebuffer)

        # Normalize any menu state left by an interactive client.
        key(sock, 0xFF1B)
        time.sleep(0.15)
        # The Escape key can itself close a menu and queue one framebuffer
        # update.  Starting the first measured action from the older retained
        # pixels made that queued close satisfy `menu-open`, shifting every
        # screenshot/result by one UI state.  A non-incremental request gives
        # this benchmark an exact post-normalization baseline.
        vnc_live_click.request_update(sock, width, height, False)
        normalized_rectangles = vnc_live_click.receive_update(
            sock, width, framebuffer)
        print(
            f"USABILITY normalized={normalized_rectangles}", flush=True)
        save_snapshot(args.snapshots, "00-normalized", width, height,
                      framebuffer)

        drag_plan = None
        if not args.skip_drag:
            if args.title_drag is None:
                drag_plan = detect_light_title_drag(
                    framebuffer, width, height)
            else:
                drag_plan = {
                    "source": "explicit",
                    "light_run": None,
                    "start": list(args.title_drag[:2]),
                    "end": list(args.title_drag[2:]),
                }
            check_point(width, height, *drag_plan["start"])
            check_point(width, height, *drag_plan["end"])
            print(
                f"USABILITY title-drag source={drag_plan['source']} "
                f"light_run={drag_plan['light_run']} "
                f"start={drag_plan['start']} end={drag_plan['end']}",
                flush=True)

        if not args.skip_menu:
            menu_click = tuple(args.menu_click)
            result = measured_action(
                sock, width, height, framebuffer,
                "menu-open", menu_rect, (menu_click,),
                lambda: (pointer(sock, 1, menu_click), time.sleep(0.04),
                         pointer(sock, 0, menu_click)),
                args.timeout, args.max_updates, args.settle_seconds)
            results.append(result)
            save_snapshot(args.snapshots, "01-menu-open", width, height,
                          framebuffer)

            previous_point = menu_click
            for index, next_point in enumerate(hover_points, 1):
                next_point = tuple(next_point)
                result = measured_action(
                    sock, width, height, framebuffer,
                    f"menu-hover-{index}", menu_rect,
                    (previous_point, next_point),
                    lambda point=next_point: pointer(sock, 0, point),
                    args.timeout, args.max_updates, args.settle_seconds)
                results.append(result)
                previous_point = next_point
                save_snapshot(args.snapshots, f"hover-{index:02d}", width,
                              height, framebuffer)

            if args.menu_select:
                select_point = tuple(args.menu_select)
                result = measured_action(
                    sock, width, height, framebuffer, "menu-select",
                    menu_rect, (previous_point, select_point),
                    lambda: (pointer(sock, 1, select_point),
                             time.sleep(0.04),
                             pointer(sock, 0, select_point)),
                    args.timeout, args.max_updates, args.settle_seconds)
                save_snapshot(args.snapshots, "menu-select", width, height,
                              framebuffer)
            else:
                result = measured_action(
                    sock, width, height, framebuffer, "menu-close", menu_rect,
                    (previous_point,), lambda: key(sock, 0xFF1B),
                    args.timeout, args.max_updates, args.settle_seconds)
                save_snapshot(args.snapshots, "menu-close", width, height,
                              framebuffer)
            results.append(result)

        if not args.skip_context:
            context_point = tuple(args.context_click)
            result = measured_action(
                sock, width, height, framebuffer, "context-menu", context_rect,
                (context_point,),
                lambda: (pointer(sock, args.context_button_mask,
                                 context_point),
                         time.sleep(args.context_hold_seconds),
                         pointer(sock, 0, context_point)),
                args.timeout, args.max_updates, args.settle_seconds)
            results.append(result)
            save_snapshot(args.snapshots, "context-menu", width, height,
                          framebuffer)

            previous_point = context_point
            for index, next_point in enumerate(args.context_hover or (), 1):
                next_point = tuple(next_point)
                result = measured_action(
                    sock, width, height, framebuffer,
                    f"context-hover-{index}", context_rect,
                    (previous_point, next_point),
                    lambda point=next_point: pointer(sock, 0, point),
                    args.timeout, args.max_updates, args.settle_seconds)
                results.append(result)
                previous_point = next_point
                save_snapshot(args.snapshots,
                              f"context-hover-{index:02d}", width,
                              height, framebuffer)

            if args.context_select:
                select_point = tuple(args.context_select)
                result = measured_action(
                    sock, width, height, framebuffer, "context-select",
                    context_rect, (previous_point, select_point),
                    lambda: (pointer(sock, 1, select_point),
                             time.sleep(0.04),
                             pointer(sock, 0, select_point)),
                    args.timeout, args.max_updates, args.settle_seconds)
                save_snapshot(args.snapshots, "context-select", width, height,
                              framebuffer)
            else:
                result = measured_action(
                    sock, width, height, framebuffer, "context-close",
                    context_rect, (previous_point,),
                    lambda: key(sock, 0xFF1B),
                    args.timeout, args.max_updates, args.settle_seconds)
                save_snapshot(args.snapshots, "context-close", width, height,
                              framebuffer)
            results.append(result)

        if not args.skip_drag:
            drag_start = tuple(drag_plan["start"])
            drag_end = tuple(drag_plan["end"])
            result = measured_action(
                sock, width, height, framebuffer, "title-drag", drag_rect,
                (drag_start, drag_end),
                lambda: vnc_native_input_test.send_drag(
                    sock, drag_start, drag_end, 12, 0.015),
                args.timeout, args.max_updates, args.settle_seconds)
            result["coordinate_source"] = drag_plan["source"]
            result["light_title_run"] = drag_plan["light_run"]
            result["input_start"] = drag_plan["start"]
            result["input_end"] = drag_plan["end"]
            results.append(result)
            save_snapshot(args.snapshots, "title-drag", width, height,
                          framebuffer)

        latencies = [result["latency_seconds"] for result in results
                     if result["latency_seconds"] is not None]
        passed = sum(result["passed"] for result in results)
        summary = {
            "name": name,
            "width": width,
            "height": height,
            "encoding": args.encoding,
            "title_drag": drag_plan,
            "min_changed_pixels": args.min_changed_pixels,
            "passed": passed,
            "total": len(results),
            "missed": len(results) - passed,
            "latency_min_seconds": min(latencies) if latencies else None,
            "latency_mean_seconds": statistics.mean(latencies)
            if latencies else None,
            "latency_p50_seconds": percentile(latencies, 0.50),
            "latency_p95_seconds": percentile(latencies, 0.95),
            "latency_max_seconds": max(latencies) if latencies else None,
            "results": results,
        }
        print(
            f"USABILITY SUMMARY passed={passed}/{len(results)} "
            f"missed={len(results) - passed} "
            f"p50={summary['latency_p50_seconds']} "
            f"p95={summary['latency_p95_seconds']} "
            f"max={summary['latency_max_seconds']}", flush=True)
        if args.json:
            with open(args.json, "w", encoding="utf-8") as output:
                json.dump(summary, output, indent=2)
                output.write("\n")
        if passed != len(results):
            raise SystemExit(2)
    except (EOFError, RuntimeError, socket.timeout) as error:
        print(f"USABILITY FAIL error={error}", flush=True)
        raise SystemExit(2) from error
    finally:
        sock.close()


if __name__ == "__main__":
    main()

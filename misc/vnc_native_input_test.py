"""Exercise native OSXvnc pointer, drag, and keyboard input in one session.

The test retains one raw Retina framebuffer and waits for changed incremental
pixels after every operation.  Keeping one RFB connection is important: a
reconnecting full-frame request would conceal a stalled live update path.

Example for a 2388x1668 Terminal desktop::

    python3 misc/vnc_native_input_test.py 192.168.1.5 \
      --title-drag 1200 70 1400 140 \
      --content-drag 1300 600 1500 600 \
      --text 'echo vnc_input_ok' --output /tmp/vnc-input.png
"""

import argparse
import hashlib
import os
import select
import socket
import struct
import time

import vnc_capture
import vnc_live_click


def frame_digest(framebuffer):
    return hashlib.sha256(framebuffer).hexdigest()


def check_point(width, height, x, y):
    if not (0 <= x < width and 0 <= y < height):
        raise ValueError(f"point ({x},{y}) outside RFB {width}x{height}")


def request_and_wait(sock, width, height, framebuffer, previous_digest,
                     operation, started, timeout, max_updates):
    rectangles_seen = []
    nonempty_updates = 0
    empty_updates = 0
    deadline = started + timeout
    while nonempty_updates < max_updates:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        readable, _, _ = select.select([sock], [], [], remaining)
        if not readable:
            break
        rectangles = vnc_live_click.receive_update(sock, width, framebuffer)
        if rectangles:
            nonempty_updates += 1
        else:
            empty_updates += 1
        rectangles_seen.extend(rectangles)
        current_digest = frame_digest(framebuffer)
        if current_digest != previous_digest:
            latency = time.monotonic() - started
            raw_rectangles = [rectangle for rectangle in rectangles_seen
                              if rectangle[4] == 0]
            raw_pixels = sum(rectangle[2] * rectangle[3]
                             for rectangle in raw_rectangles)
            encoded_pixels = sum(rectangle[2] * rectangle[3]
                                 for rectangle in rectangles_seen
                                 if rectangle[4] >= 0)
            encoded = [rectangle for rectangle in rectangles_seen
                       if rectangle[4] >= 0]
            if encoded:
                x1 = min(rectangle[0] for rectangle in encoded)
                y1 = min(rectangle[1] for rectangle in encoded)
                x2 = max(rectangle[0] + rectangle[2]
                         for rectangle in encoded)
                y2 = max(rectangle[1] + rectangle[3]
                         for rectangle in encoded)
                encoded_bounds = f"{x1},{y1},{x2 - x1},{y2 - y1}"
            else:
                encoded_bounds = "none"
            if os.environ.get("MACWS_TRACE_RECTS"):
                print(f"RECTANGLES operation={operation} values={encoded}",
                      flush=True)
            print(
                f"INPUT PASS operation={operation} latency={latency:.3f}s "
                f"updates={nonempty_updates} empty_updates={empty_updates} "
                f"rectangles={len(rectangles_seen)} "
                f"encoded_pixels={encoded_pixels} "
                f"encoded_bounds={encoded_bounds} "
                f"raw_rectangles={len(raw_rectangles)} "
                f"raw_pixels={raw_pixels} digest={current_digest}",
                flush=True)
            return current_digest, latency
        # OSXvnc legitimately replies with a zero-rectangle update when the
        # application has accepted a key but WindowServer's next composite is
        # not ready yet. Runtime evidence on 2026-07-29 showed 20 such replies
        # arrive before the 80-ms KEY-PROGRESS capture, causing the old for-
        # loop to fail in under one second despite Terminal already processing
        # the key. Empty replies do not consume the nonempty-update budget;
        # pace the next request so the loop remains bounded by the deadline.
        if not rectangles:
            time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
        vnc_live_click.request_update(sock, width, height, True)
    raise RuntimeError(
        f"no changed incremental frame for {operation}; "
        f"nonempty_updates={nonempty_updates} empty_updates={empty_updates} "
        f"rectangles={rectangles_seen}")


def send_drag(sock, start, end, steps, step_delay):
    start_x, start_y = start
    end_x, end_y = end
    sock.sendall(struct.pack(">BBHH", 5, 1, start_x, start_y))
    for step in range(1, steps + 1):
        fraction = step / steps
        x = round(start_x + (end_x - start_x) * fraction)
        y = round(start_y + (end_y - start_y) * fraction)
        if step_delay:
            time.sleep(step_delay)
        sock.sendall(struct.pack(">BBHH", 5, 1, x, y))
    if step_delay:
        time.sleep(step_delay)
    sock.sendall(struct.pack(">BBHH", 5, 0, end_x, end_y))


def send_key(sock, keysym):
    sock.sendall(struct.pack(">BBxxI", 4, 1, keysym))
    sock.sendall(struct.pack(">BBxxI", 4, 0, keysym))


def send_key_chord(sock, modifier_keysyms, keysym):
    for modifier in modifier_keysyms:
        sock.sendall(struct.pack(">BBxxI", 4, 1, modifier))
    sock.sendall(struct.pack(">BBxxI", 4, 1, keysym))
    sock.sendall(struct.pack(">BBxxI", 4, 0, keysym))
    for modifier in reversed(modifier_keysyms):
        sock.sendall(struct.pack(">BBxxI", 4, 0, modifier))


def send_pointer_click(sock, point, button_mask, hold_seconds=0.04):
    sock.sendall(struct.pack(">BBHH", 5, button_mask, point[0], point[1]))
    if hold_seconds:
        time.sleep(hold_seconds)
    sock.sendall(struct.pack(">BBHH", 5, 0, point[0], point[1]))


def send_pointer_double_click(sock, point, button_mask, hold_seconds=0.04,
                              interval_seconds=0.08):
    send_pointer_click(sock, point, button_mask, hold_seconds)
    if interval_seconds:
        time.sleep(interval_seconds)
    send_pointer_click(sock, point, button_mask, hold_seconds)


def send_text(sock, text, key_delay, send_return=True):
    for character in text:
        send_key(sock, ord(character))
        if key_delay:
            time.sleep(key_delay)
    if send_return:
        send_key(sock, 0xFF0D)


def settle_and_drain(sock, width, height, framebuffer, digest, seconds):
    """Retain the last incremental frame produced during a quiet interval.

    A changed cursor tile is not proof that the application result has landed.
    Keep one framebuffer and consume every complete update until the interval
    expires.  select() avoids timing out halfway through an RFB rectangle,
    which would leave the byte stream unusable.
    """
    if seconds <= 0:
        return digest
    deadline = time.monotonic() + seconds
    updates = 0
    changes = 0
    vnc_live_click.request_update(sock, width, height, True)
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        readable, _, _ = select.select([sock], [], [], remaining)
        if not readable:
            break
        rectangles = vnc_live_click.receive_update(sock, width, framebuffer)
        updates += 1
        current = frame_digest(framebuffer)
        if current != digest:
            changes += 1
            digest = current
        if time.monotonic() < deadline:
            vnc_live_click.request_update(sock, width, height, True)
    print(
        f"SETTLE retained={seconds:.3f}s updates={updates} "
        f"changed_updates={changes} final_digest={digest}", flush=True)
    return digest


def parse_drag(parser, value, label):
    if value is None:
        return None
    if len(value) != 4:
        parser.error(f"{label} needs X1 Y1 X2 Y2")
    return (tuple(value[:2]), tuple(value[2:]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("--port", type=int, default=5900)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--encoding", choices=("raw", "hextile", "zlib"),
                        default="zlib",
                        help="RFB encoding used for retained updates "
                             "(default: zlib)")
    parser.add_argument("--max-updates", type=int, default=20)
    parser.add_argument("--drag-steps", type=int, default=8)
    parser.add_argument("--drag-step-delay", type=float, default=0.02)
    parser.add_argument("--key-delay", type=float, default=0.015)
    parser.add_argument(
        "--pre-input-delay", type=float, default=0.0,
        help=("seconds to wait after the retained initial framebuffer and "
              "before the first input operation; useful for arming an "
              "external bounded trace without reconnecting RFB"))
    parser.add_argument(
        "--trace-sentinel",
        help=("create this sentinel immediately before the first input and "
              "remove it on exit; intended for bounded producer/display "
              "frame traces on the machine running this probe"))
    parser.add_argument("--click-hold", type=float, default=0.04,
                        help="seconds between pointer down/up (default: 0.04)")
    parser.add_argument("--settle-seconds", type=float, default=1.5,
                        help="retain later incremental frames before saving "
                             "the result (default: 1.5)")
    parser.add_argument("--keyboard-burst", action="store_true",
                        help="send all keys before reading the first update; "
                             "the default waits for visible feedback per key")
    parser.add_argument("--title-drag", nargs=4, type=int,
                        metavar=("X1", "Y1", "X2", "Y2"))
    parser.add_argument("--content-drag", nargs=4, type=int,
                        metavar=("X1", "Y1", "X2", "Y2"))
    parser.add_argument("--right-click", nargs=2, type=int,
                        metavar=("X", "Y"),
                        help="send one RFB secondary-button down/up pair")
    parser.add_argument("--left-click", nargs=2, type=int,
                        metavar=("X", "Y"),
                        help="send one RFB primary-button down/up pair")
    parser.add_argument("--double-click", nargs=2, type=int,
                        metavar=("X", "Y"),
                        help="send two primary-button pairs on one RFB "
                             "connection")
    parser.add_argument("--move", nargs=2, type=int,
                        metavar=("X", "Y"),
                        help="send one button-free RFB pointer move")
    parser.add_argument("--text")
    parser.add_argument(
        "--no-return", action="store_true",
        help="do not append Return after --text (interactive typing probe)")
    parser.add_argument("--command-key", metavar="KEY",
                        help="send Command+KEY using X11 Alt_L (0xffe9); "
                             "KEY may also be Tab")
    parser.add_argument("--capture-only", action="store_true",
                        help="save/describe one fresh non-incremental frame "
                             "without sending input")
    parser.add_argument("--output", help="save the final retained framebuffer")
    args = parser.parse_args()

    title_drag = parse_drag(parser, args.title_drag, "--title-drag")
    content_drag = parse_drag(parser, args.content_drag, "--content-drag")
    if (title_drag is None and content_drag is None and
            args.right_click is None and args.left_click is None and
            args.double_click is None and
            args.move is None and args.text is None and
            args.command_key is None and
            not args.capture_only):
        parser.error("select at least one input operation")
    if (args.timeout <= 0 or args.max_updates < 1 or args.drag_steps < 1 or
            args.drag_step_delay < 0 or args.key_delay < 0 or
            args.pre_input_delay < 0 or
            args.click_hold < 0 or
            args.settle_seconds < 0):
        parser.error("timeouts/steps must be positive and delays nonnegative")

    sock, width, height, name = vnc_capture.connect_rfb(
        args.host, args.port, args.timeout)
    framebuffer = bytearray(width * height * 4)
    completed = 0
    try:
        vnc_live_click.configure_encoding(sock, args.encoding)
        vnc_live_click.request_update(sock, width, height, False)
        initial = vnc_live_click.receive_update(sock, width, framebuffer)
        digest = frame_digest(framebuffer)
        initial_digest = digest
        initial_pixels = sum(rect[2] * rect[3] for rect in initial
                             if rect[4] >= 0)
        print(f"INPUT start name={name!r} size={width}x{height} "
              f"initial_rectangles={len(initial)} "
              f"initial_pixels={initial_pixels} digest={digest}",
              flush=True)

        if args.pre_input_delay:
            print(f"INPUT armed delay={args.pre_input_delay:.3f}s",
                  flush=True)
            time.sleep(args.pre_input_delay)
        if args.trace_sentinel:
            with open(args.trace_sentinel, "a", encoding="utf-8"):
                pass
            print(f"INPUT trace-sentinel={args.trace_sentinel}", flush=True)

        if args.capture_only:
            if args.output:
                vnc_live_click.save_frame(
                    args.output, width, height, framebuffer, "capture")
            print(f"INPUT PASS operations=0 capture_only=YES "
                  f"final_digest={digest}", flush=True)
            return

        operations = []
        if title_drag is not None:
            operations.append(("title-drag", title_drag))
        if content_drag is not None:
            operations.append(("content-drag", content_drag))
        for label, (start, end) in operations:
            check_point(width, height, *start)
            check_point(width, height, *end)
            vnc_live_click.request_update(sock, width, height, True)
            started = time.monotonic()
            send_drag(sock, start, end, args.drag_steps,
                      args.drag_step_delay)
            digest, _ = request_and_wait(
                sock, width, height, framebuffer, digest, label, started,
                args.timeout, args.max_updates)
            completed += 1

        if args.right_click is not None:
            right_click = tuple(args.right_click)
            check_point(width, height, *right_click)
            vnc_live_click.request_update(sock, width, height, True)
            started = time.monotonic()
            send_pointer_click(sock, right_click, 4, args.click_hold)
            digest, _ = request_and_wait(
                sock, width, height, framebuffer, digest, "right-click",
                started, args.timeout, args.max_updates)
            completed += 1

        if args.left_click is not None:
            left_click = tuple(args.left_click)
            check_point(width, height, *left_click)
            vnc_live_click.request_update(sock, width, height, True)
            started = time.monotonic()
            send_pointer_click(sock, left_click, 1, args.click_hold)
            digest, _ = request_and_wait(
                sock, width, height, framebuffer, digest, "left-click",
                started, args.timeout, args.max_updates)
            completed += 1

        if args.double_click is not None:
            double_click = tuple(args.double_click)
            check_point(width, height, *double_click)
            vnc_live_click.request_update(sock, width, height, True)
            started = time.monotonic()
            send_pointer_double_click(
                sock, double_click, 1, args.click_hold)
            digest, _ = request_and_wait(
                sock, width, height, framebuffer, digest, "double-click",
                started, args.timeout, args.max_updates)
            completed += 1

        if args.move is not None:
            move = tuple(args.move)
            check_point(width, height, *move)
            vnc_live_click.request_update(sock, width, height, True)
            started = time.monotonic()
            sock.sendall(struct.pack(">BBHH", 5, 0, move[0], move[1]))
            digest, _ = request_and_wait(
                sock, width, height, framebuffer, digest, "move",
                started, args.timeout, args.max_updates)
            completed += 1

        if args.text is not None:
            if args.keyboard_burst:
                vnc_live_click.request_update(sock, width, height, True)
                started = time.monotonic()
                send_text(sock, args.text, args.key_delay,
                          send_return=not args.no_return)
                digest, _ = request_and_wait(
                    sock, width, height, framebuffer, digest, "keyboard",
                    started, args.timeout, args.max_updates)
            else:
                key_latencies = []
                keys = [(repr(character), ord(character))
                        for character in args.text]
                if not args.no_return:
                    keys.append(("Return", 0xFF0D))
                for key_index, (label, keysym) in enumerate(keys, 1):
                    vnc_live_click.request_update(
                        sock, width, height, True)
                    started = time.monotonic()
                    send_key(sock, keysym)
                    digest, latency = request_and_wait(
                        sock, width, height, framebuffer, digest,
                        f"key[{key_index}]={label}", started, args.timeout,
                        args.max_updates)
                    key_latencies.append(latency)
                    if args.key_delay:
                        time.sleep(args.key_delay)
                print(
                    f"KEYBOARD PASS keys={len(keys)} "
                    f"latency_min={min(key_latencies):.3f}s "
                    f"latency_max={max(key_latencies):.3f}s "
                    f"latency_avg={sum(key_latencies) / len(key_latencies):.3f}s",
                    flush=True)
            completed += 1

        if args.command_key is not None:
            if args.command_key.lower() == "tab":
                command_keysym = 0xff09
            elif len(args.command_key) == 1:
                command_keysym = ord(args.command_key.lower())
            else:
                parser.error(
                    "--command-key needs one character or the name Tab")
            vnc_live_click.request_update(sock, width, height, True)
            started = time.monotonic()
            # Runtime-confirmed against the installed OSXvnc key table:
            # XK_Alt_L maps to macOS Command (keyCode 55); Meta/Super maps to
            # Option. Using Meta here made this supposedly semantic helper
            # test Option+KEY instead of Command+KEY.
            send_key_chord(sock, (0xffe9,), command_keysym)
            digest, _ = request_and_wait(
                sock, width, height, framebuffer, digest, "command-key",
                started, args.timeout, args.max_updates)
            completed += 1

        digest = settle_and_drain(
            sock, width, height, framebuffer, digest,
            args.settle_seconds)
        if (args.text is not None and digest == initial_digest):
            raise RuntimeError(
                "keyboard ended on the initial framebuffer after settle; "
                "only transient cursor/input frames were observed")
        if args.output:
            vnc_live_click.save_frame(
                args.output, width, height, framebuffer, "final")
        print(f"INPUT PASS operations={completed} final_digest={digest}",
              flush=True)
    except (EOFError, RuntimeError, socket.timeout) as error:
        print(f"INPUT FAIL operations={completed} error={error}", flush=True)
        raise SystemExit(2) from error
    finally:
        sock.close()
        if args.trace_sentinel:
            try:
                os.unlink(args.trace_sentinel)
            except FileNotFoundError:
                pass


if __name__ == "__main__":
    main()

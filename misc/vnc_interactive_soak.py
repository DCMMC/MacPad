"""Send repeated input and verify refresh on one persistent RFB connection.

This is the long-running counterpart to vnc_live_click.py.  It deliberately
keeps one client connection and one retained framebuffer for the whole run, so
a reconnecting full-frame request cannot hide a stalled incremental session.
Use --require-click-region-radius when the control has a visible state change;
without it, application logs remain the witness that input actually landed.
"""

import argparse
import hashlib
import socket
import struct
import time

import vnc_capture
import vnc_live_click


def digest(framebuffer):
    return hashlib.sha256(framebuffer).hexdigest()


def region_digest(framebuffer, width, height, x, y, radius):
    if radius <= 0:
        return None
    left = max(0, x - radius)
    right = min(width, x + radius + 1)
    top = max(0, y - radius)
    bottom = min(height, y + radius + 1)
    hasher = hashlib.sha256()
    for row in range(top, bottom):
        start = (row * width + left) * 4
        end = (row * width + right) * 4
        hasher.update(framebuffer[start:end])
    return hasher.hexdigest()


def send_click(sock, x, y):
    sock.sendall(struct.pack(">BBHH", 5, 1, x, y))
    time.sleep(0.05)
    sock.sendall(struct.pack(">BBHH", 5, 0, x, y))


def wait_for_changed_frame(sock, width, height, framebuffer,
                           previous_digest, previous_region_digest,
                           click_x, click_y, region_radius, deadline,
                           max_updates):
    update_count = 0
    all_rectangles = []
    while time.monotonic() < deadline and update_count < max_updates:
        rectangles = vnc_live_click.receive_update(
            sock, width, framebuffer)
        update_count += 1
        all_rectangles.extend(rectangles)
        current_digest = digest(framebuffer)
        current_region_digest = region_digest(
            framebuffer, width, height, click_x, click_y, region_radius)
        region_changed = (region_radius <= 0 or
                          current_region_digest != previous_region_digest)
        if current_digest != previous_digest and region_changed:
            return (current_digest, current_region_digest, update_count,
                    all_rectangles)
        vnc_live_click.request_update(sock, width, height, True)
    raise RuntimeError(
        f"no changed frame after {update_count} updates; "
        f"rectangles={all_rectangles}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("--port", type=int, default=5900)
    parser.add_argument("--click", nargs=2, required=True, type=int,
                        metavar=("X", "Y"))
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--interval", type=float, default=15.0)
    parser.add_argument("--timeout", type=float, default=20.0,
                        help="maximum seconds to wait after each click")
    parser.add_argument("--max-updates", type=int, default=20)
    parser.add_argument("--require-click-region-radius", type=int, default=0,
                        metavar="PIXELS",
                        help="also require this radius around the click to "
                             "change; rejects unrelated animation updates")
    parser.add_argument("--save-last")
    args = parser.parse_args()

    if (args.iterations < 1 or args.interval < 0 or args.timeout <= 0 or
            args.require_click_region_radius < 0):
        parser.error("iterations/timeout must be positive; interval and "
                     "click-region radius must be nonnegative")

    sock, width, height, name = vnc_capture.connect_rfb(
        args.host, args.port, args.timeout)
    framebuffer = bytearray(width * height * 4)
    successes = 0
    latencies = []
    started = time.monotonic()
    try:
        vnc_live_click.configure_raw(sock)
        vnc_live_click.request_update(sock, width, height, False)
        initial_rectangles = vnc_live_click.receive_update(
            sock, width, framebuffer)
        previous_digest = digest(framebuffer)
        previous_region_digest = region_digest(
            framebuffer, width, height, args.click[0], args.click[1],
            args.require_click_region_radius)
        print(
            f"SOAK start name={name!r} size={width}x{height} "
            f"digest={previous_digest} initial={initial_rectangles}",
            flush=True)

        x, y = args.click
        if not (0 <= x < width and 0 <= y < height):
            raise ValueError(
                f"click ({x},{y}) outside RFB {width}x{height}")

        for iteration in range(1, args.iterations + 1):
            if iteration > 1 and args.interval:
                time.sleep(args.interval)
            vnc_live_click.request_update(sock, width, height, True)
            click_started = time.monotonic()
            send_click(sock, x, y)
            (current_digest, current_region_digest, updates,
             rectangles) = wait_for_changed_frame(
                sock, width, height, framebuffer, previous_digest,
                previous_region_digest, x, y,
                args.require_click_region_radius,
                click_started + args.timeout, args.max_updates)
            latency = time.monotonic() - click_started
            latencies.append(latency)
            successes += 1
            full_count = sum(
                encoding == 0 and x0 == 0 and y0 == 0 and
                rect_width == width and rect_height == height
                for x0, y0, rect_width, rect_height, encoding in rectangles)
            print(
                f"SOAK iteration={iteration}/{args.iterations} "
                f"latency={latency:.3f}s updates={updates} full={full_count} "
                f"digest={current_digest} "
                f"region={current_region_digest or 'unchecked'}", flush=True)
            previous_digest = current_digest
            previous_region_digest = current_region_digest

        elapsed = time.monotonic() - started
        pass_label = ("SOAK PASS" if args.require_click_region_radius > 0
                      else "SOAK FRAME PASS")
        print(
            f"{pass_label} successes={successes}/{args.iterations} "
            f"elapsed={elapsed:.1f}s latency_min={min(latencies):.3f}s "
            f"latency_max={max(latencies):.3f}s "
            f"latency_avg={sum(latencies) / len(latencies):.3f}s",
            flush=True)
        if args.save_last:
            vnc_live_click.save_frame(
                args.save_last, width, height, framebuffer, "last")
    except (RuntimeError, socket.timeout) as error:
        elapsed = time.monotonic() - started
        print(
            f"SOAK FAIL successes={successes}/{args.iterations} "
            f"elapsed={elapsed:.1f}s error={error}", flush=True)
        raise SystemExit(2) from error
    finally:
        sock.close()


if __name__ == "__main__":
    main()

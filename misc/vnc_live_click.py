"""Verify a click and framebuffer refresh on one persistent RFB connection.

Unlike vnc_capture.py's reconnecting --after mode, this client keeps the
initial framebuffer, applies every incremental rectangle to it, and succeeds
only after OSXvnc sends changed pixels following the click.  That catches a
frozen live session which a fresh full-frame request would hide while also
accepting the mmap producer's physical-resolution tile damage.
"""

import argparse
import hashlib
import select
import socket
import struct
import time
import zlib

import vnc_capture


def configure_encoding(sock, encoding):
    encoding_numbers = {
        "raw": 0,
        "hextile": 5,
        "zlib": 6,
    }
    if encoding not in encoding_numbers:
        raise ValueError(f"unsupported encoding {encoding!r}")
    pixel_format = struct.pack(
        ">BBBBHHHBBBxxx", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0)
    sock.sendall(b"\x00\x00\x00\x00" + pixel_format)
    sock.sendall(struct.pack(
        ">BBHi", 2, 0, 1, encoding_numbers[encoding]))
    # A zlib stream belongs to one RFB client connection. File descriptors are
    # routinely reused by the benchmark process, so retaining the decoder by
    # fd across configure/connect cycles feeds a new stream into stale zlib
    # state and makes otherwise valid rectangles undecodable.
    if encoding == "zlib":
        _zlib_decoders[sock.fileno()] = zlib.decompressobj()
    else:
        _zlib_decoders.pop(sock.fileno(), None)


def configure_raw(sock):
    configure_encoding(sock, "raw")


def configure_hextile(sock):
    configure_encoding(sock, "hextile")


def configure_zlib(sock):
    configure_encoding(sock, "zlib")


_zlib_decoders = {}


def request_update(sock, width, height, incremental):
    sock.sendall(struct.pack(
        ">BBHHHH", 3, 1 if incremental else 0, 0, 0, width, height))


def receive_update(sock, width, framebuffer):
    rectangles = []
    while True:
        message_type = vnc_capture.recv_exact(sock, 1)[0]
        if message_type == 0:
            vnc_capture.recv_exact(sock, 1)
            count = struct.unpack(
                ">H", vnc_capture.recv_exact(sock, 2))[0]
            for _ in range(count):
                x, y, rect_width, rect_height, encoding = struct.unpack(
                    ">HHHHi", vnc_capture.recv_exact(sock, 12))
                rectangles.append(
                    (x, y, rect_width, rect_height, encoding))
                if encoding == 0:
                    pixels = vnc_capture.recv_exact(
                        sock, rect_width * rect_height * 4)
                    row_bytes = rect_width * 4
                    for row in range(rect_height):
                        source = row * row_bytes
                        destination = ((y + row) * width + x) * 4
                        framebuffer[destination:destination + row_bytes] = \
                            pixels[source:source + row_bytes]
                elif encoding == 5:
                    receive_hextile_rectangle(
                        sock, width, framebuffer, x, y,
                        rect_width, rect_height)
                elif encoding == 6:
                    receive_zlib_rectangle(
                        sock, width, framebuffer, x, y,
                        rect_width, rect_height)
                elif encoding not in (-223, -224):
                    raise RuntimeError(
                        f"unexpected RFB encoding {encoding}")
            return rectangles
        if message_type == 2:
            continue
        if message_type == 3:
            vnc_capture.recv_exact(sock, 3)
            length = struct.unpack(
                ">I", vnc_capture.recv_exact(sock, 4))[0]
            vnc_capture.recv_exact(sock, length)
            continue
        raise RuntimeError(
            f"unexpected RFB server message {message_type}")


def receive_zlib_rectangle(sock, width, framebuffer, x, y,
                           rect_width, rect_height):
    """Decode LibVNCServer's persistent Zlib encoding stream."""
    compressed_length = struct.unpack(
        ">I", vnc_capture.recv_exact(sock, 4))[0]
    compressed = vnc_capture.recv_exact(sock, compressed_length)
    decoder = _zlib_decoders.setdefault(sock.fileno(), zlib.decompressobj())
    pixels = decoder.decompress(compressed)
    expected = rect_width * rect_height * 4
    if len(pixels) != expected:
        raise RuntimeError(
            f"zlib rectangle decoded {len(pixels)} bytes, expected {expected}")
    row_bytes = rect_width * 4
    for row in range(rect_height):
        source = row * row_bytes
        destination = ((y + row) * width + x) * 4
        framebuffer[destination:destination + row_bytes] = \
            pixels[source:source + row_bytes]


def receive_hextile_rectangle(sock, width, framebuffer, x, y,
                              rect_width, rect_height):
    """Decode RFB Hextile into the retained 32-bit BGRX framebuffer."""
    background = None
    foreground = None
    for tile_y in range(0, rect_height, 16):
        tile_height = min(16, rect_height - tile_y)
        for tile_x in range(0, rect_width, 16):
            tile_width = min(16, rect_width - tile_x)
            subencoding = vnc_capture.recv_exact(sock, 1)[0]
            if subencoding & 1:
                pixels = vnc_capture.recv_exact(
                    sock, tile_width * tile_height * 4)
                for row in range(tile_height):
                    source = row * tile_width * 4
                    destination = (
                        ((y + tile_y + row) * width + x + tile_x) * 4)
                    framebuffer[
                        destination:destination + tile_width * 4] = \
                        pixels[source:source + tile_width * 4]
                continue

            if subencoding & 2:
                background = vnc_capture.recv_exact(sock, 4)
            if background is None:
                raise RuntimeError("Hextile tile has no background colour")
            filled_row = background * tile_width
            for row in range(tile_height):
                destination = (
                    ((y + tile_y + row) * width + x + tile_x) * 4)
                framebuffer[
                    destination:destination + tile_width * 4] = filled_row

            if subencoding & 4:
                foreground = vnc_capture.recv_exact(sock, 4)
            if not (subencoding & 8):
                continue
            subrect_count = vnc_capture.recv_exact(sock, 1)[0]
            coloured = bool(subencoding & 16)
            for _ in range(subrect_count):
                colour = (vnc_capture.recv_exact(sock, 4)
                          if coloured else foreground)
                if colour is None:
                    raise RuntimeError(
                        "Hextile subrectangle has no foreground colour")
                packed_xy, packed_wh = vnc_capture.recv_exact(sock, 2)
                sub_x = packed_xy >> 4
                sub_y = packed_xy & 0x0F
                sub_width = (packed_wh >> 4) + 1
                sub_height = (packed_wh & 0x0F) + 1
                if (sub_x + sub_width > tile_width or
                        sub_y + sub_height > tile_height):
                    raise RuntimeError("Hextile subrectangle outside tile")
                sub_row = colour * sub_width
                for row in range(sub_height):
                    destination = (
                        ((y + tile_y + sub_y + row) * width +
                         x + tile_x + sub_x) * 4)
                    framebuffer[
                        destination:destination + sub_width * 4] = sub_row


def save_frame(path, width, height, framebuffer, prefix):
    rgba = bytearray(len(framebuffer))
    nonblack = 0
    for offset in range(0, len(framebuffer), 4):
        blue, green, red = framebuffer[offset:offset + 3]
        rgba[offset:offset + 4] = bytes((red, green, blue, 255))
        if red or green or blue:
            nonblack += 1
    vnc_capture.write_rgba_png(path, width, height, rgba)
    digest = hashlib.sha256(framebuffer).hexdigest()
    print(f"{prefix} bgrx_sha256={digest} "
          f"nonblack={nonblack}/{width * height} wrote={path}")
    return digest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("before")
    parser.add_argument("after")
    parser.add_argument("--port", type=int, default=5900)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--settle-seconds", type=float, default=1.5,
                        help="retain later incremental frames after the first "
                             "changed update (default: 1.5)")
    parser.add_argument("--click", nargs=2, required=True, type=int,
                        metavar=("X", "Y"))
    parser.add_argument("--button", choices=("left", "right"),
                        default="left")
    parser.add_argument("--control", action="store_true",
                        help="hold Control across the pointer transition")
    parser.add_argument("--shift", action="store_true",
                        help="hold Shift across the pointer transition; "
                             "macOS uses this for slow window animations")
    parser.add_argument("--hold-seconds", type=float, default=0.05)
    parser.add_argument(
        "--pre-hover-seconds", type=float, default=0.0,
        help=("move the server cursor to the click point and wait before "
              "pressing; useful for native hover-expanded controls"))
    parser.add_argument("--click-count", type=int, choices=(1, 2), default=1,
                        help="send one click or a native two-click sequence "
                             "on the same RFB connection")
    parser.add_argument("--inter-click-seconds", type=float, default=0.10,
                        help="delay between clicks when --click-count=2")
    parser.add_argument("--max-updates", type=int, default=12)
    args = parser.parse_args()
    if (args.timeout <= 0 or args.settle_seconds < 0 or
            args.hold_seconds < 0 or args.pre_hover_seconds < 0 or
            args.inter_click_seconds < 0 or
            args.max_updates < 1):
        parser.error("timeout/max-updates must be positive and settle nonnegative")

    sock, width, height, name = vnc_capture.connect_rfb(
        args.host, args.port, args.timeout)
    framebuffer = bytearray(width * height * 4)
    try:
        configure_raw(sock)
        request_update(sock, width, height, False)
        initial_rectangles = receive_update(sock, width, framebuffer)
        print(f"RFB name={name!r} size={width}x{height} "
              f"initial_rectangles={initial_rectangles}")
        before_digest = save_frame(
            args.before, width, height, framebuffer, "before")

        x, y = args.click
        if not (0 <= x < width and 0 <= y < height):
            raise ValueError(
                f"click ({x},{y}) outside RFB {width}x{height}")

        # Match a normal viewer: keep an incremental request outstanding while
        # sending the pointer transition, then request the next incremental
        # update after each response.
        request_update(sock, width, height, True)
        time.sleep(0.1)
        if args.pre_hover_seconds > 0:
            sock.sendall(struct.pack(">BBHH", 5, 0, x, y))
            time.sleep(args.pre_hover_seconds)
        if args.control:
            sock.sendall(struct.pack(">BBxxI", 4, 1, 0xFFE3))
            time.sleep(0.02)
        if args.shift:
            sock.sendall(struct.pack(">BBxxI", 4, 1, 0xFFE1))
            time.sleep(0.02)
        button_mask = 1 if args.button == "left" else 4
        for click_index in range(args.click_count):
            sock.sendall(struct.pack(">BBHH", 5, button_mask, x, y))
            time.sleep(args.hold_seconds)
            sock.sendall(struct.pack(">BBHH", 5, 0, x, y))
            if click_index + 1 < args.click_count:
                time.sleep(args.inter_click_seconds)
        if args.control:
            time.sleep(0.02)
            sock.sendall(struct.pack(">BBxxI", 4, 0, 0xFFE3))
        if args.shift:
            time.sleep(0.02)
            sock.sendall(struct.pack(">BBxxI", 4, 0, 0xFFE1))

        deadline = time.monotonic() + args.timeout
        changed_update = False
        update_count = 0
        while (time.monotonic() < deadline and
               update_count < args.max_updates):
            rectangles = receive_update(sock, width, framebuffer)
            update_count += 1
            print(f"incremental[{update_count}]={rectangles}")
            changed_update = (
                hashlib.sha256(framebuffer).hexdigest() != before_digest)
            if changed_update:
                break
            print("dirty rectangles were unchanged; waiting for the next "
                  "committed generation")
            request_update(sock, width, height, True)

        # A cursor tile is often the first response to a click. Saving it and
        # closing immediately concealed whether the target control repainted.
        # Keep the same RFB framebuffer and retain every later complete update
        # during a quiet interval. select() avoids timing out in the middle of
        # an RFB rectangle and corrupting the stream.
        settled_updates = 0
        settle_changes = 0
        if changed_update and args.settle_seconds > 0:
            settle_digest = hashlib.sha256(framebuffer).hexdigest()
            settle_deadline = time.monotonic() + args.settle_seconds
            request_update(sock, width, height, True)
            while update_count < args.max_updates:
                remaining = settle_deadline - time.monotonic()
                if remaining <= 0:
                    break
                readable, _, _ = select.select([sock], [], [], remaining)
                if not readable:
                    break
                rectangles = receive_update(sock, width, framebuffer)
                update_count += 1
                settled_updates += 1
                current_digest = hashlib.sha256(framebuffer).hexdigest()
                if current_digest != settle_digest:
                    settle_changes += 1
                    settle_digest = current_digest
                if time.monotonic() < settle_deadline:
                    request_update(sock, width, height, True)
            print(
                f"settle seconds={args.settle_seconds:.3f} "
                f"updates={settled_updates} changed={settle_changes}")

        after_digest = save_frame(
            args.after, width, height, framebuffer, "after")
        changed = before_digest != after_digest
        print(f"result changed_update={changed_update} changed={changed} "
              f"updates={update_count}")
        if not changed_update or not changed:
            raise SystemExit(2)
    except socket.timeout as error:
        print(f"result timeout={error}")
        raise SystemExit(2) from error
    finally:
        sock.close()


if __name__ == "__main__":
    main()

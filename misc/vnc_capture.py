"""Capture one raw RFB framebuffer update and save it as a PNG.

This intentionally uses only the Python standard library.  Run it as:

    python3 misc/vnc_capture.py 192.168.1.6 /tmp/macws.png

The client requests a fixed little-endian 32-bit BGRX pixel format and raw
encoding, which keeps the capture path simple enough to audit while debugging
WindowServer output delivery.
"""

import argparse
import binascii
import hashlib
import socket
import struct
import time
import zlib


def recv_exact(sock, size):
    chunks = []
    remaining = size
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise EOFError(f"RFB peer closed with {remaining} bytes pending")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def png_chunk(kind, data):
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(
        ">I", binascii.crc32(body) & 0xFFFFFFFF)


def write_rgba_png(path, width, height, rgba):
    rows = bytearray()
    stride = width * 4
    for y in range(height):
        rows.append(0)  # PNG filter: None
        rows.extend(rgba[y * stride:(y + 1) * stride])

    png = bytearray(b"\x89PNG\r\n\x1a\n")
    png.extend(png_chunk(
        b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)))
    png.extend(png_chunk(b"IDAT", zlib.compress(bytes(rows), 6)))
    png.extend(png_chunk(b"IEND", b""))
    with open(path, "wb") as output:
        output.write(png)


def security_handshake(sock, server_version):
    major, minor = (int(part) for part in
                    server_version[4:11].decode("ascii").split("."))
    if (major, minor) >= (3, 7):
        count = recv_exact(sock, 1)[0]
        if count == 0:
            length = struct.unpack(">I", recv_exact(sock, 4))[0]
            raise RuntimeError(
                recv_exact(sock, length).decode("utf-8", "replace"))
        types = recv_exact(sock, count)
        if 1 not in types:
            raise RuntimeError(
                f"RFB server does not offer None security: {list(types)}")
        sock.sendall(b"\x01")
        result = struct.unpack(">I", recv_exact(sock, 4))[0]
        if result:
            length = struct.unpack(">I", recv_exact(sock, 4))[0]
            reason = recv_exact(sock, length).decode("utf-8", "replace")
            raise RuntimeError(f"RFB security failed ({result}): {reason}")
    else:
        security_type = struct.unpack(">I", recv_exact(sock, 4))[0]
        if security_type != 1:
            raise RuntimeError(
                "RFB 3.3 server selected unsupported security type "
                f"{security_type}")


def connect_rfb(host, port, timeout):
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)
    server_version = recv_exact(sock, 12)
    if not server_version.startswith(b"RFB "):
        sock.close()
        raise RuntimeError(f"invalid RFB version: {server_version!r}")
    sock.sendall(b"RFB 003.008\n")
    security_handshake(sock, server_version)
    sock.sendall(b"\x01")  # ClientInit: shared session
    init = recv_exact(sock, 24)
    width, height = struct.unpack(">HH", init[:4])
    name_length = struct.unpack(">I", init[20:24])[0]
    name = recv_exact(sock, name_length).decode("utf-8", "replace")
    return sock, width, height, name


def click(host, port, timeout, x, y):
    sock, width, height, _ = connect_rfb(host, port, timeout)
    try:
        if not (0 <= x < width and 0 <= y < height):
            raise ValueError(f"click ({x},{y}) outside RFB {width}x{height}")
        # RFB PointerEvent: type, button-mask, x-position, y-position.
        sock.sendall(struct.pack(">BBHH", 5, 1, x, y))
        time.sleep(0.05)
        sock.sendall(struct.pack(">BBHH", 5, 0, x, y))
    finally:
        sock.close()


def capture(host, port, timeout):
    sock, width, height, name = connect_rfb(host, port, timeout)
    with sock:
        # SetPixelFormat: little-endian 32bpp true-colour, R@16 G@8 B@0.
        pixel_format = struct.pack(
            ">BBBBHHHBBBxxx", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0)
        sock.sendall(b"\x00\x00\x00\x00" + pixel_format)
        # SetEncodings: request only raw encoding (0).
        sock.sendall(struct.pack(">BBHi", 2, 0, 1, 0))
        # Full, non-incremental FramebufferUpdateRequest.
        sock.sendall(struct.pack(">BBHHHH", 3, 0, 0, 0, width, height))

        bgra = bytearray(width * height * 4)
        raw_rectangles = 0
        while raw_rectangles == 0:
            message_type = recv_exact(sock, 1)[0]
            if message_type == 0:  # FramebufferUpdate
                recv_exact(sock, 1)
                rectangles = struct.unpack(">H", recv_exact(sock, 2))[0]
                for _ in range(rectangles):
                    x, y, rect_w, rect_h, encoding = struct.unpack(
                        ">HHHHi", recv_exact(sock, 12))
                    if encoding == 0:
                        pixels = recv_exact(sock, rect_w * rect_h * 4)
                        row_bytes = rect_w * 4
                        for row in range(rect_h):
                            source = row * row_bytes
                            destination = ((y + row) * width + x) * 4
                            bgra[destination:destination + row_bytes] = \
                                pixels[source:source + row_bytes]
                        raw_rectangles += 1
                    elif encoding in (-223, -224):
                        # DesktopSize and LastRect carry no pixel data.
                        continue
                    else:
                        raise RuntimeError(
                            f"unexpected RFB encoding {encoding}")
            elif message_type == 2:  # Bell
                continue
            elif message_type == 3:  # ServerCutText
                recv_exact(sock, 3)
                length = struct.unpack(">I", recv_exact(sock, 4))[0]
                recv_exact(sock, length)
            else:
                raise RuntimeError(
                    f"unexpected RFB server message {message_type}")

    rgba = bytearray(len(bgra))
    nonblack = 0
    for offset in range(0, len(bgra), 4):
        blue, green, red = bgra[offset:offset + 3]
        rgba[offset:offset + 4] = bytes((red, green, blue, 255))
        if red or green or blue:
            nonblack += 1

    return width, height, name, rgba, nonblack, bytes(bgra)


def save_capture(png_path, raw_path, capture_result, prefix=""):
    width, height, name, rgba, nonblack, bgra = capture_result
    write_rgba_png(png_path, width, height, rgba)
    pixels = width * height
    print(f"{prefix}RFB name={name!r} size={width}x{height} "
          f"rgba_sha256={hashlib.sha256(rgba).hexdigest()} "
          f"bgrx_sha256={hashlib.sha256(bgra).hexdigest()}")
    print(f"{prefix}nonblack_pixels={nonblack}/{pixels} "
          f"({100.0 * nonblack / pixels:.3f}%)")
    print(f"{prefix}wrote {png_path}")
    if raw_path:
        with open(raw_path, "wb") as output:
            output.write(bgra)
        print(f"{prefix}wrote raw BGRX {raw_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("output")
    parser.add_argument("--port", type=int, default=5900)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--raw", help="also save the initial raw BGRX bytes")
    parser.add_argument("--click", nargs=2, type=int, metavar=("X", "Y"),
                        help="send a left-button down/up after the first capture")
    parser.add_argument("--after",
                        help="capture this PNG after --click and a short settle")
    parser.add_argument("--after-raw",
                        help="also save the post-click raw BGRX bytes")
    parser.add_argument("--settle", type=float, default=2.0)
    args = parser.parse_args()

    result = capture(args.host, args.port, args.timeout)
    save_capture(args.output, args.raw, result)
    if args.click:
        click(args.host, args.port, args.timeout, *args.click)
        print(f"sent left click at ({args.click[0]},{args.click[1]})")
        time.sleep(args.settle)
        if args.after:
            result = capture(args.host, args.port, args.timeout)
            save_capture(args.after, args.after_raw, result, prefix="after ")


if __name__ == "__main__":
    main()

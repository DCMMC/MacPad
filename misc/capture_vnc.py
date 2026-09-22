"""Capture one raw RFB framebuffer update as a PNG.

This intentionally implements only the protocol subset used by the project's
passwordless OSXvnc job: RFB 3.8, security type None, true-colour BGRA32, and
Raw rectangles.  It has no third-party dependencies.
"""

import argparse
import socket
import struct
import zlib


def receive_exact(sock, length):
    chunks = []
    remaining = length
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise EOFError(f"VNC connection closed with {remaining} bytes pending")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def png_chunk(kind, payload):
    body = kind + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))


def write_bgra_png(path, width, height, bgra):
    expected = width * height * 4
    if len(bgra) != expected:
        raise ValueError(f"expected {expected} BGRA bytes, received {len(bgra)}")

    scanlines = bytearray()
    stride = width * 4
    for y in range(height):
        scanlines.append(0)
        row = bgra[y * stride:(y + 1) * stride]
        for x in range(0, stride, 4):
            scanlines.extend((row[x + 2], row[x + 1], row[x], 255))

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    with open(path, "wb") as output:
        output.write(b"\x89PNG\r\n\x1a\n")
        output.write(png_chunk(b"IHDR", header))
        output.write(png_chunk(b"IDAT", zlib.compress(scanlines, 6)))
        output.write(png_chunk(b"IEND", b""))


def capture(host, port, output_path, timeout):
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        server_version = receive_exact(sock, 12)
        if not server_version.startswith(b"RFB 003."):
            raise RuntimeError(f"unsupported RFB banner: {server_version!r}")
        sock.sendall(b"RFB 003.008\n")

        security_count = receive_exact(sock, 1)[0]
        if security_count == 0:
            reason_length = struct.unpack(">I", receive_exact(sock, 4))[0]
            reason = receive_exact(sock, reason_length).decode("utf-8", "replace")
            raise RuntimeError(f"VNC security negotiation failed: {reason}")
        security_types = receive_exact(sock, security_count)
        if 1 not in security_types:
            raise RuntimeError(f"server does not offer security type None: {security_types!r}")
        sock.sendall(b"\x01")
        security_result = struct.unpack(">I", receive_exact(sock, 4))[0]
        if security_result:
            raise RuntimeError(f"VNC security result {security_result}")

        sock.sendall(b"\x01")  # shared desktop
        server_init = receive_exact(sock, 24)
        width, height = struct.unpack(">HH", server_init[:4])
        name_length = struct.unpack(">I", server_init[20:24])[0]
        name = receive_exact(sock, name_length).decode("utf-8", "replace")

        pixel_format = struct.pack(
            ">BBBBHHHBBB3x", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0
        )
        sock.sendall(b"\x00\x00\x00\x00" + pixel_format)
        sock.sendall(struct.pack(">BBHi", 2, 0, 1, 0))  # Raw encoding only
        sock.sendall(struct.pack(">BBHHHH", 3, 0, 0, 0, width, height))

        framebuffer = bytearray(width * height * 4)
        rectangles_seen = 0
        while rectangles_seen == 0:
            message_type = receive_exact(sock, 1)[0]
            if message_type == 0:
                _, rectangle_count = struct.unpack(">BH", receive_exact(sock, 3))
                for _ in range(rectangle_count):
                    x, y, rect_width, rect_height, encoding = struct.unpack(
                        ">HHHHi", receive_exact(sock, 12)
                    )
                    if encoding != 0:
                        raise RuntimeError(f"server returned non-Raw encoding {encoding}")
                    pixels = receive_exact(sock, rect_width * rect_height * 4)
                    source_stride = rect_width * 4
                    destination_stride = width * 4
                    for row in range(rect_height):
                        source = row * source_stride
                        destination = (y + row) * destination_stride + x * 4
                        framebuffer[destination:destination + source_stride] = (
                            pixels[source:source + source_stride]
                        )
                    rectangles_seen += 1
            elif message_type == 2:  # Bell
                continue
            elif message_type == 3:  # ServerCutText
                header = receive_exact(sock, 7)
                text_length = struct.unpack(">I", header[3:])[0]
                receive_exact(sock, text_length)
            else:
                raise RuntimeError(f"unsupported VNC server message {message_type}")

    write_bgra_png(output_path, width, height, framebuffer)
    return width, height, name, rectangles_seen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("output")
    parser.add_argument("--port", type=int, default=5900)
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args()
    width, height, name, rectangles = capture(
        args.host, args.port, args.output, args.timeout
    )
    print(f"captured {width}x{height} from {name!r} in {rectangles} raw rectangle(s)")


if __name__ == "__main__":
    main()

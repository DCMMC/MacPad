"""Convert a MacWSFrameHeader + BGRA8 shared frame to a standard PNG.

Invoke explicitly with Python (there is intentionally no shebang):
    python3 misc/macws_frame_to_png.py macws_vnc_fb frame.png
"""

import binascii
import struct
import sys
import zlib


MAGIC = 0x564E4346


def png_chunk(kind: bytes, payload: bytes) -> bytes:
    body = kind + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(
        ">I", binascii.crc32(body) & 0xFFFFFFFF
    )


def main() -> int:
    if len(sys.argv) != 3:
        print(f"usage: python3 {sys.argv[0]} INPUT_FRAME OUTPUT_PNG", file=sys.stderr)
        return 2

    with open(sys.argv[1], "rb") as source:
        header = source.read(16)
        if len(header) != 16:
            raise ValueError("frame header is incomplete")
        magic, width, height, stride = struct.unpack("<IIII", header)
        if magic != MAGIC or not width or not height or stride < width * 4:
            raise ValueError(
                f"invalid frame header magic={magic:#x} size={width}x{height} stride={stride}"
            )
        rows = bytearray()
        for _ in range(height):
            bgra = bytearray(source.read(stride))
            if len(bgra) != stride:
                raise ValueError("frame payload is incomplete")
            rgba = bgra[: width * 4]
            blue = rgba[0::4]
            rgba[0::4] = rgba[2::4]
            rgba[2::4] = blue
            rows.append(0)  # PNG filter: None
            rows.extend(rgba)

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    png = signature + png_chunk(b"IHDR", ihdr)
    png += png_chunk(b"IDAT", zlib.compress(rows, level=6))
    png += png_chunk(b"IEND", b"")
    with open(sys.argv[2], "wb") as destination:
        destination.write(png)
    print(f"wrote {sys.argv[2]}: {width}x{height} RGBA")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

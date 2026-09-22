"""Decode bounded Stray Metal render-target captures on the development Mac.

The runtime writes a one-line ASCII header followed by the exact padded Metal
buffer rows.  This tool never runs in the game process and never modifies a
capture.  It emits viewable PNGs plus numeric summaries so an intermediate
attachment can be compared with the final macPad/WindowServer witnesses from
the same automated run.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import pathlib
import re
import struct
import subprocess


HEADER_RE = re.compile(r"([A-Za-z]+)=([^ ]+)")


def unsigned_float(value: int, mantissa_bits: int):
    mantissa_mask = (1 << mantissa_bits) - 1
    mantissa = value & mantissa_mask
    exponent = (value >> mantissa_bits) & 0x1F
    if exponent == 0:
        return math.ldexp(float(mantissa), -14 - mantissa_bits)
    if exponent == 0x1F:
        return math.inf if mantissa == 0 else math.nan
    return math.ldexp(1.0 + mantissa / (1 << mantissa_bits), exponent - 15)


def display_byte(value: float, hdr: bool):
    if not math.isfinite(value) or value <= 0.0:
        return 0
    if hdr:
        value = value / (1.0 + value)
        value = pow(min(1.0, value), 1.0 / 2.2)
    return round(min(1.0, value) * 255.0)


def decode_color(pixel_format: int, row: bytes, x: int):
    if pixel_format in {80, 81}:  # BGRA8Unorm / BGRA8Unorm_sRGB
        blue, green, red, _alpha = row[x * 4:x * 4 + 4]
        return red / 255.0, green / 255.0, blue / 255.0, False
    if pixel_format == 90:  # RGB10A2Unorm
        packed = struct.unpack_from("<I", row, x * 4)[0]
        return (
            (packed & 0x3FF) / 1023.0,
            ((packed >> 10) & 0x3FF) / 1023.0,
            ((packed >> 20) & 0x3FF) / 1023.0,
            False,
        )
    if pixel_format == 92:  # RG11B10Float
        packed = struct.unpack_from("<I", row, x * 4)[0]
        return (
            unsigned_float(packed & 0x7FF, 6),
            unsigned_float((packed >> 11) & 0x7FF, 6),
            unsigned_float((packed >> 22) & 0x3FF, 5),
            True,
        )
    if pixel_format == 115:  # RGBA16Float
        red, green, blue, _alpha = struct.unpack_from("<eeee", row, x * 8)
        return float(red), float(green), float(blue), True
    if pixel_format == 125:  # RGBA32Float
        red, green, blue, _alpha = struct.unpack_from("<ffff", row, x * 16)
        return red, green, blue, True
    if pixel_format == 10:  # R8Unorm
        value = row[x] / 255.0
        return value, value, value, False
    if pixel_format == 25:  # R16Float
        value = struct.unpack_from("<e", row, x * 2)[0]
        return float(value), float(value), float(value), True
    if pixel_format == 30:  # RG8Unorm
        red, green = row[x * 2:x * 2 + 2]
        return red / 255.0, green / 255.0, 0.0, False
    if pixel_format == 65:  # RG16Float
        red, green = struct.unpack_from("<ee", row, x * 4)
        return float(red), float(green), 0.0, True
    if pixel_format == 250:  # Depth16Unorm
        value = struct.unpack_from("<H", row, x * 2)[0] / 65535.0
        return value, value, value, False
    if pixel_format in {252, 260, 261}:  # float depth aspect
        value = struct.unpack_from("<f", row, x * 4)[0]
        return value, value, value, False
    return None


def parse_capture(path: pathlib.Path):
    with path.open("rb") as source:
        header = source.readline(1024).decode("ascii", "replace").strip()
        payload = source.read()
    if not header.startswith("MACWSRT "):
        return {"path": str(path), "kind": "buffer", "header": header}
    values = {key: value for key, value in HEADER_RE.findall(header)}
    width = int(values["w"], 0)
    height = int(values["h"], 0)
    pixel_format = int(values["pf"], 0)
    bytes_per_row = int(values["bpr"], 0)
    declared_length = int(values["length"], 0)
    if (width <= 0 or height <= 0 or bytes_per_row <= 0 or
            declared_length != len(payload) or
            bytes_per_row * height != len(payload)):
        raise RuntimeError(f"invalid capture geometry/header: {path}: {header}")
    return {
        "path": str(path),
        "kind": "texture",
        "header": header,
        "width": width,
        "height": height,
        "pixel_format": pixel_format,
        "attachment": int(values.get("attachment", "0"), 0),
        "bytes_per_row": bytes_per_row,
        "payload": payload,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def write_color_preview(capture: dict, output: pathlib.Path):
    width = capture["width"]
    height = capture["height"]
    pixel_format = capture["pixel_format"]
    bytes_per_row = capture["bytes_per_row"]
    payload = capture.pop("payload")
    ppm = output.with_suffix(".ppm")
    minimum = [math.inf, math.inf, math.inf]
    maximum = [-math.inf, -math.inf, -math.inf]
    nonfinite = 0
    nonfinite_pixels = 0
    nonfinite_minimum = [width, height]
    nonfinite_maximum = [-1, -1]
    nonfinite_packed = collections.Counter()
    with ppm.open("wb") as destination:
        destination.write(f"P6\n{width} {height}\n255\n".encode("ascii"))
        for y in range(height):
            source_row = payload[y * bytes_per_row:(y + 1) * bytes_per_row]
            target_row = bytearray(width * 3)
            if pixel_format in {80, 81}:
                packed = source_row[:width * 4]
                red = packed[2::4]
                green = packed[1::4]
                blue = packed[0::4]
                target_row[0::3] = red
                target_row[1::3] = green
                target_row[2::3] = blue
                channels = (red, green, blue)
                for channel, values in enumerate(channels):
                    minimum[channel] = min(minimum[channel], min(values) / 255.0)
                    maximum[channel] = max(maximum[channel], max(values) / 255.0)
                destination.write(target_row)
                continue
            for x in range(width):
                decoded = decode_color(pixel_format, source_row, x)
                if decoded is None:
                    ppm.unlink(missing_ok=True)
                    return None
                red, green, blue, hdr = decoded
                channels = (red, green, blue)
                pixel_nonfinite = False
                for channel, value in enumerate(channels):
                    if math.isfinite(value):
                        minimum[channel] = min(minimum[channel], value)
                        maximum[channel] = max(maximum[channel], value)
                    else:
                        nonfinite += 1
                        pixel_nonfinite = True
                    target_row[x * 3 + channel] = display_byte(value, hdr)
                if pixel_nonfinite:
                    nonfinite_pixels += 1
                    nonfinite_minimum[0] = min(nonfinite_minimum[0], x)
                    nonfinite_minimum[1] = min(nonfinite_minimum[1], y)
                    nonfinite_maximum[0] = max(nonfinite_maximum[0], x)
                    nonfinite_maximum[1] = max(nonfinite_maximum[1], y)
                    if pixel_format == 92:
                        packed = struct.unpack_from("<I", source_row, x * 4)[0]
                        nonfinite_packed[f"0x{packed:08x}"] += 1
            destination.write(target_row)
    converted = subprocess.run(
        ["/usr/bin/sips", "-s", "format", "png", str(ppm),
         "--out", str(output)],
        capture_output=True, text=True, timeout=60,
    )
    ppm.unlink(missing_ok=True)
    if converted.returncode:
        raise RuntimeError(
            f"sips failed for {capture['path']}: {converted.stderr.strip()}"
        )
    return {
        "preview": str(output),
        "preview_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "channel_minimum": [None if value == math.inf else value
                            for value in minimum],
        "channel_maximum": [None if value == -math.inf else value
                            for value in maximum],
        "nonfinite_channel_values": nonfinite,
        "nonfinite_pixels": nonfinite_pixels,
        "nonfinite_bounds": ({
            "x": nonfinite_minimum[0],
            "y": nonfinite_minimum[1],
            "width": nonfinite_maximum[0] - nonfinite_minimum[0] + 1,
            "height": nonfinite_maximum[1] - nonfinite_minimum[1] + 1,
        } if nonfinite_pixels else None),
        "nonfinite_packed_values": dict(nonfinite_packed.most_common(16)),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", type=pathlib.Path)
    parser.add_argument("--output", type=pathlib.Path)
    args = parser.parse_args()
    output = args.output or args.captures / "decoded"
    output.mkdir(parents=True, exist_ok=True)
    report = {"captures": str(args.captures), "output": str(output),
              "files": []}
    for path in sorted(args.captures.glob("macws_stray_*.raw")):
        capture = parse_capture(path)
        payload = capture.get("payload")
        if capture["kind"] == "texture" and payload is not None:
            preview = write_color_preview(
                capture, output / (path.stem + ".png")
            )
            capture["preview"] = preview
        capture.pop("payload", None)
        report["files"].append(capture)
    report_path = output / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps({
        "report": str(report_path),
        "files": len(report["files"]),
        "previews": sum(bool(item.get("preview"))
                        for item in report["files"]),
    }))


if __name__ == "__main__":
    main()

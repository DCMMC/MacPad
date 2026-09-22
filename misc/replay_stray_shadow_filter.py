"""Replay Stray's captured shadow-filter AIR math and compare NaN masks.

This is an offline causal diagnostic for the exact
Main_00000f37_fead035c function recovered from the captured metallib.  It
does not patch the shader or participate in the runtime translation path.
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import re
import struct


HEADER_RE = re.compile(r"([A-Za-z]+)=([^ ]+)")


def read_capture(path: pathlib.Path):
    with path.open("rb") as source:
        header = source.readline(1024).decode("ascii", "replace").strip()
        payload = source.read()
    values = {key: value for key, value in HEADER_RE.findall(header)}
    return header, values, payload


def find_one(directory: pathlib.Path, pattern: str):
    matches = sorted(directory.glob(pattern))
    if len(matches) != 1:
        raise RuntimeError(
            f"expected exactly one {pattern!r} in {directory}, got {matches}"
        )
    return matches[0]


def texture(directory: pathlib.Path, pattern: str):
    path = find_one(directory, pattern)
    header, values, payload = read_capture(path)
    width = int(values["w"], 0)
    height = int(values["h"], 0)
    bytes_per_row = int(values["bpr"], 0)
    if len(payload) != bytes_per_row * height:
        raise RuntimeError(f"invalid payload length: {path}: {header}")
    return {
        "path": str(path),
        "width": width,
        "height": height,
        "pixel_format": int(values["pf"], 0),
        "bytes_per_row": bytes_per_row,
        "payload": payload,
    }


def buffer(directory: pathlib.Path, pattern: str):
    path = find_one(directory, pattern)
    header, values, payload = read_capture(path)
    if len(payload) != int(values["length"], 0):
        raise RuntimeError(f"invalid buffer length: {path}: {header}")
    return {"path": str(path), "payload": payload}


def sample_r32(texture_capture: dict, x: int, y: int):
    offset = y * texture_capture["bytes_per_row"] + x * 4
    return struct.unpack_from("<f", texture_capture["payload"], offset)[0]


def sample_rgb10a2(texture_capture: dict, x: int, y: int):
    offset = y * texture_capture["bytes_per_row"] + x * 4
    packed = struct.unpack_from("<I", texture_capture["payload"], offset)[0]
    return (
        (packed & 0x3FF) / 1023.0,
        ((packed >> 10) & 0x3FF) / 1023.0,
        ((packed >> 20) & 0x3FF) / 1023.0,
        ((packed >> 30) & 0x3) / 3.0,
    )


def sample_rg16f(texture_capture: dict, x: int, y: int):
    offset = y * texture_capture["bytes_per_row"] + x * 4
    red, green = struct.unpack_from("<ee", texture_capture["payload"], offset)
    return float(red), float(green)


def sample_rg11b10f_nonfinite(texture_capture: dict, x: int, y: int):
    offset = y * texture_capture["bytes_per_row"] + x * 4
    packed = struct.unpack_from("<I", texture_capture["payload"], offset)[0]
    # Unsigned 11/11/10 float has an all-ones five-bit exponent in each
    # channel for Inf/NaN, just like the corresponding IEEE encoding.
    return (
        ((packed >> 6) & 0x1F) == 0x1F or
        ((packed >> 17) & 0x1F) == 0x1F or
        ((packed >> 27) & 0x1F) == 0x1F
    )


def ieee_div(numerator: float, denominator: float):
    if math.isnan(numerator) or math.isnan(denominator):
        return math.nan
    if denominator == 0.0:
        if numerator == 0.0:
            return math.nan
        sign = math.copysign(1.0, numerator) * math.copysign(1.0, denominator)
        return math.copysign(math.inf, sign)
    return numerator / denominator


def bounds(points: set[tuple[int, int]]):
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return {
        "x": min(xs), "y": min(ys),
        "width": max(xs) - min(xs) + 1,
        "height": max(ys) - min(ys) + 1,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", type=pathlib.Path)
    parser.add_argument("--output", type=pathlib.Path)
    args = parser.parse_args()

    depth = texture(args.captures, "macws_stray_input_t00_pf260.raw")
    gbuffer = texture(args.captures, "macws_stray_input_t01_pf90.raw")
    shadow = texture(args.captures, "macws_stray_input_t02_pf65.raw")
    output = texture(args.captures, "macws_stray_stage_001.raw")
    view = buffer(args.captures, "macws_stray_input_b00.raw")["payload"]
    globals_data = buffer(
        args.captures, "macws_stray_input_inline_f01.raw"
    )["payload"]

    if depth["pixel_format"] != 260 or gbuffer["pixel_format"] != 90:
        raise RuntimeError("unexpected depth/GBuffer pixel format")
    if shadow["pixel_format"] != 65 or output["pixel_format"] != 92:
        raise RuntimeError("unexpected shadow/output pixel format")
    if (depth["width"], depth["height"]) != (
            output["width"], output["height"]):
        raise RuntimeError("depth and output geometry differ")

    inv_device_z = struct.unpack_from("<4f", view, 1040)
    buffer_size_inv = struct.unpack_from("<4f", view, 2112)
    shadow_intensity = struct.unpack_from("<f", view, 3140)[0]
    scissor = struct.unpack_from("<4I", globals_data, 0)
    output_to_attenuation = struct.unpack_from("<f", globals_data, 16)[0]
    half_width = math.floor(buffer_size_inv[0] * 0.5)
    half_height = math.floor(buffer_size_inv[1] * 0.5)
    if (half_width, half_height) != (shadow["width"], shadow["height"]):
        raise RuntimeError("AIR half-size does not match captured shadow texture")

    source_nonfinite: set[tuple[int, int]] = set()
    for y in range(shadow["height"]):
        for x in range(shadow["width"]):
            red, green = sample_rg16f(shadow, x, y)
            if not math.isfinite(red) or not math.isfinite(green):
                source_nonfinite.add((x, y))

    predicted: set[tuple[int, int]] = set()
    observed: set[tuple[int, int]] = set()
    epsilon = 9.999999747378752e-05
    inv_half = (1.0 / half_width, 1.0 / half_height)
    for y in range(output["height"]):
        for x in range(output["width"]):
            if sample_rg11b10f_nonfinite(output, x, y):
                observed.add((x, y))
            frag = (x + 0.5, y + 0.5)
            uv = (
                buffer_size_inv[2] * frag[0],
                buffer_size_inv[3] * frag[1],
            )
            local_uv = (
                uv[0] - scissor[0] * buffer_size_inv[2],
                uv[1] - scissor[1] * buffer_size_inv[3],
            )
            base = (
                math.floor(local_uv[0] * half_width - 0.5),
                math.floor(local_uv[1] * half_height - 0.5),
            )
            base_uv = (
                (base[0] + 0.5) * inv_half[0],
                (base[1] + 0.5) * inv_half[1],
            )
            fraction = (
                (local_uv[0] - base_uv[0]) * half_width,
                (local_uv[1] - base_uv[1]) * half_height,
            )
            weights = (
                (1.0 - fraction[1]) * (1.0 - fraction[0]),
                (1.0 - fraction[1]) * fraction[0],
                fraction[1] * (1.0 - fraction[0]),
                fraction[1] * fraction[0],
            )
            coordinates = (
                (base[0], base[1]),
                (base[0] + 1, base[1]),
                (base[0], base[1] + 1),
                (base[0] + 1, base[1] + 1),
            )
            samples = []
            for source_x, source_y in coordinates:
                source_x = min(max(source_x, 0), shadow["width"] - 1)
                source_y = min(max(source_y, 0), shadow["height"] - 1)
                samples.append(sample_rg16f(shadow, source_x, source_y))

            scene_depth = sample_r32(depth, x, y)
            world_depth = (
                inv_device_z[0] * scene_depth + inv_device_z[1] +
                ieee_div(1.0, inv_device_z[2] * scene_depth - inv_device_z[3])
            )
            bilateral = []
            for weight, (_red, green) in zip(weights, samples):
                bilateral.append(
                    weight * ieee_div(1.0, abs(abs(green) - world_depth) + epsilon)
                )
            numerator = sum(
                weight * sample[0]
                for weight, sample in zip(bilateral, samples)
            )
            denominator = sum(bilateral)
            filtered = ieee_div(numerator, denominator)

            # The remaining AIR branch either keeps filtered or mixes it with
            # one by a finite factor (1 or View intensity), so it cannot hide
            # or create a nonfinite value for this captured finite state.
            _alpha = sample_rgb10a2(gbuffer, x, y)[3]
            if not math.isfinite(filtered):
                predicted.add((x, y))

    false_negative = observed - predicted
    false_positive = predicted - observed
    report = {
        "classification": (
            "exact-nonfinite-mask-match" if not false_negative and
            not false_positive else "nonfinite-mask-mismatch"
        ),
        "air_function": "Main_00000f37_fead035c",
        "constants": {
            "inv_device_z_to_world_z": inv_device_z,
            "buffer_size_and_inv_size": buffer_size_inv,
            "scissor_min_and_size": scissor,
            "shadow_intensity": shadow_intensity,
            "output_to_light_attenuation": output_to_attenuation,
        },
        "shadow_source_nonfinite_pixels": len(source_nonfinite),
        "shadow_source_nonfinite_bounds": bounds(source_nonfinite),
        "predicted_output_nonfinite_pixels": len(predicted),
        "predicted_output_nonfinite_bounds": bounds(predicted),
        "observed_output_nonfinite_pixels": len(observed),
        "observed_output_nonfinite_bounds": bounds(observed),
        "false_negative_pixels": len(false_negative),
        "false_negative_bounds": bounds(false_negative),
        "false_positive_pixels": len(false_positive),
        "false_positive_bounds": bounds(false_positive),
    }
    rendered = json.dumps(report, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        args.output.write_text(rendered + "\n")


if __name__ == "__main__":
    main()

"""Read-only, bounded decoder for AppInput's V2/V3 window metrics sidecar.

On iOS:
    python3 macws_window_metrics_dump.py --pid 1234
From this checkout, without installing anything on the device:
    ssh -p 2222 root@DEVICE python3 - --pid 1234 < misc/macws_window_metrics_dump.py
For a previously copied binary:
    python3 misc/macws_window_metrics_dump.py --file /tmp/metrics.bin

Dimensions are AppKit logical points, never capture pixels or iPadOS points.
The sidecar does NOT contain the current frame. A configure ACK's applied size
is the result of that identified input, not proof of the latest rendered frame.
"""

import argparse
import json
import math
import os
from pathlib import Path
import re
import stat
import struct
import sys


MAGIC = 0x4D57474D
MAX_WINDOWS = 256
MAX_DIMENSION = 16384
HEADER = struct.Struct("<IHHIIQ")
V2_ENTRY = struct.Struct("<IIIff")
V3_ENTRY = struct.Struct("<IIIffffdIffff")
MAX_FILE_BYTES = HEADER.size + MAX_WINDOWS * V3_ENTRY.size
FLAG_NAMES = (
    "visible", "on_screen", "has_shadow", "resizable", "menu_bar",
    "transient", "focused", "spatial_canvas", "fullscreen_canvas",
    "frontmost_application", "fixed_width", "fixed_height",
)


class MetricsError(ValueError):
    """Malformed or unsupported sidecar, not an absent acknowledgement."""


def _size(width, height):
    return {"width": width, "height": height}


def _check_dimension(value, name, positive=False):
    if (not math.isfinite(value) or value < 0 or
            value > MAX_DIMENSION or (positive and value == 0)):
        raise MetricsError(f"invalid {name}: {value!r}")


def decode_metrics(data):
    """Decode an immutable byte snapshot; never reads or changes system state."""
    if len(data) < HEADER.size or len(data) > MAX_FILE_BYTES:
        raise MetricsError(f"invalid file length {len(data)}")
    magic, version, header_size, entry_size, count, generation = HEADER.unpack_from(data)
    if magic != MAGIC:
        raise MetricsError(f"wrong magic {magic:#x}")
    layouts = {2: V2_ENTRY, 3: V3_ENTRY}
    entry = layouts.get(version)
    if entry is None:
        raise MetricsError(f"unsupported metrics version {version}")
    if header_size != HEADER.size or entry_size != entry.size:
        raise MetricsError(f"invalid V{version} layout: header={header_size}, entry={entry_size}")
    if count > MAX_WINDOWS or generation == 0:
        raise MetricsError(f"invalid count/generation: {count}/{generation}")
    expected = header_size + count * entry_size
    if len(data) != expected:
        raise MetricsError(f"truncated/extra bytes: expected {expected}, received {len(data)}")

    windows = []
    for index in range(count):
        fields = entry.unpack_from(data, header_size + index * entry_size)
        window_id, flags, group_id, min_width, min_height = fields[:5]
        if window_id == 0:
            raise MetricsError(f"entry {index} has zero window ID")
        _check_dimension(min_width, "minimum width")
        _check_dimension(min_height, "minimum height")
        window = {
            "window_id": window_id,
            "logical_group_id": group_id,
            "flags_raw": flags,
            "flags": [name for bit, name in enumerate(FLAG_NAMES) if flags & (1 << bit)],
            "resizable": bool(flags & (1 << 3)),
            "fixed_width": bool(flags & (1 << 10)),
            "fixed_height": bool(flags & (1 << 11)),
            "minimum_logical_size": _size(min_width, min_height),
            "maximum_logical_size": None,
            "configuration_ack": {
                "supported": version == 3,
                "received": False,
                "reason": "legacy_v2_has_no_ack" if version == 2 else "no_identified_configure_yet",
            },
        }
        if version == 3:
            max_width, max_height, timestamp, sequence, req_w, req_h, app_w, app_h = fields[5:]
            _check_dimension(max_width, "maximum width")
            _check_dimension(max_height, "maximum height")
            window["maximum_logical_size"] = _size(max_width, max_height)
            if not math.isfinite(timestamp) or timestamp < 0:
                raise MetricsError(f"invalid ACK timestamp {timestamp!r}")
            if timestamp == 0:
                if any((sequence, req_w, req_h, app_w, app_h)):
                    raise MetricsError("unidentified ACK must have a completely zero payload")
            else:
                for name, value in (("requested width", req_w), ("requested height", req_h),
                                    ("applied width", app_w), ("applied height", app_h)):
                    _check_dimension(value, name, positive=True)
                window["configuration_ack"] = {
                    "supported": True,
                    "received": True,
                    "timestamp": timestamp,
                    "sequence": sequence,
                    "requested_logical_size": _size(req_w, req_h),
                    "applied_logical_size": _size(app_w, app_h),
                }
        windows.append(window)
    return {
        "metrics_version": version,
        "header_size": header_size,
        "entry_size": entry_size,
        "entry_count": count,
        "generation": generation,
        "units": "macOS_AppKit_logical_points",
        "current_frame_included": False,
        "maximum_zero_means_unknown": True,
        "windows": windows,
    }


def read_metrics(path):
    """Read only a bounded regular file; fstat/read share the same open inode."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise MetricsError("metrics input is not a regular file")
        if info.st_size < HEADER.size or info.st_size > MAX_FILE_BYTES:
            raise MetricsError(f"invalid file length {info.st_size}")
        data = stream.read(MAX_FILE_BYTES + 1)
    result = decode_metrics(data)
    result["path"] = str(path)
    result["file_mtime_unix"] = info.st_mtime
    match = re.fullmatch(r"macws_window_metrics\.(\d+)\.bin", Path(path).name)
    result["owner_pid_from_filename"] = int(match.group(1)) if match else None
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--pid", type=int, help="PID in the mounted macOS root")
    source.add_argument("--file", type=Path, help="explicit sidecar path (read only)")
    parser.add_argument("--rootfs", type=Path, default=Path("/var/mnt/rootfs"),
                        help="mounted root for --pid (default: /var/mnt/rootfs)")
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args(argv)
    if args.pid is not None and args.pid <= 1:
        parser.error("--pid must be greater than 1")
    path = args.file if args.file is not None else (
        args.rootfs / "private/tmp" / f"macws_window_metrics.{args.pid}.bin")
    try:
        result = read_metrics(path)
    except (OSError, MetricsError) as error:
        print(f"metrics read failed: {path}: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=None if args.compact else 2, allow_nan=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Audit byte-exact Stray MTLB captures against installed substitutions.

No shebang: invoke explicitly with Python on jailbreak targets.  This tool is
read-only.  It validates every capture's filename FNV, MTLB container length,
and source identity, then proves whether that identity is covered either by
the compiled compatibility table or by a dynamic exact-directory filename.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import struct


CAPTURE_RE = re.compile(r"^macws_mtl_data_[0-9]+_([0-9a-f]{16})\.bin$")
EXACT_RE = re.compile(r"^([0-9]+)-([0-9a-f]{16})\.metallib$")
BUILTIN_RE = re.compile(
    r"\{\s*([0-9]+),\s*UINT64_C\(0x([0-9a-fA-F]{16})\)"
)
FNV_OFFSET = 1469598103934665603
FNV_PRIME = 1099511628211
FNV_MASK = (1 << 64) - 1


def fnv1a64(data: bytes) -> int:
    value = FNV_OFFSET
    for byte in data:
        value = ((value ^ byte) * FNV_PRIME) & FNV_MASK
    return value


def parse_builtin_identities(source: pathlib.Path) -> set[tuple[int, int]]:
    text = source.read_text(encoding="utf-8")
    marker = "static const MacWSStrayMetalLibrary kMacWSStrayMetalLibraries[]"
    if marker not in text:
        raise ValueError(f"compiled Stray table missing from {source}")
    block = text.split(marker, 1)[1].split("};", 1)[0]
    identities = {
        (int(length), int(source_hash, 16))
        for length, source_hash in BUILTIN_RE.findall(block)
    }
    if not identities:
        raise ValueError(f"compiled Stray table is empty in {source}")
    return identities


def parse_exact_identities(
    directory: pathlib.Path | None,
    listing: pathlib.Path | None,
) -> set[tuple[int, int]]:
    names: list[str] = []
    if directory:
        names.extend(path.name for path in directory.glob("*.metallib"))
    if listing:
        names.extend(
            pathlib.Path(line.strip()).name
            for line in listing.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    identities = set()
    for name in names:
        match = EXACT_RE.match(name)
        if match:
            identities.add((int(match.group(1)), int(match.group(2), 16)))
    return identities


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture_dir", type=pathlib.Path)
    parser.add_argument(
        "--builtin-source",
        type=pathlib.Path,
        default=pathlib.Path(__file__).parents[1] / "libmachook/Metal_hooks.x",
    )
    parser.add_argument("--exact-dir", type=pathlib.Path)
    parser.add_argument("--exact-list", type=pathlib.Path)
    parser.add_argument("--json-output", type=pathlib.Path)
    args = parser.parse_args()
    if not args.exact_dir and not args.exact_list:
        parser.error("provide --exact-dir and/or --exact-list")

    builtins = parse_builtin_identities(args.builtin_source)
    exact = parse_exact_identities(args.exact_dir, args.exact_list)
    captures: dict[tuple[int, int], pathlib.Path] = {}
    invalid = []
    for capture in sorted(args.capture_dir.glob("macws_mtl_data_*.bin")):
        match = CAPTURE_RE.match(capture.name)
        if not match:
            invalid.append({"path": str(capture), "error": "invalid filename"})
            continue
        data = capture.read_bytes()
        expected_hash = int(match.group(1), 16)
        observed_hash = fnv1a64(data)
        if len(data) < 24 or data[:4] != b"MTLB":
            invalid.append({"path": str(capture), "error": "not MTLB"})
            continue
        container_length = struct.unpack_from("<Q", data, 16)[0]
        if container_length != len(data):
            invalid.append({
                "path": str(capture),
                "error": "container length mismatch",
            })
            continue
        if observed_hash != expected_hash:
            invalid.append({"path": str(capture), "error": "FNV mismatch"})
            continue
        identity = (len(data), observed_hash)
        captures.setdefault(identity, capture)

    captured = set(captures)
    builtin_covered = captured & builtins
    exact_covered = captured & exact
    missing = captured - builtins - exact
    result = {
        "capture_files": len(list(args.capture_dir.glob("macws_mtl_data_*.bin"))),
        "unique_valid_captures": len(captured),
        "invalid": invalid,
        "compiled_identities": len(builtins),
        "dynamic_exact_identities": len(exact),
        "covered_by_compiled_table": len(builtin_covered),
        "covered_by_dynamic_exact_dir": len(exact_covered),
        "covered_total": len(captured - missing),
        "missing": [
            {
                "source_length": length,
                "source_fnv": f"{source_hash:016x}",
                "capture": str(captures[(length, source_hash)]),
            }
            for length, source_hash in sorted(missing)
        ],
    }
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.json_output:
        args.json_output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    if invalid or missing:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

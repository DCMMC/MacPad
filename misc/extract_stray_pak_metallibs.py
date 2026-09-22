"""Extract structurally valid, uncompressed MTLB archives from Stray PAKs.

No shebang: the target jailbreak rejects execve of shebang scripts.  Invoke
this file explicitly with /var/jb/usr/bin/python3 on the iPad.

This is deliberately an offline resource enumerator, not a Metal validation
bypass.  Candidate magic strings are accepted only when the container length
and all four advertised section bounds fit inside both the candidate and its
source PAK.  The stricter AIR/function validation remains in
repack_metallib_macabi.py and fails closed before producing a replacement.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import struct
import tempfile


MAGIC = b"MTLB"
HEADER_SIZE = 88
DEFAULT_CHUNK_SIZE = 8 * 1024 * 1024
DEFAULT_MAX_CONTAINER_SIZE = 64 * 1024 * 1024
FNV_OFFSET = 1469598103934665603
FNV_PRIME = 1099511628211
FNV_MASK = (1 << 64) - 1
IOS_PLATFORM = 0x0001
MACOS_PLATFORM = 0x8001


def u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def u64(data: bytes, offset: int) -> int:
    return struct.unpack_from("<Q", data, offset)[0]


def fnv1a64(data: bytes) -> int:
    value = FNV_OFFSET
    for byte in data:
        value = ((value ^ byte) * FNV_PRIME) & FNV_MASK
    return value


def section_bounds_are_valid(header: bytes, container_size: int) -> bool:
    sections = (
        (u64(header, 24), u64(header, 32), "function"),
        (u64(header, 40), u64(header, 48), "public"),
        (u64(header, 56), u64(header, 64), "private"),
        (u64(header, 72), u64(header, 80), "bitcode"),
    )
    for offset, size, name in sections:
        if offset > container_size or size > container_size - offset:
            return False
        if name in {"function", "bitcode"} and (
            offset < HEADER_SIZE or size == 0
        ):
            return False
    return True


def candidate_size(
    file_descriptor: int,
    source_size: int,
    offset: int,
    maximum_size: int,
) -> tuple[int, bytes] | None:
    if offset > source_size - HEADER_SIZE:
        return None
    header = os.pread(file_descriptor, HEADER_SIZE, offset)
    if len(header) != HEADER_SIZE or header[:4] != MAGIC:
        return None
    if u16(header, 4) not in {IOS_PLATFORM, MACOS_PLATFORM}:
        return None
    if header[10] != 0:
        return None
    size = u64(header, 16)
    if not HEADER_SIZE <= size <= maximum_size:
        return None
    if size > source_size - offset:
        return None
    if not section_bounds_are_valid(header, size):
        return None
    function_offset = u64(header, 24)
    function_count_bytes = os.pread(file_descriptor, 4, offset + function_offset)
    if len(function_count_bytes) != 4:
        return None
    function_count = u32(function_count_bytes, 0)
    if function_count == 0 or function_count > 65536:
        return None
    return size, header


def magic_offsets(file_descriptor: int, source_size: int, chunk_size: int):
    position = 0
    overlap = b""
    while position < source_size:
        block = os.pread(
            file_descriptor, min(chunk_size, source_size - position), position
        )
        if not block:
            break
        searchable = overlap + block
        base = position - len(overlap)
        cursor = 0
        while True:
            found = searchable.find(MAGIC, cursor)
            if found < 0:
                break
            yield base + found
            cursor = found + 1
        overlap = searchable[-(len(MAGIC) - 1):]
        position += len(block)


def atomic_write(path: pathlib.Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{path.name}.", dir=path.parent, delete=False
    ) as output:
        temporary = pathlib.Path(output.name)
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    try:
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pak", type=pathlib.Path, nargs="+")
    parser.add_argument("--output-dir", type=pathlib.Path, required=True)
    parser.add_argument("--manifest", type=pathlib.Path)
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument(
        "--max-container-size",
        type=int,
        default=DEFAULT_MAX_CONTAINER_SIZE,
    )
    args = parser.parse_args()
    if args.chunk_size < HEADER_SIZE:
        raise SystemExit("--chunk-size must be at least the MTLB header size")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = args.manifest or args.output_dir / "manifest.tsv"

    identities: dict[tuple[int, int], pathlib.Path] = {}
    rows = [
        "pak\toffset\tsource_length\tsource_fnv\tcapture\tplatform"
        "\tfile_version\ttarget_os\ttarget_version"
    ]
    candidate_count = 0
    duplicate_count = 0
    for pak in args.pak:
        source_size = pak.stat().st_size
        with pak.open("rb", buffering=0) as source:
            file_descriptor = source.fileno()
            for offset in magic_offsets(
                file_descriptor, source_size, args.chunk_size
            ):
                candidate = candidate_size(
                    file_descriptor,
                    source_size,
                    offset,
                    args.max_container_size,
                )
                if candidate is None:
                    continue
                size, header = candidate
                data = os.pread(file_descriptor, size, offset)
                if len(data) != size or u64(data, 16) != len(data):
                    continue
                source_hash = fnv1a64(data)
                identity = (size, source_hash)
                capture = identities.get(identity)
                if capture is None:
                    candidate_count += 1
                    capture = args.output_dir / (
                        f"macws_mtl_data_{candidate_count:06d}_"
                        f"{source_hash:016x}.bin"
                    )
                    atomic_write(capture, data)
                    identities[identity] = capture
                else:
                    duplicate_count += 1
                rows.append(
                    "\t".join(
                        (
                            str(pak),
                            str(offset),
                            str(size),
                            f"{source_hash:016x}",
                            capture.name,
                            f"0x{u16(header, 4):04x}",
                            f"{u16(header, 6)}.{u16(header, 8)}",
                            f"0x{header[11]:02x}",
                            f"{u16(header, 12)}.{u16(header, 14)}",
                        )
                    )
                )
        print(
            f"scanned={pak} bytes={source_size} "
            f"unique_so_far={len(identities)} duplicates={duplicate_count}",
            flush=True,
        )

    atomic_write(manifest, ("\n".join(rows) + "\n").encode("utf-8"))
    print(
        f"complete paks={len(args.pak)} unique={len(identities)} "
        f"duplicates={duplicate_count} manifest={manifest}"
    )


if __name__ == "__main__":
    main()

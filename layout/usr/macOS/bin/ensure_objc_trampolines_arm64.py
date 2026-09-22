"""Add an ARM64/ALL slice to Ventura's arm64e-only ObjC trampolines.

Ventura's ``imp_implementationWithBlock`` lazily loads
``/usr/lib/libobjc-trampolines.dylib``.  WindowServer in this project is an
ARM64/ALL executable, while the stock Ventura universal file contains only
x86_64, x86_64h and ARM64/E.  iOS dyld therefore rejects the lazy load before
the first AGX method swizzle can be installed.

The trampoline image is position-independent generated code with no ObjC
object layout of its own.  Preserve the stock ARM64/E slice byte-for-byte and
append an ARM64/ALL copy whose two Mach-O subtype fields describe the ABI of
the caller that will load it.  postinst re-signs the resulting universal file
and registers every CodeDirectory after this structural transformation.

This file intentionally has no shebang: AMFI rejects script shebang execs on
the target.  Invoke it explicitly with /var/jb/usr/bin/python3.
"""

from __future__ import annotations

import argparse
import os
import stat
import struct
import sys


FAT_MAGIC = 0xCAFEBABE
MH_MAGIC_64 = 0xFEEDFACF
CPU_TYPE_ARM64 = 0x0100000C
CPU_SUBTYPE_MASK = 0x00FFFFFF
CPU_SUBTYPE_ARM64_ALL = 0
CPU_SUBTYPE_ARM64E = 2


class FormatError(RuntimeError):
    pass


def read_fat_arches(data: bytes) -> list[tuple[int, int, int, int, int]]:
    if len(data) < 8:
        raise FormatError("file is shorter than a fat header")
    magic, count = struct.unpack_from(">II", data, 0)
    if magic != FAT_MAGIC:
        raise FormatError(f"unsupported fat magic {magic:#x}")
    if count == 0 or count > 64 or 8 + count * 20 > len(data):
        raise FormatError(f"invalid fat architecture count {count}")
    return [
        struct.unpack_from(">IIIII", data, 8 + index * 20)
        for index in range(count)
    ]


def subtype_base(value: int) -> int:
    return value & CPU_SUBTYPE_MASK


def has_arm64_all(arches: list[tuple[int, int, int, int, int]]) -> bool:
    return any(
        cputype == CPU_TYPE_ARM64 and
        subtype_base(cpusubtype) == CPU_SUBTYPE_ARM64_ALL
        for cputype, cpusubtype, _offset, _size, _align in arches
    )


def add_arm64_all_slice(data: bytes) -> bytes:
    arches = read_fat_arches(data)
    if has_arm64_all(arches):
        return data

    source = next((
        arch for arch in arches
        if arch[0] == CPU_TYPE_ARM64 and
        subtype_base(arch[1]) == CPU_SUBTYPE_ARM64E
    ), None)
    if source is None:
        raise FormatError("no ARM64/E source slice")

    cputype, _cpusubtype, source_offset, source_size, alignment = source
    if alignment > 30:
        raise FormatError(f"unreasonable ARM64/E alignment 2^{alignment}")
    source_end = source_offset + source_size
    if source_offset < 8 + len(arches) * 20 or source_end > len(data):
        raise FormatError("ARM64/E slice lies outside the fat file")

    source_slice = bytearray(data[source_offset:source_end])
    if len(source_slice) < 12:
        raise FormatError("ARM64/E slice is shorter than a Mach header")
    magic, thin_cputype, thin_subtype = struct.unpack_from(
        "<III", source_slice, 0)
    if (magic != MH_MAGIC_64 or thin_cputype != CPU_TYPE_ARM64 or
            subtype_base(thin_subtype) != CPU_SUBTYPE_ARM64E):
        raise FormatError(
            "ARM64/E fat entry does not match its thin Mach header")

    new_header_end = 8 + (len(arches) + 1) * 20
    first_slice_offset = min(arch[2] for arch in arches)
    if new_header_end > first_slice_offset:
        raise FormatError("fat header has no room for one architecture entry")

    slice_alignment = 1 << alignment
    new_offset = (len(data) + slice_alignment - 1) & -slice_alignment
    result = bytearray(data)
    result.extend(b"\0" * (new_offset - len(result)))
    # CPU_SUBTYPE_ARM64_ALL has no pointer-auth capability bits.
    struct.pack_into("<I", source_slice, 8, CPU_SUBTYPE_ARM64_ALL)
    result.extend(source_slice)

    struct.pack_into(">I", result, 4, len(arches) + 1)
    struct.pack_into(
        ">IIIII", result, 8 + len(arches) * 20,
        cputype, CPU_SUBTYPE_ARM64_ALL, new_offset, source_size, alignment)
    return bytes(result)


def replace_atomically(path: str, data: bytes) -> None:
    current = os.stat(path, follow_symlinks=True)
    temporary = f"{path}.macws-arm64-new-{os.getpid()}"
    try:
        with open(temporary, "xb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, stat.S_IMODE(current.st_mode))
        try:
            os.chown(temporary, current.st_uid, current.st_gid)
        except PermissionError:
            pass
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("path")
    arguments = parser.parse_args()

    try:
        with open(arguments.path, "rb") as source:
            original = source.read()
        arches = read_fat_arches(original)
        if has_arm64_all(arches):
            print(f"[SKIP] {arguments.path}: ARM64/ALL slice already present")
            return 0
        if arguments.check:
            print(f"[NEEDS-REPAIR] {arguments.path}: ARM64/ALL slice missing")
            return 1
        repaired = add_arm64_all_slice(original)
        replace_atomically(arguments.path, repaired)
        print(
            f"[PATCH] {arguments.path}: appended ARM64/ALL ObjC trampoline "
            f"slice ({len(original)} -> {len(repaired)} bytes)")
        return 0
    except (OSError, FormatError) as error:
        print(f"[ERROR] {arguments.path}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

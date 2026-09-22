#!/usr/bin/env python3
"""Add one LC_LOAD_DYLIB to every ARM64 slice without moving Mach-O data.

The command is written only into existing zero-filled header padding.  The
tool refuses binaries whose first section leaves insufficient space, so it
cannot silently shift chained fixups, exports, or code-signature offsets.
Invoke it through python3 on iOS because AMFI rejects direct script shebangs
inside the macOS chroot.
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path


FAT_MAGIC = 0xCAFEBABE
MH_MAGIC_64 = 0xFEEDFACF
CPU_TYPE_ARM64 = 0x0100000C
LC_SEGMENT_64 = 0x19
LC_LOAD_DYLIB = 0x0C
DYLIB_COMMANDS = {
    0x0C,  # LC_LOAD_DYLIB
    0x18,  # LC_LOAD_WEAK_DYLIB (without LC_REQ_DYLD)
    0x1F,  # LC_REEXPORT_DYLIB
    0x20,  # LC_LAZY_LOAD_DYLIB
    0x23,  # LC_LOAD_UPWARD_DYLIB
}


def aligned(value: int, alignment: int) -> int:
    return (value + alignment - 1) & ~(alignment - 1)


def slices(data: bytearray) -> list[tuple[int, int, int]]:
    if len(data) < 8:
        raise ValueError("file is too small")
    magic_be = struct.unpack_from(">I", data, 0)[0]
    if magic_be == FAT_MAGIC:
        count = struct.unpack_from(">I", data, 4)[0]
        if len(data) < 8 + count * 20:
            raise ValueError("truncated fat header")
        result = []
        for index in range(count):
            cpu, _subtype, offset, size, _align = struct.unpack_from(
                ">IIIII", data, 8 + index * 20
            )
            if offset + size > len(data):
                raise ValueError(f"fat slice {index} exceeds file")
            result.append((cpu, offset, size))
        return result
    return [(struct.unpack_from("<I", data, 4)[0], 0, len(data))]


def c_string(data: bytearray, start: int, limit: int) -> str:
    end = data.find(b"\0", start, limit)
    if end < 0:
        raise ValueError("unterminated dylib load-command string")
    return bytes(data[start:end]).decode("utf-8", errors="strict")


def add_to_slice(data: bytearray, base: int, size: int, dylib: str) -> bool:
    if size < 32 or struct.unpack_from("<I", data, base)[0] != MH_MAGIC_64:
        raise ValueError(f"ARM64 slice at {base:#x} is not 64-bit Mach-O")
    ncmds, sizeofcmds = struct.unpack_from("<II", data, base + 16)
    command_offset = base + 32
    commands_end = command_offset + sizeofcmds
    slice_end = base + size
    if commands_end > slice_end:
        raise ValueError("load commands exceed slice")

    first_section = slice_end
    cursor = command_offset
    for index in range(ncmds):
        if cursor + 8 > commands_end:
            raise ValueError(f"truncated load command {index}")
        command, command_size = struct.unpack_from("<II", data, cursor)
        if command_size < 8 or cursor + command_size > commands_end:
            raise ValueError(f"invalid load command {index}")
        plain_command = command & 0x7FFFFFFF
        if plain_command in DYLIB_COMMANDS and command_size >= 24:
            name_offset = struct.unpack_from("<I", data, cursor + 8)[0]
            if 24 <= name_offset < command_size:
                existing = c_string(
                    data, cursor + name_offset, cursor + command_size
                )
                if existing == dylib:
                    return False
        if plain_command == LC_SEGMENT_64 and command_size >= 72:
            section_count = struct.unpack_from("<I", data, cursor + 64)[0]
            if 72 + section_count * 80 > command_size:
                raise ValueError(f"invalid section table in command {index}")
            for section_index in range(section_count):
                section = cursor + 72 + section_index * 80
                file_offset = struct.unpack_from("<I", data, section + 48)[0]
                if file_offset:
                    first_section = min(first_section, base + file_offset)
        cursor += command_size
    if cursor != commands_end:
        raise ValueError("sizeofcmds does not match load-command traversal")

    encoded = dylib.encode("utf-8") + b"\0"
    command_size = aligned(24 + len(encoded), 8)
    new_end = commands_end + command_size
    if new_end > first_section:
        available = first_section - commands_end
        raise ValueError(
            f"need {command_size} bytes of header padding, have {available}"
        )
    if any(data[commands_end:new_end]):
        raise ValueError("prospective load-command padding is not zero-filled")

    command = struct.pack(
        "<IIIIII", LC_LOAD_DYLIB, command_size, 24, 0, 0, 0
    )
    command += encoded
    command += b"\0" * (command_size - len(command))
    data[commands_end:new_end] = command
    struct.pack_into("<II", data, base + 16, ncmds + 1,
                     sizeofcmds + command_size)
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("binary")
    parser.add_argument("dylib")
    arguments = parser.parse_args()

    path = Path(arguments.binary)
    data = bytearray(path.read_bytes())
    arm_slices = 0
    changed = 0
    for cpu, offset, size in slices(data):
        if cpu != CPU_TYPE_ARM64:
            continue
        arm_slices += 1
        if add_to_slice(data, offset, size, arguments.dylib):
            changed += 1
    if not arm_slices:
        raise SystemExit("no ARM64 Mach-O slice found")
    if changed:
        temporary = path.with_name(path.name + ".macws-load-dylib.tmp")
        temporary.write_bytes(data)
        temporary.chmod(path.stat().st_mode)
        temporary.replace(path)
    print(f"{path}: ARM64 slices={arm_slices} modified={changed} "
          f"dylib={arguments.dylib}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

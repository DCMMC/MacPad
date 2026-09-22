"""Change an iOS Mach-O build-version command to macOS 13 in place.

Invoke through python3. There is deliberately no shebang because AMFI on the
target blocks execve of text files with shebangs.
"""

import os
import struct
import sys

FAT_MAGIC = 0xCAFEBABE
FAT_MAGIC_64 = 0xCAFEBABF
MH_MAGIC_64 = 0xFEEDFACF
MH_CIGAM_64 = 0xCFFAEDFE
LC_BUILD_VERSION = 0x32
LC_VERSION_MIN_MACOSX = 0x24
LC_VERSION_MIN_IPHONEOS = 0x25
PLATFORM_MACOS = 1
MACOS_13 = 13 << 16


def patch_slice(data, offset, size, label):
    if offset < 0 or size < 32 or offset + size > len(data):
        raise ValueError(f"{label}: invalid slice range")

    magic = struct.unpack_from("<I", data, offset)[0]
    if magic == MH_MAGIC_64:
        endian = "<"
    elif magic == MH_CIGAM_64:
        endian = ">"
    else:
        raise ValueError(f"{label}: unsupported Mach-O magic 0x{magic:08x}")

    ncmds = struct.unpack_from(f"{endian}I", data, offset + 16)[0]
    command = offset + 32
    limit = offset + size
    for _ in range(ncmds):
        if command + 8 > limit:
            raise ValueError(f"{label}: truncated load-command table")
        cmd, cmdsize = struct.unpack_from(f"{endian}II", data, command)
        if cmdsize < 8 or command + cmdsize > limit:
            raise ValueError(f"{label}: invalid load command size {cmdsize}")

        if cmd == LC_BUILD_VERSION:
            if cmdsize < 24:
                raise ValueError(f"{label}: truncated LC_BUILD_VERSION")
            old_platform, old_minos, old_sdk, ntools = struct.unpack_from(
                f"{endian}IIII", data, command + 8
            )
            if (old_platform, old_minos, old_sdk) == (PLATFORM_MACOS, MACOS_13, MACOS_13):
                print(f"[{label}] LC_BUILD_VERSION already macOS 13.0")
                return False
            struct.pack_into(
                f"{endian}IIII", data, command + 8,
                PLATFORM_MACOS, MACOS_13, MACOS_13, ntools,
            )
            print(
                f"[{label}] LC_BUILD_VERSION platform={old_platform} "
                f"minos=0x{old_minos:x} sdk=0x{old_sdk:x} -> macOS 13.0"
            )
            return True

        if cmd == LC_VERSION_MIN_IPHONEOS:
            if cmdsize < 16:
                raise ValueError(f"{label}: truncated LC_VERSION_MIN_IPHONEOS")
            struct.pack_into(
                f"{endian}IIII", data, command,
                LC_VERSION_MIN_MACOSX, cmdsize, MACOS_13, MACOS_13,
            )
            print(f"[{label}] LC_VERSION_MIN_IPHONEOS -> macOS 13.0")
            return True
        command += cmdsize

    raise ValueError(f"{label}: no supported build-version command")


def patch_file(path):
    with open(path, "rb") as stream:
        data = bytearray(stream.read())
    if len(data) < 32:
        raise ValueError("file is too small")

    magic = struct.unpack_from(">I", data, 0)[0]
    slices = []
    if magic in (FAT_MAGIC, FAT_MAGIC_64):
        count = struct.unpack_from(">I", data, 4)[0]
        entry_size = 32 if magic == FAT_MAGIC_64 else 20
        if 8 + count * entry_size > len(data):
            raise ValueError("truncated fat header")
        for index in range(count):
            entry = 8 + index * entry_size
            if magic == FAT_MAGIC_64:
                cputype, cpusubtype, offset, size, _align, _reserved = struct.unpack_from(
                    ">IIQQII", data, entry
                )
            else:
                cputype, cpusubtype, offset, size, _align = struct.unpack_from(
                    ">IIIII", data, entry
                )
            slices.append((offset, size, f"slice-{index}:{cputype:#x}/{cpusubtype:#x}"))
    else:
        slices.append((0, len(data), "thin"))

    changed = False
    for offset, size, label in slices:
        changed = patch_slice(data, offset, size, label) or changed
    if changed:
        temporary = f"{path}.macws-new-{os.getpid()}"
        try:
            with open(temporary, "wb") as stream:
                stream.write(data)
            os.chmod(temporary, os.stat(path).st_mode)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        print(f"patched {path}")
    else:
        print(f"unchanged {path}")


def main():
    if len(sys.argv) < 2:
        raise SystemExit(f"usage: {sys.argv[0]} MACHO [MACHO ...]")
    try:
        for filename in sys.argv[1:]:
            patch_file(filename)
    except (OSError, ValueError, struct.error) as error:
        print(f"set_macos_version: {error}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
set_macos_version.py - Set macOS build version in Mach-O binaries

Replaces vtool -set-build-version for iOS builds where vtool is unavailable.
Handles both LC_BUILD_VERSION and LC_VERSION_MIN_IPHONEOS load commands.

Usage: python3 set_macos_version.py <path_to_macho>
"""

import sys
import struct
import os

# Mach-O constants
FAT_MAGIC = 0xcafebabe
FAT_MAGIC_64 = 0xcafebabf
MH_MAGIC_64 = 0xfeedfacf
MH_CIGAM_64 = 0xcffaedfe

LC_BUILD_VERSION = 0x32
LC_VERSION_MIN_MACOSX = 0x24
LC_VERSION_MIN_IPHONEOS = 0x25

PLATFORM_MACOS = 1
PLATFORM_IOS = 2

def pack_version(major, minor, patch=0):
    """Pack version as 32-bit: xxxx.yy.zz -> (major << 16) | (minor << 8) | patch"""
    return (major << 16) | (minor << 8) | patch

def process_macho_slice(data, offset, size, path, arch_name=""):
    """Process a single Mach-O slice and patch version load commands"""

    # Read Mach-O header
    magic = struct.unpack_from('<I', data, offset)[0]

    if magic == MH_CIGAM_64:
        endian = '>'
    elif magic == MH_MAGIC_64:
        endian = '<'
    else:
        print(f"  [{arch_name}] Not a 64-bit Mach-O (magic=0x{magic:08x}), skipping")
        return False

    # Parse header: magic, cputype, cpusubtype, filetype, ncmds, sizeofcmds, flags, reserved
    header_fmt = f'{endian}IIIIIIII'
    header_size = struct.calcsize(header_fmt)
    header = struct.unpack_from(header_fmt, data, offset)
    ncmds = header[4]

    # Walk through load commands
    cmd_offset = offset + header_size
    found = False

    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from(f'{endian}II', data, cmd_offset)

        if cmd == LC_BUILD_VERSION:
            # LC_BUILD_VERSION: cmd, cmdsize, platform, minos, sdk, ntools
            old_platform, old_minos, old_sdk, ntools = struct.unpack_from(
                f'{endian}IIII', data, cmd_offset + 8
            )

            old_platform_name = {1: 'macOS', 2: 'iOS', 6: 'macCatalyst'}.get(old_platform, f'unknown({old_platform})')
            old_minos_str = f"{(old_minos >> 16)}.{(old_minos >> 8) & 0xff}.{old_minos & 0xff}"

            # Set new values: macOS 13.0.0
            new_platform = PLATFORM_MACOS
            new_minos = pack_version(13, 0, 0)
            new_sdk = pack_version(13, 0, 0)

            # Pack and write
            new_data = struct.pack(f'{endian}IIII', new_platform, new_minos, new_sdk, ntools)

            # Modify data in place
            write_offset = cmd_offset + 8
            for i, b in enumerate(new_data):
                data[write_offset + i] = b

            print(f"  [{arch_name}] LC_BUILD_VERSION: {old_platform_name} {old_minos_str} -> macOS 13.0.0")
            found = True
            break

        elif cmd == LC_VERSION_MIN_IPHONEOS:
            # LC_VERSION_MIN_IPHONEOS: cmd, cmdsize, version, sdk (16 bytes total)
            # Same structure as LC_VERSION_MIN_MACOSX - just change cmd and versions
            old_version, old_sdk = struct.unpack_from(f'{endian}II', data, cmd_offset + 8)

            old_ver_str = f"{(old_version >> 16)}.{(old_version >> 8) & 0xff}.{old_version & 0xff}"
            old_sdk_str = f"{(old_sdk >> 16)}.{(old_sdk >> 8) & 0xff}.{old_sdk & 0xff}"

            # Convert to LC_VERSION_MIN_MACOSX with macOS 13.0.0
            new_cmd = LC_VERSION_MIN_MACOSX
            new_version = pack_version(13, 0, 0)
            new_sdk = pack_version(13, 0, 0)

            # Pack: cmd, cmdsize, version, sdk
            new_data = struct.pack(f'{endian}IIII', new_cmd, cmdsize, new_version, new_sdk)

            # Modify data in place (overwrite entire load command)
            for i, b in enumerate(new_data):
                data[cmd_offset + i] = b

            print(f"  [{arch_name}] LC_VERSION_MIN_IPHONEOS iOS {old_ver_str} -> LC_VERSION_MIN_MACOSX macOS 13.0.0")
            found = True
            break

        cmd_offset += cmdsize

    if not found:
        print(f"  [{arch_name}] No LC_BUILD_VERSION or LC_VERSION_MIN_IPHONEOS found")

    return found

def process_file(path):
    """Process a Mach-O file (fat or thin)"""

    with open(path, 'rb') as f:
        data = bytearray(f.read())

    magic = struct.unpack('>I', data[:4])[0]

    modified = False

    if magic == FAT_MAGIC or magic == FAT_MAGIC_64:
        # Fat binary
        is_64 = (magic == FAT_MAGIC_64)
        # fat_arch:    cputype(I) cpusubtype(I) offset(I) size(I) align(I)     = 20 bytes
        # fat_arch_64: cputype(I) cpusubtype(I) offset(Q) size(Q) align(I) reserved(I) = 32 bytes
        arch_fmt = '>IIIII' if not is_64 else '>IIQQII'
        arch_size = struct.calcsize(arch_fmt)

        nfat_arch = struct.unpack('>I', data[4:8])[0]
        print(f"Fat binary with {nfat_arch} architectures")

        arch_offset = 8
        for i in range(nfat_arch):
            if is_64:
                cputype, cpusubtype, offset, size, align, _reserved = struct.unpack_from('>IIQQII', data, arch_offset)
            else:
                cputype, cpusubtype, offset, size, align = struct.unpack_from('>IIIII', data, arch_offset)

            # Determine architecture name
            arch_names = {
                (0x0100000c, 0): 'arm64',
                (0x0100000c, 2): 'arm64e',
                (0x01000007, 3): 'x86_64',
            }
            arch_name = arch_names.get((cputype, cpusubtype), f'cpu{cputype}')

            if process_macho_slice(data, offset, size, path, arch_name):
                modified = True

            arch_offset += arch_size
    else:
        # Thin binary
        print("Thin binary")
        if process_macho_slice(data, 0, len(data), path, "single"):
            modified = True

    if modified:
        with open(path, 'wb') as f:
            f.write(data)
        print(f"Modified: {path}")
        return True
    else:
        print(f"No changes made to: {path}")
        return False

def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <macho_file> [macho_file2 ...]")
        sys.exit(1)

    for path in sys.argv[1:]:
        if not os.path.isfile(path):
            print(f"Error: {path} not found")
            continue
        print(f"\nProcessing: {path}")
        process_file(path)

if __name__ == '__main__':
    main()

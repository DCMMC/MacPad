"""Bounded read-only cache ranges; does not extract or map a cache in memory.

Run on the device with Python: script CACHE_DIRECTORY START_ADDRESS BYTE_COUNT.
Class metadata: script CACHE_DIRECTORY --class/--ivars IMAGE_BASE CLASS_NAME.
Only real dyld cache files with validated mapping tables are opened. In
particular the adjacent .map text file is never parsed as a binary header.
"""
import os
from pathlib import Path
import struct
import sys
import signal


def main():
    class_mode = len(sys.argv) == 5 and sys.argv[2] in ("--class", "--ivars")
    if not class_mode and len(sys.argv) != 4:
        raise SystemExit("usage: script CACHE_DIRECTORY START_ADDRESS BYTE_COUNT; or CACHE_DIRECTORY --class/--ivars IMAGE_BASE NAME")
    root = Path(sys.argv[1])
    start, count = (int(sys.argv[3], 0), 0) if class_mode else (int(sys.argv[2], 0), int(sys.argv[3], 0))
    if start <= 0 or (not class_mode and not 0 < count <= 32768):
        raise SystemExit("range must be 1..32768 bytes")
    # Read-only probe: termination cannot strand a contact or stopped process.
    signal.alarm(12)
    mappings = []
    opened = []
    try:
        for path in sorted(root.glob("dyld_shared_cache_arm64e*"))[:32]:
            if path.suffix in (".map", ".symbols"):
                continue
            stream = path.open("rb")
            opened.append(stream)
            file_size = os.fstat(stream.fileno()).st_size
            header = stream.read(32)
            if len(header) != 32 or not header.startswith(b"dyld_v1"):
                continue
            offset, entries = struct.unpack_from("<II", header, 16)
            if not 1 <= entries <= 32 or not 32 <= offset <= 16384:
                continue
            if offset + entries * 32 > file_size:
                continue
            stream.seek(offset)
            for address, size, fileoff, _, _ in struct.iter_unpack(
                    "<QQQII", stream.read(entries * 32)):
                if fileoff + size > file_size:
                    continue
                mappings.append((address, address + size, fileoff, stream))

        def read(address, size):
            if not 0 < size <= 32768:
                raise ValueError("oversized read")
            for lo, hi, offset, stream in mappings:
                if lo <= address and address + size <= hi:
                    stream.seek(offset + address - lo)
                    data = stream.read(size)
                    if len(data) == size:
                        return data
            raise ValueError(f"unmapped {address:#x}")

        def pointer(address):
            value, = struct.unpack("<Q", read(address, 8))
            if value >> 63:
                return (value & 0xffffffff) + 0x180000000
            return value & ((1 << 51) - 1)

        def string(address):
            return read(address, 256).split(b"\0")[0].decode(errors="replace")

        def sections(base):
            header = read(base, 32)
            magic, = struct.unpack_from("<I", header)
            ncmds, command_bytes = struct.unpack_from("<II", header, 16)
            if magic != 0xfeedfacf or ncmds > 2048 or command_bytes > 1048576:
                raise ValueError("invalid Mach-O header")
            cursor, end = base + 32, base + 32 + command_bytes
            for _ in range(ncmds):
                cmd, size = struct.unpack("<II", read(cursor, 8))
                if size < 8 or size > 32768 or cursor + size > end:
                    raise ValueError("invalid load command")
                if cmd == 0x19:
                    data = read(cursor, size)
                    nsects, = struct.unpack_from("<I", data, 64)
                    if 72 + nsects * 80 > size:
                        raise ValueError("invalid section table")
                    for index in range(nsects):
                        offset = 72 + index * 80
                        yield data[offset:offset + 16].split(b"\0")[0], struct.unpack_from("<QQ", data, offset + 32)
                cursor += size

        def selector_base():
            with (root / "dyld_shared_cache_arm64e").open("rb") as stream:
                header = stream.read(512)
                offset, entries = struct.unpack_from("<II", header, 0x1c0)
                file_size = os.fstat(stream.fileno()).st_size
                if not 0 < entries <= 32768 or offset + entries * 32 > file_size:
                    raise ValueError("invalid cache image table")
                for index in range(entries):
                    stream.seek(offset + index * 32)
                    base, _, _, pathoff, _ = struct.unpack("<QQQII", stream.read(32))
                    if pathoff >= file_size: continue
                    stream.seek(pathoff)
                    if b"/libobjc.A.dylib\0" not in stream.read(256): continue
                    for name, (address, _) in sections(base):
                        if name == b"__objc_opt_ro":
                            return address + struct.unpack("<q", read(address + 40, 8))[0]
            raise ValueError("selector base not found")

        if class_mode:
            wanted = sys.argv[4]
            for section, (array, length) in sections(start):
                if section != b"__objc_classlist": continue
                if length > 1048576 or length % 8:
                    raise ValueError("invalid class list")
                for offset in range(0, length, 8):
                    cls = pointer(array + offset)
                    ro = pointer(cls + 32) & ~7
                    name = string(pointer(ro + 24))
                    if wanted.startswith("*"):
                        if wanted[1:] in name: print("CLASSNAME", name)
                        continue
                    if name != wanted: continue
                    print("CLASS", name, hex(cls))
                    if sys.argv[2] == "--ivars":
                        ivars = pointer(ro + 48)
                        if not ivars: return
                        stride, entries = struct.unpack("<II", read(ivars, 8))
                        if entries > 4096 or stride != 32:
                            raise ValueError("invalid ivar list")
                        for index in range(entries):
                            entry = ivars + 8 + index * stride
                            offset, = struct.unpack("<I", read(pointer(entry), 4))
                            print("IVAR", hex(offset), string(pointer(entry + 8)),
                                  string(pointer(entry + 16)))
                        return
                    mlist = pointer(ro + 32)
                    if not mlist: return
                    flags, entries = struct.unpack("<II", read(mlist, 8))
                    stride = flags & 0xfffc
                    if entries > 4096 or stride not in (12, 24):
                        raise ValueError("invalid method list")
                    selectors = selector_base() if flags & 0x40000000 else 0
                    for index in range(entries):
                        entry = mlist + 8 + index * stride
                        if flags & 0x80000000:
                            nameoff, typeoff, impoff = struct.unpack("<iii", read(entry, 12))
                            nameaddr = selectors + nameoff if selectors else pointer(entry + nameoff)
                            print(hex(entry + 8 + impoff), string(nameaddr), string(entry + 4 + typeoff))
                        else:
                            print(hex(pointer(entry + 16)), string(pointer(entry)), string(pointer(entry + 8)))
                    return
            return

        print("CODE", hex(start), read(start, count).hex())
        for address in range(start, start + count - 3, 4):
            code, = struct.unpack("<I", read(address, 4))
            if code & 0x7c000000 != 0x14000000:  # BL or tail-call B
                continue
            delta = code & ((1 << 26) - 1)
            if delta & (1 << 25): delta -= 1 << 26
            target = address + delta * 4
            try:
                adrp, ldr = struct.unpack("<II", read(target, 8))
                if adrp & 0x9f00001f != 0x90000001:
                    continue
                imm = ((adrp >> 29) & 3) | (((adrp >> 5) & 0x7ffff) << 2)
                if imm & (1 << 20): imm -= 1 << 21
                ref = (target & ~4095) + imm * 4096 + ((ldr >> 10) & 0xfff) * 8
                name = read(pointer(ref), 256).split(b"\0")[0].decode(errors="replace")
                print("SELECTOR", hex(address), name)
            except ValueError:
                pass
    finally:
        for stream in opened:
            stream.close()
        signal.alarm(0)


if __name__ == "__main__":
    main()

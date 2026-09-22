"""Read a bounded cache image table to identify one unslid address; no extraction."""
from pathlib import Path
import signal
import struct
import sys


def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: CACHE_FILE UNSLID_ADDRESS")
    address = int(sys.argv[2], 0)
    signal.alarm(8)
    with Path(sys.argv[1]).open("rb") as stream:
        header = stream.read(512)
        if len(header) != 512 or not header.startswith(b"dyld_v1"):
            raise ValueError("invalid cache")
        offset, count = struct.unpack_from("<II", header, 0x1c0)
        size = stream.seek(0, 2)
        if not 0 < count <= 32768 or offset + count * 32 > size:
            raise ValueError("invalid image table")
        nearest = None
        for index in range(count):
            stream.seek(offset + index * 32)
            base, _, _, path_offset, _ = struct.unpack("<QQQII", stream.read(32))
            if base <= address and (nearest is None or base > nearest[0]):
                nearest = (base, path_offset)
        if nearest is None or nearest[1] >= size:
            raise ValueError("no image")
        stream.seek(nearest[1])
        path = stream.read(1024).split(b"\0")[0].decode()
        print(f"nearest-image base={nearest[0]:#x} offset={address-nearest[0]:#x} path={path}")
        print("membership=UNVERIFIED (nearest image start, not segment containment)")


if __name__ == "__main__":
    main()

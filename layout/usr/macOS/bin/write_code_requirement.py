"""Write an ldid-compatible designated-requirements blob.

The jailbreak's ldid accepts a raw requirements superblob through -Q.  Using
that path preserves a real designated requirement while still allowing ldid
to merge MacWS entitlements and produce trustcache-compatible CodeDirectories.
"""

import pathlib
import re
import struct
import sys


CSMAGIC_REQUIREMENTS = 0xFADE0C01
CSMAGIC_REQUIREMENT = 0xFADE0C00
CSSLOT_REQUIREMENTS = 3
REQUIREMENT_KIND_EXPLICIT = 1
REQUIREMENT_OP_IDENTIFIER = 2


def identifier_requirement(identifier: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", identifier):
        raise ValueError(f"unsupported code identifier: {identifier!r}")
    encoded = identifier.encode("utf-8")
    padded = encoded + b"\0" * ((-len(encoded)) & 3)
    inner = struct.pack(
        ">IIIII",
        CSMAGIC_REQUIREMENT,
        20 + len(padded),
        REQUIREMENT_KIND_EXPLICIT,
        REQUIREMENT_OP_IDENTIFIER,
        len(encoded),
    ) + padded
    outer_length = 20 + len(inner)
    return (
        struct.pack(">III", CSMAGIC_REQUIREMENTS, outer_length, 1)
        + struct.pack(">II", CSSLOT_REQUIREMENTS, 20)
        + inner
    )


def main() -> int:
    if len(sys.argv) != 3:
        print(
            f"usage: {sys.argv[0]} bundle.identifier output.bin",
            file=sys.stderr,
        )
        return 64
    output = pathlib.Path(sys.argv[2])
    output.write_bytes(identifier_requirement(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

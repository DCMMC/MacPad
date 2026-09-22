"""Port VS Code 1.130.0's Electron virtual-address geometry to iPadOS.

The arm64 Electron Framework bundled with VS Code 1.130.0 is built with the
macOS PartitionAlloc constants: two 16-GiB core pools are reserved as one
32-GiB, 32-GiB-aligned mapping.  iPadOS on the target iPad13,6 cannot create
that mapping, but it can create the two 8-GiB pools used by Chromium's iOS
build.  This tool changes the inlined core/glued-pool *mask operations*,
super-page table indices, and the PartitionAddressSpace::Init allocation
geometry together.

Do not rewrite every instruction which happens to materialize the same
number.  For example, V8 packs AstNode::position_=-1 and kBlock=7 into
0x00000007ffffffff.  Rewriting that ORR/MOV alias as if it were PartitionAlloc's
32-GiB mask changes kBlock to kWhileStatement and corrupts the AST dispatcher.
AND/ANDS logical-immediate instructions apply masks and are safe to rewrite.
Two separately locked ORR/MOV aliases initialize PartitionRoot's regular/BRP
PoolOffsetLookup::base_mask_; those must follow the new pool geometry too.
All other numerically equal ORR/EOR constants are counted and retained.

Electron's macOS Oilpan build also requests a 32-GiB compressed-pointer cage.
The target can map at most 8 GiB in one request.  Keep the existing shift-3
compressed-pointer ABI (including its sentinel representation), reduce the
useful cage to Chromium iOS's 4 GiB, and ask for the runtime-confirmed free,
suitably signed 56-GiB address.  This avoids an ABI-incompatible rewrite of
every inlined Blink compressed-pointer operation.

This is intentionally UUID- and count-locked.  It must fail instead of
silently applying a partial port to another Electron build.  Invoke it with
python3; the file has no shebang because AMFI rejects script shebangs on the
target device.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
import sys
from collections import Counter
from functools import lru_cache
from pathlib import Path


EXPECTED_UUID = "4c4c4442-5555-3144-a1a8-564169f3ff00"

# The logical immediates implement the compile-time constants in
# partition_address_space.h.  Each old/new pair preserves one invariant:
# 16-GiB core pool -> 8 GiB, and 32-GiB glued pools -> 16 GiB.
LOGICAL_IMMEDIATE_MAP = {
    0xFFFFFFFC00000000: 0xFFFFFFFE00000000,  # ~(16 GiB - 1)
    0x00000003FFFFFFFF: 0x00000001FFFFFFFF,  # 16 GiB - 1
    0xFFFFFFF800000000: 0xFFFFFFFC00000000,  # ~(32 GiB - 1)
    0x00000007FFFFFFFF: 0x00000003FFFFFFFF,  # 32 GiB - 1
}

# Static-scan-confirmed against the exact framework below.  These are only the
# AND/ANDS instructions which semantically apply the mask.
EXPECTED_LOGICAL_COUNTS = {
    0xFFFFFFFC00000000: 35875,
    0x00000003FFFFFFFF: 460,
    0xFFFFFFF800000000: 59,
    0x00000007FFFFFFFF: 9,
}

# ORR/EOR instructions can carry the same bit patterns for unrelated data.
# Keep their counts locked so a new Electron binary cannot silently move an
# address-mask operation into a form this patcher does not understand.
EXPECTED_RETAINED_LOGICAL_COUNTS = {
    0xFFFFFFFC00000000: 153,
    0x00000003FFFFFFFF: 11,
    0xFFFFFFF800000000: 19,
    0x00000007FFFFFFFF: 38,
}

# Runtime-confirmed with a hardware watchpoint on
# PartitionRoot::reservation_offset_table_.offset_lookup_.base_mask_: these
# two MOV aliases supply the regular- and BRP-pool root constructors with
# ~(16 GiB - 1). Leaving them unchanged makes SetNormalBucketsTag(0x600000000)
# write BRP reservation-table entry 4096 while AcquireInternal reads entry 0.
# Do not generalize this to every ORR carrying the same numeric value: V8 also
# uses logical-immediate MOV aliases for packed non-address data.
CORE_POOL_BASE_MASK_MATERIALIZATIONS = {
    0x0135BC9C: (0xFFFFFFFC00000000, 0xFFFFFFFE00000000),
    0x0135BD28: (0xFFFFFFFC00000000, 0xFFFFFFFE00000000),
}

# Some ReservationOffsetTable inlines materialize CorePoolSize() - 1 with a
# MOV/ORR alias and then apply it through a register-form AND.  The generic
# logical-immediate pass cannot see the semantic mask on the later register
# instruction, while rewriting every equal MOV would corrupt unrelated packed
# constants.  Per-binary manifests may therefore lock the exact PA sites here.
# Electron 39 did not need an additional set beyond its existing folded UBFX
# sites; Chrome 150 supplies its own map from the wrapper module.
CORE_POOL_OFFSET_MASK_MATERIALIZATIONS: dict[int, tuple[int, int]] = {}

# PartitionAddressSpace::Init() can also materialize a negative core-pool
# size through the MOV/ORR alias form.  It is not a mask even though it has
# the same bit pattern as one: InitMetadataRegionAndOffsets() subtracts the
# BRP pool base from the external metadata mapping.  Keep these sites in a
# separate, per-binary manifest so their arithmetic meaning is explicit.
INIT_LOGICAL_IMMEDIATE_PATCHES: dict[int, tuple[int, int]] = {}

# File/VM offsets are identical in this Mach-O's __TEXT segment.  These are
# the six positive constants in PartitionAddressSpace::Init.  The adjacent
# negative masks are covered by LOGICAL_IMMEDIATE_MAP.
INIT_MOVE_WIDE_PATCHES = {
    0x0135C3BC: (32 << 30, 16 << 30),  # glued allocation size
    0x0135C3C0: (32 << 30, 16 << 30),  # glued allocation alignment
    0x0135C3E4: (16 << 30, 8 << 30),   # BRP base = regular + core size
    0x0135C404: (16 << 30, 8 << 30),   # AddressPoolManager regular size
    0x0135C418: (16 << 30, 8 << 30),   # AddressPoolManager BRP size
    0x0135C454: (16 << 30, 8 << 30),   # metadata-region size
}

# ReservationOffsetTable::GetOffsetPointer() compiles
#
#   (address & (CorePoolSize() - 1)) >> kSuperPageShift
#
# into UBFX X8, X0, #21, #13 for a 16-GiB pool. Logical-immediate scanning
# cannot see this folded bit-field operation. An 8-GiB pool needs 12 bits;
# otherwise the BRP pool (whose new base is 24 GiB) aliases to entry 4096
# instead of entry 0 and unallocated spans are mistaken for direct maps.
# These are all six occurrences in this exact __text section.
CORE_POOL_SUPERPAGE_INDEX_PATCHES = {
    0x00006328,
    0x000075B8,
    0x00033A04,
    0x0005BBA0,
    0x003401A0,
    0x0543B7E0,
}
UBFX_21_13_X8_X0 = 0xD3558408
UBFX_21_12_X8_X0 = 0xD3558008

# Oilpan's compressed-pointer CagedHeap uses the same 16-GiB alignment mask,
# so the logical-immediate pass already ports its inlined base/offset masks.
# ReserveCagedHeap() and the unrolled four-attempt fallback also materialize
# positive sizes with MOVZ, which must be changed as one geometry invariant:
# 16-GiB useful cage -> 4 GiB and 32-GiB first attempt -> 8 GiB.  Keep the
# already-ported 8-GiB alignment: 56 GiB is aligned to it, and this lets all
# existing inlined cage-base masks remain coherent with one another.
#
# Do not change kPointerCompressionShift from 3 to 1 here.  That is a public
# inline ABI choice in this already-built framework: changing it would also
# require changing tens of thousands of Blink inline sites and the raw
# SentinelPointer value.  A 4-GiB cage at 56 GiB has address bit 34 set for
# its entire span, so the existing sign-extending compression remains valid.
CPPGC_CAGE_MOVE_WIDE_PATCHES = {
    0x01FBC114: (16 << 30, 4 << 30),  # useful reservation size
    0x01FBC120: (32 << 30, 8 << 30),  # first-attempt size
    0x01FBC124: (16 << 30, 8 << 30),  # reservation alignment
    # If the first 8-GiB mapping does not have compressed-pointer bit 34 set,
    # neither of its 4-GiB halves can fix that bit (they differ at bit 32).
    # The branch patch below sends that path to the independently aligned
    # fallback attempts, so release the complete first reservation here.
    0x01FBC150: (16 << 30, 8 << 30),  # reject: free complete first attempt
    0x01FBC1D8: (16 << 30, 4 << 30),  # clamp maximum heap size
    0x01FBC2C8: (16 << 30, 4 << 30),  # trim upper half
    0x01FBC328: (16 << 30, 4 << 30),  # fallback attempt 1 size
    0x01FBC32C: (16 << 30, 8 << 30),  # fallback attempt 1 alignment
    0x01FBC364: (16 << 30, 4 << 30),  # fallback attempt 2 size
    0x01FBC368: (16 << 30, 8 << 30),  # fallback attempt 2 alignment
    0x01FBC3A0: (16 << 30, 4 << 30),  # fallback attempt 3 size
    0x01FBC3A4: (16 << 30, 8 << 30),  # fallback attempt 3 alignment
    0x01FBC3DC: (16 << 30, 4 << 30),  # fallback attempt 4 size
    0x01FBC3E0: (16 << 30, 8 << 30),  # fallback attempt 4 alignment
}

# The source-level 16-GiB boundary is also folded into shifts.  The two LSRs
# below are alignment/max-size operations, not pointer compression.  The
# alignment check follows 16 -> 8 GiB, while the maximum-size clamp follows
# 16 -> 4 GiB.  The TBNZ X0,#34 selection in the first reservation path
# intentionally remains unchanged: bit 34 is required by the preserved
# shift-3 compression ABI.
CPPGC_CAGE_WORD_PATCHES = {
    0x01FBC188: (0xD362FD29, 0xD361FD29),  # LSR X9,X9,#34 -> #33
    0x01FBC1D4: (0xD362FD0A, 0xD360FD0A),  # LSR X10,X8,#34 -> #32
    # GetRandomMmapAddr() in the macOS build produced runtime hint
    # 0x13e00000000, outside the iPad process's usable sub-64-GiB VA range.
    # v5 LLDB showed only 128 MiB free at 48 GiB but a continuous [56,63) GiB
    # hole.  A 56-GiB hint is 8-GiB aligned and keeps bit 34 set across the
    # complete 4-GiB cage.  Supplying an already aligned hint also lets
    # PageAllocator try the exact 4-GiB mapping before its over-allocation
    # alignment fallback.
    # The old AND words below are their post-logical-pass 8-GiB forms.
    0x01FBC118: (0x925F7801, 0xD2C001C1),  # MOV X1,#0xe00000000
    # First-attempt geometry after this port is 8 GiB = two 4-GiB halves.
    # When bit 34 of the aligned start is clear, adding 4 GiB cannot set it.
    # After freeing the complete 8-GiB attempt at +0x150, enter the existing
    # four-attempt path, which both enforces 8-GiB alignment and tests bit 34.
    0x01FBC158: (0xD2800008, 0x1400006C),  # MOV X8,#0 -> B +0x1b0
    # CageBaseGlobalUpdater::UpdateCageBase must use the same new 8-GiB
    # lower-half mask as every inlined Compress/Decompress operation.  This
    # ORR is intentionally one of the few semantic OR aliases; leaving the
    # macOS 16-GiB mask here produced runtime base 0xbffffffff and decompressed
    # a valid 0xb020... object into unmapped 0x3020... memory.
    0x01FBC190: (0xB24086C9, 0xB24082C9),  # ORR X9,X22,#(8 GiB - 1)
    0x01FBC320: (0x925F7904, 0xD2C001C4),  # MOV X4,#0xe00000000, attempt 1
    0x01FBC35C: (0x925F7904, 0xD2C001C4),  # MOV X4,#0xe00000000, attempt 2
    0x01FBC398: (0x925F7904, 0xD2C001C4),  # MOV X4,#0xe00000000, attempt 3
    0x01FBC3D4: (0x925F7904, 0xD2C001C4),  # MOV X4,#0xe00000000, attempt 4
}

# CageBaseGlobal::g_base_.base is deliberately initialized to the reservation
# alignment minus one, not zero.  IsSet() masks those low bits away.  Changing
# the inlined alignment to 8 GiB without changing this initializer makes the
# old bit 33 look like an already-installed cage and trips the real
# CHECK(!CageBaseGlobal::IsSet()).  `nm -nm` identifies this UUID-locked
# __DATA,__data symbol at VM/file offset 0xb142a00.
CPPGC_CAGE_DATA_PATCHES = {
    0x0B142A00: (16 * 1024**3 - 1, 8 * 1024**3 - 1),
}

LC_SEGMENT_64 = 0x19
LC_UUID = 0x1B
MH_MAGIC_64 = 0xFEEDFACF


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_macho(data: bytearray) -> tuple[str, int, int, int]:
    if len(data) < 32:
        raise ValueError("file is too small for a Mach-O header")
    magic, _, _, _, ncmds, sizeofcmds, _, _ = struct.unpack_from(
        "<IiiIIIII", data, 0
    )
    if magic != MH_MAGIC_64:
        raise ValueError(f"expected thin little-endian Mach-O 64, got {magic:#x}")
    if 32 + sizeofcmds > len(data):
        raise ValueError("load commands extend beyond file")

    cursor = 32
    image_uuid: str | None = None
    text: tuple[int, int, int] | None = None
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from("<II", data, cursor)
        if cmdsize < 8 or cursor + cmdsize > 32 + sizeofcmds:
            raise ValueError("invalid Mach-O load command size")
        if cmd == LC_UUID:
            raw = bytes(data[cursor + 8 : cursor + 24])
            image_uuid = (
                raw[0:4].hex()
                + "-"
                + raw[4:6].hex()
                + "-"
                + raw[6:8].hex()
                + "-"
                + raw[8:10].hex()
                + "-"
                + raw[10:16].hex()
            )
        elif cmd == LC_SEGMENT_64:
            segment = struct.unpack_from("<16sQQQQiiII", data, cursor + 8)
            segment_name = segment[0].split(b"\0", 1)[0]
            nsects = segment[7]
            section_cursor = cursor + 72
            for _ in range(nsects):
                section = struct.unpack_from("<16s16sQQIIIIIIII", data, section_cursor)
                section_name = section[0].split(b"\0", 1)[0]
                section_segment = section[1].split(b"\0", 1)[0]
                if section_segment == b"__TEXT" and section_name == b"__text":
                    text = (section[2], section[3], section[4])
                section_cursor += 80
        cursor += cmdsize

    if image_uuid is None or text is None:
        raise ValueError("Mach-O UUID or __TEXT,__text section is missing")
    return image_uuid, *text


def rotate_right(value: int, amount: int, width: int) -> int:
    mask = (1 << width) - 1
    amount %= width
    return ((value >> amount) | (value << (width - amount))) & mask


def decode_logical_immediate(word: int) -> int | None:
    # All four logical-immediate opcodes share this fixed bit pattern when sf,
    # opc, N, immr, imms and registers are masked out.
    if (word & 0x1F800000) != 0x12000000:
        return None
    width = 64 if word >> 31 else 32
    n = (word >> 22) & 1
    immr = (word >> 16) & 0x3F
    imms = (word >> 10) & 0x3F
    value = (n << 6) | ((~imms) & 0x3F)
    length = value.bit_length() - 1
    if length < 1:
        return None
    levels = (1 << length) - 1
    s = imms & levels
    r = immr & levels
    if s == levels:
        return None
    element_width = 1 << length
    element = rotate_right((1 << (s + 1)) - 1, r, element_width)
    result = 0
    for shift in range(0, width, element_width):
        result |= element << shift
    return result & ((1 << width) - 1)


@lru_cache(maxsize=None)
def logical_immediate_fields(value: int, width: int = 64) -> tuple[int, int, int]:
    for n in range(2):
        for immr in range(64):
            for imms in range(64):
                candidate = (
                    (1 << 31 if width == 64 else 0)
                    | 0x12000000
                    | (n << 22)
                    | (immr << 16)
                    | (imms << 10)
                )
                if decode_logical_immediate(candidate) == value:
                    return n, immr, imms
    raise ValueError(f"value {value:#x} is not an AArch64 logical immediate")


def rewrite_logical_immediate(word: int, value: int) -> int:
    n, immr, imms = logical_immediate_fields(value)
    fields = (1 << 22) | (0x3F << 16) | (0x3F << 10)
    return (word & ~fields) | (n << 22) | (immr << 16) | (imms << 10)


def is_logical_mask_operation(word: int) -> bool:
    """Return true only for A64 AND/ANDS-immediate mask operations."""
    opcode = (word >> 29) & 3
    return opcode in (0, 3)


def decode_move_wide(word: int) -> int | None:
    if (word & 0x1F800000) != 0x12800000 or not (word >> 31):
        return None
    opcode = (word >> 29) & 3
    shift = ((word >> 21) & 3) * 16
    immediate = (word >> 5) & 0xFFFF
    if opcode == 2:  # MOVZ
        return immediate << shift
    if opcode == 0:  # MOVN
        return (~(immediate << shift)) & 0xFFFFFFFFFFFFFFFF
    return None


def rewrite_move_wide(word: int, value: int) -> int:
    # The six locked call-site constants are positive powers of two and remain
    # single-instruction MOVZ values after shrinking.
    for hardware_shift in range(4):
        shift = hardware_shift * 16
        if value & ~((0xFFFF) << shift) == 0:
            immediate = (value >> shift) & 0xFFFF
            fields = (3 << 29) | (3 << 21) | (0xFFFF << 5)
            return (
                (word & ~fields)
                | (2 << 29)
                | (hardware_shift << 21)
                | (immediate << 5)
            )
    raise ValueError(f"value {value:#x} is not a single MOVZ immediate")


def patch(input_path: Path, output_path: Path) -> dict[str, object]:
    if input_path.resolve() == output_path.resolve():
        raise ValueError("input and output must be different; preserve the original")
    data = bytearray(input_path.read_bytes())
    image_uuid, text_address, text_size, text_offset = parse_macho(data)
    if image_uuid.lower() != EXPECTED_UUID:
        raise ValueError(
            f"unsupported Electron Framework UUID {image_uuid}; expected {EXPECTED_UUID}"
        )
    if text_address != text_offset:
        raise ValueError(
            "this manifest requires __text VM addresses to equal file offsets"
        )
    if text_offset + text_size > len(data):
        raise ValueError("__text extends beyond file")

    original_hash = hashlib.sha256(data).hexdigest()
    logical_counts: Counter[int] = Counter()
    retained_logical_counts: Counter[int] = Counter()
    base_mask_materializations: list[dict[str, str]] = []
    offset_mask_materializations: list[dict[str, str]] = []
    init_logical_patches: list[dict[str, str]] = []
    changed_offsets: list[int] = []
    for offset in range(text_offset, text_offset + text_size, 4):
        word = struct.unpack_from("<I", data, offset)[0]
        immediate = decode_logical_immediate(word)
        if immediate not in LOGICAL_IMMEDIATE_MAP:
            continue
        if not is_logical_mask_operation(word):
            materialization = CORE_POOL_BASE_MASK_MATERIALIZATIONS.get(offset)
            if materialization is not None:
                old_value, new_value = materialization
                opcode = (word >> 29) & 3
                source_register = (word >> 5) & 31
                if immediate != old_value or opcode != 1 or source_register != 31:
                    raise ValueError(
                        f"offset {offset:#x}: expected MOV/ORR Xd, XZR, "
                        f"{old_value:#018x}, got word {word:#010x} "
                        f"immediate {immediate:#018x}"
                    )
                replacement = rewrite_logical_immediate(word, new_value)
                struct.pack_into("<I", data, offset, replacement)
                changed_offsets.append(offset)
                base_mask_materializations.append(
                    {
                        "offset": f"{offset:#x}",
                        "old": f"{old_value:#018x}",
                        "new": f"{new_value:#018x}",
                    }
                )
                continue
            materialization = CORE_POOL_OFFSET_MASK_MATERIALIZATIONS.get(offset)
            if materialization is not None:
                old_value, new_value = materialization
                opcode = (word >> 29) & 3
                source_register = (word >> 5) & 31
                if immediate != old_value or opcode != 1 or source_register != 31:
                    raise ValueError(
                        f"offset {offset:#x}: expected MOV/ORR Xd, XZR, "
                        f"{old_value:#018x}, got word {word:#010x} "
                        f"immediate {immediate:#018x}"
                    )
                replacement = rewrite_logical_immediate(word, new_value)
                struct.pack_into("<I", data, offset, replacement)
                changed_offsets.append(offset)
                offset_mask_materializations.append(
                    {
                        "offset": f"{offset:#x}",
                        "old": f"{old_value:#018x}",
                        "new": f"{new_value:#018x}",
                    }
                )
                continue
            materialization = INIT_LOGICAL_IMMEDIATE_PATCHES.get(offset)
            if materialization is not None:
                old_value, new_value = materialization
                opcode = (word >> 29) & 3
                source_register = (word >> 5) & 31
                if immediate != old_value or opcode != 1 or source_register != 31:
                    raise ValueError(
                        f"offset {offset:#x}: expected MOV/ORR Xd, XZR, "
                        f"{old_value:#018x}, got word {word:#010x} "
                        f"immediate {immediate:#018x}"
                    )
                replacement = rewrite_logical_immediate(word, new_value)
                struct.pack_into("<I", data, offset, replacement)
                changed_offsets.append(offset)
                init_logical_patches.append(
                    {
                        "offset": f"{offset:#x}",
                        "old": f"{old_value:#018x}",
                        "new": f"{new_value:#018x}",
                    }
                )
                continue
            retained_logical_counts[immediate] += 1
            continue
        logical_counts[immediate] += 1
        replacement = rewrite_logical_immediate(
            word, LOGICAL_IMMEDIATE_MAP[immediate]
        )
        struct.pack_into("<I", data, offset, replacement)
        changed_offsets.append(offset)

    if dict(logical_counts) != EXPECTED_LOGICAL_COUNTS:
        printable = {f"{key:#018x}": value for key, value in logical_counts.items()}
        expected = {
            f"{key:#018x}": value for key, value in EXPECTED_LOGICAL_COUNTS.items()
        }
        raise ValueError(
            f"logical-immediate manifest mismatch: got {printable}, expected {expected}"
        )

    if dict(retained_logical_counts) != EXPECTED_RETAINED_LOGICAL_COUNTS:
        printable = {
            f"{key:#018x}": value for key, value in retained_logical_counts.items()
        }
        expected = {
            f"{key:#018x}": value
            for key, value in EXPECTED_RETAINED_LOGICAL_COUNTS.items()
        }
        raise ValueError(
            "retained logical-immediate manifest mismatch: "
            f"got {printable}, expected {expected}"
        )

    if len(base_mask_materializations) != len(CORE_POOL_BASE_MASK_MATERIALIZATIONS):
        found = {entry["offset"] for entry in base_mask_materializations}
        expected = {f"{offset:#x}" for offset in CORE_POOL_BASE_MASK_MATERIALIZATIONS}
        raise ValueError(
            "core-pool base-mask materialization mismatch: "
            f"found {sorted(found)}, expected {sorted(expected)}"
        )

    if len(offset_mask_materializations) != len(
        CORE_POOL_OFFSET_MASK_MATERIALIZATIONS
    ):
        found = {entry["offset"] for entry in offset_mask_materializations}
        expected = {
            f"{offset:#x}" for offset in CORE_POOL_OFFSET_MASK_MATERIALIZATIONS
        }
        raise ValueError(
            "core-pool offset-mask materialization mismatch: "
            f"found {sorted(found)}, expected {sorted(expected)}"
        )

    if len(init_logical_patches) != len(INIT_LOGICAL_IMMEDIATE_PATCHES):
        found = {entry["offset"] for entry in init_logical_patches}
        expected = {f"{offset:#x}" for offset in INIT_LOGICAL_IMMEDIATE_PATCHES}
        raise ValueError(
            "init logical-immediate materialization mismatch: "
            f"found {sorted(found)}, expected {sorted(expected)}"
        )

    move_patches: list[dict[str, object]] = []
    for offset, (old_value, new_value) in INIT_MOVE_WIDE_PATCHES.items():
        if not (text_offset <= offset < text_offset + text_size):
            raise ValueError(f"locked offset {offset:#x} is outside __text")
        word = struct.unpack_from("<I", data, offset)[0]
        actual = decode_move_wide(word)
        if actual != old_value:
            raise ValueError(
                f"offset {offset:#x}: got {actual!r}, expected MOVZ {old_value:#x}"
            )
        replacement = rewrite_move_wide(word, new_value)
        struct.pack_into("<I", data, offset, replacement)
        changed_offsets.append(offset)
        move_patches.append(
            {"offset": f"{offset:#x}", "old": f"{old_value:#x}", "new": f"{new_value:#x}"}
        )

    cppgc_move_patches: list[dict[str, object]] = []
    for offset, (old_value, new_value) in CPPGC_CAGE_MOVE_WIDE_PATCHES.items():
        if not (text_offset <= offset < text_offset + text_size):
            raise ValueError(f"locked offset {offset:#x} is outside __text")
        word = struct.unpack_from("<I", data, offset)[0]
        actual = decode_move_wide(word)
        if actual != old_value:
            raise ValueError(
                f"offset {offset:#x}: got {actual!r}, expected MOVZ {old_value:#x}"
            )
        replacement = rewrite_move_wide(word, new_value)
        struct.pack_into("<I", data, offset, replacement)
        changed_offsets.append(offset)
        cppgc_move_patches.append(
            {"offset": f"{offset:#x}", "old": f"{old_value:#x}", "new": f"{new_value:#x}"}
        )

    cppgc_word_patches: list[dict[str, str]] = []
    for offset, (old_word, new_word) in CPPGC_CAGE_WORD_PATCHES.items():
        if not (text_offset <= offset < text_offset + text_size):
            raise ValueError(f"locked offset {offset:#x} is outside __text")
        word = struct.unpack_from("<I", data, offset)[0]
        if word != old_word:
            raise ValueError(
                f"offset {offset:#x}: got {word:#010x}, expected {old_word:#010x}"
            )
        struct.pack_into("<I", data, offset, new_word)
        changed_offsets.append(offset)
        cppgc_word_patches.append(
            {"offset": f"{offset:#x}", "old": f"{old_word:#010x}", "new": f"{new_word:#010x}"}
        )

    cppgc_data_patches: list[dict[str, str]] = []
    for offset, (old_value, new_value) in CPPGC_CAGE_DATA_PATCHES.items():
        if offset + 8 > len(data):
            raise ValueError(f"locked data offset {offset:#x} is outside the file")
        value = struct.unpack_from("<Q", data, offset)[0]
        if value != old_value:
            raise ValueError(
                f"data offset {offset:#x}: got {value:#018x}, "
                f"expected {old_value:#018x}"
            )
        struct.pack_into("<Q", data, offset, new_value)
        cppgc_data_patches.append(
            {"offset": f"{offset:#x}", "old": f"{old_value:#018x}", "new": f"{new_value:#018x}"}
        )

    superpage_index_patches: list[str] = []
    for offset in sorted(CORE_POOL_SUPERPAGE_INDEX_PATCHES):
        if not (text_offset <= offset < text_offset + text_size):
            raise ValueError(f"locked offset {offset:#x} is outside __text")
        word = struct.unpack_from("<I", data, offset)[0]
        if word != UBFX_21_13_X8_X0:
            raise ValueError(
                f"offset {offset:#x}: got {word:#010x}, "
                f"expected UBFX #21,#13 word {UBFX_21_13_X8_X0:#010x}"
            )
        struct.pack_into("<I", data, offset, UBFX_21_12_X8_X0)
        changed_offsets.append(offset)
        superpage_index_patches.append(f"{offset:#x}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(data)
    shutil.copymode(input_path, output_path)
    return {
        "uuid": image_uuid,
        "input_sha256": original_hash,
        "output_sha256": hashlib.sha256(data).hexdigest(),
        "text_address": f"{text_address:#x}",
        "text_size": f"{text_size:#x}",
        "logical_counts": {
            f"{key:#018x}": logical_counts[key] for key in LOGICAL_IMMEDIATE_MAP
        },
        "retained_logical_counts": {
            f"{key:#018x}": retained_logical_counts[key]
            for key in LOGICAL_IMMEDIATE_MAP
        },
        "core_pool_base_mask_materializations": base_mask_materializations,
        "core_pool_offset_mask_materializations": offset_mask_materializations,
        "init_logical_immediate_patches": init_logical_patches,
        "move_wide_patches": move_patches,
        "cppgc_cage_move_wide_patches": cppgc_move_patches,
        "cppgc_cage_word_patches": cppgc_word_patches,
        "cppgc_cage_data_patches": cppgc_data_patches,
        "core_pool_superpage_index_patches": superpage_index_patches,
        "total_instructions_changed": len(changed_offsets),
        "first_changed_offsets": [f"{offset:#x}" for offset in changed_offsets[:16]],
        "last_changed_offsets": [f"{offset:#x}" for offset in changed_offsets[-16:]],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        manifest = patch(args.input, args.output)
    except (OSError, ValueError, struct.error) as error:
        print(f"patch_electron_pa_ios_va: {error}", file=sys.stderr)
        return 1
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

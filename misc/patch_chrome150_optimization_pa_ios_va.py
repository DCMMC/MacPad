"""Port Chrome 150's optimization-guide PartitionAlloc copy to iPadOS.

Google Chrome 150.0.7871.187 embeds a second copy of PartitionAlloc in
liboptimization_guide_internal.dylib.  Its macOS build independently reserves
two 16-GiB core pools as one 32-GiB-aligned 32-GiB mapping.  The main Chrome
Framework's already-ported allocator occupies 24 GiB (runtime-confirmed in
Google Chrome-2026-07-29-015329.ips), after which this library's earlier
8-GiB-core port still failed its 16-GiB glued mapping and reached the real
HandlePoolAllocFailure() BRK at +0xb4a7a8.  This UUID-locked manifest gives the
secondary allocator a self-consistent 1-GiB core geometry: 2 GiB glued and
1 GiB external metadata.  That is four times Chromium's own 256-MiB iOS test
pool while adding only 3 GiB of reserved VA beside the primary allocator.

The dylib also has a large ML implementation with unrelated numeric constants.
For that reason every non-AND materialization below is individually classified
and locked.  The patch does not skip PartitionAlloc's failure trap: it changes
the allocation, root masks, reservation-table indexing, and external-metadata
offset arithmetic as one invariant.

Invoke with python3.  There is deliberately no shebang because AMFI rejects
script shebangs on the target device.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

import patch_electron_pa_ios_va as engine


EXPECTED_UUID = "4c4c448b-5555-3144-a117-5b3d8107eb3c"

LOGICAL_IMMEDIATE_MAP = {
    0xFFFFFFFC00000000: 0xFFFFFFFFC0000000,  # ~(16 GiB - 1) -> ~(1 GiB - 1)
    0x00000003FFFFFFFF: 0x000000003FFFFFFF,  # 16 GiB - 1 -> 1 GiB - 1
    0xFFFFFFF800000000: 0xFFFFFFFF80000000,  # ~(32 GiB - 1) -> ~(2 GiB - 1)
    0x00000007FFFFFFFF: 0x000000007FFFFFFF,  # 32 GiB - 1 -> 2 GiB - 1
}

# Only AND/ANDS logical immediates are rewritten by the generic pass.  The
# positive pool-offset mask is materialized with MOV and is locked below.
EXPECTED_LOGICAL_COUNTS = {
    0xFFFFFFFC00000000: 1279,
    0xFFFFFFF800000000: 25,
}

# The two root masks and the metadata-offset subtraction are excluded from
# the retained negative-core count.  Five PA table-index MOVs are likewise
# excluded from the retained positive-core count.  The remaining equal MOV /
# ORR values were disassembled and belong to ML packed values or size math.
EXPECTED_RETAINED_LOGICAL_COUNTS = {
    0xFFFFFFFC00000000: 56,
    0x00000003FFFFFFFF: 1,
    0xFFFFFFF800000000: 1,
    0x00000007FFFFFFFF: 1,
}

# PartitionRoot constructor paths for regular and BRP roots.  Each value is
# stored with its pool base at root+0x28 and reused at root+0x48.
CORE_POOL_BASE_MASK_MATERIALIZATIONS = {
    0x00B57C9C: (0xFFFFFFFC00000000, 0xFFFFFFFFC0000000),
    0x00B57E4C: (0xFFFFFFFC00000000, 0xFFFFFFFFC0000000),
}

# ReservationOffsetTable::GetOffsetPointer() is inlined here as
#
#   MOV  Xn, CorePoolSize() - 1
#   AND  Xn, Xn, address
#   LSR  Xn, Xn, #21
#
# rather than as the UBFX form used by some main-Framework call sites.
CORE_POOL_OFFSET_MASK_MATERIALIZATIONS = {
    0x00B5A504: (0x00000003FFFFFFFF, 0x000000003FFFFFFF),
    0x00B5A53C: (0x00000003FFFFFFFF, 0x000000003FFFFFFF),
    0x00B5AB78: (0x00000003FFFFFFFF, 0x000000003FFFFFFF),
    0x00B5ABB8: (0x00000003FFFFFFFF, 0x000000003FFFFFFF),
    0x00D35F0C: (0x00000003FFFFFFFF, 0x000000003FFFFFFF),
}

# InitMetadataRegionAndOffsets() computes
# address - BRPPoolBase() + MetadataInnerOffset(kBRPPoolHandle).  BRP base is
# regular base + CorePoolSize(), so the inlined -16-GiB term must also shrink.
INIT_LOGICAL_IMMEDIATE_PATCHES = {
    0x00B4A6E8: (0xFFFFFFFC00000000, 0xFFFFFFFFC0000000),
}

INIT_MOVE_WIDE_PATCHES = {
    0x00B4A62C: (32 << 30, 2 << 30),  # glued allocation size
    0x00B4A630: (32 << 30, 2 << 30),  # glued allocation alignment
    0x00B4A64C: (16 << 30, 1 << 30),  # BRP base = regular + core size
    0x00B4A66C: (16 << 30, 1 << 30),  # AddressPoolManager regular size
    0x00B4A680: (16 << 30, 1 << 30),  # AddressPoolManager BRP size
    0x00B4A6B0: (16 << 30, 1 << 30),  # external metadata-region size
}


def configure_engine() -> None:
    engine.EXPECTED_UUID = EXPECTED_UUID
    engine.LOGICAL_IMMEDIATE_MAP = LOGICAL_IMMEDIATE_MAP
    engine.EXPECTED_LOGICAL_COUNTS = EXPECTED_LOGICAL_COUNTS
    engine.EXPECTED_RETAINED_LOGICAL_COUNTS = EXPECTED_RETAINED_LOGICAL_COUNTS
    engine.CORE_POOL_BASE_MASK_MATERIALIZATIONS = (
        CORE_POOL_BASE_MASK_MATERIALIZATIONS
    )
    engine.CORE_POOL_OFFSET_MASK_MATERIALIZATIONS = (
        CORE_POOL_OFFSET_MASK_MATERIALIZATIONS
    )
    engine.INIT_LOGICAL_IMMEDIATE_PATCHES = INIT_LOGICAL_IMMEDIATE_PATCHES
    engine.INIT_MOVE_WIDE_PATCHES = INIT_MOVE_WIDE_PATCHES
    engine.CORE_POOL_SUPERPAGE_INDEX_PATCHES = set()
    engine.CPPGC_CAGE_MOVE_WIDE_PATCHES = {}
    engine.CPPGC_CAGE_WORD_PATCHES = {}
    engine.CPPGC_CAGE_DATA_PATCHES = {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    configure_engine()
    try:
        manifest = engine.patch(args.input, args.output)
    except (OSError, ValueError, struct.error) as error:
        print(
            f"patch_chrome150_optimization_pa_ios_va: {error}",
            file=sys.stderr,
        )
        return 1
    manifest["profile"] = (
        "Chrome 150.0.7871.187 optimization-guide arm64 / iPadOS secondary VA"
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

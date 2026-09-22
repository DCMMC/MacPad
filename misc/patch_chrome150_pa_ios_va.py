"""Port Chrome 150.0.7871.187's PartitionAlloc geometry to iPadOS.

This is the Chrome-specific, UUID-locked manifest for the transformation
engine in patch_electron_pa_ios_va.py.  It changes the complete set of
compile-time pool masks, root base-mask materializations, super-page table
indices, pool-allocation constants, and Oilpan compressed-pointer cage state
as one invariant-preserving 16-GiB -> 8-GiB geometry port.

This does not accept an unaligned allocation or bypass PartitionAlloc's
checks.  Every count, fixed instruction, and data value is validated against
the exact arm64 Chrome Framework before an output file is written.  Invoke
with python3; the file deliberately has no shebang for the target's AMFI.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

import patch_electron_pa_ios_va as engine


EXPECTED_UUID = "4c4c4442-5555-3144-a16b-cc66de5d6913"

EXPECTED_LOGICAL_COUNTS = {
    0xFFFFFFFC00000000: 80320,
    0x00000003FFFFFFFF: 548,
    0xFFFFFFF800000000: 179,
    0x00000007FFFFFFFF: 10,
}

# The two locked PartitionRoot materializations below are handled separately,
# so they are intentionally excluded from the retained negative-core count.
EXPECTED_RETAINED_LOGICAL_COUNTS = {
    0xFFFFFFFC00000000: 114,
    0x00000003FFFFFFFF: 14,
    0xFFFFFFF800000000: 24,
    0x00000007FFFFFFFF: 33,
}

CORE_POOL_BASE_MASK_MATERIALIZATIONS = {
    0x0128ABE8: (0xFFFFFFFC00000000, 0xFFFFFFFE00000000),
    0x0128ADE0: (0xFFFFFFFC00000000, 0xFFFFFFFE00000000),
}

# Chrome 150 inlines ReservationOffsetTable::GetOffsetPointer() in two forms.
# Four UBFX sites are covered below.  These nine sites instead materialize
# CorePoolSize() - 1 with MOV/ORR and feed it to a register-form AND.  The
# branch-heads/7871 source requires a pool-relative index; after the core pool
# shrinks from 16 to 8 GiB these must become 8 GiB - 1 as well.  Runtime LLDB
# confirmed the missed +0x6cf2f4 site made malloc_size(0x6000...) read BRP
# table entry 4096 while PartitionRoot writes the same allocation at entry 0.
CORE_POOL_OFFSET_MASK_MATERIALIZATIONS = {
    0x00021CA8: (0x00000003FFFFFFFF, 0x00000001FFFFFFFF),
    0x00021CE8: (0x00000003FFFFFFFF, 0x00000001FFFFFFFF),
    0x00021E28: (0x00000003FFFFFFFF, 0x00000001FFFFFFFF),
    0x00021E60: (0x00000003FFFFFFFF, 0x00000001FFFFFFFF),
    0x005E2864: (0x00000003FFFFFFFF, 0x00000001FFFFFFFF),
    0x006CF2F4: (0x00000003FFFFFFFF, 0x00000001FFFFFFFF),
    0x006CF340: (0x00000003FFFFFFFF, 0x00000001FFFFFFFF),
    0x07772E48: (0x00000003FFFFFFFF, 0x00000001FFFFFFFF),
    0x07772E6C: (0x00000003FFFFFFFF, 0x00000001FFFFFFFF),
}

INIT_MOVE_WIDE_PATCHES = {
    0x0128B500: (32 << 30, 16 << 30),  # glued allocation size
    0x0128B504: (32 << 30, 16 << 30),  # glued allocation alignment
    0x0128B528: (16 << 30, 8 << 30),   # BRP base = regular + core size
    0x0128B548: (16 << 30, 8 << 30),   # AddressPoolManager regular size
    0x0128B55C: (16 << 30, 8 << 30),   # AddressPoolManager BRP size
    0x0128B590: (16 << 30, 8 << 30),   # external metadata-region size
}

CORE_POOL_SUPERPAGE_INDEX_PATCHES = {
    0x0000CA50,
    0x00021AE0,
    0x00022CC8,
    0x00029080,
}

CPPGC_CAGE_MOVE_WIDE_PATCHES = {
    0x025F0B38: (16 << 30, 4 << 30),  # useful reservation size
    0x025F0B44: (32 << 30, 8 << 30),  # first-attempt size
    0x025F0B48: (16 << 30, 8 << 30),  # reservation alignment
    0x025F0B74: (16 << 30, 8 << 30),  # reject: free full first attempt
    0x025F0BFC: (16 << 30, 4 << 30),  # clamp maximum heap size
    0x025F0CEC: (16 << 30, 4 << 30),  # trim upper half
    0x025F0D4C: (16 << 30, 4 << 30),  # fallback attempt 1 size
    0x025F0D50: (16 << 30, 8 << 30),  # fallback attempt 1 alignment
    0x025F0D88: (16 << 30, 4 << 30),  # fallback attempt 2 size
    0x025F0D8C: (16 << 30, 8 << 30),  # fallback attempt 2 alignment
    0x025F0DC4: (16 << 30, 4 << 30),  # fallback attempt 3 size
    0x025F0DC8: (16 << 30, 8 << 30),  # fallback attempt 3 alignment
    0x025F0E00: (16 << 30, 4 << 30),  # fallback attempt 4 size
    0x025F0E04: (16 << 30, 8 << 30),  # fallback attempt 4 alignment
}

CPPGC_CAGE_WORD_PATCHES = {
    # These AND words are their post-logical-pass 8-GiB forms.
    0x025F0B3C: (0x925F7801, 0xD2C001C1),  # MOV X1,#0xe00000000
    0x025F0B7C: (0xD2800008, 0x1400006C),  # failed half -> fallback path
    0x025F0BAC: (0xD362FD29, 0xD361FD29),  # alignment LSR #34 -> #33
    0x025F0BB4: (0xB24086C9, 0xB24082C9),  # cage base mask 16 -> 8 GiB
    0x025F0BF8: (0xD362FD0A, 0xD360FD0A),  # max-size LSR #34 -> #32
    0x025F0D44: (0x925F7904, 0xD2C001C4),  # fallback hint 56 GiB
    0x025F0D80: (0x925F7904, 0xD2C001C4),
    0x025F0DBC: (0x925F7904, 0xD2C001C4),
    0x025F0DF8: (0x925F7904, 0xD2C001C4),
}

CPPGC_CAGE_DATA_PATCHES = {
    # CageBaseGlobal::g_base_.base. IsSet() masks the low alignment bits, so
    # its initial sentinel must change with the inlined alignment mask.
    0x0DB869C0: (16 * 1024**3 - 1, 8 * 1024**3 - 1),
}


def configure_engine() -> None:
    engine.EXPECTED_UUID = EXPECTED_UUID
    engine.EXPECTED_LOGICAL_COUNTS = EXPECTED_LOGICAL_COUNTS
    engine.EXPECTED_RETAINED_LOGICAL_COUNTS = EXPECTED_RETAINED_LOGICAL_COUNTS
    engine.CORE_POOL_BASE_MASK_MATERIALIZATIONS = (
        CORE_POOL_BASE_MASK_MATERIALIZATIONS
    )
    engine.CORE_POOL_OFFSET_MASK_MATERIALIZATIONS = (
        CORE_POOL_OFFSET_MASK_MATERIALIZATIONS
    )
    engine.INIT_LOGICAL_IMMEDIATE_PATCHES = {}
    engine.INIT_MOVE_WIDE_PATCHES = INIT_MOVE_WIDE_PATCHES
    engine.CORE_POOL_SUPERPAGE_INDEX_PATCHES = (
        CORE_POOL_SUPERPAGE_INDEX_PATCHES
    )
    engine.CPPGC_CAGE_MOVE_WIDE_PATCHES = CPPGC_CAGE_MOVE_WIDE_PATCHES
    engine.CPPGC_CAGE_WORD_PATCHES = CPPGC_CAGE_WORD_PATCHES
    engine.CPPGC_CAGE_DATA_PATCHES = CPPGC_CAGE_DATA_PATCHES


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    configure_engine()
    try:
        manifest = engine.patch(args.input, args.output)
    except (OSError, ValueError, struct.error) as error:
        print(f"patch_chrome150_pa_ios_va: {error}", file=sys.stderr)
        return 1
    manifest["profile"] = "Google Chrome 150.0.7871.187 arm64 / iPadOS VA"
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

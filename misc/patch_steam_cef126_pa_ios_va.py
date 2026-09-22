"""Port Steam CEF 126's primary PartitionAlloc geometry to iPadOS.

Steam's macOS arm64 CEF reserves two independent 16-GiB core pools.  The
target iPad cannot reliably fit both after CEF's large image graph is mapped.
This UUID-locked transformation ports the primary PartitionAlloc instance to
the 8-GiB geometry Chromium itself builds for iOS.  It updates the allocation
sizes, every inlined 16-GiB pool-base mask, and the non-folded mask
materializations together.  It never accepts an unaligned mapping or bypasses
an allocator check.

Invoke with python3; this file deliberately has no shebang because the target
device's AMFI rejects exec of scripts carrying shebangs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
import sys
from pathlib import Path

import patch_electron_pa_ios_va as engine


EXPECTED_UUID = "4c4c44c4-5555-3144-a13e-0a3390079bb0"
EXPECTED_INPUT_SHA256S = {
    # Valve's extracted arm64 slice before MacWS signing.
    "bc333167318f3e7b468315de8533c0c5c79c663db16dca4455ddcba9e367306b",
    # The same UUID/code after the production third-party entitlement signer.
    # Runtime-recovered from the installed build on 2026-08-14.  The patch's
    # instruction manifests below still verify every transformed site, so this
    # admits only the known signature-container variant, not arbitrary CEF 126.
    "2072fe50e4d6e0fcf7b928fc1e50783e21ea1299de441a5810bfe5c451dc5b3f",
    # Arm64 slice extracted from Valve's macos-signed-2 universal package for
    # the same build.  The UUID and the complete 54,310-instruction manifest
    # below were independently revalidated before admitting this container.
    "343e02d60ca848927c031eea73f8a938e8e376d6a12fb08cdb0b9fc7b7bc9d5f",
}

# Code-signing rewrites Mach-O load commands and __LINKEDIT, so a whole-file
# digest cannot identify an already-ported image after ldid has signed it.
# The executable __text bytes are invariant across those signatures.  This
# digest was produced by the complete 54,310-instruction transformation below
# and is therefore both cheap to verify and strict enough to reject a partial
# or updater-replaced port.
EXPECTED_PATCHED_TEXT_SHA256 = (
    "56c9c8a25a07f21c9311ef1b6d39e6766bed8217fd09b3952c267db79cd9fc8b"
)

OLD_POOL_BASE_MASK = 0xFFFFFFFC00000000  # ~(16 GiB - 1)
NEW_POOL_BASE_MASK = 0xFFFFFFFE00000000  # ~(8 GiB - 1)
EXPECTED_INLINED_MASK_COUNT = 54298

# RE-confirmed from PartitionAddressSpace::Init in the exact CEF image.  This
# build does not glue the pools: the first triple allocates the regular pool,
# and the second triple allocates/adds the BRP pool and registers both sizes.
POOL_SIZE_SITES = {
    0x03B4DAC0,
    0x03B4DAC4,
    0x03B4DAC8,
    0x03B4DAFC,
    0x03B4DB30,
    0x03B4DB44,
}

# These are the only non-AND materializations tied to the same setup object.
# Nearby equal constants were disassembled and are unrelated numeric codecs,
# ranges, or sentinels, so they are deliberately retained.
POOL_BASE_MASK_MATERIALIZATIONS = {
    0x03B5A2D0,
    0x06080A1C,
    0x06080C08,
    0x06081228,
    0x060814F0,
    0x060817B4,
}


def patch(input_path: Path, output_path: Path) -> dict[str, object]:
    if input_path.resolve() == output_path.resolve():
        raise ValueError("input and output must differ; preserve the original")
    data = bytearray(input_path.read_bytes())
    original_hash = hashlib.sha256(data).hexdigest()
    if original_hash not in EXPECTED_INPUT_SHA256S:
        raise ValueError(
            f"unsupported input SHA-256 {original_hash}; "
            f"expected one of {sorted(EXPECTED_INPUT_SHA256S)}"
        )

    image_uuid, text_address, text_size, text_offset = engine.parse_macho(data)
    if image_uuid.lower() != EXPECTED_UUID:
        raise ValueError(
            f"unsupported CEF UUID {image_uuid}; expected {EXPECTED_UUID}"
        )
    if text_address != text_offset:
        raise ValueError("manifest requires identical __text VM/file offsets")

    changed: list[int] = []
    mask_sites: list[int] = []
    for offset in range(text_offset, text_offset + text_size, 4):
        word = struct.unpack_from("<I", data, offset)[0]
        if engine.decode_logical_immediate(word) != OLD_POOL_BASE_MASK:
            continue
        if not engine.is_logical_mask_operation(word):
            continue
        replacement = engine.rewrite_logical_immediate(word, NEW_POOL_BASE_MASK)
        struct.pack_into("<I", data, offset, replacement)
        changed.append(offset)
        mask_sites.append(offset)
    if len(mask_sites) != EXPECTED_INLINED_MASK_COUNT:
        raise ValueError(
            f"inlined pool-mask count {len(mask_sites)}; "
            f"expected {EXPECTED_INLINED_MASK_COUNT}"
        )

    size_sites: list[int] = []
    for offset in sorted(POOL_SIZE_SITES):
        word = struct.unpack_from("<I", data, offset)[0]
        actual = engine.decode_move_wide(word)
        if actual != 16 << 30:
            raise ValueError(
                f"size site {offset:#x}: got {actual!r}, expected 16 GiB"
            )
        struct.pack_into(
            "<I", data, offset, engine.rewrite_move_wide(word, 8 << 30)
        )
        changed.append(offset)
        size_sites.append(offset)

    materialization_sites: list[int] = []
    for offset in sorted(POOL_BASE_MASK_MATERIALIZATIONS):
        word = struct.unpack_from("<I", data, offset)[0]
        immediate = engine.decode_logical_immediate(word)
        opcode = (word >> 29) & 3
        source_register = (word >> 5) & 31
        if (immediate != OLD_POOL_BASE_MASK or opcode != 1 or
                source_register != 31):
            raise ValueError(
                f"mask materialization {offset:#x}: unexpected word {word:#010x}"
            )
        struct.pack_into(
            "<I", data, offset,
            engine.rewrite_logical_immediate(word, NEW_POOL_BASE_MASK)
        )
        changed.append(offset)
        materialization_sites.append(offset)

    patched_text_hash = hashlib.sha256(
        data[text_offset:text_offset + text_size]
    ).hexdigest()
    if patched_text_hash != EXPECTED_PATCHED_TEXT_SHA256:
        raise ValueError(
            "patched __text SHA-256 "
            f"{patched_text_hash}; expected {EXPECTED_PATCHED_TEXT_SHA256}"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(data)
    shutil.copymode(input_path, output_path)
    return {
        "profile": "Steam CEF 126.0.6478.183 arm64 / iPadOS VA",
        "uuid": image_uuid,
        "input_sha256": original_hash,
        "output_sha256": hashlib.sha256(data).hexdigest(),
        "patched_text_sha256": patched_text_hash,
        "inlined_pool_masks": len(mask_sites),
        "pool_size_sites": [f"{x:#x}" for x in size_sites],
        "pool_base_mask_materializations": [
            f"{x:#x}" for x in materialization_sites
        ],
        "total_instructions_changed": len(changed),
    }


def verify_patched(input_path: Path) -> dict[str, object]:
    """Verify the exact executable port independently of its signature."""
    data = bytearray(input_path.read_bytes())
    image_uuid, text_address, text_size, text_offset = engine.parse_macho(data)
    if image_uuid.lower() != EXPECTED_UUID:
        raise ValueError(
            f"unsupported CEF UUID {image_uuid}; expected {EXPECTED_UUID}"
        )
    if text_address != text_offset:
        raise ValueError("manifest requires identical __text VM/file offsets")
    text_hash = hashlib.sha256(
        data[text_offset:text_offset + text_size]
    ).hexdigest()
    if text_hash != EXPECTED_PATCHED_TEXT_SHA256:
        raise ValueError(
            f"unported or partial __text SHA-256 {text_hash}; "
            f"expected {EXPECTED_PATCHED_TEXT_SHA256}"
        )

    for offset in sorted(POOL_SIZE_SITES):
        actual = engine.decode_move_wide(struct.unpack_from("<I", data, offset)[0])
        if actual != 8 << 30:
            raise ValueError(
                f"ported size site {offset:#x}: got {actual!r}, expected 8 GiB"
            )
    for offset in sorted(POOL_BASE_MASK_MATERIALIZATIONS):
        word = struct.unpack_from("<I", data, offset)[0]
        immediate = engine.decode_logical_immediate(word)
        opcode = (word >> 29) & 3
        source_register = (word >> 5) & 31
        if (immediate != NEW_POOL_BASE_MASK or opcode != 1 or
                source_register != 31):
            raise ValueError(
                f"ported mask materialization {offset:#x}: "
                f"unexpected word {word:#010x}"
            )
    return {
        "profile": "Steam CEF 126.0.6478.183 arm64 / iPadOS VA",
        "uuid": image_uuid,
        "patched_text_sha256": text_hash,
        "pool_size_gib": 8,
        "status": "verified",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--verify-patched", action="store_true",
        help="verify an already-ported arm64 slice without modifying it",
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path, nargs="?")
    args = parser.parse_args()
    try:
        if args.verify_patched:
            if args.output is not None:
                parser.error("--verify-patched does not accept an output path")
            manifest = verify_patched(args.input)
        else:
            if args.output is None:
                parser.error("output path is required when applying the port")
            manifest = patch(args.input, args.output)
    except (OSError, ValueError, struct.error) as error:
        print(f"patch_steam_cef126_pa_ios_va: {error}", file=sys.stderr)
        return 1
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

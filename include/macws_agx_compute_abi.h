#pragma once
#include <stddef.h>
#include <stdint.h>
#include <string.h>

static inline uint32_t MacWSAGXRead32(const void *bytes, size_t offset) {
    uint32_t value;
    memcpy(&value, (const unsigned char *)bytes + offset, sizeof(value));
    return value;
}

// Runtime-paired on iPad13,6 / iOS 16.3, 2026-09-13:
// mps-submit-52199-s1-kcmd.bin (macOS, error 0x103) versus
// mps-submit-52160-s1-kcmd.bin (native, numeric readback 16/16).
// This compute variant has no resource trailer and no leading 1 before the
// all-ones sentinel. Preserve its zero flag at +0x1cc; remove only the
// macOS-only zero window [0x1d0,0x1e0). Resource addresses, command tokens,
// shader payload and synchronization state are deliberately never rewritten.
// Caller must first validate the complete segment list, then propagate the
// returned shrink to every affected range and the storage current pointer.
static inline int MacWSAGXIsTrailerlessCompute(const void *bytes, size_t size) {
    if (!bytes || size != 0x1f0) return 0;
    const unsigned char *record = bytes;
    if (MacWSAGXRead32(record, 0) != 0x10000 ||
        MacWSAGXRead32(record, 4) != size ||
        MacWSAGXRead32(record, 8) != 4 ||
        MacWSAGXRead32(record, 0x24) != 0 ||
        MacWSAGXRead32(record, 0x28) != 0x1e8 ||
        MacWSAGXRead32(record, 0x2c) != 0x1b8 ||
        MacWSAGXRead32(record, 0x30) != 0x30 ||
        MacWSAGXRead32(record, 0x34) != 3 ||
        MacWSAGXRead32(record, 0x1ec) != 0) return 0;
    for (size_t i = 0x1c8; i < 0x1e0; i++)
        if (record[i]) return 0;
    for (size_t i = 0x1e0; i < 0x1ec; i++)
        if (record[i] != 0xff) return 0;
    return 1;
}

// Runtime-paired on the same device/build, 2026-09-13, using the public
// MPSImageGaussianBlur API with identical 64x64 RGBA8 textures:
//
//   macOS:  KCMD 0x228, SHA-256 dfc84af4b7d7402b9a9991fcde2c20d6...
//            completion status=5, Internal Error 00000103
//   iOS:    KCMD 0x218, SHA-256 a8fd67b22470e48641b4bf7ed6f7097b...
//            completion status=4, error=nil
//
// Both use one fully structured 0xf0-byte resource list containing thirteen
// resources in three groups.  Deleting the macOS-only zero window
// [0x1d0,0x1e0), then updating span/end/body-size and the independently
// validated resource-list range, produces the native 0x218 framing while
// preserving every resource entry and payload field.  The caller must still
// validate that complete structured list before changing any bytes.
static inline int MacWSAGXIsMode2ComputeWithResourceTrailer(
        const void *bytes, size_t size) {
    if (!bytes || size != 0x228) return 0;
    const unsigned char *record = bytes;
    if (MacWSAGXRead32(record, 0) != 0x10000 ||
        MacWSAGXRead32(record, 4) != size ||
        MacWSAGXRead32(record, 8) != 4 ||
        MacWSAGXRead32(record, 0x24) != 0x30 ||
        MacWSAGXRead32(record, 0x28) != 0x1e8 ||
        MacWSAGXRead32(record, 0x2c) != 0x1b8 ||
        MacWSAGXRead32(record, 0x30) != 0x30 ||
        MacWSAGXRead32(record, 0x34) != 3 ||
        MacWSAGXRead32(record, 0x120) != 11 ||
        MacWSAGXRead32(record, 0x128) != 6 ||
        MacWSAGXRead32(record, 0x1ec) != 0 ||
        MacWSAGXRead32(record, 0x1f0) != 0 ||
        MacWSAGXRead32(record, 0x1f4) != 2 ||
        MacWSAGXRead32(record, 0x1fc) != 0x15) return 0;
    for (size_t i = 0x1c8; i < 0x1e0; i++)
        if (record[i]) return 0;
    for (size_t i = 0x1e0; i < 0x1ec; i++)
        if (record[i] != 0xff) return 0;
    return 1;
}

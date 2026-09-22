#ifndef MACWS_IOSURFACE_LAYOUT_ABI_H
#define MACWS_IOSURFACE_LAYOUT_ABI_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

// Only call after validating the measured macOS IOSurface UUID/instructions
// and native kernel pair. These are real native Client fields, not an
// IOSurfaceRef/CF object. See iosurface-layout-accessors-20260919.md.
typedef enum {
    MacWSNativePlaneWidth,
    MacWSNativePlaneHeight,
    MacWSNativePlaneBytesPerRow,
    MacWSNativePlaneBytesPerElement,
    MacWSNativePlaneElementWidth,
    MacWSNativePlaneElementHeight,
    MacWSNativePlaneOffset,
    MacWSNativePlaneSize,
    MacWSNativePlaneBaseAddress,
    MacWSNativePlaneCompressionType,
    MacWSNativePlaneWidthInCompressedTiles,
    MacWSNativePlaneHeightInCompressedTiles,
    MacWSNativePlaneNumberOfComponents,
    MacWSNativePlaneBytesPerTileData,
    MacWSNativePlaneAddressFormat,
    MacWSNativePlaneFieldCount,
} MacWSNativePlaneField;

static inline bool MacWSIOSurfaceReadNativePlane(
        const void *client, size_t plane, MacWSNativePlaneField field,
        uintptr_t *value) {
    if (!client || !value || (unsigned)field >= MacWSNativePlaneFieldCount)
        return false;
    const uint8_t *bytes = client;
    uint32_t count;
    memcpy(&count, bytes + 0xa0, sizeof(count));
    // Preserve stock no-plane/invalid-index behavior at the original API.
    // ObjC validates the full NSUInteger before reaching a 32-bit Client API.
    if (count == 0 || plane >= count) return false;
    const uint8_t *record = bytes + plane * 0x80;
    static const struct { uint16_t offset; uint8_t size; } fields[] = {
        {0xcc, 4}, {0xd0, 4}, {0xdc, 4}, {0xe4, 2}, {0xe6, 1},
        {0xe7, 1}, {0xd8, 4}, {0xe0, 4}, {0xd8, 4}, {0x120, 1},
        {0x114, 4}, {0x118, 4}, {0xe8, 1}, {0x12c, 4}, {0xe9, 1},
    };
    // Exact native BPR/base semantics: compression type 1 exposes no linear
    // mapping. Zero is a genuine protocol result, not a missing field.
    if ((field == MacWSNativePlaneBytesPerRow ||
         field == MacWSNativePlaneBaseAddress) && record[0x120] == 1) {
        *value = 0;
        return true;
    }
    uintptr_t result = 0;
    memcpy(&result, record + fields[field].offset, fields[field].size);
    if (field == MacWSNativePlaneBaseAddress) {
        uintptr_t allocation;
        memcpy(&allocation, bytes + 0x68, sizeof(allocation));
        result += allocation;
    }
    *value = result;
    return true;
}

#endif

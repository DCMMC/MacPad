#include "macws_iosurface_layout_abi.h"
#include <assert.h>
#include <stdio.h>

static void put(void *bytes, size_t offset, uint32_t value) {
    memcpy((uint8_t *)bytes + offset, &value, sizeof(value));
}
static void init(unsigned char *client) {
    memset(client, 0, 512);
    put(client, 0xa0, 2);
    uintptr_t base = 0x10000000;
    memcpy(client + 0x68, &base, sizeof(base));
    for (size_t plane = 0; plane < 2; ++plane) {
        unsigned char *record = client + plane * 0x80;
        put(record, 0xcc, 19); put(record, 0xd0, 11);
        put(record, 0xd8, plane ? 4096 : 0);
        put(record, 0xdc, plane ? 64 : 128);
        put(record, 0xe0, plane ? 1024 : 2048);
        put(record, 0xe4, 0x01010000 | (plane ? 1 : 4));
        record[0xe8] = plane ? 1 : 4; record[0xe9] = 5;
        put(record, 0x114, 101 + plane); put(record, 0x118, 37 + plane);
        record[0x120] = 3;
        put(record, 0x12c, 1024 + 64 * plane);
        // The obsolete four-byte-shifted macOS compression field differs.
        record[0x124] = 1;
    }
}
static uintptr_t read_field(void *client, size_t plane, MacWSNativePlaneField f) {
    uintptr_t value = UINTPTR_MAX;
    assert(MacWSIOSurfaceReadNativePlane(client, plane, f, &value));
    return value;
}

/* PRODUCTION_CLIENT_WRAPPERS */
/* PRODUCTION_OBJC_WRAPPERS */

int main(void) {
    unsigned char client[512];
    init(client);
    for (size_t plane = 0; plane < 2; ++plane) {
        const uintptr_t expected[MacWSNativePlaneFieldCount] = {
            19, 11, plane ? 64 : 128, plane ? 1 : 4, 1, 1,
            plane ? 4096 : 0, plane ? 1024 : 2048,
            0x10000000 + (plane ? 4096 : 0), 3, 101 + plane, 37 + plane,
            plane ? 1 : 4, 1024 + 64 * plane, 5,
        };
        for (unsigned field = 0; field < MacWSNativePlaneFieldCount; ++field)
            assert(read_field(client, plane, field) == expected[field]);
    }
    client[0x120] = 1;
    assert(read_field(client, 0, MacWSNativePlaneBytesPerRow) == 0);
    assert(read_field(client, 0, MacWSNativePlaneBaseAddress) == 0);
    assert(read_field(client, 0, MacWSNativePlaneWidth) == 19);
    client[0x120] = 0;
    assert(read_field(client, 0, MacWSNativePlaneBytesPerRow) == 128);
    assert(read_field(client, 0, MacWSNativePlaneBaseAddress) == 0x10000000);
    put(client, 0xdc, 0); // Valid zero is not permission to consult a wrong field.
    assert(read_field(client, 0, MacWSNativePlaneBytesPerRow) == 0);
    uintptr_t value = 0xabcdef;
    assert(!MacWSIOSurfaceReadNativePlane(NULL, 0, MacWSNativePlaneWidth, &value));
    assert(!MacWSIOSurfaceReadNativePlane(client, 0, MacWSNativePlaneWidth, NULL));
    assert(!MacWSIOSurfaceReadNativePlane(client, 2, MacWSNativePlaneWidth, &value));
    assert(!MacWSIOSurfaceReadNativePlane(client, SIZE_MAX, MacWSNativePlaneWidth, &value));
    assert(!MacWSIOSurfaceReadNativePlane(client, 0, MacWSNativePlaneFieldCount, &value));
    assert(value == 0xabcdef);
    put(client, 0xa0, 0);
    assert(!MacWSIOSurfaceReadNativePlane(client, 0, MacWSNativePlaneWidth, &value));
    assert(value == 0xabcdef);
    /* EXERCISE_PRODUCTION_WRAPPERS */
    puts("native IOSurface plane reader and fallback contract PASS");
    return 0;
}

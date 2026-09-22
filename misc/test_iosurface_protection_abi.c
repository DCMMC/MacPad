#include "macws_iosurface_protection_abi.h"
#include <assert.h>
#include <stdio.h>

int main(void) {
    uint8_t client[0xe0] = {0}, shared[0x40] = {0};
    const void *header = shared;
    memcpy(client + 0x70, &header, sizeof(header));
    // This is the old macOS cached-field location. On the real native client
    // its high word overlaps plane-0 width; it is not a protection authority.
    const uint64_t wrong = UINT64_C(16) << 32;
    memcpy(client + 0xc8, &wrong, sizeof(wrong));
    const uint64_t cases[] = {
        0, 1, 2, 7, UINT64_C(1) << 32, UINT64_C(1) << 63,
        UINT64_C(0x0123456789abcdef), UINT64_MAX,
    };
    for (unsigned i = 0; i < sizeof(cases) / sizeof(cases[0]); ++i) {
        memcpy(shared + 0x30, &cases[i], sizeof(cases[i]));
        uint64_t actual = ~cases[i];
        assert(macws_iosurface_native_protection_options(client, &actual));
        assert(actual == cases[i]); // all 64 bits, including real nonzero flags
        uint64_t unchanged;
        memcpy(&unchanged, client + 0xc8, sizeof(unchanged));
        assert(unchanged == wrong); // translation never changes shared data
    }
    uint64_t untouched = 42;
    assert(!macws_iosurface_native_protection_options(NULL, &untouched));
    assert(untouched == 42);
    assert(!macws_iosurface_native_protection_options(client, NULL));
    header = NULL;
    memcpy(client + 0x70, &header, sizeof(header));
    assert(!macws_iosurface_native_protection_options(client, &untouched));
    assert(untouched == 42);
    puts("IOSurface protection ABI: full-width current-value and failure contracts PASS");
    return 0;
}

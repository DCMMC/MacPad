#ifndef MACWS_IOSURFACE_PROTECTION_ABI_H
#define MACWS_IOSURFACE_PROTECTION_ABI_H

#include <stdbool.h>
#include <stdint.h>
#include <string.h>

// iOS 16.3.1 IOSurface DF041B53-4BAA-3668-8781-43DE39FA8905:
// IOSurfaceClientGetProtectionOptions is exactly
//   ldr x8, [x0, #0x70]; ldr x0, [x8, #0x30]; ret
// The caller must validate the host/consumer ABI and supply a live client.
// Read its current kernel-shared value; do not cache, mask, or infer protection
// from format, plane dimensions, creation properties, or an absent property.
static inline bool macws_iosurface_native_protection_options(
        const void *client, uint64_t *options) {
    if (!client || !options) return false;
    const uint8_t *shared = NULL;
    memcpy(&shared, (const uint8_t *)client + 0x70, sizeof(shared));
    if (!shared) return false;
    memcpy(options, shared + 0x30, sizeof(*options));
    return true;
}

#endif

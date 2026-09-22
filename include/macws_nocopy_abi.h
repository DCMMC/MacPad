#ifndef MACWS_NOCOPY_ABI_H
#define MACWS_NOCOPY_ABI_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

// Measured macOS IOGPU CE2B5551 / AGX 727C250E -> iOS 16.3 (20D67).
// The macOS producer has an extra uint32 alignment + padding before the
// client-memory union. Remove that slot in a wire COPY, never its storage:
// IOGPUMetalResource reads the original +0x38 after the kernel returns.
// Unknown tail fields are preserved, not interpreted or zeroed.
struct MacWSNoCopyScope {
    uintptr_t bytes;
    size_t length;
    size_t page_size;
    struct MacWSNoCopyScope *previous;
};

#ifdef __cplusplus
extern "C" {
#endif
extern __thread struct MacWSNoCopyScope *g_macws_nocopy_scope;
bool MacWSAGXNoCopyABIReady(const void *agx_initializer,
                           const void *iogpu_initializer);
#ifdef __cplusplus
}
#endif

static inline bool MacWSNoCopySpanValid(uintptr_t bytes, size_t length,
                                       size_t page_size) {
    return page_size && !(page_size & (page_size - 1)) && bytes && length &&
        !(bytes & (page_size - 1)) && !(length & (page_size - 1)) &&
        length <= UINTPTR_MAX - bytes;
}

static inline uint64_t MacWSNoCopyRead64(const uint8_t *bytes) {
    uint64_t value;
    memcpy(&value, bytes, sizeof(value));
    return value;
}

static inline bool MacWSNoCopyTranslateRequest(
        const struct MacWSNoCopyScope *scope,
        const void *source, size_t source_size,
        void *destination, size_t destination_capacity) {
    if (!scope || !source || !destination || source_size != 104 ||
        destination_capacity < 96 ||
        !MacWSNoCopySpanValid(scope->bytes, scope->length, scope->page_size))
        return false;
    // The original macOS record remains live after the kernel call. Reject
    // overlaps instead of silently altering it through an aliased destination.
    // Distance comparisons cannot overflow as end-address addition could.
    uintptr_t src_address = (uintptr_t)source;
    uintptr_t dst_address = (uintptr_t)destination;
    if (dst_address >= src_address ? dst_address - src_address < source_size
                                  : src_address - dst_address < 96)
        return false;
    const uint8_t *src = (const uint8_t *)source;
    uint32_t type;
    memcpy(&type, src, sizeof(type));
    if (type != 0x80 || (src[0x15] & 0x08) ||
        MacWSNoCopyRead64(src + 0x20) != 0 ||
        MacWSNoCopyRead64(src + 0x28) != 0 ||
        MacWSNoCopyRead64(src + 0x30) != 1 ||
        MacWSNoCopyRead64(src + 0x38) != scope->bytes ||
        MacWSNoCopyRead64(src + 0x40) != scope->bytes ||
        MacWSNoCopyRead64(src + 0x48) != scope->length)
        return false;
    // Validate the entire source before writing the separate wire copy.
    uint8_t wire[96];
    memcpy(wire, src, 0x30);
    memcpy(wire + 0x30, src + 0x38, 0x30);
    memcpy(destination, wire, sizeof(wire));
    return true;
}

#endif

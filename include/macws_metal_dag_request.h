#ifndef MACWS_METAL_DAG_REQUEST_H
#define MACWS_METAL_DAG_REQUEST_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

// RE-confirmed MTLCompiler 482ee5289d703ed7a746d19bb741245b,
// +0x79b40: discriminator 14 contains a NUL-terminated DAG followed by
// optional AIR version, function count, then optional target strings and
// length-prefixed wrapped AIR modules. Reject truncation/unknown trailers.
// Recognize only the two runtime-validated input contracts: metal2metal's
// Catalyst AIR and Ventura CoreImage's native macOS AIR. Every module and
// optional explicit target must agree; ordinary iOS inputs remain untouched.
typedef enum {
    MacWSMetalDAGInputUnknown = 0,
    MacWSMetalDAGInputMacOS134 = 1,
    MacWSMetalDAGInputCatalyst = 6,
} MacWSMetalDAGInputTarget;

static inline MacWSMetalDAGInputTarget MacWSMetalDAGExactTarget(
        const uint8_t *bytes, size_t length) {
    static const char catalyst[] = "air64-apple-ios19.0.0-macabi";
    static const char macos[] = "air64-apple-macosx13.4.0";
    if (length == sizeof(catalyst)-1 && !memcmp(bytes, catalyst, length))
        return MacWSMetalDAGInputCatalyst;
    if (length == sizeof(macos)-1 && !memcmp(bytes, macos, length))
        return MacWSMetalDAGInputMacOS134;
    return MacWSMetalDAGInputUnknown;
}

static inline MacWSMetalDAGInputTarget MacWSMetalDAGModuleTarget(
        const uint8_t *air, size_t size) {
    static const char prefix[] = "air64-apple-";
    static const char *const targets[] = {
        "air64-apple-ios19.0.0-macabi", "air64-apple-macosx13.4.0"
    };
    MacWSMetalDAGInputTarget result = MacWSMetalDAGInputUnknown;
    for (size_t i = 0; i + sizeof(prefix)-1 <= size; ++i) {
        if (memcmp(air + i, prefix, sizeof(prefix)-1)) continue;
        MacWSMetalDAGInputTarget found = MacWSMetalDAGInputUnknown;
        for (size_t j = 0; j < sizeof(targets)/sizeof(targets[0]); ++j) {
            size_t length = strlen(targets[j]);
            if (length > size-i || memcmp(air+i, targets[j], length)) continue;
            // This is a bounded string-table witness, not a general LLVM
            // bitcode semantic parser. The captured AIR serializes the exact
            // triple followed by end/NUL or `llvm.metadata`; do not accept an
            // arbitrary alphabetic suffix as a supported platform/version.
            static const char follower[] = "llvm.metadata";
            size_t remaining = size-i-length;
            if (remaining && air[i+length] &&
                (remaining < sizeof(follower)-1 ||
                 memcmp(air+i+length, follower, sizeof(follower)-1))) continue;
            found = MacWSMetalDAGExactTarget(air+i, length);
        }
        if (!found || (result && result != found)) return MacWSMetalDAGInputUnknown;
        result = found;
    }
    return result;
}

static inline MacWSMetalDAGInputTarget MacWSMetalDAGGetInputTarget(
        const void *data, size_t size) {
    if (!data || size < 20 || size > 32U * 1024U * 1024U) return false;
    const uint8_t *bytes = data, *cursor = bytes, *end = bytes + size;
    if (memcmp(cursor, " gad", 4)) return false;
    cursor += 4;
    const uint8_t *terminator = memchr(cursor, 0, (size_t)(end - cursor));
    if (!terminator || terminator == cursor) return false;
    cursor = terminator + 1;
    if (end - cursor >= 4 && !memcmp(cursor, "vria", 4)) {
        if (end - cursor < 12) return false;
        cursor += 12;
    }
    if (end - cursor < 8 || memcmp(cursor, "fmun", 4)) return false;
    uint32_t count;
    memcpy(&count, cursor + 4, sizeof(count));
    cursor += 8;
    if (!count || count > 4096) return false;
    MacWSMetalDAGInputTarget requestTarget = MacWSMetalDAGInputUnknown;
    for (uint32_t i = 0; i < count; i++) {
        MacWSMetalDAGInputTarget explicitTarget = MacWSMetalDAGInputUnknown;
        if (end - cursor >= 4 && !memcmp(cursor, "lprt", 4)) {
            cursor += 4;
            terminator = memchr(cursor, 0, (size_t)(end - cursor));
            if (!terminator) return MacWSMetalDAGInputUnknown;
            explicitTarget = MacWSMetalDAGExactTarget(cursor, (size_t)(terminator-cursor));
            if (!explicitTarget) return MacWSMetalDAGInputUnknown;
            cursor = terminator + 1;
        }
        if (end - cursor < 8 || memcmp(cursor, "ctib", 4)) return false;
        uint32_t length;
        memcpy(&length, cursor + 4, sizeof(length));
        cursor += 8;
        if (length < 24 || length > (size_t)(end - cursor)) return false;
        uint32_t magic, version, bitcodeOffset, bitcodeSize;
        memcpy(&magic, cursor, 4);
        memcpy(&version, cursor + 4, 4);
        memcpy(&bitcodeOffset, cursor + 8, 4);
        memcpy(&bitcodeSize, cursor + 12, 4);
        if (magic != UINT32_C(0x0b17c0de) || version || bitcodeOffset < 20 ||
            bitcodeOffset > length || bitcodeSize > length - bitcodeOffset ||
            bitcodeSize < 4 ||
            memcmp(cursor + bitcodeOffset, "BC\xc0\xde", 4)) return false;
        const uint8_t *air = cursor + bitcodeOffset;
        MacWSMetalDAGInputTarget moduleTarget = MacWSMetalDAGModuleTarget(air, bitcodeSize);
        if (!moduleTarget || (explicitTarget && explicitTarget != moduleTarget) ||
            (requestTarget && requestTarget != moduleTarget)) return MacWSMetalDAGInputUnknown;
        requestTarget = moduleTarget;
        cursor += length;
    }
    return cursor == end ? requestTarget : MacWSMetalDAGInputUnknown;
}

static inline bool MacWSMetalDAGHasCatalystInputs(const void *data, size_t size) {
    return MacWSMetalDAGGetInputTarget(data, size) == MacWSMetalDAGInputCatalyst;
}
#endif

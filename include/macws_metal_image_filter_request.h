#ifndef MACWS_METAL_IMAGE_FILTER_REQUEST_H
#define MACWS_METAL_IMAGE_FILTER_REQUEST_H

#include "macws_metal_dag_request.h"

// Input classification only; this does not validate LLVM IR or replace any
// compiler check. RE-confirmed in MTLCompiler 482EE528-9D70-3ED7-A746-D19BB741245B:
// request kind 5 (+0x2710) supplies wrapped modules to ComposeFilters (+0x31f0).
// The captured CoreUI request raw-80913-001-5 has this 20-byte LE header:
// functionCount, functionInfoOffset, moduleCount, moduleTableOffset, flags.
// Module table entries are (uint32_t offset, uint32_t length). Names and
// function metadata preceding that table remain OPAQUE: Apple must still parse
// and validate them. A recognized target is not proof of a valid request.
//
// Metal 2BAB169C-42DA-36E3-955A-F30B709EC2AD builds kind 5 at +0x22f0c:
// +0x23500..0x23510 rounds the names/metadata sections to 8-byte boundaries;
// +0x23608..0x23610 advances each module by align8(originalLength), while
// +0x23840/+0x23850 stores its offset and UNROUNDED length in the table.
// +0x236b0..0x236e8 sets only flag 0x200, from the genuine
// _MTLCompilePerformanceStatisticsEnabled result. Preserve that flag for the
// original compiler; it does not change the module-target classification.
// Deliberately recognize only this bounded layout and wrapper version, with
// all modules agreeing on the exact supported target. Unknown flags, nonzero
// alignment padding, wrapper extensions, targets, or layouts remain untouched.
static inline uint32_t MacWSMetalImageFilterReadLE32(const uint8_t *bytes) {
    return (uint32_t)bytes[0] | ((uint32_t)bytes[1] << 8) |
           ((uint32_t)bytes[2] << 16) | ((uint32_t)bytes[3] << 24);
}

static inline MacWSMetalDAGInputTarget MacWSMetalImageFilterGetInputTarget(
        const void *data, size_t size) {
    if (!data || size < 20 || size > 32U * 1024U * 1024U)
        return MacWSMetalDAGInputUnknown;
    const uint8_t *bytes = data;
    uint32_t functionCount = MacWSMetalImageFilterReadLE32(bytes);
    size_t functionInfoOffset = MacWSMetalImageFilterReadLE32(bytes + 4);
    uint32_t moduleCount = MacWSMetalImageFilterReadLE32(bytes + 8);
    size_t moduleTableOffset = MacWSMetalImageFilterReadLE32(bytes + 12);
    uint32_t flags = MacWSMetalImageFilterReadLE32(bytes + 16);
    if (!functionCount || functionCount > 4096 || !moduleCount ||
        moduleCount > 4096 || (flags & ~UINT32_C(0x200)) || functionInfoOffset <= 20 ||
        ((functionInfoOffset - 20) & 7) || (moduleTableOffset & 7) ||
        functionInfoOffset >= moduleTableOffset || moduleTableOffset > size ||
        moduleCount > (size - moduleTableOffset) / 8)
        return MacWSMetalDAGInputUnknown;

    // The division above validates the multiplication/addition on 32-bit size_t
    // as well as 64-bit. Do not form an out-of-range pointer before these checks.
    size_t nextOffset = moduleTableOffset + (size_t)moduleCount * 8;
    MacWSMetalDAGInputTarget requestTarget = MacWSMetalDAGInputUnknown;
    for (uint32_t i = 0; i < moduleCount; ++i) {
        const uint8_t *entry = bytes + moduleTableOffset + (size_t)i * 8;
        size_t offset = MacWSMetalImageFilterReadLE32(entry);
        size_t length = MacWSMetalImageFilterReadLE32(entry + 4);
        // Equality rejects table aliases, reordered entries, overlap, and gaps.
        if (offset != nextOffset || (offset & 7) ||
            length < 32 || offset > size || length > size - offset)
            return MacWSMetalDAGInputUnknown;

        const uint8_t *wrapper = bytes + offset;
        uint32_t magic = MacWSMetalImageFilterReadLE32(wrapper);
        uint32_t version = MacWSMetalImageFilterReadLE32(wrapper + 4);
        size_t bitcodeOffset = MacWSMetalImageFilterReadLE32(wrapper + 8);
        size_t bitcodeSize = MacWSMetalImageFilterReadLE32(wrapper + 12);
        uint32_t cpuType = MacWSMetalImageFilterReadLE32(wrapper + 16);
        if (magic != UINT32_C(0x0b17c0de) || version || bitcodeOffset != 20 ||
            cpuType != UINT32_MAX || bitcodeSize < 4 ||
            bitcodeSize > length - bitcodeOffset ||
            memcmp(wrapper + bitcodeOffset, "BC\xc0\xde", 4))
            return MacWSMetalDAGInputUnknown;

        size_t contentEnd = bitcodeOffset + bitcodeSize;
        if (length - contentEnd > 15) return MacWSMetalDAGInputUnknown;
        for (size_t j = contentEnd; j < length; ++j)
            if (wrapper[j]) return MacWSMetalDAGInputUnknown;

        // Reuse the existing bounded string-table witness; do not infer a
        // target from the opaque function metadata or from wrapper padding.
        MacWSMetalDAGInputTarget target = MacWSMetalDAGModuleTarget(
            wrapper + bitcodeOffset, bitcodeSize);
        if (!target || (requestTarget && requestTarget != target))
            return MacWSMetalDAGInputUnknown;
        requestTarget = target;
        size_t moduleEnd = offset + length; // Proven <= size above.
        size_t alignmentPadding = (8 - (length & 7)) & 7;
        if (alignmentPadding > size - moduleEnd)
            return MacWSMetalDAGInputUnknown;
        for (size_t j = 0; j < alignmentPadding; ++j)
            if (bytes[moduleEnd + j]) return MacWSMetalDAGInputUnknown;
        nextOffset = moduleEnd + alignmentPadding;
    }
    return nextOffset == size ? requestTarget : MacWSMetalDAGInputUnknown;
}

#endif

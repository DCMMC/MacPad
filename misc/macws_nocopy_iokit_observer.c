// Diagnostic-only native-iOS resource request observer. No mutation, no replay.
// Intended only for the owned one-page macws_nocopy_contract_probe process.
#include <IOKit/IOKitLib.h>
#include <dlfcn.h>
#include <errno.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

static _Atomic unsigned samples;
struct observation {
    uint8_t bytes[0x68];
    size_t count;
    unsigned sample;
};

static struct observation observe_request(uint32_t selector,
        const void *structure, size_t structureSize) {
    struct observation observation = {0};
    if (!structure || structureSize < 16 ||
        (selector != 9 && selector != 10)) return observation;
    unsigned sample = atomic_load_explicit(&samples, memory_order_relaxed);
    do {
        if (sample >= 8) return observation;
    } while (!atomic_compare_exchange_weak_explicit(&samples, &sample, sample + 1,
        memory_order_relaxed, memory_order_relaxed));
    observation.sample = sample + 1;
    observation.count = structureSize < sizeof(observation.bytes)
        ? structureSize : sizeof(observation.bytes);
    memcpy(observation.bytes, structure, observation.count);
    return observation;
}

static void report(const char *api, uint32_t selector, size_t structureSize,
        kern_return_t result, const struct observation *observation,
        const void *caller) {
    if (observation->sample) {
        Dl_info info = {0};
        const char *module = "unknown";
        uintptr_t offset = 0;
        if (dladdr(caller, &info) && info.dli_fname) {
            const char *slash = strrchr(info.dli_fname, '/');
            module = slash ? slash + 1 : info.dli_fname;
            offset = (uintptr_t)caller - (uintptr_t)info.dli_fbase;
        }
        char line[1200];
        int used = snprintf(line, sizeof(line),
            "NOCOPY-IOKIT diagnostic=1 pid=%d sample=%u/8 api=%s selector=%u "
            "inputSize=%zu snapshotSize=%zu result=%#x caller=%s+0x%llx words=",
            getpid(), observation->sample, api, selector, structureSize,
            observation->count, result, module, (unsigned long long)offset);
        for (size_t offset = 0; offset < observation->count; offset += 8) {
            uint64_t word = 0;
            size_t size = observation->count - offset;
            if (size > 8) size = 8;
            memcpy(&word, observation->bytes + offset, size);
            used += snprintf(line + used, sizeof(line) - (size_t)used,
                             "+%02zx:%016llx ", offset,
                             (unsigned long long)word);
        }
        line[used++] = '\n';
        (void)write(STDERR_FILENO, line, (size_t)used);
    }
}

static kern_return_t observed_IOConnectCallMethod(mach_port_t connection,
    uint32_t selector, const uint64_t *input, uint32_t inputCount,
    const void *structure, size_t structureSize, uint64_t *output,
    uint32_t *outputCount, void *outputStructure, size_t *outputStructureSize) {
    int incoming_errno = errno;
    struct observation observation = observe_request(selector, structure, structureSize);
    errno = incoming_errno;
    kern_return_t result = IOConnectCallMethod(connection, selector, input,
        inputCount, structure, structureSize, output, outputCount,
        outputStructure, outputStructureSize);
    int result_errno = errno;
    report("IOConnectCallMethod", selector, structureSize, result, &observation,
        __builtin_extract_return_addr(__builtin_return_address(0)));
    errno = result_errno;
    return result;
}

static kern_return_t observed_IOConnectCallStructMethod(mach_port_t connection,
    uint32_t selector, const void *structure, size_t structureSize,
    void *outputStructure, size_t *outputStructureSize) {
    int incoming_errno = errno;
    struct observation observation = observe_request(selector, structure, structureSize);
    errno = incoming_errno;
    kern_return_t result = IOConnectCallStructMethod(connection, selector,
        structure, structureSize, outputStructure, outputStructureSize);
    int result_errno = errno;
    report("IOConnectCallStructMethod", selector, structureSize, result, &observation,
        __builtin_extract_return_addr(__builtin_return_address(0)));
    errno = result_errno;
    return result;
}

__attribute__((used, section("__DATA,__interpose")))
static const struct { const void *replacement, *replacee; } interpose_method = {
    (void *)&observed_IOConnectCallMethod, (void *)&IOConnectCallMethod
};
__attribute__((used, section("__DATA,__interpose")))
static const struct { const void *replacement, *replacee; } interpose_struct = {
    (void *)&observed_IOConnectCallStructMethod, (void *)&IOConnectCallStructMethod
};

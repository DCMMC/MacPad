// Resolve the actual chroot macOS IOGPU exports without creating a device.
//
// This is a read-only RE helper: it loads the dyld-shared-cache image, prints
// each selected export's live address, image base, and static image offset,
// then exits.  It does not open an IOKit user client or allocate GPU memory.

#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>

static const char *const kSymbols[] = {
    "IOGPUResourceCreate",
    "IOGPUResourceGetDataBytes",
    "IOGPUResourceGetClientShared",
    "IOGPUResourceGetGPUVirtualAddress",
    "IOGPUResourceGetGPUVirtualAddressLength",
};

int main(void) {
    const char *path =
        "/System/Library/PrivateFrameworks/IOGPU.framework/IOGPU";
    void *image = dlopen(path, RTLD_LAZY | RTLD_LOCAL);
    if (!image) {
        fprintf(stderr, "IOGPU_SYMBOL_PROBE dlopen failed: %s\n", dlerror());
        return 1;
    }

    int failed = 0;
    for (size_t i = 0; i < sizeof(kSymbols) / sizeof(kSymbols[0]); i++) {
        dlerror();
        void *address = dlsym(image, kSymbols[i]);
        const char *error = dlerror();
        if (!address || error) {
            fprintf(stderr, "IOGPU_SYMBOL_PROBE missing %s: %s\n",
                    kSymbols[i], error ? error : "unknown error");
            failed = 1;
            continue;
        }
        Dl_info info = {0};
        if (!dladdr(address, &info) || !info.dli_fbase) {
            fprintf(stderr, "IOGPU_SYMBOL_PROBE dladdr failed %s address=%p\n",
                    kSymbols[i], address);
            failed = 1;
            continue;
        }
        printf("IOGPU_SYMBOL_PROBE symbol=%s address=%p base=%p offset=%#llx "
               "image=%s\n",
               kSymbols[i], address, info.dli_fbase,
               (unsigned long long)((uintptr_t)address -
                                    (uintptr_t)info.dli_fbase),
               info.dli_fname ? info.dli_fname : "(unknown)");
    }
    dlclose(image);
    return failed;
}

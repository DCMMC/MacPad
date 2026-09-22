// Probe the macOS 13 LaunchServices shared-page menu-bar-owner accessors.
//
// This intentionally writes the existing owner value back unchanged.  It is
// used to establish whether a chroot application maps the shared page writable
// before AppInputBridge uses the same upstream accessor during activation.

#include <CoreFoundation/CoreFoundation.h>
#include <dlfcn.h>
#include <inttypes.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>

typedef CFTypeRef (*LSSharedMemoryCopyForSessionIDFn)(int32_t, bool);
typedef uint32_t (*LSSharedMemoryGetUInt32Fn)(CFTypeRef);
typedef void (*LSSharedMemorySetUInt32Fn)(CFTypeRef, uint32_t);

int main(void) {
    const char *path =
        "/System/Library/Frameworks/CoreServices.framework/Versions/A/"
        "Frameworks/LaunchServices.framework/Versions/A/LaunchServices";
    void *image = dlopen(path, RTLD_NOW | RTLD_LOCAL);
    if (!image) {
        fprintf(stderr, "dlopen failed: %s\n", dlerror());
        return 2;
    }

    LSSharedMemoryCopyForSessionIDFn copyPage =
        (LSSharedMemoryCopyForSessionIDFn)dlsym(
            image, "_LSSharedMemoryCopyForSessionID");
    LSSharedMemoryGetUInt32Fn getOwner =
        (LSSharedMemoryGetUInt32Fn)dlsym(
            image, "_LSSharedMemoryGetMenuBarOwnerASNLow");
    LSSharedMemoryGetUInt32Fn getSeed =
        (LSSharedMemoryGetUInt32Fn)dlsym(
            image, "_LSSharedMemoryGetMenuBarOwnerASNSeed");
    LSSharedMemorySetUInt32Fn setOwner =
        (LSSharedMemorySetUInt32Fn)dlsym(
            image, "_LSSharedMemorySetMenuBarOwnerASNLow");
    if (!copyPage || !getOwner || !getSeed || !setOwner) {
        fprintf(stderr,
                "missing symbol copy=%p get=%p seed=%p set=%p\n",
                copyPage, getOwner, getSeed, setOwner);
        return 3;
    }

    CFTypeRef page = copyPage(-2, true);
    if (!page) {
        fprintf(stderr, "shared page unavailable\n");
        return 4;
    }

    uint32_t ownerBefore = getOwner(page);
    uint32_t seedBefore = getSeed(page);
    printf("page=%p owner-before=0x%08" PRIx32
           " seed-before=%" PRIu32 "\n",
           page, ownerBefore, seedBefore);
    fflush(stdout);

    setOwner(page, ownerBefore);

    uint32_t ownerAfter = getOwner(page);
    uint32_t seedAfter = getSeed(page);
    printf("owner-after=0x%08" PRIx32 " seed-after=%" PRIu32 "\n",
           ownerAfter, seedAfter);
    CFRelease(page);
    return ownerAfter == ownerBefore && seedAfter == seedBefore + 1 ? 0 : 5;
}

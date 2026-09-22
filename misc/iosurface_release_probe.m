// Isolated IOSurface create/map/release lifetime probe.
//
// This is intentionally Metal-free.  It creates a configurable number of
// linear IOSurfaces, faults each mapping in, and immediately balances the
// IOSurfaceCreate reference with CFRelease.  The process then stays alive so
// `footprint <pid>` can show whether the IOSurface VM objects actually left
// the task after the last client reference was released.
//
// Build a macOS arm64 binary on the host:
//   xcrun --sdk macosx clang -arch arm64 -mmacosx-version-min=13.0 \
//     -fobjc-arc -framework Foundation -framework IOSurface \
//     misc/iosurface_release_probe.m -o /tmp/iosurface_release_probe
//
// Usage: iosurface_release_probe [count [bytes [hold_seconds]]]

#import <Foundation/Foundation.h>
#import <IOSurface/IOSurface.h>

#import <stdint.h>
#import <stdio.h>
#import <stdlib.h>
#import <unistd.h>

static unsigned long long parse_arg(const char *text,
                                    unsigned long long fallback) {
    if (!text || !*text) return fallback;
    char *end = NULL;
    unsigned long long value = strtoull(text, &end, 0);
    return end && *end == '\0' && value ? value : fallback;
}

int main(int argc, char **argv) {
    @autoreleasepool {
        const unsigned long long count =
            parse_arg(argc > 1 ? argv[1] : NULL, 64);
        const unsigned long long bytes =
            parse_arg(argc > 2 ? argv[2] : NULL, 16ull << 20);
        const unsigned long long hold_seconds =
            parse_arg(argc > 3 ? argv[3] : NULL, 20);
        if (bytes > UINTPTR_MAX || bytes < 4096) {
            fprintf(stderr, "IOSURFACE-RELEASE invalid bytes=%llu\n", bytes);
            return 2;
        }

        fprintf(stderr,
            "IOSURFACE-RELEASE start pid=%d count=%llu bytes=%llu hold=%llu\n",
            getpid(), count, bytes, hold_seconds);
        for (unsigned long long index = 0; index < count; index++) {
            NSDictionary *properties = @{
                @"IOSurfaceWidth": @((NSUInteger)bytes / 4),
                @"IOSurfaceHeight": @1,
                @"IOSurfaceBytesPerElement": @4,
                @"IOSurfaceBytesPerRow": @((NSUInteger)bytes),
                @"IOSurfaceAllocSize": @((NSUInteger)bytes),
                @"IOSurfacePixelFormat": @((uint32_t)'BGRA'),
                @"IOSurfaceCacheMode": @0,
                @"IOSurfaceName": @"MacWS IOSurface release probe",
            };
            IOSurfaceRef surface = IOSurfaceCreate(
                (__bridge CFDictionaryRef)properties);
            if (!surface) {
                fprintf(stderr,
                    "IOSURFACE-RELEASE create failed index=%llu\n", index);
                return 3;
            }
            uint32_t surface_id = IOSurfaceGetID(surface);
            IOReturn lock_result = IOSurfaceLock(surface, 0, NULL);
            void *base = IOSurfaceGetBaseAddress(surface);
            if (lock_result == kIOReturnSuccess && base) {
                *(volatile uint8_t *)base = (uint8_t)index;
                IOSurfaceUnlock(surface, 0, NULL);
            }
            CFRelease(surface);
            if (index < 8 || (index + 1) % 32 == 0) {
                fprintf(stderr,
                    "IOSURFACE-RELEASE balanced index=%llu id=%u "
                    "lock=%#x base=%p\n",
                    index, surface_id, lock_result, base);
            }
        }
        fprintf(stderr,
            "IOSURFACE-RELEASE all-balanced pid=%d count=%llu; sleeping\n",
            getpid(), count);
        sleep((unsigned int)hold_seconds);
        fprintf(stderr, "IOSURFACE-RELEASE done\n");
    }
    return 0;
}

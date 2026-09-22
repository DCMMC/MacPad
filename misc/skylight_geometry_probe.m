// Read-only diagnostic for deciding whether a narrow SkyLight geometry API
// exposes live presentation state during Spaces/Mission Control animations.
//
// Build on the controlling Mac:
//   xcrun --sdk macosx clang -arch arm64 -mmacosx-version-min=13.0 -O2 \
//     -fobjc-arc -framework Foundation -framework CoreGraphics \
//     misc/skylight_geometry_probe.m -o /tmp/skylight_geometry_probe
//
// The private function prototypes below are RE-confirmed from the exported
// entry points in SkyLight. SLSGetWindowBounds forwards x0/x1/x2 and supplies
// x3=0. SLSGetWindowTransform forwards x0/x1, supplies placement=0 and
// optionalSeed=NULL, and moves the caller's x2 output to x4 before entering
// SLSGetWindowTransformAtPlacement. The probe also dumps the first 32 bytes of
// every resolved entry point so the ABI can be checked against the exact
// target image instead of assuming that another macOS build is identical.

#import <CoreGraphics/CoreGraphics.h>
#import <Foundation/Foundation.h>

#include <dlfcn.h>
#include <mach/mach_time.h>
#include <math.h>
#include <stdio.h>

typedef uint32_t (*MacWSCGSMainConnectionIDFn)(void);
typedef int32_t (*MacWSSLSGetWindowBoundsFn)(uint32_t, uint32_t, CGRect *);
typedef int32_t (*MacWSSLSGetWindowTransformFn)(uint32_t, uint32_t,
                                                CGAffineTransform *);
typedef int32_t (*MacWSSLSGetWindowTransformAtPlacementFn)(
    uint32_t, uint32_t, uint32_t, uint32_t *, CGAffineTransform *);
typedef int32_t (*MacWSSLSGetOnScreenWindowCountFn)(uint32_t, uint32_t,
                                                    uint32_t *);
typedef int32_t (*MacWSSLSGetOnScreenWindowListFn)(uint32_t, uint32_t,
                                                   uint32_t, uint32_t *,
                                                   uint32_t *);

static double MonotonicSeconds(void) {
    static mach_timebase_info_data_t info;
    static dispatch_once_t once;
    dispatch_once(&once, ^{ mach_timebase_info(&info); });
    return mach_continuous_time() * (double)info.numer /
        (double)info.denom / 1.0e9;
}

static void PrintBytes(const char *name, const void *address) {
    const unsigned char *bytes = address;
    printf("\"%s\":{\"address\":\"%p\",\"first32\":\"", name,
           address);
    for (size_t index = 0; index < 32; index++) printf("%02x", bytes[index]);
    printf("\"}");
}

static void PrintCodeBytes(const char *name, const void *address,
                           size_t length) {
    const unsigned char *bytes = address;
    printf("\"%s\":{\"address\":\"%p\",\"code\":\"", name,
           address);
    for (size_t index = 0; index < length; index++)
        printf("%02x", bytes[index]);
    printf("\"}");
}

static void PrintRect(CGRect rect) {
    if (!isfinite(rect.origin.x) || !isfinite(rect.origin.y) ||
        !isfinite(rect.size.width) || !isfinite(rect.size.height)) {
        printf("null");
        return;
    }
    printf("[%.6f,%.6f,%.6f,%.6f]", rect.origin.x, rect.origin.y,
           rect.size.width, rect.size.height);
}

static void PrintTransform(CGAffineTransform transform) {
    if (!isfinite(transform.a) || !isfinite(transform.b) ||
        !isfinite(transform.c) || !isfinite(transform.d) ||
        !isfinite(transform.tx) || !isfinite(transform.ty)) {
        printf("null");
        return;
    }
    printf("[%.9f,%.9f,%.9f,%.9f,%.6f,%.6f]", transform.a, transform.b,
           transform.c, transform.d, transform.tx, transform.ty);
}

static CGRect TargetedDescriptionBounds(const uint32_t *windowIDs,
                                        size_t windowCount,
                                        uint32_t targetWindowID,
                                        bool *valid) {
    CGRect result = CGRectNull;
    *valid = false;
    const void *values[256] = {0};
    for (size_t index = 0; index < windowCount; index++)
        values[index] = (const void *)(uintptr_t)windowIDs[index];
    CFArrayRef identifiers = CFArrayCreate(kCFAllocatorDefault, values,
                                           windowCount, NULL);
    CFArrayRef descriptions = identifiers
        ? CGWindowListCreateDescriptionFromArray(identifiers) : NULL;
    for (CFIndex index = 0;
         descriptions && index < CFArrayGetCount(descriptions); index++) {
        NSDictionary *description = (__bridge NSDictionary *)
            CFArrayGetValueAtIndex(descriptions, index);
        if ([description[(id)kCGWindowNumber] unsignedIntValue] !=
            targetWindowID) continue;
        NSDictionary *bounds = description[(id)kCGWindowBounds];
        if (bounds && CGRectMakeWithDictionaryRepresentation(
                          (__bridge CFDictionaryRef)bounds, &result)) {
            *valid = true;
        }
        break;
    }
    if (descriptions) CFRelease(descriptions);
    if (identifiers) CFRelease(identifiers);
    return result;
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc < 2) {
            fprintf(stderr, "usage: %s <window-id> [duration-seconds] [hz] "
                            "[all|description|onscreen]\n",
                    argv[0]);
            return 64;
        }
        uint32_t windowIDs[256] = {0};
        size_t windowCount = 0;
        char *windowList = strdup(argv[1]);
        char *cursor = windowList;
        char *token = NULL;
        while (windowCount < 256 &&
               (token = strsep(&cursor, ",")) != NULL) {
            unsigned long value = strtoul(token, NULL, 0);
            if (value > 0 && value <= UINT32_MAX)
                windowIDs[windowCount++] = (uint32_t)value;
        }
        free(windowList);
        if (windowCount == 0) {
            fprintf(stderr, "no valid window IDs\n");
            return 64;
        }
        uint32_t windowID = windowIDs[0];
        double duration = argc > 2 ? fmax(0.25, atof(argv[2])) : 2.0;
        double rate = argc > 3 ? fmin(240.0, fmax(1.0, atof(argv[3]))) : 120.0;
        bool descriptionOnly = argc > 4 &&
            strcmp(argv[4], "description") == 0;
        bool onScreenList = argc > 4 && strcmp(argv[4], "onscreen") == 0;
        descriptionOnly |= onScreenList;

        void *skyLight = dlopen(
            "/System/Library/PrivateFrameworks/SkyLight.framework/SkyLight",
            RTLD_LAZY | RTLD_LOCAL);
        void *scope = skyLight ?: RTLD_DEFAULT;
        MacWSCGSMainConnectionIDFn mainConnection =
            (MacWSCGSMainConnectionIDFn)dlsym(scope, "CGSMainConnectionID");
        MacWSSLSGetWindowBoundsFn getBounds =
            (MacWSSLSGetWindowBoundsFn)dlsym(scope, "SLSGetWindowBounds");
        MacWSSLSGetWindowTransformFn getTransform =
            (MacWSSLSGetWindowTransformFn)dlsym(scope,
                                                "SLSGetWindowTransform");
        MacWSSLSGetWindowTransformAtPlacementFn getTransformAtPlacement =
            (MacWSSLSGetWindowTransformAtPlacementFn)dlsym(
                scope, "SLSGetWindowTransformAtPlacement");
        MacWSSLSGetOnScreenWindowCountFn getOnScreenWindowCount =
            (MacWSSLSGetOnScreenWindowCountFn)dlsym(
                scope, "SLSGetOnScreenWindowCount");
        MacWSSLSGetOnScreenWindowListFn getOnScreenWindowList =
            (MacWSSLSGetOnScreenWindowListFn)dlsym(
                scope, "SLSGetOnScreenWindowList");
        void *setSpaceTransform = dlsym(
            scope, "SLSTransactionSetSpaceTransform");
        void *setWindowTransform = dlsym(
            scope, "SLSTransactionSetWindowTransform");
        void *setWindowTransform3D = dlsym(
            scope, "SLSTransactionSetWindowTransform3D");
        if (!mainConnection || !getBounds || !getTransform ||
            !getTransformAtPlacement) {
            fprintf(stderr,
                    "unavailable main=%p bounds=%p transform=%p placement=%p\n",
                    mainConnection, getBounds, getTransform,
                    getTransformAtPlacement);
            if (skyLight) dlclose(skyLight);
            return 69;
        }

        uint32_t connectionID = mainConnection();
        printf("{\"schema\":\"macws-skylight-geometry-probe-v1\",");
        printf("\"window_id\":%u,\"connection_id\":%u,\"symbols\":{",
               windowID, connectionID);
        PrintBytes("SLSGetWindowBounds", (const void *)getBounds);
        printf(",");
        PrintBytes("SLSGetWindowTransform", (const void *)getTransform);
        printf(",");
        PrintBytes("SLSGetWindowTransformAtPlacement",
                   (const void *)getTransformAtPlacement);
        if (getOnScreenWindowCount && getOnScreenWindowList) {
            printf(",");
            PrintBytes("SLSGetOnScreenWindowCount",
                       (const void *)getOnScreenWindowCount);
            printf(",");
            PrintBytes("SLSGetOnScreenWindowList",
                       (const void *)getOnScreenWindowList);
        }
        if (setSpaceTransform) {
            printf(",");
            PrintCodeBytes("SLSTransactionSetSpaceTransform",
                           setSpaceTransform, 384);
        }
        if (setWindowTransform) {
            printf(",");
            PrintCodeBytes("SLSTransactionSetWindowTransform",
                           setWindowTransform, 384);
        }
        if (setWindowTransform3D) {
            printf(",");
            PrintCodeBytes("SLSTransactionSetWindowTransform3D",
                           setWindowTransform3D, 384);
        }
        printf("},\"samples\":[");

        double started = MonotonicSeconds();
        double deadline = started + duration;
        uint64_t index = 0;
        bool first = true;
        while (MonotonicSeconds() < deadline) {
            @autoreleasepool {
                double sampleStarted = MonotonicSeconds();
                CGRect bounds = CGRectNull;
                CGAffineTransform transform = CGAffineTransformIdentity;
                CGAffineTransform placement0 = CGAffineTransformIdentity;
                CGAffineTransform placement1 = CGAffineTransformIdentity;
                double t0 = MonotonicSeconds();
                int32_t boundsStatus = descriptionOnly ? -1 :
                    getBounds(connectionID, windowID, &bounds);
                double t1 = MonotonicSeconds();
                int32_t transformStatus = descriptionOnly ? -1 :
                    getTransform(connectionID, windowID, &transform);
                double t2 = MonotonicSeconds();
                int32_t placement0Status = descriptionOnly ? -1 :
                    getTransformAtPlacement(connectionID, windowID, 0, NULL,
                                            &placement0);
                double t3 = MonotonicSeconds();
                int32_t placement1Status = descriptionOnly ? -1 :
                    getTransformAtPlacement(connectionID, windowID, 1, NULL,
                                            &placement1);
                double t4 = MonotonicSeconds();
                bool descriptionValid = false;
                uint32_t sampledWindowIDs[256] = {0};
                const uint32_t *descriptionWindowIDs = windowIDs;
                size_t descriptionWindowCount = windowCount;
                if (onScreenList && getOnScreenWindowCount &&
                    getOnScreenWindowList) {
                    uint32_t reportedCount = 0;
                    if (getOnScreenWindowCount(connectionID, 0,
                                               &reportedCount) == 0) {
                        uint32_t capacity = MIN(reportedCount, 256u);
                        uint32_t copiedCount = 0;
                        if (capacity > 0 &&
                            getOnScreenWindowList(connectionID, 0, capacity,
                                                  sampledWindowIDs,
                                                  &copiedCount) == 0) {
                            descriptionWindowIDs = sampledWindowIDs;
                            descriptionWindowCount = MIN(copiedCount,
                                                         capacity);
                        }
                    }
                }
                CGRect description = TargetedDescriptionBounds(
                    descriptionWindowIDs, descriptionWindowCount, windowID,
                    &descriptionValid);
                double t5 = MonotonicSeconds();

                if (!first) printf(",");
                first = false;
                printf("{\"i\":%llu,\"t_ms\":%.3f,",
                       (unsigned long long)index,
                       (sampleStarted - started) * 1000.0);
                printf("\"status\":[%d,%d,%d,%d],\"bounds\":",
                       boundsStatus, transformStatus, placement0Status,
                       placement1Status);
                PrintRect(bounds);
                printf(",\"transform\":");
                PrintTransform(transform);
                printf(",\"placement0\":");
                PrintTransform(placement0);
                printf(",\"placement1\":");
                PrintTransform(placement1);
                printf(",\"description_valid\":%s,\"description\":",
                       descriptionValid ? "true" : "false");
                PrintRect(description);
                printf(",\"cost_us\":[%.3f,%.3f,%.3f,%.3f,%.3f]}",
                       (t1 - t0) * 1.0e6, (t2 - t1) * 1.0e6,
                       (t3 - t2) * 1.0e6, (t4 - t3) * 1.0e6,
                       (t5 - t4) * 1.0e6);
                fflush(stdout);

                index++;
                double next = started + index / rate;
                double remaining = next - MonotonicSeconds();
                if (remaining > 0.0)
                    [NSThread sleepForTimeInterval:remaining];
            }
        }
        printf("],\"elapsed_ms\":%.3f}\n",
               (MonotonicSeconds() - started) * 1000.0);
        if (skyLight) dlclose(skyLight);
        return 0;
    }
}

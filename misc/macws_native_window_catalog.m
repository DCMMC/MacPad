// Read-only catalog diagnostic. No NSApplication, activation, capture stream,
// window mutation or event injection. Invoke inside the macOS chroot.
#import <Foundation/Foundation.h>
#import <AppKit/AppKit.h>
#import <CoreGraphics/CoreGraphics.h>
#import <objc/runtime.h>
#include <dlfcn.h>
#include <mach-o/dyld.h>
#include <errno.h>
#include <signal.h>
#include <stdlib.h>
#include <unistd.h>

static void Deadline(int signal) { (void)signal; _exit(124); }

int main(int argc, const char **argv) {
    signal(SIGALRM, Deadline);
    alarm(5);
    @autoreleasepool {
        if (argc == 3 && !strcmp(argv[1], "--symbol-addresses")) {
            // Symbol lookup in this short-lived helper, never in/attached to
            // the target. Libraries are loaded but no context/device is made.
            const char *images[] = {
                "/System/Library/Frameworks/CoreImage.framework/CoreImage",
                "/System/Library/Frameworks/OpenGL.framework/Versions/A/Libraries/libGLProgrammability.dylib",
                "/System/Library/Frameworks/OpenGL.framework/Versions/A/Libraries/libGLRendererFloat.dylib",
            };
            for (unsigned i = 0; i < sizeof(images)/sizeof(images[0]); i++) {
                void *loaded = dlopen(images[i], RTLD_LAZY | RTLD_LOCAL);
                fprintf(stderr, "symbol-library path=%s loaded=%d\n", images[i], loaded != NULL);
            }
            Dl_info image = {0};
            intptr_t slide = 0;
            if (!dladdr(dlsym(RTLD_DEFAULT, "CGPreflightScreenCaptureAccess"), &image)) return 2;
            for (uint32_t j = 0; j < _dyld_image_count(); j++)
                if (_dyld_get_image_header(j) == image.dli_fbase)
                    slide = _dyld_get_image_vmaddr_slide(j);
            NSArray *addresses = [[NSString stringWithUTF8String:argv[2]] componentsSeparatedByString:@","];
            if (addresses.count > 16) return 64;
            for (NSString *text in addresses) {
                char *end = NULL;
                uintptr_t address = strtoull(text.UTF8String, &end, 0);
                if (!address || !end || *end) return 64;
                Dl_info info = {0};
                BOOL found = dladdr((void *)(address + slide), &info);
                fprintf(stderr, "symbol address=0x%lx found=%d image=%s name=%s offset=%lu\n",
                    (unsigned long)address, found, info.dli_fname ?: "none",
                    info.dli_sname ?: "none", info.dli_saddr ?
                    (unsigned long)(address + slide - (uintptr_t)info.dli_saddr) : 0);
            }
            return 0;
        }
        if (argc != 2) { fprintf(stderr, "usage: %s OWNER_PID\n", argv[0]); return 64; }
        if (!strcmp(argv[1], "--capture-permission-status")) {
            // Read-only dependency diagnostic. RE-confirmed SkyLight UUID
            // 96676A53-B1E0-3D7E-B98B-B73873CD1880, 18520d1f8 loads TCC
            // with RTLD_LAZY|RTLD_LOCAL; 18520d030 calls Preflight(service,0).
            // No request, entitlement mutation, TCC write, or stream creation.
            BOOL granted = CGPreflightScreenCaptureAccess();
            void *tcc = dlopen("/System/Library/PrivateFrameworks/TCC.framework/TCC",
                               RTLD_LAZY | RTLD_LOCAL);
            const char *error = tcc ? NULL : dlerror();
            fprintf(stderr, "capture-permission native-preflight=%d tcc-loaded=%d error=%s\n",
                granted, tcc != NULL, error ?: "none");
            if (!tcc) return 2;
            int (*preflight)(CFStringRef, CFDictionaryRef) = dlsym(tcc, "TCCAccessPreflight");
            CFStringRef *service = dlsym(tcc, "kTCCServiceScreenCapture");
            fprintf(stderr, "capture-permission preflight-symbol=%d service-symbol=%d\n",
                preflight != NULL, service != NULL);
            if (preflight) {
                Dl_info info = {0};
                if (dladdr((void *)preflight, &info)) {
                    intptr_t slide = 0;
                    for (uint32_t j = 0; j < _dyld_image_count(); ++j)
                        if (_dyld_get_image_header(j) == info.dli_fbase)
                            slide = _dyld_get_image_vmaddr_slide(j);
                    fprintf(stderr, "capture-permission image=%s preflight-unslid=0x%lx\n",
                        info.dli_fname, (unsigned long)((uintptr_t)preflight - slide));
                }
            }
            if (preflight && service && *service) {
                int status = preflight(*service, NULL);
                fprintf(stderr, "capture-permission tcc-status=%d\n", status);
            }
            dlclose(tcc);
            return 0;
        }
        if (!strcmp(argv[1], "--capture-methods")) {
            const char *names[] = {"CGPreflightScreenCaptureAccess",
                                   "CGRequestScreenCaptureAccess"};
            for (size_t i = 0; i < sizeof(names) / sizeof(names[0]); ++i) {
                void *address = dlsym(RTLD_DEFAULT, names[i]);
                Dl_info info = {0};
                if (!address || !dladdr(address, &info)) return 2;
                intptr_t slide = 0;
                for (uint32_t j = 0; j < _dyld_image_count(); ++j)
                    if (_dyld_get_image_header(j) == info.dli_fbase)
                        slide = _dyld_get_image_vmaddr_slide(j);
                printf("%s 0x%lx %s\n", names[i],
                    (unsigned long)((uintptr_t)address - slide), info.dli_fname);
            }
            return 0;
        }
        if (!strcmp(argv[1], "--window-methods")) {
            unsigned count = 0;
            Method *methods = class_copyMethodList(NSWindow.class, &count);
            for (unsigned i = 0; i < count; ++i) {
                const char *name = sel_getName(method_getName(methods[i]));
                if (!strcasestr(name, "size") && !strcasestr(name, "frame") &&
                    !strcasestr(name, "constraint") && !strcasestr(name, "layout")) continue;
                IMP imp = method_getImplementation(methods[i]);
                Dl_info info = {0};
                if (!dladdr((void *)imp, &info)) continue;
                intptr_t slide = 0;
                for (uint32_t j = 0; j < _dyld_image_count(); ++j)
                    if (_dyld_get_image_header(j) == info.dli_fbase)
                        slide = _dyld_get_image_vmaddr_slide(j);
                printf("%s %s 0x%lx %s\n", name,
                    method_getTypeEncoding(methods[i]),
                    (unsigned long)((uintptr_t)imp - slide), info.dli_fname);
            }
            free(methods);
            return 0;
        }
        char *end = NULL;
        errno = 0;
        BOOL allOnscreen = !strcmp(argv[1], "--onscreen");
        long owner = allOnscreen ? 0 : strtol(argv[1], &end, 10);
        if (!allOnscreen && (errno || !end || *end || owner <= 1 || owner > INT_MAX)) return 64;
        NSArray *catalog = CFBridgingRelease(CGWindowListCopyWindowInfo(
            allOnscreen ? kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements
                        : kCGWindowListOptionAll, kCGNullWindowID));
        if (!catalog) return 2;
        NSMutableArray *windows = [NSMutableArray array];
        for (NSDictionary *entry in catalog) {
            if (allOnscreen || [entry[(id)kCGWindowOwnerPID] intValue] == owner)
                [windows addObject:entry];
            if (windows.count >= 64) break;
        }
        CGDirectDisplayID display = CGMainDisplayID();
        CGRect rect = CGDisplayBounds(display);
        NSDictionary *result = @{
            @"time": @(NSDate.date.timeIntervalSince1970),
            @"display_id": @(display),
            @"display_bounds": @[@(rect.origin.x), @(rect.origin.y),
                @(rect.size.width), @(rect.size.height)],
            @"windows": windows,
        };
        NSData *json = [NSJSONSerialization dataWithJSONObject:result
            options:NSJSONWritingPrettyPrinted error:NULL];
        if (!json) return 3;
        fwrite(json.bytes, 1, json.length, stdout);
        fputc('\n', stdout);
    }
    return 0;
}

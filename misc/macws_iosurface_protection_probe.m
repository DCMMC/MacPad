// Standalone five-second metadata contract test; no GPU work or user surfaces.
// Build for native iOS and macOS, then compare both with identical arguments:
// xcrun clang -arch arm64 -mmacosx-version-min=13.0 -Wall -Wextra -Werror \
//   -framework Foundation -framework IOSurface macws_iosurface_protection_probe.m -o probe
// Usage: probe [0|1]. Nonzero protection must remain nonzero; never mask it.
#import <Foundation/Foundation.h>
#import <IOSurface/IOSurfaceRef.h>
#include <objc/runtime.h>
#include <objc/message.h>
#include <mach-o/dyld.h>
#include <dlfcn.h>
#include <unistd.h>

int main(int argc, char **argv) {
    if (argc > 2 || (argc == 2 && strcmp(argv[1], "0") && strcmp(argv[1], "1")))
        return 2;
    uint64_t expected = argc == 2 ? strtoull(argv[1], NULL, 10) : 0;
    alarm(5);
    setvbuf(stdout, NULL, _IONBF, 0);
    const struct mach_header *mainHeader = _dyld_get_image_header(0);
    printf("main-cputype=%d cpusubtype=%#x\n", mainHeader->cputype,
           (unsigned)mainHeader->cpusubtype);
    @autoreleasepool {
        typedef uint64_t (*PublicGetter)(IOSurfaceRef);
        typedef uint64_t (*ClientGetter)(void *);
        PublicGetter get = (PublicGetter)dlsym(RTLD_DEFAULT,
                                              "IOSurfaceGetProtectionOptions");
        ClientGetter getClient = (ClientGetter)dlsym(RTLD_DEFAULT,
                                      "IOSurfaceClientGetProtectionOptions");
        if (!get || !getClient) return 3;
        for (unsigned planar = 0; planar < 2; ++planar) {
            NSMutableDictionary *props = [@{
                @"IOSurfaceWidth": @16, @"IOSurfaceHeight": @16,
                @"IOSurfaceBytesPerElement": planar ? @5 : @4,
                @"IOSurfaceBytesPerRow": @128, @"IOSurfaceAllocSize": @16384,
                @"IOSurfacePixelFormat": @((uint32_t)(planar ? 'b3a8' : 'BGRA')),
                @"IOSurfaceProtectionOptions": @(expected),
            } mutableCopy];
            if (planar) props[@"IOSurfacePlaneInfo"] = @[
                @{@"IOSurfacePlaneWidth": @16, @"IOSurfacePlaneHeight": @16,
                  @"IOSurfacePlaneBytesPerElement": @4,
                  @"IOSurfacePlaneBytesPerRow": @64,
                  @"IOSurfacePlaneOffset": @0, @"IOSurfacePlaneSize": @1024},
                @{@"IOSurfacePlaneWidth": @16, @"IOSurfacePlaneHeight": @16,
                  @"IOSurfacePlaneBytesPerElement": @1,
                  @"IOSurfacePlaneBytesPerRow": @64,
                  @"IOSurfacePlaneOffset": @1024, @"IOSurfacePlaneSize": @1024},
            ];
            IOSurfaceRef surface = IOSurfaceCreate((__bridge CFDictionaryRef)props);
            if (!surface) return 4;
            Class cls = object_getClass((__bridge id)surface);
            // The probe validates these private entry points explicitly. This
            // is not an arbitrary-object cast in a production surface API.
            Ivar impl = class_getInstanceVariable(cls, "_impl");
            if (!impl) impl = class_getInstanceVariable(cls, "_surface");
            Method method = class_getInstanceMethod(cls,
                                      sel_registerName("protectionOptions"));
            if (!impl || !method ||
                strcmp(method_getTypeEncoding(method), "Q16@0:8")) return 5;
            void *client = NULL;
            memcpy(&client, (const uint8_t *)surface + ivar_getOffset(impl),
                   sizeof(client));
            if (!client) return 6;
            uint64_t publicValue = get(surface);
            uint64_t clientValue = getClient(client);
            uint64_t objcValue = ((uint64_t (*)(id, SEL))objc_msgSend)(
                (__bridge id)surface, sel_registerName("protectionOptions"));
            printf("format=%s expected=%#llx public=%#llx client=%#llx objc=%#llx\n",
                   planar ? "b3a8" : "BGRA", (unsigned long long)expected,
                   (unsigned long long)publicValue, (unsigned long long)clientValue,
                   (unsigned long long)objcValue);
            CFRelease(surface);
            if (publicValue != expected || clientValue != expected ||
                objcValue != expected) return 7;
        }
    }
    return 0;
}

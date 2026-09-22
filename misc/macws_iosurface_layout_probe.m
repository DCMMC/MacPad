// Five-second, self-owned IOSurface metadata discriminator. No Metal/GPU work,
// no external surfaces, and no production overrides. Run native and injected
// chroot variants separately; a disagreement is evidence, not an automatic fix.
#import <Foundation/Foundation.h>
#import <IOSurface/IOSurfaceRef.h>
#include <dlfcn.h>
#include <mach/mach.h>
#include <mach-o/dyld.h>
#include <objc/message.h>
#include <objc/runtime.h>
#include <ptrauth.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

typedef size_t (*PublicScalar)(IOSurfaceRef);
typedef size_t (*PublicPlane)(IOSurfaceRef, size_t);
typedef size_t (*ClientScalar)(void *);
typedef size_t (*ClientPlane)(void *, size_t);

static size_t PublicPlaneBase(IOSurfaceRef surface, size_t plane) {
    return (uintptr_t)IOSurfaceGetBaseAddressOfPlane(surface, plane);
}

static void PrintImage(const char *what, const void *pointer) {
    Dl_info info = {0};
    if (!dladdr(pointer, &info) || !info.dli_fbase) {
        printf("identity what=%s unresolved\n", what);
        return;
    }
    const struct mach_header_64 *header = info.dli_fbase;
    const uint8_t *command = (const uint8_t *)(header + 1);
    printf("identity what=%s image=%s offset=%#llx uuid=", what,
           info.dli_fname, (unsigned long long)((uintptr_t)pointer -
                                               (uintptr_t)info.dli_fbase));
    for (uint32_t index = 0; index < header->ncmds; ++index) {
        const struct load_command *lc = (const void *)command;
        if (lc->cmd == LC_UUID) {
            const struct uuid_command *uuid = (const void *)command;
            for (size_t byte = 0; byte < 16; ++byte)
                printf("%02x", uuid->uuid[byte]);
            break;
        }
        command += lc->cmdsize;
    }
    putchar('\n');
}

static BOOL ReadObjC(id surface, const char *name, BOOL planar,
                     size_t plane, uintptr_t *value) {
    SEL selector = sel_registerName(name);
    Method method = class_getInstanceMethod(object_getClass(surface), selector);
    if (!method) {
        printf("objc selector=%s unavailable\n", name);
        return NO;
    }
    char resultType[32] = {0}, argumentType[32] = {0};
    method_getReturnType(method, resultType, sizeof(resultType));
    if (planar) method_getArgumentType(method, 2, argumentType,
                                       sizeof(argumentType));
    printf("objc selector=%s types=%s\n", name, method_getTypeEncoding(method));
    PrintImage(name, (const void *)method_getImplementation(method));
    if ((strcmp(resultType, "Q") && strcmp(resultType, "q") &&
         strcmp(resultType, "^v")) ||
        method_getNumberOfArguments(method) != (planar ? 3u : 2u) ||
        (planar && strcmp(argumentType, "Q"))) return NO;
    if (!strcmp(resultType, "^v")) {
        *value = planar
            ? (uintptr_t)((void *(*)(id, SEL, size_t))objc_msgSend)(surface, selector, plane)
            : (uintptr_t)((void *(*)(id, SEL))objc_msgSend)(surface, selector);
    } else if (!strcmp(resultType, "q")) {
        *value = planar
            ? (uintptr_t)((intptr_t (*)(id, SEL, size_t))objc_msgSend)(surface, selector, plane)
            : (uintptr_t)((intptr_t (*)(id, SEL))objc_msgSend)(surface, selector);
    } else {
        *value = planar
            ? ((uintptr_t (*)(id, SEL, size_t))objc_msgSend)(surface, selector, plane)
            : ((uintptr_t (*)(id, SEL))objc_msgSend)(surface, selector);
    }
    return YES;
}

static unsigned Scalar(id surface, void *client, const char *field,
                       PublicScalar publicGetter, const char *clientName,
                       const char *selector, uintptr_t expected) {
    ClientScalar clientGetter = (ClientScalar)dlsym(RTLD_DEFAULT, clientName);
    if (!clientGetter) return 1;
    uintptr_t objcValue = 0;
    if (!ReadObjC(surface, selector, NO, 0, &objcValue)) return 1;
    uintptr_t publicValue = publicGetter((__bridge IOSurfaceRef)surface);
    uintptr_t clientValue = clientGetter(client);
    PrintImage(field, (const void *)publicGetter);
    PrintImage(clientName, (const void *)clientGetter);
    printf("scalar field=%s expected=%llu public=%llu client=%llu objc=%llu\n",
           field, (unsigned long long)expected, (unsigned long long)publicValue,
           (unsigned long long)clientValue, (unsigned long long)objcValue);
    return (publicValue != expected || clientValue != expected ||
            objcValue != expected) ? 1u : 0u;
}

static unsigned Plane(id surface, void *client, size_t plane, const char *field,
                      PublicPlane publicGetter, const char *clientName,
                      const char *selector, uintptr_t expected, BOOL address) {
    ClientPlane clientGetter = (ClientPlane)dlsym(RTLD_DEFAULT, clientName);
    if (!publicGetter || !clientGetter) return 1;
    uintptr_t objcValue = 0;
    BOOL hasObjC = selector != NULL;
    if (hasObjC && !ReadObjC(surface, selector, YES, plane, &objcValue)) return 1;
    uintptr_t publicValue = publicGetter((__bridge IOSurfaceRef)surface, plane);
    uintptr_t clientValue = address
        ? (uintptr_t)((void *(*)(void *, size_t))dlsym(RTLD_DEFAULT, clientName))(client, plane)
        : clientGetter(client, plane);
    uintptr_t base = address
        ? (uintptr_t)IOSurfaceGetBaseAddress((__bridge IOSurfaceRef)surface) : 0;
    PrintImage(field, address ? (const void *)IOSurfaceGetBaseAddressOfPlane
                             : (const void *)publicGetter);
    PrintImage(clientName, (const void *)clientGetter);
    printf("plane=%zu field=%s expected=%llu public=%llu client=%llu "
           "objc=%llu has-objc=%d base=%#llx address=%d\n", plane, field,
           (unsigned long long)expected, (unsigned long long)(publicValue - base),
           (unsigned long long)(clientValue - base),
           (unsigned long long)(hasObjC ? objcValue - base : 0), hasObjC,
           (unsigned long long)base, address);
    return (publicValue - base != expected || clientValue - base != expected ||
            (hasObjC && objcValue - base != expected)) ? 1u : 0u;
}

int main(int argc, char **argv) {
    if (argc > 2 || (argc == 2 && strcmp(argv[1], "--code"))) return 2;
    alarm(5);
    setvbuf(stdout, NULL, _IONBF, 0);
    Dl_info executable = {0};
    if (!dladdr((const void *)main, &executable) || !executable.dli_fbase) return 2;
    const struct mach_header *header = executable.dli_fbase;
    printf("pid=%d main-cputype=%d main-subtype=%#x\n", getpid(),
           header->cputype, (unsigned)header->cpusubtype);
    PrintImage("main", (const void *)main);
    for (uint32_t index = 0; index < _dyld_image_count(); ++index) {
        const char *name = _dyld_get_image_name(index);
        if (strstr(name, "IOSurface.framework/") || strstr(name, "libmachook"))
            PrintImage("mapped", _dyld_get_image_header(index));
    }
    if (argc == 2) {
        static const char *const names[] = {
            "WidthOfPlane", "HeightOfPlane", "BytesPerRowOfPlane",
            "BytesPerElementOfPlane", "ElementWidthOfPlane", "ElementHeightOfPlane",
            "OffsetOfPlane", "SizeOfPlane", "BaseAddressOfPlane",
            "CompressionTypeOfPlane", "WidthInCompressedTilesOfPlane",
            "HeightInCompressedTilesOfPlane", "NumberOfComponentsOfPlane",
            "BytesPerTileDataOfPlane", "AddressFormatOfPlane",
            "Width", "Height", "BytesPerRow", "BaseAddress", "PlaneCount",
        };
        for (size_t index = 0; index < sizeof(names) / sizeof(names[0]); ++index) {
            char symbol[96];
            snprintf(symbol, sizeof(symbol), "IOSurfaceClientGet%s", names[index]);
            void *function = dlsym(RTLD_DEFAULT, symbol);
            if (!function) return 6;
            function = ptrauth_strip(function, ptrauth_key_function_pointer);
            PrintImage(symbol, function);
            uint8_t bytes[96];
            vm_size_t count = 0;
            kern_return_t kr = vm_read_overwrite(mach_task_self(),
                (vm_address_t)function, sizeof(bytes), (vm_address_t)bytes, &count);
            if (kr != KERN_SUCCESS || count != sizeof(bytes)) return 6;
            printf("code symbol=%s bytes=", symbol);
            for (size_t byte = 0; byte < sizeof(bytes); ++byte) printf("%02x", bytes[byte]);
            putchar('\n');
        }
        return 0;
    }
    unsigned failures = 0;
    @autoreleasepool {
        for (unsigned planar = 0; planar < 2; ++planar) {
            NSMutableDictionary *props = [@{
                @"IOSurfaceWidth": @19, @"IOSurfaceHeight": @11,
                @"IOSurfaceBytesPerElement": planar ? @5 : @4,
                @"IOSurfaceBytesPerRow": @128, @"IOSurfaceAllocSize": @16384,
                @"IOSurfacePixelFormat": @((uint32_t)(planar ? 'b3a8' : 'BGRA')),
                @"IOSurfaceProtectionOptions": @0,
            } mutableCopy];
            if (planar) props[@"IOSurfacePlaneInfo"] = @[
                @{@"IOSurfacePlaneWidth": @19, @"IOSurfacePlaneHeight": @11,
                  @"IOSurfacePlaneBytesPerElement": @4,
                  @"IOSurfacePlaneElementWidth": @1,
                  @"IOSurfacePlaneElementHeight": @1,
                  @"IOSurfacePlaneBytesPerRow": @128,
                  @"IOSurfacePlaneOffset": @0, @"IOSurfacePlaneSize": @2048},
                @{@"IOSurfacePlaneWidth": @19, @"IOSurfacePlaneHeight": @11,
                  @"IOSurfacePlaneBytesPerElement": @1,
                  @"IOSurfacePlaneElementWidth": @1,
                  @"IOSurfacePlaneElementHeight": @1,
                  @"IOSurfacePlaneBytesPerRow": @64,
                  @"IOSurfacePlaneOffset": @4096, @"IOSurfacePlaneSize": @1024},
            ];
            IOSurfaceRef raw = IOSurfaceCreate((__bridge CFDictionaryRef)props);
            if (!raw) { printf("create-failed planar=%u\n", planar); return 3; }
            id surface = (__bridge id)raw;
            Class cls = object_getClass(surface);
            Ivar impl = class_getInstanceVariable(cls, "_impl");
            if (!impl) impl = class_getInstanceVariable(cls, "_surface");
            if (!impl || ivar_getOffset(impl) != 8) { CFRelease(raw); return 4; }
            void *client = NULL;
            memcpy(&client, (const uint8_t *)raw + ivar_getOffset(impl),
                   sizeof(client));
            if (!client) { CFRelease(raw); return 4; }
            CFDictionaryRef values = IOSurfaceCopyAllValues(raw);
            printf("surface planar=%u id=%u class=%s impl=%s+%td\n", planar,
                   IOSurfaceGetID(raw), class_getName(cls), ivar_getName(impl),
                   ivar_getOffset(impl));
            printf("requested=%s\n", [[props description] UTF8String]);
            printf("actual-properties=%s\n",
                   [[(__bridge NSDictionary *)values description] UTF8String]);
            if (values) CFRelease(values);
            failures += Scalar(surface, client, "width", IOSurfaceGetWidth,
                               "IOSurfaceClientGetWidth", "width", 19);
            failures += Scalar(surface, client, "height", IOSurfaceGetHeight,
                               "IOSurfaceClientGetHeight", "height", 11);
            failures += Scalar(surface, client, "bytesPerRow", IOSurfaceGetBytesPerRow,
                               "IOSurfaceClientGetBytesPerRow", "bytesPerRow", 128);
            failures += Scalar(surface, client, "bytesPerElement", IOSurfaceGetBytesPerElement,
                               "IOSurfaceClientGetBytesPerElement", "bytesPerElement",
                               planar ? 5 : 4);
            if (planar && IOSurfaceGetPlaneCount(raw) != 2) {
                CFRelease(raw); return 5;
            }
            for (size_t plane = 0; planar && plane < 2; ++plane) {
#define FIELD(name, selector, expected) \
                failures += Plane(surface, client, plane, #name, \
                    IOSurfaceGet##name##OfPlane, "IOSurfaceClientGet" #name "OfPlane", \
                    selector, expected, NO)
                FIELD(Width, "widthOfPlaneAtIndex:", 19);
                FIELD(Height, "heightOfPlaneAtIndex:", 11);
                FIELD(BytesPerRow, "bytesPerRowOfPlaneAtIndex:", plane ? 64 : 128);
                FIELD(BytesPerElement, "bytesPerElementOfPlaneAtIndex:", plane ? 1 : 4);
                FIELD(ElementWidth, "elementWidthOfPlaneAtIndex:", 1);
                FIELD(ElementHeight, "elementHeightOfPlaneAtIndex:", 1);
#undef FIELD
                failures += Plane(surface, client, plane, "BaseAddress",
                    PublicPlaneBase,
                    "IOSurfaceClientGetBaseAddressOfPlane", "baseAddressOfPlaneAtIndex:",
                    plane ? 4096 : 0, YES);
                failures += Plane(surface, client, plane, "Offset",
                    (PublicPlane)dlsym(RTLD_DEFAULT, "IOSurfaceGetOffsetOfPlane"),
                    "IOSurfaceClientGetOffsetOfPlane", NULL, plane ? 4096 : 0, NO);
                failures += Plane(surface, client, plane, "Size",
                    (PublicPlane)dlsym(RTLD_DEFAULT, "IOSurfaceGetSizeOfPlane"),
                    "IOSurfaceClientGetSizeOfPlane", NULL, plane ? 1024 : 2048, NO);
            }
            if (planar) {
                uint8_t metadata[384] = {0};
                vm_size_t count = 0;
                kern_return_t kr = vm_read_overwrite(mach_task_self(),
                    (vm_address_t)client + 0x80, sizeof(metadata),
                    (vm_address_t)metadata, &count);
                printf("owned-client+0x80 read=%d count=%lu bytes=", kr,
                       (unsigned long)count);
                if (kr == KERN_SUCCESS && count == sizeof(metadata))
                    for (size_t byte = 0; byte < sizeof(metadata); ++byte)
                        printf("%02x", metadata[byte]);
                putchar('\n');
            }
            CFRelease(raw);
        }
    }
    printf("layout-disagreements=%u result=%s\n", failures,
           failures ? "FAIL" : "PASS");
    return failures ? 1 : 0;
}

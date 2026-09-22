// Native-iOS, read-only AGX texture reverse-engineering locator.
//
// Creating the default Metal device loads the exact iOS AGXMetal13_3 bundle.
// The probe then asks the Objective-C runtime for the implementation address of
// the texture initImpl method.  It does not allocate a resource, create a
// command queue, or submit GPU work.

@import Foundation;
@import Metal;
@import CoreVideo;
@import IOSurface;

#import <dlfcn.h>
#import <objc/runtime.h>
#import <ptrauth.h>
#import <signal.h>
#import <stdint.h>
#import <stdio.h>
#import <stdlib.h>
#import <string.h>
#import <unistd.h>

static void MacWSDumpTextureDescriptor(id<MTLTexture> texture,
                                       size_t expectedWidth,
                                       size_t expectedHeight,
                                       const char *role) {
    if (!texture) return;
    Ivar implIvar = class_getInstanceVariable([texture class], "_impl");
    ptrdiff_t implOffset = implIvar ? ivar_getOffset(implIvar) : 0x208;
    void *impl = *(void **)((char *)(__bridge void *)texture + implOffset);
    fprintf(stderr,
        "IOS-AGX-NV12 role=%s texture=%p class=%s impl=%p implOffset=%#tx "
        "shape=%zux%zu pixelFormat=%lu\n",
        role, (__bridge void *)texture, object_getClassName(texture), impl,
        implOffset,
        expectedWidth, expectedHeight,
        (unsigned long)texture.pixelFormat);
    if (!impl) return;

    IOSurfaceRef boundSurface =
        *(IOSurfaceRef volatile *)((char *)impl + 0xa0);
    uint32_t boundPlane = *(volatile uint32_t *)((char *)impl + 0xa8);
    void *cpuMapping = *(void * volatile *)((char *)impl + 0x130);
    uint64_t gpuMapping = *(volatile uint64_t *)((char *)impl + 0x40);
    fprintf(stderr,
        "IOS-AGX-NV12 role=%s boundSurface=%p surfaceID=%u boundPlane=%u "
        "cpu130=%p gpu40=%#llx\n",
        role, (void *)boundSurface,
        boundSurface ? IOSurfaceGetID(boundSurface) : 0, boundPlane,
        cpuMapping, (unsigned long long)gpuMapping);

    for (ptrdiff_t offset = 0x140; offset <= 0x240; offset++) {
        uint64_t word0 = 0, word1 = 0, word2 = 0;
        memcpy(&word0, (char *)impl + offset, sizeof(word0));
        memcpy(&word1, (char *)impl + offset + 8, sizeof(word1));
        memcpy(&word2, (char *)impl + offset + 16, sizeof(word2));
        size_t width = (size_t)((word0 >> 28) & 0x3fff) + 1;
        size_t height = (size_t)((word0 >> 42) & 0x3fff) + 1;
        if (width != expectedWidth || height != expectedHeight) continue;
        const uint8_t *bytes = (const uint8_t *)impl + offset;
        char hex[49] = {0};
        for (size_t i = 0; i < 24; i++)
            snprintf(hex + i * 2, 3, "%02x", bytes[i]);
        uint64_t address = ((word1 >> 2) & UINT64_C(0xfffffffff)) << 4;
        fprintf(stderr,
            "IOS-AGX-NV12-DESCRIPTOR role=%s offset=%#tx word0=%#llx "
            "word1=%#llx word2=%#llx address=%#llx layout=%llu "
            "compressed=%llu extended=%llu bytes=%s\n",
            role, offset, (unsigned long long)word0,
            (unsigned long long)word1, (unsigned long long)word2,
            (unsigned long long)address,
            (unsigned long long)((word0 >> 4) & 3),
            (unsigned long long)((word1 >> 39) & 1),
            (unsigned long long)((word1 >> 63) & 1), hex);
        return;
    }
    fprintf(stderr,
        "IOS-AGX-NV12-DESCRIPTOR role=%s NOT-FOUND expected=%zux%zu\n",
        role, expectedWidth, expectedHeight);
}

int main(void) {
    @autoreleasepool {
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        fprintf(stderr, "IOS-AGX-TEXTURE-RE device=%p class=%s\n",
            (__bridge void *)device,
            device ? object_getClassName(device) : "(nil)");
        if (!device) return 2;

        Class textureClass = objc_getClass("AGXG13GFamilyTexture");
        SEL selector = sel_registerName(
            "initImplWithDevice:Descriptor:iosurface:plane:buffer:bytesPerRow:"
            "allowNPOT:sparsePageSize:isCompressedIOSurface:isHeapBacked:");
        Method method = textureClass
            ? class_getInstanceMethod(textureClass, selector) : NULL;
        IMP implementation = method ? method_getImplementation(method) : NULL;
        IMP strippedImplementation = implementation
            ? ptrauth_strip(implementation, ptrauth_key_function_pointer)
            : NULL;
        Dl_info image = {0};
        if (strippedImplementation) {
            dladdr((const void *)strippedImplementation, &image);
        }

        fprintf(stderr,
            "IOS-AGX-TEXTURE-RE class=%p method=%p imp=%p stripped=%p "
            "image=%s base=%p offset=%#llx\n",
            (__bridge void *)textureClass, (void *)method,
            (void *)implementation,
            (void *)strippedImplementation,
            image.dli_fname ?: "(nil)", image.dli_fbase,
            (unsigned long long)(strippedImplementation && image.dli_fbase
                ? (uintptr_t)strippedImplementation - (uintptr_t)image.dli_fbase
                : 0));

        // Opt-in runtime ABI witness for the Chromium failure captured on
        // 2026-07-30.  The default invocation above remains read-only.  With
        // MACWS_IOS_MIP_TEXTURE_PROBE=1, create the exact small mipmapped
        // shape that made macOS AGXMetal13_3 request a parent type-0x80
        // resource.  MACWS_IOS_MIP_TEXTURE_HOLD=1 stops after the native
        // device is ready so the project's LLDB resource-return tracer can
        // be installed before any texture resource is created.
        if (getenv("MACWS_IOS_MIP_TEXTURE_PROBE")) {
            if (getenv("MACWS_IOS_MIP_TEXTURE_HOLD")) {
                fprintf(stderr,
                    "IOS-AGX-TEXTURE-RE mip hold pid=%d before texture\n",
                    getpid());
                raise(SIGSTOP);
            }
            MTLTextureDescriptor *descriptor =
                [MTLTextureDescriptor
                    texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm
                    width:6
                    height:1
                    mipmapped:YES];
            descriptor.storageMode = MTLStorageModeShared;
            descriptor.usage = MTLTextureUsageShaderRead |
                MTLTextureUsageShaderWrite | MTLTextureUsageRenderTarget;
            fprintf(stderr,
                "IOS-AGX-TEXTURE-RE mip create size=%lux%lu pf=%lu "
                "mips=%lu storage=%lu usage=%#lx\n",
                (unsigned long)descriptor.width,
                (unsigned long)descriptor.height,
                (unsigned long)descriptor.pixelFormat,
                (unsigned long)descriptor.mipmapLevelCount,
                (unsigned long)descriptor.storageMode,
                (unsigned long)descriptor.usage);
            id<MTLTexture> texture =
                [device newTextureWithDescriptor:descriptor];
            fprintf(stderr,
                "IOS-AGX-TEXTURE-RE mip result=%p class=%s\n",
                (__bridge void *)texture,
                texture ? object_getClassName(texture) : "(nil)");
            if (!texture) return 4;
        }
        // Native reference for the exact VideoToolbox surface shape consumed
        // by VS Code/Electron. This creates no command queue and submits no GPU
        // work. It gives the macOS-chroot observer a byte-for-byte iOS
        // AGXMetal13_3 texture descriptor control for R8 plane 0 and RG8 plane
        // 1, instead of inferring format semantics from non-nil objects.
        if (getenv("MACWS_IOS_NV12_TEXTURE_PROBE")) {
            const size_t width = 640, height = 360;
            NSDictionary *attributes = @{
                (id)kCVPixelBufferIOSurfacePropertiesKey: @{},
                (id)kCVPixelBufferMetalCompatibilityKey: @YES,
            };
            CVPixelBufferRef pixelBuffer = NULL;
            CVReturn status = CVPixelBufferCreate(
                kCFAllocatorDefault, width, height,
                kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange,
                (__bridge CFDictionaryRef)attributes, &pixelBuffer);
            fprintf(stderr,
                "IOS-AGX-NV12 pixelBuffer status=%d object=%p\n",
                status, (void *)pixelBuffer);
            if (status != kCVReturnSuccess || !pixelBuffer) return 5;
            CVPixelBufferLockBaseAddress(pixelBuffer, 0);
            size_t planes = CVPixelBufferGetPlaneCount(pixelBuffer);
            for (size_t plane = 0; plane < planes; plane++) {
                void *base = CVPixelBufferGetBaseAddressOfPlane(pixelBuffer,
                                                               plane);
                size_t bytesPerRow = CVPixelBufferGetBytesPerRowOfPlane(
                    pixelBuffer, plane);
                size_t planeHeight = CVPixelBufferGetHeightOfPlane(
                    pixelBuffer, plane);
                memset(base, plane == 0 ? 81 : 128,
                       bytesPerRow * planeHeight);
                fprintf(stderr,
                    "IOS-AGX-NV12 plane=%zu base=%p width=%zu height=%zu "
                    "bytesPerRow=%zu\n",
                    plane, base,
                    CVPixelBufferGetWidthOfPlane(pixelBuffer, plane),
                    planeHeight, bytesPerRow);
            }
            CVPixelBufferUnlockBaseAddress(pixelBuffer, 0);

            IOSurfaceRef surface = CVPixelBufferGetIOSurface(pixelBuffer);
            fprintf(stderr,
                "IOS-AGX-NV12 surface=%p id=%u fourcc=%#x alloc=%zu\n",
                (void *)surface, surface ? IOSurfaceGetID(surface) : 0,
                surface ? IOSurfaceGetPixelFormat(surface) : 0,
                surface ? IOSurfaceGetAllocSize(surface) : 0);
            if (!surface) {
                CFRelease(pixelBuffer);
                return 6;
            }
            if (getenv("MACWS_IOS_NV12_TEXTURE_HOLD")) {
                fprintf(stderr,
                    "IOS-AGX-NV12 hold pid=%d before plane textures\n",
                    getpid());
                raise(SIGSTOP);
            }

            MTLTextureDescriptor *yDescriptor =
                [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:
                    MTLPixelFormatR8Unorm width:width height:height
                    mipmapped:NO];
            yDescriptor.usage = MTLTextureUsageShaderRead;
            yDescriptor.storageMode = MTLStorageModeShared;
            id<MTLTexture> yTexture = [device
                newTextureWithDescriptor:yDescriptor iosurface:surface plane:0];

            MTLTextureDescriptor *uvDescriptor =
                [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:
                    MTLPixelFormatRG8Unorm width:width / 2 height:height / 2
                    mipmapped:NO];
            uvDescriptor.usage = MTLTextureUsageShaderRead;
            uvDescriptor.storageMode = MTLStorageModeShared;
            id<MTLTexture> uvTexture = [device
                newTextureWithDescriptor:uvDescriptor iosurface:surface plane:1];
            MacWSDumpTextureDescriptor(yTexture, width, height, "Y");
            MacWSDumpTextureDescriptor(uvTexture, width / 2, height / 2,
                                       "UV");
            CFRelease(pixelBuffer);
            if (!yTexture || !uvTexture) return 7;
        }
        return implementation ? 0 : 3;
    }
}

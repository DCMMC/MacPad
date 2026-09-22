@import Foundation;
@import IOSurface;

static IOSurfaceRef ProbeSurface(uint32_t pixelFormat,
                                 NSUInteger bytesPerElement,
                                 NSUInteger cacheMode,
                                 BOOL keepSurface) {
    const NSUInteger width = 1024;
    const NSUInteger height = 1024;
    const NSUInteger bytesPerRow = width * bytesPerElement;
    NSDictionary *properties = @{
        (__bridge id)kIOSurfaceWidth: @(width),
        (__bridge id)kIOSurfaceHeight: @(height),
        (__bridge id)kIOSurfacePixelFormat: @(pixelFormat),
        (__bridge id)kIOSurfaceBytesPerElement: @(bytesPerElement),
        (__bridge id)kIOSurfaceBytesPerRow: @(bytesPerRow),
        (__bridge id)kIOSurfaceAllocSize: @(bytesPerRow * height),
        (__bridge id)kIOSurfaceCacheMode: @(cacheMode),
    };
    IOSurfaceRef surface = IOSurfaceCreate(
        (__bridge CFDictionaryRef)properties);
    char fourcc[5] = {
        (char)(pixelFormat >> 24), (char)(pixelFormat >> 16),
        (char)(pixelFormat >> 8), (char)pixelFormat, 0,
    };
    printf("pixel-format=%#x fourcc=%s bpe=%lu cache=%lu row=%lu alloc=%lu "
           "surface=%p\n",
           pixelFormat, fourcc, (unsigned long)bytesPerElement,
           (unsigned long)cacheMode,
           (unsigned long)bytesPerRow,
           (unsigned long)(bytesPerRow * height), surface);
    if (surface && !keepSurface) CFRelease(surface);
    return surface;
}

int main(void) {
    @autoreleasepool {
        printf("queue=main\n");
        ProbeSurface((uint32_t)'RGhA', 8, 0, NO);
        ProbeSurface((uint32_t)'RGhA', 8, 1024, NO);
        ProbeSurface((uint32_t)'RGBA', 8, 0, NO);
        ProbeSurface((uint32_t)'BGRA', 4, 0, NO);
        dispatch_sync(dispatch_get_global_queue(QOS_CLASS_DEFAULT, 0), ^{
            @autoreleasepool {
                printf("queue=global-default\n");
                ProbeSurface((uint32_t)'RGhA', 8, 0, NO);
                ProbeSurface((uint32_t)'RGhA', 8, 1024, NO);
                ProbeSurface((uint32_t)'RGBA', 8, 0, NO);
                ProbeSurface((uint32_t)'BGRA', 4, 0, NO);
            }
        });
        printf("queue=main-held\n");
        IOSurfaceRef held[16] = {0};
        for (NSUInteger index = 0; index < 16; index++) {
            held[index] = ProbeSurface((uint32_t)'RGhA', 8, 0, YES);
            if (!held[index]) break;
        }
        for (NSUInteger index = 0; index < 16; index++) {
            if (held[index]) CFRelease(held[index]);
        }
    }
    return 0;
}

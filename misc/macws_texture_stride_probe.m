// Standalone, explicitly invoked diagnostic: no UI, no remote surfaces, no flags.
// Build with Foundation, Metal, QuartzCore, CoreGraphics and IOSurface frameworks.
// Usage: probe {plain|surface|ca-image|buffer-view|buffer-copy|
//               nocopy-buffer-copy|nocopy-buffer-view}
//              {shared|managed}
//              [--actual-size|--office-row-stride]
// One asymmetric 19x11 owned image, padded CPU rows and a 256-byte GPU readback
// stride distinguish row/dimension errors from a uniform-color 16x16 success.
// The only larger mode is the template's actual 309x250 picture, restricted
// to plain/surface/ca-image/NoCopy modes. No arbitrary dimensions or benchmark.
// --office-row-stride is only NoCopy modes at 309x250, with the observed
// 1248-byte Office upload rows and independent 1280-byte GPU readback rows.
// This is not Office/Weather acceptance: a failure narrows a primitive; a pass
// does not prove the applications use this pixel format or allocation path.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <QuartzCore/QuartzCore.h>
#import <IOSurface/IOSurfaceRef.h>
#include <TargetConditionals.h>
#include <mach-o/dyld.h>
#include <mach-o/loader.h>
#include <stdio.h>
#include <stdatomic.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

static unsigned W = 19, H = 11, SOURCE_ROW = 128, OUTPUT_ROW = 256;
static unsigned SURFACE_BYTES = 16384;

static int ConfigureShape(int actualSize) {
    W = actualSize ? 309 : 19;
    H = actualSize ? 250 : 11;
    SOURCE_ROW = actualSize ? ((W * 4 + 63) & ~63u) : 128;
    OUTPUT_ROW = (W * 4 + 255) & ~255u;
    SURFACE_BYTES = (SOURCE_ROW * H + 16383) & ~16383u;
    // One source buffer, one output buffer reused for CPU getBytes, and one
    // surface/texture backing. This bounds explicitly owned pixel storage;
    // the framework's internal renderer/device allocations are not included.
    return SOURCE_ROW * H + OUTPUT_ROW * H + SURFACE_BYTES <= 1024 * 1024;
}

static unsigned ConfigureNoCopyRows(int officeRowStride, unsigned page) {
    if (page < 4096 || page > 65536 || (page & (page - 1))) return 0;
    if (officeRowStride && (W != 309 || H != 250)) return 0;
    SOURCE_ROW = officeRowStride ? 1248 : OUTPUT_ROW;
    return (SOURCE_ROW * H + page - 1) & ~(page - 1);
}

static void Provenance(void) {
    for (uint32_t i = 0; i < _dyld_image_count(); ++i) {
        const char *name = _dyld_get_image_name(i);
        const struct mach_header_64 *h = (const void *)_dyld_get_image_header(i);
        if (!name || !h) continue;
        // Injected libraries can precede MH_EXECUTE in this dyld build. Index
        // zero is not sufficient evidence of the main executable's slice.
        if (h->filetype == MH_EXECUTE)
            printf("main-executable=%s main-subtype=%#x\n", name,
                   (unsigned)h->cpusubtype);
        if (!strstr(name, "libmachook")) continue;
        const uint8_t *p = (const void *)(h + 1);
        printf("library=%s subtype=%#x", name, (unsigned)h->cpusubtype);
        for (uint32_t j = 0; j < h->ncmds; ++j) {
            const struct load_command *cmd = (const void *)p;
            if (cmd->cmd == LC_UUID) {
                const struct uuid_command *u = (const void *)p;
                printf(" uuid=");
                for (unsigned k = 0; k < 16; ++k) printf("%02x", u->uuid[k]);
            }
            p += cmd->cmdsize;
        }
        puts("");
    }
}

static _Atomic unsigned NoCopyCallbacks, NoCopyCallbackMismatch;

static void Fill(uint8_t *bytes) {
    memset(bytes, 0xa5, SOURCE_ROW * H);
    for (unsigned y = 0; y < H; ++y) for (unsigned x = 0; x < W; ++x) {
        uint8_t *p = bytes + y * SOURCE_ROW + x * 4;
        p[0] = (uint8_t)(17 + x * 7);  // BGRA; distinct rows AND columns
        p[1] = (uint8_t)(29 + y * 17);
        p[2] = (uint8_t)(47 + (x * 3 + y * 11) % 160);
        p[3] = 255;
    }
}

static BOOL Completed(id<MTLCommandBuffer> command, const char *phase) {
    [command commit];
    [command waitUntilCompleted];
    printf("phase=%s status=%lu error=%s\n", phase,
        (unsigned long)command.status,
        command.error ? command.error.description.UTF8String : "none");
    return command.status == MTLCommandBufferStatusCompleted;
}

static unsigned Check(const uint8_t *actual, NSUInteger row,
                      const uint8_t *expected, BOOL caOrigin,
                      const char *phase) {
    unsigned mismatches = 0, flipped = 0;
    for (unsigned y = 0; y < H; ++y) for (unsigned x = 0; x < W; ++x) {
        BOOL bad = NO, badFlipped = NO;
        for (unsigned c = 0; c < 4; ++c) {
            int value = actual[y * row + x * 4 + c];
            // A top-down CGImage is drawn into CARenderer's bottom-left layer
            // coordinates. Stock macOS control confirms this vertical order;
            // plain texture uploads retain the original row order.
            unsigned expectedY = caOrigin ? H - y - 1 : y;
            int want = expected[expectedY * SOURCE_ROW + x * 4 + c];
            int flip = expected[(H - expectedY - 1) * SOURCE_ROW + x * 4 + c];
            bad |= abs(value - want) > (caOrigin ? 2 : 0);
            badFlipped |= abs(value - flip) > (caOrigin ? 2 : 0);
        }
        mismatches += bad;
        flipped += badFlipped;
    }
    printf("phase=%s mismatched-pixels=%u/%u vertically-flipped-mismatches=%u "
           "first=%u,%u,%u,%u last=%u,%u,%u,%u\n", phase, mismatches, W * H,
           flipped, actual[0], actual[1], actual[2], actual[3],
           actual[(H - 1) * row + (W - 1) * 4],
           actual[(H - 1) * row + (W - 1) * 4 + 1],
           actual[(H - 1) * row + (W - 1) * 4 + 2],
           actual[(H - 1) * row + (W - 1) * 4 + 3]);
    return mismatches;
}

static int RunProbe(int argc, char **argv, uint8_t **mappedSource,
                    size_t *mappedLength) {
    if (argc != 3 && argc != 4) return 2;
    BOOL officeRowStride = argc == 4 && !strcmp(argv[3], "--office-row-stride");
    BOOL actualSize = officeRowStride || (argc == 4 && !strcmp(argv[3], "--actual-size"));
    if (argc == 4 && !actualSize) return 2;
    BOOL surfaceMode = !strcmp(argv[1], "surface");
    BOOL caMode = !strcmp(argv[1], "ca-image");
    BOOL bufferView = !strcmp(argv[1], "buffer-view");
    BOOL bufferCopy = !strcmp(argv[1], "buffer-copy");
    BOOL noCopyBufferCopy = !strcmp(argv[1], "nocopy-buffer-copy");
    BOOL noCopyBufferView = !strcmp(argv[1], "nocopy-buffer-view");
    BOOL noCopyMode = noCopyBufferCopy || noCopyBufferView;
    BOOL managed = !strcmp(argv[2], "managed");
    if ((!surfaceMode && !caMode && !bufferView && !bufferCopy && !noCopyMode &&
         strcmp(argv[1], "plain")) ||
        (!managed && strcmp(argv[2], "shared"))) return 2;
    if (bufferView && managed) return 2;
    if (officeRowStride && !noCopyMode) return 2;
    if (actualSize && (bufferView || bufferCopy)) return 2;
    if (!ConfigureShape(actualSize)) return 2;
    size_t sourceBytes = SOURCE_ROW * H;
    if (noCopyMode) {
        // The owned mmap replaces (rather than duplicates) the source array.
        // Standard rows use 256-byte alignment; the separately whitelisted
        // Office preset retains its recorded 16-byte-aligned upload stride.
        sourceBytes = ConfigureNoCopyRows(officeRowStride, (unsigned)getpagesize());
        if (!sourceBytes) return 2;
    }
    size_t ownedPixelBytes = sourceBytes + OUTPUT_ROW * H + SURFACE_BYTES;
    if (ownedPixelBytes > 1024 * 1024) return 2;
#if TARGET_OS_IPHONE
    if (managed) { puts("managed storage is not an iOS-native control"); return 77; }
#endif
    alarm(10);
    setvbuf(stdout, NULL, _IONBF, 0);
    @autoreleasepool {
        Provenance();
        printf("shape-preset=%s explicit-owned-pixel-storage-upper-bound=%zu\n",
            actualSize ? "309x250" : "19x11",
            ownedPixelBytes);
        printf("row-stride-preset=%s\n", officeRowStride ? "office-1248" : "standard");
        uint8_t stackSource[noCopyMode ? 1 : SOURCE_ROW * H];
        uint8_t *source = stackSource;
        if (noCopyMode) {
            source = mmap(NULL, sourceBytes, PROT_READ | PROT_WRITE,
                          MAP_ANON | MAP_PRIVATE, -1, 0);
            if (source == MAP_FAILED) return 16;
            *mappedSource = source;
            *mappedLength = sourceBytes;
            memset(source, 0x25, sourceBytes); // Not the final pattern.
        } else {
            Fill(source);
        }
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        id<MTLCommandQueue> queue = [device newCommandQueue];
        if (!device || !queue) return 3;
        MTLTextureDescriptor *desc = [MTLTextureDescriptor
            texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
            width:W height:H mipmapped:NO];
        desc.storageMode = managed ? (MTLStorageMode)1 : MTLStorageModeShared;
        desc.usage = MTLTextureUsageShaderRead | MTLTextureUsageRenderTarget;
        if (bufferView || noCopyBufferView)
            desc.usage = MTLTextureUsageShaderRead | MTLTextureUsageShaderWrite;
        id<MTLBuffer> staging = nil;
        if (noCopyMode) {
            uint8_t *original = source;
            staging = [device newBufferWithBytesNoCopy:original length:sourceBytes
                options:(managed ? (1UL << MTLResourceStorageModeShift)
                                 : MTLResourceStorageModeShared)
                deallocator:^(void *pointer, NSUInteger length) {
                    if (pointer != original || length != sourceBytes)
                        atomic_fetch_add(&NoCopyCallbackMismatch, 1);
                    // Record only: a broken early callback must not unmap the
                    // memory before this diagnostic can report its failure.
                    atomic_fetch_add(&NoCopyCallbacks, 1);
                }];
            BOOL alias = staging && staging.contents == original;
            BOOL storage = staging && staging.storageMode == desc.storageMode;
            printf("nocopy original=%p contents=%p length=%zu alias=%d "
                   "requested-storage=%lu actual-storage=%lu early-callbacks=%u\n",
                   original, staging.contents, sourceBytes, alias,
                   (unsigned long)desc.storageMode,
                   (unsigned long)staging.storageMode, atomic_load(&NoCopyCallbacks));
            if (!alias || !storage || atomic_load(&NoCopyCallbacks)) return 17;
            // This write MUST follow NoCopy creation, and MUST target the
            // original mmap, never buffer.contents (which would hide a copy).
            Fill(original);
#if !TARGET_OS_IPHONE
            if (managed) [staging didModifyRange:NSMakeRange(0, SOURCE_ROW * H)];
#endif
            puts("nocopy pattern-written-after-creation=1 target=original-mmap");
        } else if (bufferView || bufferCopy) {
            // Deliberately nonzero offset, padded rows, and no public texture
            // upload. These are actual mso40ui-imported staging API shapes.
            staging = [device newBufferWithLength:OUTPUT_ROW * (H + 1)
                options:MTLResourceStorageModeShared];
            if (!staging || !staging.contents) return 13;
            memset(staging.contents, 0xa5, OUTPUT_ROW * (H + 1));
            for (unsigned y = 0; y < H; ++y)
                memcpy((uint8_t *)staging.contents + OUTPUT_ROW * (y + 1),
                       source + SOURCE_ROW * y, W * 4);
        }
        IOSurfaceRef surface = NULL;
        if (surfaceMode) {
            surface = IOSurfaceCreate((__bridge CFDictionaryRef)@{
                @"IOSurfaceWidth":@(W), @"IOSurfaceHeight":@(H),
                @"IOSurfaceBytesPerElement":@4,
                @"IOSurfaceBytesPerRow":@(SOURCE_ROW),
                @"IOSurfaceAllocSize":@(SURFACE_BYTES),
                @"IOSurfacePixelFormat":@((uint32_t)'BGRA')});
            if (!surface) return 4;
            printf("surface=%u width=%zu height=%zu bpr=%zu alloc=%zu planes=%zu\n",
                IOSurfaceGetID(surface), IOSurfaceGetWidth(surface),
                IOSurfaceGetHeight(surface), IOSurfaceGetBytesPerRow(surface),
                IOSurfaceGetAllocSize(surface), IOSurfaceGetPlaneCount(surface));
        }
        if (bufferView || noCopyBufferView)
            printf("buffer-view requested-storage=%lu offset=%u source-bpr=%u\n",
                   (unsigned long)desc.storageMode,
                   noCopyBufferView ? 0 : OUTPUT_ROW,
                   noCopyBufferView ? SOURCE_ROW : OUTPUT_ROW);
        id<MTLTexture> texture = (bufferView || noCopyBufferView)
            ? [staging newTextureWithDescriptor:desc
                offset:(noCopyBufferView ? 0 : OUTPUT_ROW)
                bytesPerRow:(noCopyBufferView ? SOURCE_ROW : OUTPUT_ROW)]
            : surface
                ? [device newTextureWithDescriptor:desc iosurface:surface plane:0]
                : [device newTextureWithDescriptor:desc];
        if (!texture) {
            printf("texture-create mode=%s result=NIL contract=FAIL\n", argv[1]);
            return 5;
        }
        printf("mode=%s requested-storage=%lu actual-storage=%lu shape=%lux%lu "
            "upload-bpr=%u gpu-readback-bpr=%u\n", argv[1],
            (unsigned long)desc.storageMode, (unsigned long)texture.storageMode,
            (unsigned long)texture.width, (unsigned long)texture.height,
            SOURCE_ROW, OUTPUT_ROW);
        if (texture.width != W || texture.height != H) return 6;
        if (noCopyBufferView) {
            BOOL metadata = texture.storageMode == desc.storageMode &&
                texture.pixelFormat == MTLPixelFormatBGRA8Unorm &&
                texture.buffer == staging && texture.bufferOffset == 0 &&
                texture.bufferBytesPerRow == SOURCE_ROW;
            printf("buffer-view result=non-NIL buffer-match=%d offset=%lu "
                   "actual-bpr=%lu metadata=%s\n", texture.buffer == staging,
                   (unsigned long)texture.bufferOffset,
                   (unsigned long)texture.bufferBytesPerRow,
                   metadata ? "PASS" : "FAIL");
            if (!metadata) return 20;
        }
        if (caMode) {
            CGColorSpaceRef color = CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
            CGDataProviderRef provider = CGDataProviderCreateWithData(
                NULL, source, SOURCE_ROW * H, NULL);
            CGImageRef image = CGImageCreate(W, H, 8, 32, SOURCE_ROW, color,
                kCGBitmapByteOrder32Little | kCGImageAlphaPremultipliedFirst,
                provider, NULL, NO, kCGRenderingIntentDefault);
            if (!image) return 7;
            CARenderer *renderer = [CARenderer rendererWithMTLTexture:texture
                options:@{kCARendererMetalCommandQueue:queue,
                          kCARendererColorSpace:(__bridge id)color}];
            CALayer *layer = [CALayer layer];
            layer.frame = CGRectMake(0, 0, W, H);
            layer.contents = (__bridge id)image;
            layer.contentsGravity = kCAGravityResize;
            layer.minificationFilter = kCAFilterNearest;
            layer.magnificationFilter = kCAFilterNearest;
            renderer.bounds = layer.frame;
            renderer.layer = layer;
            [CATransaction flush];
            [renderer beginFrameAtTime:CACurrentMediaTime() timeStamp:NULL];
            [renderer addUpdateRect:renderer.bounds];
            [renderer render];
            [renderer endFrame];
            if (!Completed([queue commandBuffer], "ca-fence")) return 8;
            renderer.layer = nil;
            CGImageRelease(image);
            CGDataProviderRelease(provider);
            CGColorSpaceRelease(color);
        } else if (bufferCopy || noCopyBufferCopy) {
            id<MTLCommandBuffer> upload = [queue commandBuffer];
            id<MTLBlitCommandEncoder> encoder = [upload blitCommandEncoder];
            if (!encoder) return 14;
            NSUInteger stagingRow = noCopyBufferCopy ? SOURCE_ROW : OUTPUT_ROW;
            printf("buffer-upload source-offset=%u source-bpr=%lu "
                   "source-bytes-per-image=%lu\n", noCopyBufferCopy ? 0 : OUTPUT_ROW,
                   (unsigned long)stagingRow, (unsigned long)(stagingRow * H));
            [encoder copyFromBuffer:staging sourceOffset:(noCopyBufferCopy ? 0 : OUTPUT_ROW)
                sourceBytesPerRow:stagingRow sourceBytesPerImage:stagingRow * H
                sourceSize:MTLSizeMake(W, H, 1) toTexture:texture
                destinationSlice:0 destinationLevel:0
                destinationOrigin:MTLOriginMake(0, 0, 0)];
            [encoder endEncoding];
            if (!Completed(upload, "buffer-upload")) return 15;
        } else if (!bufferView && !noCopyBufferView) {
            [texture replaceRegion:MTLRegionMake2D(0, 0, W, H) mipmapLevel:0
                withBytes:source bytesPerRow:SOURCE_ROW];
            // Exercise a padded subrectangle through the full 6-argument API.
            // Distinct values ensure an ignored upload cannot accidentally pass.
            uint8_t patch[SOURCE_ROW * 5];
            memset(patch, 0xa5, sizeof(patch));
            for (unsigned y = 0; y < 5; ++y) for (unsigned x = 0; x < 7; ++x) {
                uint8_t *p = patch + y * SOURCE_ROW + x * 4;
                p[0] = (uint8_t)(231 - x * 13);
                p[1] = (uint8_t)(219 - y * 19);
                p[2] = (uint8_t)(31 + x * 5 + y * 7);
                p[3] = 255;
                memcpy(source + (y + 2) * SOURCE_ROW + (x + 3) * 4, p, 4);
            }
            [texture replaceRegion:MTLRegionMake2D(3, 2, 7, 5) mipmapLevel:0
                slice:0 withBytes:patch
                bytesPerRow:SOURCE_ROW bytesPerImage:SOURCE_ROW * 5];
        }
        id<MTLBuffer> output = [device newBufferWithLength:OUTPUT_ROW * H
            options:MTLResourceStorageModeShared];
        if (!output || !output.contents) return 9;
        memset(output.contents, 0x5a, OUTPUT_ROW * H);
        id<MTLCommandBuffer> copy = [queue commandBuffer];
        id<MTLBlitCommandEncoder> blit = [copy blitCommandEncoder];
        if (!blit) return 10;
        [blit copyFromTexture:texture sourceSlice:0 sourceLevel:0
            sourceOrigin:MTLOriginMake(0, 0, 0) sourceSize:MTLSizeMake(W, H, 1)
            toBuffer:output destinationOffset:0 destinationBytesPerRow:OUTPUT_ROW
            destinationBytesPerImage:OUTPUT_ROW * H];
#if !TARGET_OS_IPHONE
        if (texture.storageMode == MTLStorageModeManaged)
            [blit synchronizeResource:texture];
#endif
        [blit endEncoding];
        if (!Completed(copy, "gpu-copy")) return 11;
        unsigned bad = Check(output.contents, OUTPUT_ROW, source, caMode, "gpu");
        // GPU copy has completed and was independently checked. Reuse its
        // destination for CPU readback instead of allocating a third pixel
        // buffer; expected pixels remain in the separate source buffer.
        uint8_t *direct = output.contents;
        memset(direct, 0x5a, OUTPUT_ROW * H);
        [texture getBytes:direct bytesPerRow:SOURCE_ROW
            fromRegion:MTLRegionMake2D(0, 0, W, H) mipmapLevel:0];
        bad += Check(direct, SOURCE_ROW, source, caMode, "getBytes");
        unsigned modifiedPadding = 0;
        for (unsigned y = 0; y < H; ++y)
            for (unsigned x = W * 4; x < SOURCE_ROW; ++x)
                modifiedPadding += direct[y * SOURCE_ROW + x] != 0x5a;
        printf("getBytes-modified-padding=%u\n", modifiedPadding);
        if (surface) CFRelease(surface);
        if (noCopyMode) {
            if (atomic_load(&NoCopyCallbacks)) return 18;
#if __has_feature(objc_arc)
            if (noCopyBufferView) texture = nil;
            staging = nil;
#else
            if (noCopyBufferView) [texture release];
            [staging release];
#endif
        }
        return bad || modifiedPadding ? 12 : 0;
    }
}

int main(int argc, char **argv) {
    uint8_t *mappedSource = NULL;
    size_t mappedLength = 0;
    int result = RunProbe(argc, argv, &mappedSource, &mappedLength);
    // RunProbe's autorelease pool (and completed command buffers) has drained.
    // The callback validates ownership, but only this owner unmaps its memory.
    if (mappedSource) {
        unsigned callbacks = atomic_load(&NoCopyCallbacks);
        unsigned mismatch = atomic_load(&NoCopyCallbackMismatch);
        printf("nocopy callbacks-after-drain=%u callback-mismatch=%u\n",
               callbacks, mismatch);
        if (!result && (callbacks != 1 || mismatch)) result = 19;
        munmap(mappedSource, mappedLength);
    }
    return result;
}

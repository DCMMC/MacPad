// Read-only, native-iOS Metal readback probe for a globally visible IOSurface.
//
// This is intended for diagnosing a producer's completed GPU output without
// changing the producer process.  The caller supplies the IOSurface ID and the
// Metal pixel format used to create the texture.  The probe wraps that surface
// on the native iOS Metal device, blits it to a shared linear buffer, and
// reports a digest/non-zero count.  An optional fourth argument writes the
// tightly packed readback bytes for offline inspection.

@import Foundation;
@import IOSurface;
@import Metal;

#import <errno.h>
#import <stdint.h>
#import <stdio.h>
#import <stdlib.h>
#import <string.h>

static NSUInteger bytes_per_pixel(MTLPixelFormat format) {
    switch (format) {
        case MTLPixelFormatBGRA8Unorm:
        case MTLPixelFormatBGRA8Unorm_sRGB:
        case MTLPixelFormatRGBA8Unorm:
        case MTLPixelFormatRGBA8Unorm_sRGB:
            return 4;
        case MTLPixelFormatRGBA16Float:
            return 8;
        default:
            return 0;
    }
}

static uint64_t fnv1a64(const uint8_t *bytes, size_t length,
                        size_t *nonzero_out) {
    uint64_t digest = UINT64_C(1469598103934665603);
    size_t nonzero = 0;
    for (size_t index = 0; index < length; index++) {
        uint8_t value = bytes[index];
        nonzero += value != 0;
        digest ^= value;
        digest *= UINT64_C(1099511628211);
    }
    if (nonzero_out) *nonzero_out = nonzero;
    return digest;
}

int main(int argc, char **argv) {
    @autoreleasepool {
        if (argc < 3 || argc > 4) {
            fprintf(stderr, "usage: %s IOSURFACE_ID METAL_PIXEL_FORMAT [RAW]\n",
                    argv[0]);
            return 64;
        }

        IOSurfaceID surface_id = (IOSurfaceID)strtoul(argv[1], NULL, 0);
        MTLPixelFormat format = (MTLPixelFormat)strtoul(argv[2], NULL, 0);
        NSUInteger bpp = bytes_per_pixel(format);
        if (surface_id == 0 || bpp == 0) {
            fprintf(stderr,
                    "IOSURFACE-READBACK invalid id=%u pixel-format=%lu\n",
                    surface_id, (unsigned long)format);
            return 64;
        }

        IOSurfaceRef surface = IOSurfaceLookup(surface_id);
        if (!surface) {
            fprintf(stderr, "IOSURFACE-READBACK lookup-failed id=%u\n",
                    surface_id);
            return 2;
        }

        NSUInteger width = IOSurfaceGetWidth(surface);
        NSUInteger height = IOSurfaceGetHeight(surface);
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        MTLTextureDescriptor *descriptor =
            [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:format
                                                                width:width
                                                               height:height
                                                            mipmapped:NO];
        descriptor.storageMode = MTLStorageModeShared;
        descriptor.usage = MTLTextureUsageShaderRead;
        id<MTLTexture> texture =
            [device newTextureWithDescriptor:descriptor iosurface:surface plane:0];
        if (!texture) {
            fprintf(stderr,
                    "IOSURFACE-READBACK wrap-failed id=%u size=%lux%lu "
                    "pixel-format=%lu fourcc=%u\n",
                    surface_id, (unsigned long)width, (unsigned long)height,
                    (unsigned long)format, IOSurfaceGetPixelFormat(surface));
            CFRelease(surface);
            return 3;
        }

        NSUInteger packed_row = width * bpp;
        NSUInteger gpu_row = (packed_row + 255u) & ~255u;
        NSUInteger buffer_size = gpu_row * height;
        id<MTLBuffer> buffer =
            [device newBufferWithLength:buffer_size options:MTLResourceStorageModeShared];
        id<MTLCommandQueue> queue = [device newCommandQueue];
        id<MTLCommandBuffer> command = [queue commandBuffer];
        id<MTLBlitCommandEncoder> blit = [command blitCommandEncoder];
        [blit copyFromTexture:texture
                 sourceSlice:0
                 sourceLevel:0
                sourceOrigin:MTLOriginMake(0, 0, 0)
                  sourceSize:MTLSizeMake(width, height, 1)
                    toBuffer:buffer
           destinationOffset:0
      destinationBytesPerRow:gpu_row
    destinationBytesPerImage:buffer_size];
        [blit endEncoding];
        [command commit];
        [command waitUntilCompleted];
        if (command.status != MTLCommandBufferStatusCompleted || command.error) {
            fprintf(stderr,
                    "IOSURFACE-READBACK gpu-failed id=%u status=%lu error=%s\n",
                    surface_id, (unsigned long)command.status,
                    command.error.description.UTF8String ?: "(nil)");
            CFRelease(surface);
            return 4;
        }

        const uint8_t *source = buffer.contents;
        size_t packed_size = packed_row * height;
        uint8_t *packed = malloc(packed_size);
        if (!packed) {
            fprintf(stderr, "IOSURFACE-READBACK malloc-failed bytes=%zu\n",
                    packed_size);
            CFRelease(surface);
            return 5;
        }
        for (NSUInteger row = 0; row < height; row++) {
            memcpy(packed + row * packed_row, source + row * gpu_row, packed_row);
        }
        size_t nonzero = 0;
        uint64_t digest = fnv1a64(packed, packed_size, &nonzero);
        fprintf(stderr,
                "IOSURFACE-READBACK id=%u size=%lux%lu pixel-format=%lu "
                "fourcc=%u packed-row=%lu bytes=%zu nonzero=%zu "
                "fnv1a64=%016llx\n",
                surface_id, (unsigned long)width, (unsigned long)height,
                (unsigned long)format, IOSurfaceGetPixelFormat(surface),
                (unsigned long)packed_row, packed_size, nonzero,
                (unsigned long long)digest);

        if (argc == 4) {
            FILE *output = fopen(argv[3], "wb");
            BOOL write_failed = !output;
            if (output) {
                write_failed = fwrite(packed, 1, packed_size, output) != packed_size;
                if (fclose(output) != 0) write_failed = YES;
            }
            if (write_failed) {
                fprintf(stderr,
                        "IOSURFACE-READBACK write-failed path=%s errno=%d\n",
                        argv[3], errno);
                free(packed);
                CFRelease(surface);
                return 6;
            }
        }

        free(packed);
        CFRelease(surface);
    }
    return 0;
}

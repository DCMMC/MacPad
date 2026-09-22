// Native-iOS reference for AGX kernel-command encoder record layouts.
//
// This diagnostic creates ordinary public Metal work in the iOS process
// context and copies the command buffer's populated KCMD bytes through the
// same getCurrentKernelCommandBufferStart:current:end: SPI already used by
// iosclear_ref.m.  It never changes a driver object or command byte.  Select
// the public encoder with MACWS_IOS_KCMD_MODE=blit|blittexture|mipmap|scale|compute|statistics|render|renderzero|draw|drawblit|drawblitsignal|drawblitlegacy|eventqueues|eventcycle|drawchain|aquariumchain|rawcompute|rawconcurrent|richcompute|externalcompute|iosurfaceformat
// (default: blit).

@import Foundation;
@import IOSurface;
@import Metal;
@import MetalPerformanceShaders;

#import <objc/message.h>
#import <objc/runtime.h>
#import <limits.h>
#import <signal.h>
#import <stdint.h>
#import <stdio.h>
#import <stdlib.h>
#import <string.h>
#import <unistd.h>

static uint32_t macws_u32(const unsigned char *bytes, size_t offset) {
    uint32_t value = 0;
    memcpy(&value, bytes + offset, sizeof(value));
    return value;
}

static int macws_save_file(const char *path, const void *bytes,
                           size_t length) {
    FILE *output = fopen(path, "wb");
    if (!output) return -1;
    size_t written = fwrite(bytes, 1, length, output);
    int close_status = fclose(output);
    return written == length && close_status == 0 ? 0 : -1;
}

static uintptr_t macws_strip_pointer(uintptr_t pointer) {
    return pointer & UINT64_C(0x0000ffffffffffff);
}

static void macws_dump_segment_list(id<MTLCommandBuffer> command_buffer,
                                    const char *mode) {
    // RE-confirmed via iOS 16.3
    // -[IOGPUMetalCommandBuffer fillCommandBufferArgs:commandQueue:]:
    // command-buffer +0x1f0 is IOGPUMetalCommandBufferStorage*.  The storage
    // fields below are the same read-only fields used by
    // lldb_dump_iosclear.py.  Copying them here avoids a remote-debugger hold
    // while preserving the exact bytes submitted by the native producer.
    uintptr_t object = (uintptr_t)(__bridge void *)command_buffer;
    uintptr_t storage_raw = 0;
    // The concrete iOS class stores `_storage` at +0x1f0, while the macOS
    // producer can place the inherited ivar at another offset.  Resolve the
    // real runtime ivar first so one source captures both sides of the same
    // experiment; retain +0x1f0 only as the native fallback documented above.
    Ivar storage_ivar = class_getInstanceVariable(
        object_getClass(command_buffer), "_storage");
    ptrdiff_t storage_offset = storage_ivar
        ? ivar_getOffset(storage_ivar) : 0x1f0;
    memcpy(&storage_raw, (const void *)(object + storage_offset),
           sizeof(storage_raw));
    uintptr_t storage = macws_strip_pointer(storage_raw);
    if (!storage) {
        fprintf(stderr, "IOS-AGX-SEGMENT mode=%s missing-storage\n", mode);
        return;
    }

    uintptr_t start_raw = 0, limit_raw = 0, current_raw = 0;
    int32_t segment_mode = 0;
    memcpy(&start_raw, (const void *)(storage + 0x68), sizeof(start_raw));
    memcpy(&limit_raw, (const void *)(storage + 0x70), sizeof(limit_raw));
    memcpy(&current_raw, (const void *)(storage + 0x328),
           sizeof(current_raw));
    memcpy(&segment_mode, (const void *)(storage + 0x340),
           sizeof(segment_mode));
    uintptr_t start = macws_strip_pointer(start_raw);
    uintptr_t limit = macws_strip_pointer(limit_raw);
    uintptr_t current = macws_strip_pointer(current_raw);
    size_t length = start && start <= current && current <= limit
        ? (size_t)(current - start) : 0;
    fprintf(stderr,
        "IOS-AGX-SEGMENT mode=%s storage-offset=%#tx storage=%p "
        "start=%p current=%p "
        "limit=%p length=%#zx segment-mode=%d\n",
        mode, storage_offset, (void *)storage, (void *)start, (void *)current,
        (void *)limit, length, segment_mode);
    if (!length || length > 0x10000) return;

    char path[PATH_MAX];
    snprintf(path, sizeof(path), "/tmp/ios-agx-segment-%s.bin", mode);
    int save_status = macws_save_file(path, (const void *)start, length);
    fprintf(stderr, "IOS-AGX-SEGMENT saved=%s status=%d\n",
            path, save_status);
}

static int macws_dump_kcmd(id<MTLCommandBuffer> command_buffer,
                           const char *mode) {
    // Use the C runtime entry point here.  The on-device lld arm64e image can
    // otherwise emit a pre-authenticated Objective-C constant reference that
    // iOS 16.3 rejects before NSSelectorFromString reaches this SPI (runtime
    // crash report: objc_msgSend -> NSSelectorFromString at this exact line).
    SEL selector = sel_registerName(
        "getCurrentKernelCommandBufferStart:current:end:");
    if (![command_buffer respondsToSelector:selector]) {
        fprintf(stderr, "IOS-AGX-KCMD mode=%s missing-command-buffer-SPI\n",
                mode);
        return 2;
    }

    void *start = NULL;
    void *current = NULL;
    void *end = NULL;
    IMP method = [(NSObject *)command_buffer methodForSelector:selector];
    void (*implementation)(id, SEL, void **, void **, void **) =
        (void *)method;
    implementation(command_buffer, selector, &start, &current, &end);
    size_t length = start && current > start
        ? (size_t)((uintptr_t)current - (uintptr_t)start) : 0;
    fprintf(stderr,
        "IOS-AGX-KCMD mode=%s start=%p current=%p end=%p length=%#zx\n",
        mode, start, current, end, length);
    if (!start || length < 0x18 || length > 0x10000) return 3;

    char path[PATH_MAX];
    snprintf(path, sizeof(path), "/tmp/ios-agx-kcmd-%s.bin", mode);
    if (macws_save_file(path, start, length) != 0) return 4;

    const unsigned char *bytes = start;
    size_t offset = 0;
    unsigned record = 0;
    while (offset + 0x18 <= length && record < 64) {
        uint32_t type = macws_u32(bytes, offset + 0x00);
        uint32_t span = macws_u32(bytes, offset + 0x04);
        if ((type != 3 && type != 4 && type != 5 && type != 6 &&
             type != 10) || span != 0x18) {
            break;
        }
        uint32_t event_id = macws_u32(bytes, offset + 0x08);
        uint32_t word_0c = macws_u32(bytes, offset + 0x0c);
        uint64_t value = 0;
        memcpy(&value, bytes + offset + 0x10, sizeof(value));
        fprintf(stderr,
            "IOS-AGX-KCMD event=%u offset=%#zx type=%#x span=%#x "
            "id=%#x word0c=%#x value=%llu\n",
            record, offset, type, span, event_id, word_0c,
            (unsigned long long)value);
        record++;
        offset += 0x18;
    }
    while (offset + 0x38 <= length && record < 64) {
        uint32_t type = macws_u32(bytes, offset + 0x00);
        uint32_t span = macws_u32(bytes, offset + 0x04);
        uint32_t end_offset = macws_u32(bytes, offset + 0x28);
        uint32_t size = macws_u32(bytes, offset + 0x2c);
        uint32_t inner = macws_u32(bytes, offset + 0x30);
        uint32_t subtype = macws_u32(bytes, offset + 0x34);
        fprintf(stderr,
            "IOS-AGX-KCMD record=%u offset=%#zx type=%#x span=%#x "
            "end=%#x size=%#x inner=%#x subtype=%u\n",
            record, offset, type, span, end_offset, size, inner, subtype);
        record++;
        if ((type != 0x10000 && type != 0x10001) ||
            span < 0x38 || span > length - offset) break;
        offset += span;
    }
    fprintf(stderr, "IOS-AGX-KCMD saved=%s records=%u chain-end=%#zx\n",
            path, record, offset);
    macws_dump_segment_list(command_buffer, mode);
    return 0;
}

static id<MTLCommandBuffer> macws_new_command_buffer(
    id<MTLCommandQueue> queue) {
    MTLCommandBufferDescriptor *descriptor =
        [MTLCommandBufferDescriptor new];
    descriptor.retainedReferences = YES;
    descriptor.errorOptions = MTLCommandBufferErrorOptionEncoderExecutionStatus;
    return [queue commandBufferWithDescriptor:descriptor];
}

static void macws_commit_deferred_encoder(
    id<MTLCommandBuffer> command_buffer) {
    SEL selector = sel_registerName("commitEncoder");
    if (![(NSObject *)command_buffer respondsToSelector:selector]) return;
    IMP method = [(NSObject *)command_buffer methodForSelector:selector];
    ((void (*)(id, SEL))method)(command_buffer, selector);
}

static int macws_encode_blit(id<MTLDevice> device,
                             id<MTLCommandQueue> queue) {
    id<MTLBuffer> source = [device newBufferWithLength:4096
                                              options:MTLResourceStorageModeShared];
    id<MTLBuffer> destination = [device newBufferWithLength:4096
                                                   options:MTLResourceStorageModeShared];
    if (!source || !destination) return 10;
    memset(source.contents, 0x5a, source.length);

    id<MTLCommandBuffer> command_buffer = macws_new_command_buffer(queue);
    id<MTLBlitCommandEncoder> encoder =
        [command_buffer blitCommandEncoder];
    [encoder copyFromBuffer:source sourceOffset:0
                   toBuffer:destination destinationOffset:0 size:4096];
    [encoder endEncoding];

    int dump_status = macws_dump_kcmd(command_buffer, "blit");
    if (getenv("MACWS_IOS_KCMD_HOLD")) raise(SIGSTOP);
    [command_buffer commit];
    [command_buffer waitUntilCompleted];
    fprintf(stderr, "IOS-AGX-KCMD mode=blit status=%ld error=%s "
                    "first=%#x dump=%d\n",
        (long)command_buffer.status,
        command_buffer.error.description.UTF8String ?: "nil",
        ((const unsigned char *)destination.contents)[0], dump_status);
    return dump_status || command_buffer.error ? 11 : 0;
}

static int macws_encode_blit_texture(id<MTLDevice> device,
                                     id<MTLCommandQueue> queue) {
    MTLTextureDescriptor *descriptor =
        [MTLTextureDescriptor
            texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm
            width:64 height:64 mipmapped:NO];
    descriptor.storageMode = MTLStorageModeShared;
    descriptor.usage = MTLTextureUsageShaderRead | MTLTextureUsageShaderWrite |
        MTLTextureUsageRenderTarget;
    id<MTLTexture> source = [device newTextureWithDescriptor:descriptor];
    id<MTLTexture> destination = [device newTextureWithDescriptor:descriptor];
    if (!source || !destination) return 12;

    id<MTLCommandBuffer> command_buffer = macws_new_command_buffer(queue);
    id<MTLBlitCommandEncoder> encoder =
        [command_buffer blitCommandEncoder];
    [encoder copyFromTexture:source sourceSlice:0 sourceLevel:0
                sourceOrigin:MTLOriginMake(0, 0, 0)
                  sourceSize:MTLSizeMake(64, 64, 1)
                   toTexture:destination destinationSlice:0
            destinationLevel:0 destinationOrigin:MTLOriginMake(0, 0, 0)];
    [encoder endEncoding];
    // Texture blits are coalesced by AGX until the command buffer commits its
    // deferred encoder.  Flush only that user-space encoder so the native
    // kernel-command record can be captured before IOConnect submission.
    macws_commit_deferred_encoder(command_buffer);

    int dump_status = macws_dump_kcmd(command_buffer, "blittexture");
    if (getenv("MACWS_IOS_KCMD_HOLD")) raise(SIGSTOP);
    [command_buffer commit];
    [command_buffer waitUntilCompleted];
    fprintf(stderr,
        "IOS-AGX-KCMD mode=blittexture status=%ld error=%s dump=%d\n",
        (long)command_buffer.status,
        command_buffer.error.description.UTF8String ?: "nil", dump_status);
    return dump_status || command_buffer.error ? 13 : 0;
}

static int macws_encode_mipmap(id<MTLDevice> device,
                               id<MTLCommandQueue> queue) {
    MTLTextureDescriptor *descriptor =
        [MTLTextureDescriptor
            texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm
            width:256 height:256 mipmapped:YES];
    descriptor.storageMode = MTLStorageModeShared;
    descriptor.usage = MTLTextureUsageShaderRead |
        MTLTextureUsageShaderWrite | MTLTextureUsageRenderTarget;
    id<MTLTexture> texture = [device newTextureWithDescriptor:descriptor];
    if (!texture) return 14;

    // Seed level zero so the driver must produce the complete downsample
    // chain.  AGX's texture-blit implementation may select the Fast2D
    // renderTexture path whose native record is the subtype-2 ABI reference
    // needed by the macOS-to-iOS translator.
    size_t row_bytes = 256 * 4;
    unsigned char *pixels = calloc(256, row_bytes);
    if (!pixels) return 15;
    for (size_t y = 0; y < 256; y++) {
        for (size_t x = 0; x < 256; x++) {
            size_t offset = y * row_bytes + x * 4;
            pixels[offset + 0] = (unsigned char)x;
            pixels[offset + 1] = (unsigned char)y;
            pixels[offset + 2] = (unsigned char)(x ^ y);
            pixels[offset + 3] = 0xff;
        }
    }
    [texture replaceRegion:MTLRegionMake2D(0, 0, 256, 256)
                mipmapLevel:0 withBytes:pixels bytesPerRow:row_bytes];
    free(pixels);

    id<MTLCommandBuffer> command_buffer = macws_new_command_buffer(queue);
    id<MTLBlitCommandEncoder> encoder =
        [command_buffer blitCommandEncoder];
    [encoder generateMipmapsForTexture:texture];
    [encoder endEncoding];
    macws_commit_deferred_encoder(command_buffer);

    int dump_status = macws_dump_kcmd(command_buffer, "mipmap");
    if (getenv("MACWS_IOS_KCMD_HOLD")) raise(SIGSTOP);
    [command_buffer commit];
    [command_buffer waitUntilCompleted];
    fprintf(stderr,
        "IOS-AGX-KCMD mode=mipmap status=%ld error=%s dump=%d\n",
        (long)command_buffer.status,
        command_buffer.error.description.UTF8String ?: "nil", dump_status);
    return dump_status || command_buffer.error ? 16 : 0;
}

static int macws_encode_compute(id<MTLDevice> device,
                                id<MTLCommandQueue> queue) {
    MTLTextureDescriptor *descriptor =
        [MTLTextureDescriptor
            texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm
            width:64 height:64 mipmapped:NO];
    descriptor.storageMode = MTLStorageModeShared;
    descriptor.usage = MTLTextureUsageShaderRead | MTLTextureUsageShaderWrite;
    id<MTLTexture> source = [device newTextureWithDescriptor:descriptor];
    id<MTLTexture> destination = [device newTextureWithDescriptor:descriptor];
    if (!source || !destination) return 20;

    MPSImageGaussianBlur *blur =
        [[MPSImageGaussianBlur alloc] initWithDevice:device sigma:2.0f];
    id<MTLCommandBuffer> command_buffer = macws_new_command_buffer(queue);
    [blur encodeToCommandBuffer:command_buffer
                  sourceTexture:source
             destinationTexture:destination];

    // MPS defers its compute encoder until commit.  A pre-commit snapshot is
    // therefore empty on both the native iOS driver and the macOS producer
    // (`start == current`).  Commit first, then copy the still-live finalized
    // command storage before waiting for completion.  This is read-only and
    // matches the already proven statistics probe ordering below.
    [command_buffer commit];
    int dump_status = macws_dump_kcmd(command_buffer, "compute");
    if (getenv("MACWS_IOS_KCMD_HOLD")) raise(SIGSTOP);
    [command_buffer waitUntilCompleted];
    fprintf(stderr, "IOS-AGX-KCMD mode=compute status=%ld error=%s dump=%d\n",
        (long)command_buffer.status,
        command_buffer.error.description.UTF8String ?: "nil", dump_status);
    return dump_status || command_buffer.error ? 21 : 0;
}

// Native-iOS control for the exact two-pass MPS reduction selected by the
// macOS transition compositor.  Runtime pipeline traces from WindowServer
// name the two functions sum_rgba_columns and sum_rgba_rows.  The public
// MPSImageStatisticsMean API performs that RGBA reduction without mutating
// any private driver state, so its successful iOS command stream is the
// authoritative ABI reference for the failing macOS-produced submission.
static int macws_encode_statistics(id<MTLDevice> device,
                                   id<MTLCommandQueue> queue) {
    MTLTextureDescriptor *source_descriptor =
        [MTLTextureDescriptor
            texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm
            width:1366 height:1024 mipmapped:NO];
    source_descriptor.storageMode = MTLStorageModeShared;
    source_descriptor.usage = MTLTextureUsageShaderRead |
        MTLTextureUsageShaderWrite;
    MTLTextureDescriptor *destination_descriptor =
        [MTLTextureDescriptor
            texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float
            width:1 height:1 mipmapped:NO];
    destination_descriptor.storageMode = MTLStorageModeShared;
    destination_descriptor.usage = MTLTextureUsageShaderRead |
        MTLTextureUsageShaderWrite;
    id<MTLTexture> source =
        [device newTextureWithDescriptor:source_descriptor];
    id<MTLTexture> destination =
        [device newTextureWithDescriptor:destination_descriptor];
    MPSImageStatisticsMean *statistics =
        [[MPSImageStatisticsMean alloc] initWithDevice:device];
    if (!source || !destination || !statistics) return 28;

    id<MTLCommandBuffer> command_buffer = macws_new_command_buffer(queue);
    [statistics encodeToCommandBuffer:command_buffer
                         sourceTexture:source
                    destinationTexture:destination];
    macws_commit_deferred_encoder(command_buffer);
    // -commit finalizes state+0x328 (the segment-list logical end).  The
    // command-buffer object and its storage remain valid until completion,
    // so dump immediately after the successful native submission instead of
    // observing the intentionally empty pre-finalization list.
    [command_buffer commit];
    int dump_status = macws_dump_kcmd(command_buffer, "statistics");
    if (getenv("MACWS_IOS_KCMD_HOLD")) raise(SIGSTOP);
    [command_buffer waitUntilCompleted];
    fprintf(stderr,
        "IOS-AGX-KCMD mode=statistics status=%ld error=%s dump=%d\n",
        (long)command_buffer.status,
        command_buffer.error.description.UTF8String ?: "nil", dump_status);
    return dump_status || command_buffer.error ? 29 : 0;
}

static int macws_encode_scale(id<MTLDevice> device,
                              id<MTLCommandQueue> queue) {
    MTLTextureDescriptor *source_descriptor =
        [MTLTextureDescriptor
            texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm
            width:64 height:64 mipmapped:NO];
    source_descriptor.storageMode = MTLStorageModeShared;
    source_descriptor.usage = MTLTextureUsageShaderRead |
        MTLTextureUsageShaderWrite;
    MTLTextureDescriptor *destination_descriptor =
        [MTLTextureDescriptor
            texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm
            width:96 height:80 mipmapped:NO];
    destination_descriptor.storageMode = MTLStorageModeShared;
    destination_descriptor.usage = MTLTextureUsageShaderRead |
        MTLTextureUsageShaderWrite | MTLTextureUsageRenderTarget;
    id<MTLTexture> source = [device newTextureWithDescriptor:source_descriptor];
    id<MTLTexture> destination =
        [device newTextureWithDescriptor:destination_descriptor];
    MPSImageLanczosScale *scale =
        [[MPSImageLanczosScale alloc] initWithDevice:device];
    if (!source || !destination || !scale) return 22;

    id<MTLCommandBuffer> command_buffer = macws_new_command_buffer(queue);
    [scale encodeToCommandBuffer:command_buffer
                   sourceTexture:source
              destinationTexture:destination];
    macws_commit_deferred_encoder(command_buffer);
    int dump_status = macws_dump_kcmd(command_buffer, "scale");
    if (getenv("MACWS_IOS_KCMD_HOLD")) raise(SIGSTOP);
    [command_buffer commit];
    [command_buffer waitUntilCompleted];
    fprintf(stderr, "IOS-AGX-KCMD mode=scale status=%ld error=%s dump=%d\n",
        (long)command_buffer.status,
        command_buffer.error.description.UTF8String ?: "nil", dump_status);
    return dump_status || command_buffer.error ? 23 : 0;
}

// Native-iOS structural control for the first fully decoded VS Code
// Aquarium failure after the segment-list parser was raised to 256 records.
// That runtime-correlated submit is a direct count-37 list containing twelve
// repetitions of {subtype-3 mode 2, subtype-2, subtype-2}, followed by one
// subtype-3 mode-1 texture blit.  Reproduce the same public-operation chain in
// a single native command buffer.  This probe remains read-only with respect
// to driver storage; LLDB captures the real selector-0x1a payload at commit.
static int macws_encode_aquarium_chain(id<MTLDevice> device,
                                       id<MTLCommandQueue> queue) {
    const NSUInteger chain_count = 12;
    MTLTextureDescriptor *source_descriptor =
        [MTLTextureDescriptor
            texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm
            width:128 height:128 mipmapped:NO];
    source_descriptor.storageMode = MTLStorageModeShared;
    source_descriptor.usage = MTLTextureUsageShaderRead |
        MTLTextureUsageShaderWrite;

    MTLTextureDescriptor *destination_descriptor =
        [MTLTextureDescriptor
            texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm
            width:256 height:256 mipmapped:YES];
    destination_descriptor.storageMode = MTLStorageModeShared;
    destination_descriptor.usage = MTLTextureUsageShaderRead |
        MTLTextureUsageShaderWrite | MTLTextureUsageRenderTarget;

    MTLTextureDescriptor *copy_descriptor =
        [MTLTextureDescriptor
            texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm
            width:256 height:256 mipmapped:NO];
    copy_descriptor.storageMode = MTLStorageModeShared;
    copy_descriptor.usage = MTLTextureUsageShaderRead |
        MTLTextureUsageShaderWrite | MTLTextureUsageRenderTarget;

    MPSImageLanczosScale *scale =
        [[MPSImageLanczosScale alloc] initWithDevice:device];
    id<MTLCommandBuffer> command_buffer = macws_new_command_buffer(queue);
    NSMutableArray *sources = [NSMutableArray arrayWithCapacity:chain_count];
    NSMutableArray *destinations =
        [NSMutableArray arrayWithCapacity:chain_count];
    if (!scale || !command_buffer) return 45;

    for (NSUInteger i = 0; i < chain_count; i++) {
        id<MTLTexture> source =
            [device newTextureWithDescriptor:source_descriptor];
        id<MTLTexture> destination =
            [device newTextureWithDescriptor:destination_descriptor];
        if (!source || !destination) return 46;
        [sources addObject:source];
        [destinations addObject:destination];

        [scale encodeToCommandBuffer:command_buffer
                       sourceTexture:source
                  destinationTexture:destination];
        id<MTLBlitCommandEncoder> mipmap =
            [command_buffer blitCommandEncoder];
        [mipmap generateMipmapsForTexture:destination];
        [mipmap endEncoding];
    }

    id<MTLTexture> copy_destination =
        [device newTextureWithDescriptor:copy_descriptor];
    if (!copy_destination) return 47;
    id<MTLBlitCommandEncoder> copy = [command_buffer blitCommandEncoder];
    [copy copyFromTexture:destinations[0]
              sourceSlice:0 sourceLevel:0
             sourceOrigin:MTLOriginMake(0, 0, 0)
               sourceSize:MTLSizeMake(256, 256, 1)
                toTexture:copy_destination destinationSlice:0
         destinationLevel:0 destinationOrigin:MTLOriginMake(0, 0, 0)];
    [copy endEncoding];
    macws_commit_deferred_encoder(command_buffer);

    int dump_status = macws_dump_kcmd(command_buffer, "aquariumchain");
    if (getenv("MACWS_IOS_KCMD_HOLD")) raise(SIGSTOP);
    [command_buffer commit];
    [command_buffer waitUntilCompleted];
    fprintf(stderr,
        "IOS-AGX-KCMD mode=aquariumchain groups=%lu status=%ld error=%s "
        "dump=%d\n",
        (unsigned long)chain_count, (long)command_buffer.status,
        command_buffer.error.description.UTF8String ?: "nil", dump_status);
    return dump_status || command_buffer.error ? 48 : 0;
}

static int macws_encode_chain(id<MTLDevice> device,
                              id<MTLCommandQueue> queue) {
    MTLTextureDescriptor *descriptor =
        [MTLTextureDescriptor
            texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm
            width:64 height:64 mipmapped:NO];
    descriptor.storageMode = MTLStorageModeShared;
    descriptor.usage = MTLTextureUsageShaderRead |
        MTLTextureUsageShaderWrite | MTLTextureUsageRenderTarget;
    id<MTLTexture> source = [device newTextureWithDescriptor:descriptor];
    id<MTLTexture> middle = [device newTextureWithDescriptor:descriptor];
    id<MTLTexture> destination = [device newTextureWithDescriptor:descriptor];
    id<MTLBuffer> input = [device newBufferWithLength:4096
                                             options:MTLResourceStorageModeShared];
    id<MTLBuffer> output = [device newBufferWithLength:4096
                                              options:MTLResourceStorageModeShared];
    MPSImageGaussianBlur *blur =
        [[MPSImageGaussianBlur alloc] initWithDevice:device sigma:2.0f];
    if (!source || !middle || !destination || !input || !output || !blur)
        return 24;

    id<MTLCommandBuffer> command_buffer = macws_new_command_buffer(queue);
    id<MTLBlitCommandEncoder> buffer_blit =
        [command_buffer blitCommandEncoder];
    [buffer_blit copyFromBuffer:input sourceOffset:0
                       toBuffer:output destinationOffset:0 size:4096];
    [buffer_blit endEncoding];

    [blur encodeToCommandBuffer:command_buffer
                  sourceTexture:source
             destinationTexture:middle];

    id<MTLBlitCommandEncoder> texture_blit =
        [command_buffer blitCommandEncoder];
    [texture_blit copyFromTexture:middle sourceSlice:0 sourceLevel:0
                     sourceOrigin:MTLOriginMake(0, 0, 0)
                       sourceSize:MTLSizeMake(64, 64, 1)
                        toTexture:destination destinationSlice:0
                 destinationLevel:0 destinationOrigin:MTLOriginMake(0, 0, 0)];
    [texture_blit endEncoding];
    macws_commit_deferred_encoder(command_buffer);

    int dump_status = macws_dump_kcmd(command_buffer, "chain");
    if (getenv("MACWS_IOS_KCMD_HOLD")) raise(SIGSTOP);
    [command_buffer commit];
    [command_buffer waitUntilCompleted];
    fprintf(stderr, "IOS-AGX-KCMD mode=chain status=%ld error=%s dump=%d\n",
        (long)command_buffer.status,
        command_buffer.error.description.UTF8String ?: "nil", dump_status);
    return dump_status || command_buffer.error ? 25 : 0;
}

static int macws_encode_raw_compute(id<MTLDevice> device,
                                    id<MTLCommandQueue> queue,
                                    BOOL concurrent) {
    const char *mode = concurrent ? "rawconcurrent" : "rawcompute";
    const char *source_utf8 =
        "#include <metal_stdlib>\n"
        "using namespace metal;\n"
        "kernel void macws_k(device uint *values [[buffer(0)]], "
        "uint i [[thread_position_in_grid]]) { values[i] += 1; }\n";
    NSString *source = [NSString stringWithUTF8String:source_utf8];
    NSError *error = nil;
    id<MTLLibrary> library = [device newLibraryWithSource:source
                                                 options:nil
                                                   error:&error];
    id<MTLFunction> function = [library newFunctionWithName:
        [NSString stringWithUTF8String:"macws_k"]];
    id<MTLComputePipelineState> pipeline = function
        ? [device newComputePipelineStateWithFunction:function error:&error]
        : nil;
    id<MTLBuffer> buffer = [device newBufferWithLength:4096
                                               options:MTLResourceStorageModeShared];
    fprintf(stderr,
        "IOS-AGX-KCMD %s library=%p function=%p pipeline=%p "
        "buffer=%p error=%s\n",
        mode,
        (__bridge void *)library, (__bridge void *)function,
        (__bridge void *)pipeline, (__bridge void *)buffer,
        error.description.UTF8String ?: "nil");
    if (!pipeline || !buffer) return 26;

    id<MTLCommandBuffer> command_buffer = macws_new_command_buffer(queue);
    id<MTLComputeCommandEncoder> encoder = concurrent
        ? [command_buffer
            computeCommandEncoderWithDispatchType:MTLDispatchTypeConcurrent]
        : [command_buffer computeCommandEncoder];
    [encoder setComputePipelineState:pipeline];
    [encoder setBuffer:buffer offset:0 atIndex:0];
    [encoder dispatchThreads:MTLSizeMake(64, 1, 1)
        threadsPerThreadgroup:MTLSizeMake(32, 1, 1)];
    [encoder endEncoding];
    macws_commit_deferred_encoder(command_buffer);

    [command_buffer commit];
    int dump_status = macws_dump_kcmd(command_buffer, mode);
    if (getenv("MACWS_IOS_KCMD_HOLD")) raise(SIGSTOP);
    [command_buffer waitUntilCompleted];
    fprintf(stderr,
        "IOS-AGX-KCMD mode=%s status=%ld error=%s dump=%d\n",
        mode,
        (long)command_buffer.status,
        command_buffer.error.description.UTF8String ?: "nil", dump_status);
    return dump_status || command_buffer.error ? 27 : 0;
}

// Native-iOS control for the resource shape of Stray's first failing UE4
// skinning dispatch.  The game shader has a 28-byte constant buffer at index
// 8, two writable texture buffers, and six readable texture buffers.  The
// shader below deliberately reproduces only that public Metal argument shape;
// it does not reproduce or consume the proprietary game shader.  A successful
// submission gives us an authoritative iOS-side subtype-3 command layout with
// a comparable resource trailer.
static int macws_encode_rich_compute(id<MTLDevice> device,
                                     id<MTLCommandQueue> queue) {
    const char *source_utf8 =
        "#include <metal_stdlib>\n"
        "using namespace metal;\n"
        "kernel void macws_rich(constant uint *globals [[buffer(8)]], "
        "texture_buffer<float, access::write> out0 [[texture(0)]], "
        "texture_buffer<float, access::write> out1 [[texture(1)]], "
        "texture_buffer<float, access::read> in2 [[texture(2)]], "
        "texture_buffer<float, access::read> in3 [[texture(3)]], "
        "texture_buffer<float, access::read> in4 [[texture(4)]], "
        "texture_buffer<uint, access::read> in5 [[texture(5)]], "
        "texture_buffer<float, access::read> in6 [[texture(6)]], "
        "texture_buffer<float, access::read> in7 [[texture(7)]], "
        "uint3 gid [[thread_position_in_grid]]) { "
        "uint i = gid.x & 63; "
        "float4 value = in2.read(i) + in3.read(i) + in4.read(i) + "
        "float4(in5.read(i)) + in6.read(i) + in7.read(i) + "
        "float4(globals[0]); "
        "out0.write(value, i); out1.write(value, i); }\n";
    NSError *error = nil;
    id<MTLLibrary> library = [device
        newLibraryWithSource:[NSString stringWithUTF8String:source_utf8]
        options:nil error:&error];
    id<MTLFunction> function = [library newFunctionWithName:@"macws_rich"];
    id<MTLComputePipelineState> pipeline = function
        ? [device newComputePipelineStateWithFunction:function error:&error]
        : nil;

    id<MTLBuffer> globals = [device newBufferWithLength:28
                                                options:MTLResourceStorageModeShared];
    NSMutableArray<id<MTLTexture>> *textures = [NSMutableArray array];
    for (NSUInteger index = 0; index < 8; index++) {
        MTLPixelFormat format = index == 5
            ? MTLPixelFormatRGBA32Uint : MTLPixelFormatRGBA32Float;
        MTLTextureUsage usage = index < 2
            ? MTLTextureUsageShaderWrite : MTLTextureUsageShaderRead;
        MTLTextureDescriptor *descriptor =
            [MTLTextureDescriptor textureBufferDescriptorWithPixelFormat:format
                width:64 resourceOptions:MTLResourceStorageModeShared
                usage:usage];
        id<MTLTexture> texture = [device newTextureWithDescriptor:descriptor];
        if (!texture) break;
        [textures addObject:texture];
    }
    fprintf(stderr,
        "IOS-AGX-KCMD richcompute library=%p function=%p pipeline=%p "
        "globals=%p textures=%lu error=%s\n",
        (__bridge void *)library, (__bridge void *)function,
        (__bridge void *)pipeline, (__bridge void *)globals,
        (unsigned long)textures.count,
        error.description.UTF8String ?: "nil");
    if (!pipeline || !globals || textures.count != 8) return 69;

    id<MTLCommandBuffer> command_buffer = macws_new_command_buffer(queue);
    id<MTLComputeCommandEncoder> encoder =
        [command_buffer computeCommandEncoder];
    [encoder setComputePipelineState:pipeline];
    [encoder setBuffer:globals offset:0 atIndex:8];
    for (NSUInteger index = 0; index < textures.count; index++)
        [encoder setTexture:textures[index] atIndex:index];
    [encoder dispatchThreads:MTLSizeMake(64, 1, 1)
        threadsPerThreadgroup:MTLSizeMake(32, 1, 1)];
    [encoder endEncoding];
    macws_commit_deferred_encoder(command_buffer);

    int dump_status = macws_dump_kcmd(command_buffer, "richcompute");
    if (getenv("MACWS_IOS_KCMD_HOLD")) raise(SIGSTOP);
    [command_buffer commit];
    int postcommit_dump_status =
        macws_dump_kcmd(command_buffer, "richcompute-postcommit");
    [command_buffer waitUntilCompleted];
    fprintf(stderr,
        "IOS-AGX-KCMD mode=richcompute status=%ld error=%s "
        "dump=%d postcommit-dump=%d\n",
        (long)command_buffer.status,
        command_buffer.error.description.UTF8String ?: "nil", dump_status,
        postcommit_dump_status);
    return dump_status || postcommit_dump_status || command_buffer.error
        ? 70 : 0;
}

static int macws_encode_parallel_render(id<MTLDevice> device,
                                        id<MTLCommandQueue> queue) {
    MTLTextureDescriptor *descriptor =
        [MTLTextureDescriptor
            texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm
            width:64 height:64 mipmapped:NO];
    descriptor.storageMode = MTLStorageModeShared;
    descriptor.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
    id<MTLTexture> target = [device newTextureWithDescriptor:descriptor];
    if (!target) return 32;

    id<MTLCommandBuffer> command_buffer = macws_new_command_buffer(queue);
    MTLRenderPassDescriptor *pass =
        [MTLRenderPassDescriptor renderPassDescriptor];
    pass.colorAttachments[0].texture = target;
    pass.colorAttachments[0].loadAction = MTLLoadActionClear;
    pass.colorAttachments[0].storeAction = MTLStoreActionStore;
    pass.colorAttachments[0].clearColor =
        MTLClearColorMake(0.25, 0.5, 0.125, 1.0);
    id<MTLParallelRenderCommandEncoder> parallel =
        [command_buffer parallelRenderCommandEncoderWithDescriptor:pass];
    id<MTLRenderCommandEncoder> child = [parallel renderCommandEncoder];
    [child endEncoding];
    [parallel endEncoding];

    int dump_status = macws_dump_kcmd(command_buffer, "parallel");
    if (getenv("MACWS_IOS_KCMD_HOLD")) raise(SIGSTOP);
    [command_buffer commit];
    [command_buffer waitUntilCompleted];
    fprintf(stderr,
        "IOS-AGX-KCMD mode=parallel status=%ld error=%s dump=%d\n",
        (long)command_buffer.status,
        command_buffer.error.description.UTF8String ?: "nil", dump_status);
    return dump_status || command_buffer.error ? 33 : 0;
}

static int macws_encode_render(id<MTLDevice> device,
                               id<MTLCommandQueue> queue,
                               BOOL zero_clear) {
    MTLTextureDescriptor *descriptor =
        [MTLTextureDescriptor
            texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm
            width:64 height:64 mipmapped:NO];
    descriptor.storageMode = MTLStorageModeShared;
    descriptor.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
    id<MTLTexture> target = [device newTextureWithDescriptor:descriptor];
    if (!target) return 30;

    id<MTLCommandBuffer> command_buffer = macws_new_command_buffer(queue);
    MTLRenderPassDescriptor *pass =
        [MTLRenderPassDescriptor renderPassDescriptor];
    pass.colorAttachments[0].texture = target;
    pass.colorAttachments[0].loadAction = MTLLoadActionClear;
    pass.colorAttachments[0].storeAction = MTLStoreActionStore;
    // `renderzero` is a native-iOS control for macOS command records whose
    // clear-value field is zero.  The chroot KCMD translator must distinguish
    // semantic clear data from the two producer-version padding windows; this
    // public-Metal control lets us inspect the authoritative iOS layout rather
    // than broadening a structural predicate from a guess.
    pass.colorAttachments[0].clearColor = zero_clear
        ? MTLClearColorMake(0.0, 0.0, 0.0, 0.0)
        : MTLClearColorMake(0.125, 0.25, 0.5, 1.0);
    id<MTLRenderCommandEncoder> encoder =
        [command_buffer renderCommandEncoderWithDescriptor:pass];
    [encoder endEncoding];
    // Render encoders can remain deferred until commit.  Flush the user-space
    // encoder so the pre-submit dump contains the same finalized KCMD bytes
    // that the native driver will submit; this does not submit or edit them.
    macws_commit_deferred_encoder(command_buffer);

    const char *mode = zero_clear ? "renderzero" : "render";
    int dump_status = macws_dump_kcmd(command_buffer, mode);
    if (getenv("MACWS_IOS_KCMD_HOLD")) raise(SIGSTOP);
    [command_buffer commit];
    [command_buffer waitUntilCompleted];
    fprintf(stderr, "IOS-AGX-KCMD mode=%s status=%ld error=%s dump=%d\n",
        mode, (long)command_buffer.status,
        command_buffer.error.description.UTF8String ?: "nil", dump_status);
    return dump_status || command_buffer.error ? 31 : 0;
}

static int macws_encode_draw(id<MTLDevice> device,
                             id<MTLCommandQueue> queue) {
    MTLTextureDescriptor *texture_descriptor =
        [MTLTextureDescriptor
            texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
            width:64 height:64 mipmapped:NO];
    texture_descriptor.storageMode = MTLStorageModeShared;
    texture_descriptor.usage = MTLTextureUsageRenderTarget |
        MTLTextureUsageShaderRead;
    id<MTLTexture> source =
        [device newTextureWithDescriptor:texture_descriptor];
    id<MTLTexture> target =
        [device newTextureWithDescriptor:texture_descriptor];

    NSError *error = nil;
    // Keep Objective-C string constants out of this arm64e diagnostic image.
    // On iOS 16.3 the on-device lld emits those constants with an auth bit
    // which Foundation later rejects (runtime-confirmed by the draw probe
    // crash at -[NSURL initFileURLWithPath:]).  Runtime-created NSStrings do
    // not carry that malformed pre-authenticated pointer.
    NSString *library_path = [NSString stringWithUTF8String:
        "/System/Library/Frameworks/QuartzCore.framework/default.metallib"];
    NSURL *library_url = [NSURL fileURLWithPath:library_path];
    id<MTLLibrary> library =
        [device newLibraryWithURL:library_url error:&error];
    id<MTLFunction> vertex =
        [library newFunctionWithName:
            [NSString stringWithUTF8String:"read_surf_vert"]];
    id<MTLFunction> fragment =
        [library newFunctionWithName:
            [NSString stringWithUTF8String:"read_surf_frag"]];
    MTLRenderPipelineDescriptor *pipeline_descriptor =
        [MTLRenderPipelineDescriptor new];
    pipeline_descriptor.vertexFunction = vertex;
    pipeline_descriptor.fragmentFunction = fragment;
    pipeline_descriptor.colorAttachments[0].pixelFormat =
        MTLPixelFormatBGRA8Unorm;
    id<MTLRenderPipelineState> pipeline =
        [device newRenderPipelineStateWithDescriptor:pipeline_descriptor
                                                error:&error];
    fprintf(stderr,
        "IOS-AGX-KCMD draw source=%p target=%p library=%p vertex=%p "
        "fragment=%p pipeline=%p error=%s\n",
        (__bridge void *)source, (__bridge void *)target,
        (__bridge void *)library, (__bridge void *)vertex,
        (__bridge void *)fragment, (__bridge void *)pipeline,
        error.description.UTF8String ?: "nil");
    if (!source || !target || !pipeline) return 40;

    id<MTLCommandBuffer> command_buffer = macws_new_command_buffer(queue);
    MTLRenderPassDescriptor *pass =
        [MTLRenderPassDescriptor renderPassDescriptor];
    pass.colorAttachments[0].texture = target;
    pass.colorAttachments[0].loadAction = MTLLoadActionDontCare;
    pass.colorAttachments[0].storeAction = MTLStoreActionStore;
    id<MTLRenderCommandEncoder> encoder =
        [command_buffer renderCommandEncoderWithDescriptor:pass];
    [encoder setRenderPipelineState:pipeline];
    [encoder setFragmentTexture:source atIndex:0];
    [encoder drawPrimitives:MTLPrimitiveTypeTriangleStrip
                vertexStart:0 vertexCount:4];
    [encoder endEncoding];

    int dump_status = macws_dump_kcmd(command_buffer, "draw");
    if (getenv("MACWS_IOS_KCMD_HOLD")) raise(SIGSTOP);
    [command_buffer commit];
    [command_buffer waitUntilCompleted];
    fprintf(stderr, "IOS-AGX-KCMD mode=draw status=%ld error=%s dump=%d\n",
        (long)command_buffer.status,
        command_buffer.error.description.UTF8String ?: "nil", dump_status);
    return dump_status || command_buffer.error ? 41 : 0;
}

// Native-iOS structural control for the video-compositor submission captured
// from VS Code on 2026-08-01.  That macOS producer emitted one subtype-1 draw,
// a type-5 signal record, one subtype-3 mode-1 texture blit, then another
// type-5 signal record.  Encode the same public draw -> texture-copy sequence
// in one native command buffer.  The probe copies, but never edits, the KCMD;
// the selector-0x1a segment list is captured independently with the project
// LLDB just as for drawchain/aquariumchain.
static int macws_encode_draw_blit(id<MTLDevice> device,
                                  id<MTLCommandQueue> queue,
                                  unsigned signal_kind) {
    MTLTextureDescriptor *texture_descriptor =
        [MTLTextureDescriptor
            texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
            width:96 height:64 mipmapped:NO];
    texture_descriptor.storageMode = MTLStorageModeShared;
    texture_descriptor.usage = MTLTextureUsageRenderTarget |
        MTLTextureUsageShaderRead | MTLTextureUsageShaderWrite;
    id<MTLTexture> source =
        [device newTextureWithDescriptor:texture_descriptor];
    id<MTLTexture> rendered =
        [device newTextureWithDescriptor:texture_descriptor];
    id<MTLTexture> destination =
        [device newTextureWithDescriptor:texture_descriptor];

    NSError *error = nil;
    NSString *library_path = [NSString stringWithUTF8String:
        "/System/Library/Frameworks/QuartzCore.framework/default.metallib"];
    NSURL *library_url = [NSURL fileURLWithPath:library_path];
    id<MTLLibrary> library =
        [device newLibraryWithURL:library_url error:&error];
    id<MTLFunction> vertex =
        [library newFunctionWithName:
            [NSString stringWithUTF8String:"read_surf_vert"]];
    id<MTLFunction> fragment =
        [library newFunctionWithName:
            [NSString stringWithUTF8String:"read_surf_frag"]];
    MTLRenderPipelineDescriptor *pipeline_descriptor =
        [MTLRenderPipelineDescriptor new];
    pipeline_descriptor.vertexFunction = vertex;
    pipeline_descriptor.fragmentFunction = fragment;
    pipeline_descriptor.colorAttachments[0].pixelFormat =
        MTLPixelFormatBGRA8Unorm;
    id<MTLRenderPipelineState> pipeline =
        [device newRenderPipelineStateWithDescriptor:pipeline_descriptor
                                                error:&error];
    // Both event families are public Metal APIs but exercise distinct IOGPU
    // producers on iOS 16.3: _MTLSharedEvent emits signal type 3, while the
    // legacy IOGPUMTLEvent returned by -newEvent emits type 5.  Capturing both
    // controls lets us compare the chroot's macOS type-5 stream with the exact
    // native producer instead of inferring an ABI conversion from one family.
    id<MTLEvent> signal_event = signal_kind == 1
        ? (id<MTLEvent>)[device newSharedEvent]
        : (signal_kind == 2 ? [device newEvent] : nil);
    if (!source || !rendered || !destination || !pipeline ||
        (signal_kind != 0 && !signal_event)) {
        fprintf(stderr,
            "IOS-AGX-KCMD drawblit setup source=%p rendered=%p "
            "destination=%p pipeline=%p event=%p error=%s\n",
            (__bridge void *)source, (__bridge void *)rendered,
            (__bridge void *)destination, (__bridge void *)pipeline,
            (__bridge void *)signal_event,
            error.description.UTF8String ?: "nil");
        return 49;
    }

    id<MTLCommandBuffer> command_buffer = macws_new_command_buffer(queue);
    MTLRenderPassDescriptor *pass =
        [MTLRenderPassDescriptor renderPassDescriptor];
    pass.colorAttachments[0].texture = rendered;
    pass.colorAttachments[0].loadAction = MTLLoadActionDontCare;
    pass.colorAttachments[0].storeAction = MTLStoreActionStore;
    id<MTLRenderCommandEncoder> render =
        [command_buffer renderCommandEncoderWithDescriptor:pass];
    [render setRenderPipelineState:pipeline];
    [render setFragmentTexture:source atIndex:0];
    [render drawPrimitives:MTLPrimitiveTypeTriangleStrip
               vertexStart:0 vertexCount:4];
    [render endEncoding];
    if (signal_kind != 0)
        [command_buffer encodeSignalEvent:signal_event value:1];

    id<MTLBlitCommandEncoder> blit = [command_buffer blitCommandEncoder];
    [blit copyFromTexture:rendered sourceSlice:0 sourceLevel:0
             sourceOrigin:MTLOriginMake(0, 0, 0)
               sourceSize:MTLSizeMake(96, 64, 1)
                toTexture:destination destinationSlice:0
         destinationLevel:0 destinationOrigin:MTLOriginMake(0, 0, 0)];
    [blit endEncoding];
    if (signal_kind != 0)
        [command_buffer encodeSignalEvent:signal_event value:2];
    macws_commit_deferred_encoder(command_buffer);

    const char *mode = signal_kind == 1 ? "drawblitsignal"
        : (signal_kind == 2 ? "drawblitlegacy" : "drawblit");
    int dump_status = macws_dump_kcmd(command_buffer, mode);
    if (getenv("MACWS_IOS_KCMD_HOLD")) raise(SIGSTOP);
    [command_buffer commit];
    [command_buffer waitUntilCompleted];
    fprintf(stderr,
        "IOS-AGX-KCMD mode=%s status=%ld error=%s dump=%d\n", mode,
        (long)command_buffer.status,
        command_buffer.error.description.UTF8String ?: "nil", dump_status);
    return dump_status || command_buffer.error ? 50 : 0;
}

// Native-iOS control for the cross-command-queue legacy-event topology found
// in Stray's post-processing submissions.  Commit the waiter first, then
// immediately commit the producer on a second ordinary command queue.  This
// deliberately exercises forward cross-queue dependencies while preserving
// every event command byte produced by iOS's own IOGPU framework.
//
// The macOS 13.4 IOGPU implementation selects wait record type 6 when the
// legacy event's barrier flag is enabled and type 10 when it is disabled.
// Encode one of each, matching the two wait families captured from Stray,
// and dump both command buffers before submission for byte-level comparison.
static int macws_encode_event_queues(id<MTLDevice> device,
                                     id<MTLCommandQueue> wait_queue) {
    id<MTLCommandQueue> signal_queue = [device newCommandQueue];
    id<MTLEvent> barrier_event = [device newEvent];
    id<MTLEvent> nonbarrier_event = [device newEvent];
    SEL set_barrier = sel_registerName("setEnableBarrier:");
    if (nonbarrier_event &&
        [(NSObject *)nonbarrier_event respondsToSelector:set_barrier]) {
        IMP method = [(NSObject *)nonbarrier_event
            methodForSelector:set_barrier];
        ((void (*)(id, SEL, BOOL))method)(nonbarrier_event, set_barrier, NO);
    }
    if (!signal_queue || !barrier_event || !nonbarrier_event) {
        fprintf(stderr,
            "IOS-AGX-EVENTQUEUES setup wait-queue=%p signal-queue=%p "
            "barrier-event=%p nonbarrier-event=%p\n",
            (__bridge void *)wait_queue, (__bridge void *)signal_queue,
            (__bridge void *)barrier_event,
            (__bridge void *)nonbarrier_event);
        return 51;
    }

    id<MTLCommandBuffer> wait_buffer =
        macws_new_command_buffer(wait_queue);
    id<MTLCommandBuffer> signal_buffer =
        macws_new_command_buffer(signal_queue);
    [wait_buffer encodeWaitForEvent:barrier_event value:64];
    [wait_buffer encodeWaitForEvent:nonbarrier_event value:32];
    [signal_buffer encodeSignalEvent:barrier_event value:64];
    [signal_buffer encodeSignalEvent:nonbarrier_event value:32];

    int wait_dump = macws_dump_kcmd(wait_buffer, "eventqueues-wait");
    int signal_dump = macws_dump_kcmd(signal_buffer, "eventqueues-signal");
    if (getenv("MACWS_IOS_KCMD_HOLD")) raise(SIGSTOP);

    struct timespec before = {0}, after = {0};
    clock_gettime(CLOCK_MONOTONIC, &before);
    [wait_buffer commit];
    [signal_buffer commit];
    [wait_buffer waitUntilCompleted];
    [signal_buffer waitUntilCompleted];
    clock_gettime(CLOCK_MONOTONIC, &after);
    double wall_ms = (double)(after.tv_sec - before.tv_sec) * 1000.0 +
        (double)(after.tv_nsec - before.tv_nsec) / 1000000.0;
    fprintf(stderr,
        "IOS-AGX-EVENTQUEUES wait-status=%ld signal-status=%ld "
        "wait-error=%s signal-error=%s wall-ms=%.3f "
        "wait-gpu=%.6f..%.6f signal-gpu=%.6f..%.6f dumps=%d/%d\n",
        (long)wait_buffer.status, (long)signal_buffer.status,
        wait_buffer.error.description.UTF8String ?: "nil",
        signal_buffer.error.description.UTF8String ?: "nil", wall_ms,
        wait_buffer.GPUStartTime, wait_buffer.GPUEndTime,
        signal_buffer.GPUStartTime, signal_buffer.GPUEndTime,
        wait_dump, signal_dump);
    BOOL completed =
        wait_buffer.status == MTLCommandBufferStatusCompleted &&
        signal_buffer.status == MTLCommandBufferStatusCompleted &&
        wait_buffer.error == nil && signal_buffer.error == nil;
    return completed && wait_dump == 0 && signal_dump == 0 ? 0 : 52;
}

// Two-way legacy-event handshake matching the scheduling invariant in the
// Stray failure window.  Queue A cannot finish until B advances, and B cannot
// advance until A has run its preceding signal.  A second reverse handshake
// proves that the scheduler can switch back to A and then back to B again.
static int macws_encode_event_cycle(id<MTLDevice> device,
                                    id<MTLCommandQueue> queue_a) {
    id<MTLCommandQueue> queue_b = [device newCommandQueue];
    id<MTLEvent> event_a0 = [device newEvent];
    id<MTLEvent> event_b0 = [device newEvent];
    id<MTLEvent> event_a1 = [device newEvent];
    id<MTLEvent> event_b1 = [device newEvent];
    SEL set_barrier = sel_registerName("setEnableBarrier:");
    if (event_b1 && [(NSObject *)event_b1
            respondsToSelector:set_barrier]) {
        IMP method = [(NSObject *)event_b1 methodForSelector:set_barrier];
        ((void (*)(id, SEL, BOOL))method)(event_b1, set_barrier, NO);
    }
    if (!queue_b || !event_a0 || !event_b0 || !event_a1 || !event_b1)
        return 53;

    id<MTLCommandBuffer> buffer_a = macws_new_command_buffer(queue_a);
    id<MTLCommandBuffer> buffer_b = macws_new_command_buffer(queue_b);
    [buffer_a encodeSignalEvent:event_a0 value:32];
    [buffer_a encodeWaitForEvent:event_b0 value:64];
    [buffer_a encodeSignalEvent:event_a1 value:32];
    [buffer_a encodeWaitForEvent:event_b1 value:32];
    [buffer_b encodeWaitForEvent:event_a0 value:32];
    [buffer_b encodeSignalEvent:event_b0 value:64];
    [buffer_b encodeWaitForEvent:event_a1 value:32];
    [buffer_b encodeSignalEvent:event_b1 value:32];

    int dump_a = macws_dump_kcmd(buffer_a, "eventcycle-a");
    int dump_b = macws_dump_kcmd(buffer_b, "eventcycle-b");
    struct timespec before = {0}, after = {0};
    clock_gettime(CLOCK_MONOTONIC, &before);
    [buffer_a commit];
    [buffer_b commit];
    [buffer_a waitUntilCompleted];
    [buffer_b waitUntilCompleted];
    clock_gettime(CLOCK_MONOTONIC, &after);
    double wall_ms = (double)(after.tv_sec - before.tv_sec) * 1000.0 +
        (double)(after.tv_nsec - before.tv_nsec) / 1000000.0;
    fprintf(stderr,
        "IOS-AGX-EVENTCYCLE a-status=%ld b-status=%ld a-error=%s "
        "b-error=%s wall-ms=%.3f a-gpu=%.6f..%.6f "
        "b-gpu=%.6f..%.6f dumps=%d/%d\n",
        (long)buffer_a.status, (long)buffer_b.status,
        buffer_a.error.description.UTF8String ?: "nil",
        buffer_b.error.description.UTF8String ?: "nil", wall_ms,
        buffer_a.GPUStartTime, buffer_a.GPUEndTime,
        buffer_b.GPUStartTime, buffer_b.GPUEndTime, dump_a, dump_b);
    BOOL completed =
        buffer_a.status == MTLCommandBufferStatusCompleted &&
        buffer_b.status == MTLCommandBufferStatusCompleted &&
        buffer_a.error == nil && buffer_b.error == nil;
    return completed && dump_a == 0 && dump_b == 0 ? 0 : 54;
}

// Native control for Chromium's runtime-captured multi-segment submission.
// Encode thirteen ordinary render passes into one command buffer so the iOS
// AGX producer, rather than a guessed C layout, shows how repeated subtype-1
// records are framed and completed by this exact iOS 16.3 driver.  The probe
// remains read-only with respect to KCMD storage: macws_dump_kcmd copies the
// bytes before the public -commit path submits them unchanged.
static int macws_encode_draw_chain(id<MTLDevice> device,
                                   id<MTLCommandQueue> queue) {
    const NSUInteger pass_count = 13;
    MTLTextureDescriptor *texture_descriptor =
        [MTLTextureDescriptor
            texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
            width:256 height:256 mipmapped:NO];
    texture_descriptor.storageMode = MTLStorageModeShared;
    texture_descriptor.usage = MTLTextureUsageRenderTarget |
        MTLTextureUsageShaderRead;
    id<MTLTexture> source =
        [device newTextureWithDescriptor:texture_descriptor];

    NSError *error = nil;
    NSString *library_path = [NSString stringWithUTF8String:
        "/System/Library/Frameworks/QuartzCore.framework/default.metallib"];
    NSURL *library_url = [NSURL fileURLWithPath:library_path];
    id<MTLLibrary> library =
        [device newLibraryWithURL:library_url error:&error];
    id<MTLFunction> vertex =
        [library newFunctionWithName:
            [NSString stringWithUTF8String:"read_surf_vert"]];
    id<MTLFunction> fragment =
        [library newFunctionWithName:
            [NSString stringWithUTF8String:"read_surf_frag"]];
    MTLRenderPipelineDescriptor *pipeline_descriptor =
        [MTLRenderPipelineDescriptor new];
    pipeline_descriptor.vertexFunction = vertex;
    pipeline_descriptor.fragmentFunction = fragment;
    pipeline_descriptor.colorAttachments[0].pixelFormat =
        MTLPixelFormatBGRA8Unorm;
    id<MTLRenderPipelineState> pipeline =
        [device newRenderPipelineStateWithDescriptor:pipeline_descriptor
                                                error:&error];
    if (!source || !pipeline) {
        fprintf(stderr,
            "IOS-AGX-KCMD drawchain setup source=%p pipeline=%p error=%s\n",
            (__bridge void *)source, (__bridge void *)pipeline,
            error.description.UTF8String ?: "nil");
        return 42;
    }

    NSMutableArray *targets =
        [NSMutableArray arrayWithCapacity:pass_count];
    id<MTLCommandBuffer> command_buffer = macws_new_command_buffer(queue);
    for (NSUInteger pass_index = 0; pass_index < pass_count; pass_index++) {
        id<MTLTexture> target =
            [device newTextureWithDescriptor:texture_descriptor];
        if (!target) return 43;
        [targets addObject:target];

        MTLRenderPassDescriptor *pass =
            [MTLRenderPassDescriptor renderPassDescriptor];
        pass.colorAttachments[0].texture = target;
        pass.colorAttachments[0].loadAction = MTLLoadActionDontCare;
        pass.colorAttachments[0].storeAction = MTLStoreActionStore;
        id<MTLRenderCommandEncoder> encoder =
            [command_buffer renderCommandEncoderWithDescriptor:pass];
        [encoder setRenderPipelineState:pipeline];
        [encoder setFragmentTexture:source atIndex:0];
        [encoder drawPrimitives:MTLPrimitiveTypeTriangleStrip
                    vertexStart:0 vertexCount:4];
        [encoder endEncoding];
    }

    int dump_status = macws_dump_kcmd(command_buffer, "drawchain");
    if (getenv("MACWS_IOS_KCMD_HOLD")) raise(SIGSTOP);
    [command_buffer commit];
    [command_buffer waitUntilCompleted];
    fprintf(stderr,
        "IOS-AGX-KCMD mode=drawchain passes=%lu status=%ld error=%s "
        "dump=%d\n",
        (unsigned long)pass_count, (long)command_buffer.status,
        command_buffer.error.description.UTF8String ?: "nil", dump_status);
    return dump_status || command_buffer.error ? 44 : 0;
}

// Load a byte-exact external MTLB and ask the native iOS AGX device to create
// the same descriptor-based compute pipeline used by UE4.  This is a bounded
// compatibility witness: it neither edits the library nor substitutes a
// result.  The caller supplies both values so a captured game shader is never
// silently selected by a generic probe invocation.
static int macws_test_external_compute(id<MTLDevice> device) {
    const char *path_utf8 = getenv("MACWS_IOS_MTLB_PATH");
    const char *function_utf8 = getenv("MACWS_IOS_MTLB_FUNCTION");
    if (!path_utf8 || !*path_utf8 || !function_utf8 || !*function_utf8) {
        fprintf(stderr,
            "IOS-AGX-KCMD externalcompute requires MACWS_IOS_MTLB_PATH "
            "and MACWS_IOS_MTLB_FUNCTION\n");
        return 65;
    }
    NSString *path = [NSString stringWithUTF8String:path_utf8];
    NSData *bytes = [NSData dataWithContentsOfFile:path];
    if (!bytes.length) {
        fprintf(stderr,
            "IOS-AGX-KCMD externalcompute unreadable path=%s\n", path_utf8);
        return 66;
    }
    NSError *error = nil;
    id<MTLLibrary> library = [device
        newLibraryWithURL:[NSURL fileURLWithPath:path] error:&error];
    NSString *function_name = [NSString stringWithUTF8String:function_utf8];
    id<MTLFunction> function =
        [library newFunctionWithName:function_name];
    if (!function) {
        fprintf(stderr,
            "IOS-AGX-KCMD externalcompute path=%s bytes=%lu library=%p "
            "function=%s/(nil) names=%s error-domain=%s error-code=%ld "
            "description=%s\n",
            path_utf8, (unsigned long)bytes.length,
            (__bridge void *)library, function_utf8,
            library.functionNames.description.UTF8String ?: "(nil)",
            error.domain.UTF8String ?: "(nil)", (long)error.code,
            error.localizedDescription.UTF8String ?: "(nil)");
        return 68;
    }
    MTLComputePipelineDescriptor *descriptor =
        [MTLComputePipelineDescriptor new];
    descriptor.label = function_name;
    descriptor.computeFunction = function;
    MTLAutoreleasedComputePipelineReflection reflection = nil;
    id<MTLComputePipelineState> pipeline = function
        ? [device newComputePipelineStateWithDescriptor:descriptor
                                                options:MTLPipelineOptionNone
                                             reflection:&reflection
                                                  error:&error]
        : nil;
    fprintf(stderr,
        "IOS-AGX-KCMD externalcompute path=%s bytes=%lu library=%p "
        "function=%s/%p pipeline=%p class=%s reflection=%p "
        "error-domain=%s error-code=%ld description=%s\n",
        path_utf8, (unsigned long)bytes.length,
        (__bridge void *)library, function_utf8, (__bridge void *)function,
        (__bridge void *)pipeline,
        pipeline ? object_getClassName(pipeline) : "(nil)",
        (__bridge void *)reflection,
        error.domain.UTF8String ?: "(nil)", (long)error.code,
        error.localizedDescription.UTF8String ?: "(nil)");
    return pipeline ? 0 : 67;
}

static int macws_test_iosurface_format(id<MTLDevice> device) {
    // Native-iOS control for the Stray RGBA16Unorm failure.  CoreVideo defines
    // 'l64r' as little-endian 64-bit RGBA with full-range 16-bit samples,
    // matching MTLPixelFormatRGBA16Unorm's byte layout.  Exercise the exact
    // 98x64 descriptor and private-storage contract observed at runtime.
    const uint32_t pixel_format = 'l64r';
    NSDictionary *properties = @{
        @"IOSurfaceWidth": @98,
        @"IOSurfaceHeight": @64,
        @"IOSurfaceBytesPerElement": @8,
        @"IOSurfacePixelFormat": @(pixel_format),
        @"IOSurfaceIsGlobal": @NO,
        @"IOSurfaceCacheMode": @0,
    };
    IOSurfaceRef surface = IOSurfaceCreate(
        (__bridge CFDictionaryRef)properties);
    MTLTextureDescriptor *descriptor = [MTLTextureDescriptor
        texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA16Unorm
                                    width:98 height:64 mipmapped:NO];
    descriptor.storageMode = MTLStorageModePrivate;
    descriptor.usage = MTLTextureUsageUnknown;
    SEL selector = sel_registerName(
        "newTextureWithDescriptor:iosurface:plane:");
    id texture = nil;
    if (surface && [device respondsToSelector:selector]) {
        id (*implementation)(id, SEL, id, IOSurfaceRef, NSUInteger) =
            (void *)objc_msgSend;
        texture = implementation(device, selector, descriptor, surface, 0);
    }
    fprintf(stderr,
        "IOS-AGX-IOSURFACE-FORMAT pf=%lu desc=98x64 selected-fcc=%#x "
        "surface=%p id=%u w=%zu h=%zu bpr=%zu bpe=%zu surface-fcc=%#x "
        "selector=%s texture=%p class=%s\n",
        (unsigned long)descriptor.pixelFormat, (unsigned)pixel_format,
        (void *)surface, surface ? IOSurfaceGetID(surface) : 0,
        surface ? IOSurfaceGetWidth(surface) : 0,
        surface ? IOSurfaceGetHeight(surface) : 0,
        surface ? IOSurfaceGetBytesPerRow(surface) : 0,
        surface ? IOSurfaceGetBytesPerElement(surface) : 0,
        surface ? IOSurfaceGetPixelFormat(surface) : 0,
        [device respondsToSelector:selector] ? "YES" : "NO",
        (__bridge void *)texture,
        texture ? object_getClassName(texture) : "(nil)");
    if (surface) CFRelease(surface);
    return texture ? 0 : 68;
}

int main(void) {
    @autoreleasepool {
        const char *mode = getenv("MACWS_IOS_KCMD_MODE");
        if (!mode || !*mode) mode = "blit";
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        id<MTLCommandQueue> queue = [device newCommandQueue];
        fprintf(stderr, "IOS-AGX-KCMD pid=%d mode=%s device=%p class=%s "
                        "queue=%p\n",
            getpid(), mode, (__bridge void *)device,
            device ? object_getClassName(device) : "(nil)",
            (__bridge void *)queue);
        if (!device || !queue) return 1;
        if (strcmp(mode, "blit") == 0)
            return macws_encode_blit(device, queue);
        if (strcmp(mode, "blittexture") == 0)
            return macws_encode_blit_texture(device, queue);
        if (strcmp(mode, "mipmap") == 0)
            return macws_encode_mipmap(device, queue);
        if (strcmp(mode, "compute") == 0)
            return macws_encode_compute(device, queue);
        if (strcmp(mode, "statistics") == 0)
            return macws_encode_statistics(device, queue);
        if (strcmp(mode, "scale") == 0)
            return macws_encode_scale(device, queue);
        if (strcmp(mode, "chain") == 0)
            return macws_encode_chain(device, queue);
        if (strcmp(mode, "rawcompute") == 0)
            return macws_encode_raw_compute(device, queue, NO);
        if (strcmp(mode, "rawconcurrent") == 0)
            return macws_encode_raw_compute(device, queue, YES);
        if (strcmp(mode, "richcompute") == 0)
            return macws_encode_rich_compute(device, queue);
        if (strcmp(mode, "render") == 0)
            return macws_encode_render(device, queue, NO);
        if (strcmp(mode, "renderzero") == 0)
            return macws_encode_render(device, queue, YES);
        if (strcmp(mode, "parallel") == 0)
            return macws_encode_parallel_render(device, queue);
        if (strcmp(mode, "draw") == 0)
            return macws_encode_draw(device, queue);
        if (strcmp(mode, "drawblit") == 0)
            return macws_encode_draw_blit(device, queue, 0);
        if (strcmp(mode, "drawblitsignal") == 0)
            return macws_encode_draw_blit(device, queue, 1);
        if (strcmp(mode, "drawblitlegacy") == 0)
            return macws_encode_draw_blit(device, queue, 2);
        if (strcmp(mode, "eventqueues") == 0)
            return macws_encode_event_queues(device, queue);
        if (strcmp(mode, "eventcycle") == 0)
            return macws_encode_event_cycle(device, queue);
        if (strcmp(mode, "drawchain") == 0)
            return macws_encode_draw_chain(device, queue);
        if (strcmp(mode, "aquariumchain") == 0)
            return macws_encode_aquarium_chain(device, queue);
        if (strcmp(mode, "externalcompute") == 0)
            return macws_test_external_compute(device);
        if (strcmp(mode, "iosurfaceformat") == 0)
            return macws_test_iosurface_format(device);
        fprintf(stderr, "IOS-AGX-KCMD unknown mode=%s\n", mode);
        return 64;
    }
}

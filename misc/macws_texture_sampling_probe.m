// Explicit, owned-texture shader diagnostic, never production linked.
// Usage: probe {plain|nocopy-buffer-view} {shared|managed} [--office-row-stride] [--render]
// --office-shader implies --render and loads only the exact owned fixture below.
// Presets: 19x11/256-byte rows or the observed Office 309x250/1248-byte rows.
// Actual Office metadata: RGBA8Unorm (70), 2D, ShaderRead (1), one mip/sample.
// One queue, two compute submissions or one quad+fragment+readback submission.
// Build -fno-objc-arc -fblocks with Foundation and Metal. No UI or flags.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <CommonCrypto/CommonDigest.h>
#include <TargetConditionals.h>
#include <mach-o/dyld.h>
#include <mach-o/loader.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

static _Atomic unsigned callbacks, callbackMismatch;
static const char *OfficeLibraryPath = "/private/tmp/macws-office-Metal2DShaders-20260919.metallib";
static const char *OfficeLibrarySHA256 = "9eac296d60f976a0ef4e1cfe90b440101045b4eaed437af3a6dd803997959a02";

static id<MTLLibrary> LoadOfficeLibrary(id<MTLDevice> device, NSError **error) {
    struct stat status;
    if (stat(OfficeLibraryPath, &status) || !S_ISREG(status.st_mode) || status.st_size != 155074) {
        puts("office-fixture size/path mismatch contract=FAIL");
        return nil;
    }
    NSURL *url = [NSURL fileURLWithPath:[NSString stringWithUTF8String:OfficeLibraryPath]];
    NSData *data = [[NSData alloc] initWithContentsOfURL:url options:0 error:error];
    if (!data || data.length != 155074) { [data release]; return nil; }
    unsigned char digest[CC_SHA256_DIGEST_LENGTH];
    char hexadecimal[CC_SHA256_DIGEST_LENGTH * 2 + 1];
    CC_SHA256(data.bytes, (CC_LONG)data.length, digest);
    for (unsigned i = 0; i < sizeof(digest); ++i) snprintf(hexadecimal + i * 2, 3, "%02x", digest[i]);
    printf("office-fixture bytes=%lu sha256=%s\n", (unsigned long)data.length, hexadecimal);
    if (strcmp(hexadecimal, OfficeLibrarySHA256)) { [data release]; return nil; }
    // Hand Metal the exact verified bytes, not a second path-based read.
    void *bytes = malloc(data.length);
    if (!bytes) { [data release]; return nil; }
    memcpy(bytes, data.bytes, data.length);
    dispatch_data_t payload = dispatch_data_create(bytes, data.length, NULL, DISPATCH_DATA_DESTRUCTOR_FREE);
    [data release];
    if (!payload) { free(bytes); return nil; }
    id<MTLLibrary> library = [device newLibraryWithData:payload error:error];
    dispatch_release(payload);
    return library;
}

#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
static BOOL CheckOfficeReflection(MTLRenderPipelineReflection *reflection) {
    unsigned vertices = 0, fragments = 0;
    for (MTLArgument *argument in reflection.vertexArguments) {
        if (!argument.active) continue;
        printf("office-reflect vertex name=%s type=%lu index=%lu size=%lu align=%lu\n",
               argument.name.UTF8String, (unsigned long)argument.type, (unsigned long)argument.index,
               (unsigned long)argument.bufferDataSize, (unsigned long)argument.bufferAlignment);
        if (argument.type != MTLArgumentTypeBuffer) continue;
        if (argument.index == 0 && argument.bufferDataType == MTLDataTypeFloat2) vertices |= 1;
        if (argument.index == 2 && argument.bufferDataSize == 64 &&
            argument.bufferDataType == MTLDataTypeFloat4x4) vertices |= 2;
        if (argument.index == 3 && argument.bufferDataSize == 48 &&
            argument.bufferDataType == MTLDataTypeFloat3x3) vertices |= 4;
    }
    for (MTLArgument *argument in reflection.fragmentArguments) {
        if (!argument.active) continue;
        printf("office-reflect fragment name=%s type=%lu index=%lu\n",
               argument.name.UTF8String, (unsigned long)argument.type, (unsigned long)argument.index);
        if (argument.type == MTLArgumentTypeBuffer && argument.index == 0 &&
            argument.bufferDataSize == 4 && argument.bufferDataType == MTLDataTypeFloat) fragments |= 1;
        if (argument.type == MTLArgumentTypeTexture && argument.index == 1) fragments |= 2;
        if (argument.type == MTLArgumentTypeSampler && argument.index == 1) fragments |= 4;
    }
    printf("office-reflect vertex-mask=%u fragment-mask=%u contract=%s\n",
           vertices, fragments, vertices == 7 && fragments == 7 ? "PASS" : "FAIL");
    return vertices == 7 && fragments == 7;
}
#pragma clang diagnostic pop

static void Provenance(void) {
    for (uint32_t i = 0; i < _dyld_image_count(); ++i) {
        const char *name = _dyld_get_image_name(i);
        const struct mach_header_64 *header = (const void *)_dyld_get_image_header(i);
        if (!name || !header || header->magic != MH_MAGIC_64) continue;
        if (header->filetype == MH_EXECUTE)
            printf("main-executable=%s subtype=%#x\n", name, (unsigned)header->cpusubtype);
        if (!strstr(name, "libmachook")) continue;
        printf("library=%s subtype=%#x uuid=", name, (unsigned)header->cpusubtype);
        const uint8_t *cursor = (const void *)(header + 1);
        for (uint32_t j = 0; j < header->ncmds; ++j) {
            const struct load_command *command = (const void *)cursor;
            if (command->cmd == LC_UUID) {
                const struct uuid_command *uuid = (const void *)cursor;
                for (unsigned k = 0; k < 16; ++k) printf("%02x", uuid->uuid[k]);
            }
            cursor += command->cmdsize;
        }
        puts("");
    }
}

static void Pattern(unsigned x, unsigned y, uint8_t pixel[4]) {
    pixel[0] = (uint8_t)(17 + x * 7); // RGBA, no BGRA swizzle.
    pixel[1] = (uint8_t)(29 + y * 17);
    pixel[2] = (uint8_t)(47 + (x * 3 + y * 11) % 160);
    pixel[3] = (uint8_t)(128 + (x + y) % 128);
}

static void Fill(uint8_t *original, unsigned width, unsigned height, unsigned row) {
    memset(original, 0xa5, row * height);
    for (unsigned y = 0; y < height; ++y) for (unsigned x = 0; x < width; ++x) {
        uint8_t *pixel = original + y * row + x * 4;
        Pattern(x, y, pixel);
    }
}

static NSString * const ShaderSource =
    @"#include <metal_stdlib>\n"
     "using namespace metal;\n"
     "kernel void read_texture(texture2d<float, access::read> input [[texture(0)]], "
     "device uchar4 *output [[buffer(0)]], uint2 p [[thread_position_in_grid]]) {\n"
     "  if (p.x >= input.get_width() || p.y >= input.get_height()) return;\n"
     "  output[p.y * input.get_width() + p.x] = "
     "uchar4(round(clamp(input.read(p), 0.0f, 1.0f) * 255.0f));\n"
     "}\n"
     "kernel void sample_texture(texture2d<float, access::sample> input [[texture(0)]], "
     "device uchar4 *output [[buffer(0)]], uint2 p [[thread_position_in_grid]]) {\n"
     "  if (p.x >= input.get_width() || p.y >= input.get_height()) return;\n"
     "  constexpr sampler nearest(coord::pixel, address::clamp_to_edge, filter::nearest);\n"
     "  output[p.y * input.get_width() + p.x] = "
     "uchar4(round(clamp(input.sample(nearest, float2(p) + 0.5f), 0.0f, 1.0f) * 255.0f));\n"
     "}\n"
     "struct Raster { float4 position [[position]]; };\n"
     "vertex Raster quad_vertex(uint index [[vertex_id]]) {\n"
     "  const float2 positions[4] = {float2(-1,-1), float2(1,-1), "
     "float2(-1,1), float2(1,1)};\n"
     "  Raster out; out.position = float4(positions[index], 0, 1); return out;\n"
     "}\n"
     "fragment float4 quad_fragment(Raster in [[stage_in]], "
     "texture2d<float, access::sample> input [[texture(0)]]) {\n"
     "  constexpr sampler nearest(coord::pixel, address::clamp_to_edge, filter::nearest);\n"
     "  return input.sample(nearest, in.position.xy);\n"
     "}\n";

static BOOL CheckPixels(const uint8_t *actual, unsigned actualRow,
                        const uint8_t *expected, unsigned expectedRow,
                        unsigned width, unsigned height, const char *phase) {
    unsigned mismatchedPixels = 0, mismatchedBytes = 0;
    for (unsigned y = 0; y < height; ++y) for (unsigned x = 0; x < width; ++x) {
        uint8_t pattern[4];
        Pattern(x, y, pattern);
        BOOL mismatch = NO;
        for (unsigned c = 0; c < 4; ++c) {
            uint8_t want = expected ? expected[y * expectedRow + x * 4 + c] : pattern[c];
            BOOL bad = actual[y * actualRow + x * 4 + c] != want;
            mismatch |= bad;
            mismatchedBytes += bad;
        }
        mismatchedPixels += mismatch;
    }
    printf("phase=%s mismatched-pixels=%u/%u mismatched-bytes=%u/%u "
           "first=%u,%u,%u,%u last=%u,%u,%u,%u\n", phase,
           mismatchedPixels, width * height, mismatchedBytes, width * height * 4,
           actual[0], actual[1], actual[2], actual[3],
           actual[(height - 1) * actualRow + (width - 1) * 4],
           actual[(height - 1) * actualRow + (width - 1) * 4 + 1],
           actual[(height - 1) * actualRow + (width - 1) * 4 + 2],
           actual[(height - 1) * actualRow + (width - 1) * 4 + 3]);
    return mismatchedPixels == 0;
}

static BOOL RunKernel(id<MTLCommandQueue> queue, id<MTLComputePipelineState> pipeline,
                      id<MTLTexture> texture, id<MTLBuffer> output,
                      const uint8_t *expected, unsigned row, const char *phase) {
    unsigned width = (unsigned)texture.width, height = (unsigned)texture.height;
    memset(output.contents, 0x5a, output.length);
    id<MTLCommandBuffer> command = [queue commandBuffer];
    id<MTLComputeCommandEncoder> encoder = [command computeCommandEncoder];
    if (!command || !encoder) {
        printf("phase=%s encoder=NIL contract=FAIL\n", phase);
        return NO;
    }
    [encoder setComputePipelineState:pipeline];
    [encoder setTexture:texture atIndex:0];
    [encoder setBuffer:output offset:0 atIndex:0];
    NSUInteger threads = MIN((NSUInteger)8, pipeline.maxTotalThreadsPerThreadgroup);
    if (!threads) { [encoder endEncoding]; return NO; }
    [encoder dispatchThreadgroups:MTLSizeMake((width + threads - 1) / threads, height, 1)
           threadsPerThreadgroup:MTLSizeMake(threads, 1, 1)];
    [encoder endEncoding];
    [command commit];
    [command waitUntilCompleted];
    printf("phase=%s status=%lu error=%s\n", phase, (unsigned long)command.status,
           command.error ? command.error.description.UTF8String : "none");
    if (command.status != MTLCommandBufferStatusCompleted) return NO;
    return CheckPixels(output.contents, width * 4, expected, row, width, height, phase);
}

static BOOL RunRender(id<MTLDevice> device, id<MTLCommandQueue> queue,
                      id<MTLLibrary> library, id<MTLTexture> input, size_t outputLength,
                      BOOL officeShader) {
    BOOL passed = NO;
    unsigned width = (unsigned)input.width, height = (unsigned)input.height;
    unsigned outputRow = (width * 4 + 255) & ~255u;
    id<MTLFunction> vertex = nil, fragment = nil;
    id<MTLRenderPipelineState> pipeline = nil;
    id<MTLTexture> target = nil;
    id<MTLBuffer> output = nil;
    id<MTLSamplerState> sampler = nil;
    NSError *error = nil;
    if (officeShader) {
        id<MTLFunction> base = [library newFunctionWithName:@"bitmapVS"];
        MTLFunctionConstant *constant = base.functionConstantsDictionary[@"hasStencil"];
        printf("office-function-constant name=%s index=%lu type=%lu required=%d\n",
               constant ? constant.name.UTF8String : "NIL", (unsigned long)constant.index,
               (unsigned long)constant.type, constant.required);
        BOOL valid = constant && constant.index == 0 && constant.type == MTLDataTypeBool;
        [base release];
        if (!valid) goto cleanup;
        BOOL hasStencil = NO;
        MTLFunctionConstantValues *values = [[MTLFunctionConstantValues alloc] init];
        [values setConstantValue:&hasStencil type:MTLDataTypeBool atIndex:0];
        vertex = [library newFunctionWithName:@"bitmapVS" constantValues:values error:&error];
        printf("office-specialize name=bitmapVS hasStencil=0 result=%s error=%s\n",
               vertex ? "non-NIL" : "NIL", error ? error.description.UTF8String : "none");
        error = nil;
        fragment = [library newFunctionWithName:@"bitmapPS" constantValues:values error:&error];
        printf("office-specialize name=bitmapPS hasStencil=0 result=%s error=%s\n",
               fragment ? "non-NIL" : "NIL", error ? error.description.UTF8String : "none");
        [values release];
    } else {
        vertex = [library newFunctionWithName:@"quad_vertex"];
        fragment = [library newFunctionWithName:@"quad_fragment"];
    }
    if (!vertex || !fragment) goto cleanup;
    MTLRenderPipelineDescriptor *pipelineDesc = [[MTLRenderPipelineDescriptor alloc] init];
    pipelineDesc.vertexFunction = vertex;
    pipelineDesc.fragmentFunction = fragment;
    pipelineDesc.colorAttachments[0].pixelFormat = MTLPixelFormatRGBA8Unorm;
    pipelineDesc.colorAttachments[0].blendingEnabled = NO;
    error = nil;
    MTLRenderPipelineReflection *reflection = nil;
    pipeline = officeShader
        ? [device newRenderPipelineStateWithDescriptor:pipelineDesc
            options:MTLPipelineOptionBindingInfo | MTLPipelineOptionBufferTypeInfo
            reflection:&reflection error:&error]
        : [device newRenderPipelineStateWithDescriptor:pipelineDesc error:&error];
    [pipelineDesc release];
    printf("pipeline=render result=%s error=%s\n", pipeline ? "non-NIL" : "NIL",
           error ? error.description.UTF8String : "none");
    if (!pipeline) goto cleanup;
    if (officeShader && !CheckOfficeReflection(reflection)) goto cleanup;
    MTLTextureDescriptor *targetDesc = [MTLTextureDescriptor
        texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:width height:height mipmapped:NO];
    targetDesc.storageMode = MTLStorageModeShared;
    targetDesc.usage = MTLTextureUsageRenderTarget;
    target = [device newTextureWithDescriptor:targetDesc];
    output = [device newBufferWithLength:outputLength options:MTLResourceStorageModeShared];
    if (!target || !output || !output.contents) { puts("render-target/output=NIL contract=FAIL"); goto cleanup; }
    printf("render-target format=%lu storage=%lu shape=%lux%lu readback-bpr=%u\n",
           (unsigned long)target.pixelFormat, (unsigned long)target.storageMode,
           (unsigned long)target.width, (unsigned long)target.height, outputRow);
    if (target.storageMode != MTLStorageModeShared || target.pixelFormat != MTLPixelFormatRGBA8Unorm ||
        target.width != width || target.height != height || output.storageMode != MTLStorageModeShared)
        goto cleanup;
    memset(output.contents, 0x5a, outputLength);
    MTLRenderPassDescriptor *pass = [MTLRenderPassDescriptor renderPassDescriptor];
    pass.colorAttachments[0].texture = target;
    pass.colorAttachments[0].loadAction = MTLLoadActionClear;
    pass.colorAttachments[0].clearColor = MTLClearColorMake(0, 0, 0, 0);
    pass.colorAttachments[0].storeAction = MTLStoreActionStore;
    id<MTLCommandBuffer> command = [queue commandBuffer];
    id<MTLRenderCommandEncoder> encoder = [command renderCommandEncoderWithDescriptor:pass];
    if (!command || !encoder) { puts("render-encoder=NIL contract=FAIL"); goto cleanup; }
    [encoder setRenderPipelineState:pipeline];
    [encoder setViewport:(MTLViewport){0, 0, width, height, 0, 1}];
    [encoder setScissorRect:(MTLScissorRect){0, 0, width, height}];
    [encoder setCullMode:MTLCullModeNone];
    if (officeShader) {
        const float vertices[8] = {0, 0, width, 0, 0, height, width, height};
        // AIR bitmapVS negates clip-space Y after this column-major float4x4.
        const float transform[16] = {
            2.0f / width, 0, 0, 0, 0, 2.0f / height, 0, 0,
            0, 0, 1, 0, -1, -1, 0, 1};
        // Metal float3x3 has three 16-byte columns, not nine packed floats.
        const float bitmapTransform[12] = {
            1.0f / width, 0, 0, 0, 0, 1.0f / height, 0, 0, 0, 0, 1, 0};
        const float opacity = 1.0f;
        MTLSamplerDescriptor *sampling = [[MTLSamplerDescriptor alloc] init];
        sampling.normalizedCoordinates = YES;
        sampling.minFilter = MTLSamplerMinMagFilterNearest;
        sampling.magFilter = MTLSamplerMinMagFilterNearest;
        sampling.sAddressMode = MTLSamplerAddressModeClampToEdge;
        sampling.tAddressMode = MTLSamplerAddressModeClampToEdge;
        sampler = [device newSamplerStateWithDescriptor:sampling];
        [sampling release];
        if (!sampler) { [encoder endEncoding]; goto cleanup; }
        [encoder setVertexBytes:vertices length:sizeof(vertices) atIndex:0];
        [encoder setVertexBytes:transform length:sizeof(transform) atIndex:2];
        [encoder setVertexBytes:bitmapTransform length:sizeof(bitmapTransform) atIndex:3];
        [encoder setFragmentBytes:&opacity length:sizeof(opacity) atIndex:0];
        [encoder setFragmentTexture:input atIndex:1];
        [encoder setFragmentSamplerState:sampler atIndex:1];
        puts("office-bind vertices=0 xfrm=2/64 bmpXfrm=3/48 opacity=0/4 texture=1 sampler=1");
    } else {
        [encoder setFragmentTexture:input atIndex:0];
    }
    [encoder drawPrimitives:MTLPrimitiveTypeTriangleStrip vertexStart:0 vertexCount:4];
    [encoder endEncoding];
    id<MTLBlitCommandEncoder> blit = [command blitCommandEncoder];
    if (!blit) { puts("render-readback-encoder=NIL contract=FAIL"); goto cleanup; }
    [blit copyFromTexture:target sourceSlice:0 sourceLevel:0
        sourceOrigin:MTLOriginMake(0, 0, 0) sourceSize:MTLSizeMake(width, height, 1)
        toBuffer:output destinationOffset:0 destinationBytesPerRow:outputRow
        destinationBytesPerImage:outputRow * height];
    [blit endEncoding];
    [command commit];
    [command waitUntilCompleted];
    printf("phase=render status=%lu error=%s\n", (unsigned long)command.status,
           command.error ? command.error.description.UTF8String : "none");
    if (command.status != MTLCommandBufferStatusCompleted) goto cleanup;
    passed = CheckPixels(output.contents, outputRow, NULL, 0, width, height, "render");
    unsigned modifiedPadding = 0;
    for (unsigned y = 0; y < height; ++y)
        for (unsigned x = width * 4; x < outputRow; ++x)
            modifiedPadding += ((uint8_t *)output.contents)[y * outputRow + x] != 0x5a;
    printf("render-modified-padding=%u\n", modifiedPadding);
    passed &= modifiedPadding == 0;
cleanup:
    [output release]; [target release]; [pipeline release]; [sampler release];
    [fragment release]; [vertex release];
    return passed;
}

static int RunProbe(BOOL view, BOOL managed, BOOL render, BOOL officeShader, unsigned width, unsigned height,
                    unsigned row, uint8_t **originalOwner, size_t sourceLength,
                    size_t outputLength) {
    int result = 4;
    uint8_t *original = *originalOwner;
    @autoreleasepool {
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        id<MTLCommandQueue> queue = [device newCommandQueue];
        id<MTLBuffer> input = nil, output = nil;
        id<MTLTexture> texture = nil;
        id<MTLLibrary> library = nil;
        id<MTLFunction> readFunction = nil, sampleFunction = nil;
        id<MTLComputePipelineState> readPipeline = nil, samplePipeline = nil;
        NSError *error = nil;
        if (!device || !queue) goto cleanup;
        MTLTextureDescriptor *desc = [MTLTextureDescriptor
            texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm
            width:width height:height mipmapped:NO];
        desc.storageMode = managed ? (MTLStorageMode)1 : MTLStorageModeShared;
        desc.usage = MTLTextureUsageShaderRead;
        if (view) {
            input = [device newBufferWithBytesNoCopy:original length:sourceLength
                options:(managed ? (1UL << MTLResourceStorageModeShift)
                                 : MTLResourceStorageModeShared)
                deallocator:^(void *pointer, NSUInteger length) {
                    if (pointer != original || length != sourceLength)
                        atomic_fetch_add(&callbackMismatch, 1);
                    // The owning main function unmaps after this pool drains.
                    // Recording avoids crashing on a broken early callback.
                    atomic_fetch_add(&callbacks, 1);
                }];
            printf("nocopy alias=%d requested-storage=%lu actual-storage=%lu "
                   "length=%zu early-callbacks=%u\n", input && input.contents == original,
                   (unsigned long)desc.storageMode, (unsigned long)input.storageMode,
                   sourceLength, atomic_load(&callbacks));
            if (!input || input.contents != original || input.storageMode != desc.storageMode ||
                atomic_load(&callbacks)) goto cleanup;
            Fill(original, width, height, row);
#if !TARGET_OS_IPHONE
            if (managed) [input didModifyRange:NSMakeRange(0, row * height)];
#endif
            puts("nocopy pattern-written-after-creation=1 target=original-mmap");
            texture = [input newTextureWithDescriptor:desc offset:0 bytesPerRow:row];
        } else {
            Fill(original, width, height, row);
            texture = [device newTextureWithDescriptor:desc];
            if (texture) [texture replaceRegion:MTLRegionMake2D(0, 0, width, height)
                mipmapLevel:0 withBytes:original bytesPerRow:row];
            if (render) {
                // Public replaceRegion has copied the CPU bytes. Release the
                // source before allocating the render target/output so both
                // plain and view modes stay under the same pixel-storage cap.
                munmap(original, sourceLength);
                *originalOwner = NULL;
                original = NULL;
                puts("render plain-source-released-before-target=1");
            }
        }
        if (!texture) { puts("texture=NIL contract=FAIL"); goto cleanup; }
        printf("texture format=%lu type=%lu usage=%lu requested-storage=%lu "
               "actual-storage=%lu shape=%lux%lu mips=%lu samples=%lu\n",
               (unsigned long)texture.pixelFormat, (unsigned long)texture.textureType,
               (unsigned long)texture.usage, (unsigned long)desc.storageMode,
               (unsigned long)texture.storageMode, (unsigned long)texture.width,
               (unsigned long)texture.height, (unsigned long)texture.mipmapLevelCount,
               (unsigned long)texture.sampleCount);
        if (texture.pixelFormat != MTLPixelFormatRGBA8Unorm ||
            texture.textureType != MTLTextureType2D || texture.usage != MTLTextureUsageShaderRead ||
            texture.width != width || texture.height != height ||
            texture.mipmapLevelCount != 1 || texture.sampleCount != 1) goto cleanup;
        if (view && (texture.buffer != input || texture.bufferOffset != 0 ||
            texture.bufferBytesPerRow != row || texture.storageMode != desc.storageMode))
            goto cleanup;
        library = officeShader ? LoadOfficeLibrary(device, &error)
                               : [device newLibraryWithSource:ShaderSource options:nil error:&error];
        printf("shader-library=%s error=%s\n", library ? "non-NIL" : "NIL",
               error ? error.description.UTF8String : "none");
        if (!library) goto cleanup;
        if (render) {
            result = RunRender(device, queue, library, texture, outputLength, officeShader) &&
                     atomic_load(&callbacks) == 0 ? 0 : 5;
            goto cleanup;
        }
        readFunction = [library newFunctionWithName:@"read_texture"];
        sampleFunction = [library newFunctionWithName:@"sample_texture"];
        if (!readFunction || !sampleFunction) goto cleanup;
        error = nil;
        readPipeline = [device newComputePipelineStateWithFunction:readFunction error:&error];
        printf("pipeline=read result=%s error=%s\n", readPipeline ? "non-NIL" : "NIL",
               error ? error.description.UTF8String : "none");
        if (!readPipeline) goto cleanup;
        error = nil;
        samplePipeline = [device newComputePipelineStateWithFunction:sampleFunction error:&error];
        printf("pipeline=sample result=%s error=%s\n", samplePipeline ? "non-NIL" : "NIL",
               error ? error.description.UTF8String : "none");
        if (!samplePipeline) goto cleanup;
        output = [device newBufferWithLength:outputLength options:MTLResourceStorageModeShared];
        if (!output || !output.contents || output.storageMode != MTLStorageModeShared) goto cleanup;
        BOOL readPassed = RunKernel(queue, readPipeline, texture, output, original, row, "read");
        BOOL samplePassed = RunKernel(queue, samplePipeline, texture, output, original, row, "sample");
        result = readPassed && samplePassed && atomic_load(&callbacks) == 0 ? 0 : 5;
cleanup:
        [samplePipeline release]; [readPipeline release];
        [sampleFunction release]; [readFunction release]; [library release];
        [output release]; [texture release]; [input release];
        [queue release]; [device release];
    }
    return result;
}

int main(int argc, char **argv) {
    if (argc < 3 || argc > 6) return 2;
    BOOL view = !strcmp(argv[1], "nocopy-buffer-view");
    BOOL managed = !strcmp(argv[2], "managed");
    BOOL office = NO, render = NO, officeShader = NO;
    for (int i = 3; i < argc; ++i) {
        if (!strcmp(argv[i], "--office-row-stride") && !office) office = YES;
        else if (!strcmp(argv[i], "--render") && !render) render = YES;
        else if (!strcmp(argv[i], "--office-shader") && !officeShader) officeShader = YES;
        else return 2;
    }
    if ((!view && strcmp(argv[1], "plain")) || (!managed && strcmp(argv[2], "shared"))) return 2;
    if (officeShader) render = YES;
#if TARGET_OS_IPHONE
    if (managed) { puts("managed storage is not an iOS-native control"); return 77; }
#endif
    alarm(10);
    setvbuf(stdout, NULL, _IONBF, 0);
    unsigned width = office ? 309 : 19, height = office ? 250 : 11;
    unsigned row = office ? 1248 : 256;
    size_t page = (size_t)getpagesize();
    if (page < 4096 || page > 65536 || (page & (page - 1))) return 2;
    size_t sourceLength = (row * height + page - 1) & ~(page - 1);
    unsigned outputRow = render ? ((width * 4 + 255) & ~255u) : width * 4;
    size_t outputLength = (outputRow * height + page - 1) & ~(page - 1);
    size_t textureUpperBound = ((((width * 4 + 255) & ~255u) * height + 16383) & ~16383u);
    size_t ownedBytes = sourceLength + outputLength + textureUpperBound;
    if (render && !view) {
        size_t uploadPhase = sourceLength + textureUpperBound;
        size_t renderPhase = 2 * textureUpperBound + outputLength;
        ownedBytes = MAX(uploadPhase, renderPhase);
    }
    // Count independent texture backing even when the buffer view aliases it.
    // Driver/compiler internal allocations are not included in pixel storage.
    if (ownedBytes > 1024 * 1024) return 2;
    uint8_t *original = mmap(NULL, sourceLength, PROT_READ | PROT_WRITE,
                            MAP_ANON | MAP_PRIVATE, -1, 0);
    if (original == MAP_FAILED) return 3;
    memset(original, 0x25, sourceLength);
    Provenance();
    printf("mode=%s requested=%s shape=%ux%u source-bpr=%u source-bytes=%u "
           "source-allocation=%zu output-allocation=%zu pixel-storage-bound=%zu\n",
           argv[1], argv[2], width, height, row, row * height,
           sourceLength, outputLength, ownedBytes);
    printf("pipeline-mode=%s office-shader=%d\n", render ? "render" : "compute", officeShader);
    int result = RunProbe(view, managed, render, officeShader, width, height, row,
                          &original, sourceLength, outputLength);
    printf("callbacks-after-drain=%u callback-mismatch=%u\n",
           atomic_load(&callbacks), atomic_load(&callbackMismatch));
    if (!result && (atomic_load(&callbacks) != (view ? 1u : 0u) ||
                    atomic_load(&callbackMismatch))) result = 6;
    if (original) munmap(original, sourceLength);
    printf("shader-%s-contract=%s\n", render ? "render" : "sampling", result ? "FAIL" : "PASS");
    return result;
}

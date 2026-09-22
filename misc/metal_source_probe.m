#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <objc/message.h>
#import <objc/runtime.h>
#include <dlfcn.h>
#include <math.h>
#include <ptrauth.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static BOOL ConfigureVertexDescriptorFromEnvironment(
        MTLVertexDescriptor *descriptor) {
    const char *attributeText =
        getenv("MACWS_METAL_PROBE_VERTEX_ATTRIBUTES");
    const char *layoutText = getenv("MACWS_METAL_PROBE_VERTEX_LAYOUTS");
    if ((!attributeText || !attributeText[0]) &&
        (!layoutText || !layoutText[0])) return YES;

    char *attributes = attributeText ? strdup(attributeText) : NULL;
    char *save = NULL;
    for (char *token = attributes
             ? strtok_r(attributes, ",", &save) : NULL;
         token; token = strtok_r(NULL, ",", &save)) {
        unsigned long index = 0, format = 0, offset = 0, buffer = 0;
        if (sscanf(token, "%lu:%lu:%lu:%lu", &index, &format,
                   &offset, &buffer) != 4 || index >= 31 || buffer >= 31) {
            fprintf(stderr,
                "METAL_SOURCE_PROBE invalidVertexAttribute=%s\n", token);
            free(attributes);
            return NO;
        }
        MTLVertexAttributeDescriptor *attribute =
            descriptor.attributes[index];
        attribute.format = (MTLVertexFormat)format;
        attribute.offset = offset;
        attribute.bufferIndex = buffer;
    }
    free(attributes);

    char *layouts = layoutText ? strdup(layoutText) : NULL;
    save = NULL;
    for (char *token = layouts ? strtok_r(layouts, ",", &save) : NULL;
         token; token = strtok_r(NULL, ",", &save)) {
        unsigned long index = 0, stride = 0, stepFunction = 0, stepRate = 0;
        if (sscanf(token, "%lu:%lu:%lu:%lu", &index, &stride,
                   &stepFunction, &stepRate) != 4 || index >= 31) {
            fprintf(stderr,
                "METAL_SOURCE_PROBE invalidVertexLayout=%s\n", token);
            free(layouts);
            return NO;
        }
        MTLVertexBufferLayoutDescriptor *layout = descriptor.layouts[index];
        layout.stride = stride;
        layout.stepFunction = (MTLVertexStepFunction)stepFunction;
        layout.stepRate = stepRate;
    }
    free(layouts);
    return YES;
}

static void DumpNewEventImplementation(id device) {
    if (!getenv("MACWS_METAL_PROBE_DUMP_EVENT_IMP")) return;

    SEL selector = sel_registerName("newEvent");
    Class concreteClass = object_getClass(device);
    Method resolved = class_getInstanceMethod(concreteClass, selector);
    IMP signedImplementation = resolved ? method_getImplementation(resolved) : NULL;
    const unsigned char *implementation = signedImplementation
        ? (const unsigned char *)ptrauth_strip(
              signedImplementation, ptrauth_key_function_pointer)
        : NULL;
    Class owner = Nil;
    for (Class candidate = concreteClass; candidate && !owner;
         candidate = class_getSuperclass(candidate)) {
        unsigned count = 0;
        Method *methods = class_copyMethodList(candidate, &count);
        for (unsigned index = 0; methods && index < count; index++) {
            if (method_getName(methods[index]) == selector) {
                owner = candidate;
                break;
            }
        }
        free(methods);
    }

    Dl_info imageInfo = {0};
    BOOL hasImage = implementation && dladdr(implementation, &imageInfo);
    fprintf(stderr,
            "METAL_SOURCE_PROBE newEventIMP class=%s owner=%s imp=%p "
            "image=%s imageBase=%p symbol=%s symbolBase=%p\n",
            class_getName(concreteClass),
            owner ? class_getName(owner) : "(none)", implementation,
            hasImage && imageInfo.dli_fname ? imageInfo.dli_fname : "(unknown)",
            hasImage ? imageInfo.dli_fbase : NULL,
            hasImage && imageInfo.dli_sname ? imageInfo.dli_sname : "(unknown)",
            hasImage ? imageInfo.dli_saddr : NULL);
    if (!implementation) return;

    // A bounded byte witness avoids debugger/shared-cache symbol dependence.
    // The executable mapping is readable on both tested arm64e systems.
    for (size_t offset = 0; offset < 192; offset += 16) {
        fprintf(stderr, "METAL_SOURCE_PROBE newEventCode +0x%03zx:", offset);
        for (size_t byte = 0; byte < 16; byte++)
            fprintf(stderr, " %02x", implementation[offset + byte]);
        fputc('\n', stderr);
    }

    Class eventClass = objc_getClass("IOGPUMTLEvent");
    SEL initSelector = sel_registerName("initWithDevice:");
    Method initMethod = eventClass
        ? class_getInstanceMethod(eventClass, initSelector) : NULL;
    IMP signedInit = initMethod ? method_getImplementation(initMethod) : NULL;
    const unsigned char *initImplementation = signedInit
        ? (const unsigned char *)ptrauth_strip(
              signedInit, ptrauth_key_function_pointer)
        : NULL;
    Dl_info initImageInfo = {0};
    BOOL hasInitImage = initImplementation &&
        dladdr(initImplementation, &initImageInfo);
    fprintf(stderr,
            "METAL_SOURCE_PROBE eventInitIMP class=%s imp=%p image=%s "
            "imageBase=%p symbol=%s symbolBase=%p\n",
            eventClass ? class_getName(eventClass) : "(none)",
            initImplementation,
            hasInitImage && initImageInfo.dli_fname
                ? initImageInfo.dli_fname : "(unknown)",
            hasInitImage ? initImageInfo.dli_fbase : NULL,
            hasInitImage && initImageInfo.dli_sname
                ? initImageInfo.dli_sname : "(unknown)",
            hasInitImage ? initImageInfo.dli_saddr : NULL);
    if (!initImplementation) return;
    for (size_t offset = 0; offset < 256; offset += 16) {
        fprintf(stderr, "METAL_SOURCE_PROBE eventInitCode +0x%03zx:", offset);
        for (size_t byte = 0; byte < 16; byte++)
            fprintf(stderr, " %02x", initImplementation[offset + byte]);
        fputc('\n', stderr);
    }

    // Event creation and destruction are adjacent external-method ABI
    // operations, but their selector deltas differ across the macOS 13.4 and
    // iOS 16.3 IOGPU builds.  Dump the concrete dealloc IMP as a second,
    // independent byte witness so the translation table can be derived from
    // both producers instead of inferred from selector numbering.
    SEL deallocSelector = sel_registerName("dealloc");
    Method deallocMethod = eventClass
        ? class_getInstanceMethod(eventClass, deallocSelector) : NULL;
    IMP signedDealloc = deallocMethod
        ? method_getImplementation(deallocMethod) : NULL;
    const unsigned char *deallocImplementation = signedDealloc
        ? (const unsigned char *)ptrauth_strip(
              signedDealloc, ptrauth_key_function_pointer)
        : NULL;
    Dl_info deallocImageInfo = {0};
    BOOL hasDeallocImage = deallocImplementation &&
        dladdr(deallocImplementation, &deallocImageInfo);
    fprintf(stderr,
            "METAL_SOURCE_PROBE eventDeallocIMP class=%s imp=%p image=%s "
            "imageBase=%p symbol=%s symbolBase=%p\n",
            eventClass ? class_getName(eventClass) : "(none)",
            deallocImplementation,
            hasDeallocImage && deallocImageInfo.dli_fname
                ? deallocImageInfo.dli_fname : "(unknown)",
            hasDeallocImage ? deallocImageInfo.dli_fbase : NULL,
            hasDeallocImage && deallocImageInfo.dli_sname
                ? deallocImageInfo.dli_sname : "(unknown)",
            hasDeallocImage ? deallocImageInfo.dli_saddr : NULL);
    if (!deallocImplementation) return;
    for (size_t offset = 0; offset < 160; offset += 16) {
        fprintf(stderr,
                "METAL_SOURCE_PROBE eventDeallocCode +0x%03zx:", offset);
        for (size_t byte = 0; byte < 16; byte++)
            fprintf(stderr, " %02x", deallocImplementation[offset + byte]);
        fputc('\n', stderr);
    }
}

static void DumpFunctionMethods(id function) {
    if (!function || !getenv("MACWS_METAL_PROBE_DUMP_FUNCTION_METHODS"))
        return;
    for (Class cls = object_getClass(function); cls;
         cls = class_getSuperclass(cls)) {
        unsigned int methodCount = 0;
        Method *methods = class_copyMethodList(cls, &methodCount);
        for (unsigned int methodIndex = 0;
             methods && methodIndex < methodCount; methodIndex++) {
            SEL selector = method_getName(methods[methodIndex]);
            const char *selectorName = sel_getName(selector);
            if (selectorName &&
                (strstr(selectorName, "pecial") ||
                 strstr(selectorName, "onstant") ||
                 strstr(selectorName, "unction") ||
                 strstr(selectorName, "ibrary"))) {
                fprintf(stderr,
                    "METAL_SOURCE_PROBE functionMethod class=%s "
                    "selector=%s types=%s imp=%p\n",
                    class_getName(cls), selectorName,
                    method_getTypeEncoding(methods[methodIndex]),
                    method_getImplementation(methods[methodIndex]));
            }
        }
        free(methods);
    }
}

static void DumpFunctionSpecializationRequirement(id function) {
    SEL selector = sel_registerName("needsFunctionConstantValues");
    BOOL needsValues = function &&
        [function respondsToSelector:selector]
        ? ((BOOL (*)(id, SEL))objc_msgSend)(function, selector) : NO;
    fprintf(stderr,
        "METAL_SOURCE_PROBE functionRequirement name=%s function=%p "
        "needsFunctionConstantValues=%d constants=%lu\n",
        function ? [[function name] UTF8String] : "(nil)", function,
        needsValues,
        (unsigned long)(function
            ? [[function functionConstantsDictionary] count] : 0));
}

static void StoreIdentityMatrix(uint8_t *bytes, NSUInteger offset) {
    float *matrix = (float *)(bytes + offset);
    memset(matrix, 0, 16 * sizeof(float));
    matrix[0] = matrix[5] = matrix[10] = matrix[15] = 1.0f;
}

static id<MTLTexture> NewProbeTextureBuffer(
        id<MTLDevice> device, MTLPixelFormat pixelFormat,
        NSUInteger width, NSUInteger bytesPerPixel,
        MTLTextureUsage usage, id<MTLBuffer> __strong *backingOut) {
    NSUInteger length = (width * bytesPerPixel + 255u) & ~255u;
    id<MTLBuffer> backing = [device newBufferWithLength:length
        options:MTLResourceStorageModeShared];
    if (!backing) return nil;
    MTLTextureDescriptor *descriptor =
        [MTLTextureDescriptor textureBufferDescriptorWithPixelFormat:
            pixelFormat width:width
            resourceOptions:MTLResourceStorageModeShared usage:usage];
    id<MTLTexture> texture = [backing newTextureWithDescriptor:descriptor
        offset:0 bytesPerRow:width * bytesPerPixel];
    if (texture && backingOut) *backingOut = backing;
    return texture;
}

// Execute the exact captured Stray capsule-shadow compute function once with
// a bounded 8x8 resource set.  This is a semantic witness for the generic
// half-float pipeline selector, not a game/performance benchmark.  The View
// and Globals layouts/locations below come from this function's actual AIR
// metadata; a different function is rejected by the caller before entry.
static BOOL RunStrayShadowHalfFloatWitness(
        id<MTLDevice> device, id<MTLComputePipelineState> pipeline) {
    const char *formatText = getenv("MACWS_METAL_PROBE_SHADOW_FORMAT");
    BOOL halfOutput = !formatText || strcmp(formatText, "rg32float") != 0;
    id<MTLBuffer> view = [device newBufferWithLength:2128
        options:MTLResourceStorageModeShared];
    id<MTLBuffer> globals = [device newBufferWithLength:64
        options:MTLResourceStorageModeShared];
    if (!view || !globals) return NO;
    uint8_t *viewBytes = view.contents;
    uint8_t *globalBytes = globals.contents;
    memset(viewBytes, 0, 2128);
    StoreIdentityMatrix(viewBytes, 256);
    StoreIdentityMatrix(viewBytes, 448);
    StoreIdentityMatrix(viewBytes, 576);
    StoreIdentityMatrix(viewBytes, 768);
    float *inverseZ = (float *)(viewBytes + 1040);
    inverseZ[0] = 0.0f;
    inverseZ[1] = 0.0f;
    inverseZ[2] = 1.0f;
    inverseZ[3] = -1.0e-8f;
    float *screenBias = (float *)(viewBytes + 1056);
    screenBias[0] = screenBias[1] = 1.0f;
    float *viewSize = (float *)(viewBytes + 2080);
    viewSize[0] = viewSize[1] = 8.0f;
    viewSize[2] = viewSize[3] = 0.125f;
    float *bufferSize = (float *)(viewBytes + 2112);
    memcpy(bufferSize, viewSize, 4 * sizeof(float));

    uint32_t *scissor = (uint32_t *)(globalBytes + 16);
    scissor[0] = scissor[1] = 0;
    scissor[2] = scissor[3] = 8;
    float *groups = (float *)(globalBytes + 32);
    groups[0] = groups[1] = 1.0f;
    *(uint32_t *)(globalBytes + 48) = 1;
    *(float *)(globalBytes + 52) = 20000.0f;
    uint32_t *tileDimensions = (uint32_t *)(globalBytes + 56);
    tileDimensions[0] = tileDimensions[1] = 8;

    id<MTLBuffer> countsBacking = nil;
    id<MTLBuffer> shapesBacking = nil;
    id<MTLBuffer> lightBacking = nil;
    id<MTLTexture> counts = NewProbeTextureBuffer(
        device, MTLPixelFormatR32Uint, 64, 4,
        MTLTextureUsageShaderRead | MTLTextureUsageShaderWrite,
        &countsBacking);
    id<MTLTexture> shapes = NewProbeTextureBuffer(
        device, MTLPixelFormatRGBA32Float, 16, 16,
        MTLTextureUsageShaderRead, &shapesBacking);
    id<MTLTexture> light = NewProbeTextureBuffer(
        device, MTLPixelFormatRGBA32Float, 16, 16,
        MTLTextureUsageShaderRead, &lightBacking);

    MTLTextureDescriptor *shadowDescriptor =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:
            (halfOutput ? MTLPixelFormatRG16Float : MTLPixelFormatRG32Float)
            width:8 height:8 mipmapped:NO];
    shadowDescriptor.storageMode = MTLStorageModeShared;
    shadowDescriptor.usage =
        MTLTextureUsageShaderRead | MTLTextureUsageShaderWrite;
    id<MTLTexture> shadow = [device newTextureWithDescriptor:shadowDescriptor];
    MTLTextureDescriptor *depthDescriptor =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:
            MTLPixelFormatR32Float width:8 height:8 mipmapped:NO];
    depthDescriptor.storageMode = MTLStorageModeShared;
    depthDescriptor.usage = MTLTextureUsageShaderRead;
    id<MTLTexture> depth = [device newTextureWithDescriptor:depthDescriptor];
    float depthBytes[64] = {0};
    [depth replaceRegion:MTLRegionMake2D(0, 0, 8, 8) mipmapLevel:0
               withBytes:depthBytes bytesPerRow:8 * sizeof(float)];

    MTLSamplerDescriptor *samplerDescriptor = [MTLSamplerDescriptor new];
    samplerDescriptor.minFilter = MTLSamplerMinMagFilterNearest;
    samplerDescriptor.magFilter = MTLSamplerMinMagFilterNearest;
    samplerDescriptor.sAddressMode = MTLSamplerAddressModeClampToEdge;
    samplerDescriptor.tAddressMode = MTLSamplerAddressModeClampToEdge;
    id<MTLSamplerState> sampler =
        [device newSamplerStateWithDescriptor:samplerDescriptor];
    if (!counts || !shapes || !light || !shadow || !depth || !sampler)
        return NO;

    id<MTLCommandQueue> queue = [device newCommandQueue];
    id<MTLCommandBuffer> commandBuffer = [queue commandBuffer];
    id<MTLComputeCommandEncoder> encoder =
        [commandBuffer computeCommandEncoder];
    [encoder setComputePipelineState:pipeline];
    [encoder setBuffer:view offset:0 atIndex:4];
    [encoder setBuffer:globals offset:0 atIndex:5];
    [encoder setTexture:counts atIndex:0];
    [encoder setTexture:shadow atIndex:1];
    [encoder setTexture:shapes atIndex:2];
    [encoder setTexture:light atIndex:3];
    [encoder setTexture:depth atIndex:4];
    [encoder setSamplerState:sampler atIndex:0];
    [encoder dispatchThreadgroups:MTLSizeMake(1, 1, 1)
        threadsPerThreadgroup:MTLSizeMake(8, 8, 1)];
    [encoder endEncoding];
    [commandBuffer commit];
    [commandBuffer waitUntilCompleted];

    if (!halfOutput) {
        float control[8 * 8 * 2] = {0};
        [shadow getBytes:control bytesPerRow:8 * 2 * sizeof(float)
                 fromRegion:MTLRegionMake2D(0, 0, 8, 8) mipmapLevel:0];
        fprintf(stderr,
            "METAL_SOURCE_PROBE shadowControlDispatch status=%lu "
            "errorDomain=%s errorCode=%ld description=%s userInfo=%s "
            "first=%g,%g\n",
            (unsigned long)commandBuffer.status,
            commandBuffer.error ? commandBuffer.error.domain.UTF8String
                                : "(nil)",
            (long)(commandBuffer.error ? commandBuffer.error.code : 0),
            commandBuffer.error
                ? commandBuffer.error.localizedDescription.UTF8String
                : "(nil)",
            commandBuffer.error
                ? commandBuffer.error.userInfo.description.UTF8String
                : "(nil)",
            control[0], control[1]);
        return commandBuffer.status == MTLCommandBufferStatusCompleted &&
               isfinite(control[0]) && isfinite(control[1]);
    }

    uint16_t result[8 * 8 * 2] = {0};
    [shadow getBytes:result bytesPerRow:8 * 2 * sizeof(uint16_t)
             fromRegion:MTLRegionMake2D(0, 0, 8, 8) mipmapLevel:0];
    NSUInteger finiteMaximum = 0, infinity = 0, other = 0;
    for (NSUInteger pixel = 0; pixel < 64; pixel++) {
        uint16_t green = result[pixel * 2 + 1];
        if (green == UINT16_C(0x7bff)) finiteMaximum++;
        else if (green == UINT16_C(0x7c00)) infinity++;
        else other++;
    }
    fprintf(stderr,
        "METAL_SOURCE_PROBE shadowHalfDispatch status=%lu errorDomain=%s "
        "errorCode=%ld description=%s userInfo=%s "
        "finiteMax=%lu infinity=%lu other=%lu "
        "first=0x%04x,0x%04x\n",
        (unsigned long)commandBuffer.status,
        commandBuffer.error ? commandBuffer.error.domain.UTF8String : "(nil)",
        (long)(commandBuffer.error ? commandBuffer.error.code : 0),
        commandBuffer.error
            ? commandBuffer.error.localizedDescription.UTF8String : "(nil)",
        commandBuffer.error
            ? commandBuffer.error.userInfo.description.UTF8String : "(nil)",
        (unsigned long)finiteMaximum, (unsigned long)infinity,
        (unsigned long)other, result[0], result[1]);
    return commandBuffer.status == MTLCommandBufferStatusCompleted &&
           infinity == 0 && finiteMaximum == 64;
}

// Minimal format-semantic witness for the generic half-float selector.  The
// source function is intentionally ordinary Metal source and its identity,
// function name and writable slot are discovered by the offline builder.
// Nothing in libmachook is keyed to this probe.
static BOOL RunHalfSaturationWitness(
        id<MTLDevice> device, id<MTLComputePipelineState> pipeline) {
    MTLTextureDescriptor *descriptor =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:
            MTLPixelFormatRGBA16Float width:1 height:1 mipmapped:NO];
    descriptor.storageMode = MTLStorageModeShared;
    descriptor.usage = MTLTextureUsageShaderWrite | MTLTextureUsageShaderRead;
    id<MTLTexture> output = [device newTextureWithDescriptor:descriptor];
    id<MTLCommandQueue> queue = [device newCommandQueue];
    id<MTLCommandBuffer> commandBuffer = [queue commandBuffer];
    id<MTLComputeCommandEncoder> encoder =
        [commandBuffer computeCommandEncoder];
    if (!output || !queue || !commandBuffer || !encoder) return NO;
    [encoder setComputePipelineState:pipeline];
    [encoder setTexture:output atIndex:0];
    [encoder dispatchThreads:MTLSizeMake(1, 1, 1)
        threadsPerThreadgroup:MTLSizeMake(1, 1, 1)];
    [encoder endEncoding];
    [commandBuffer commit];
    [commandBuffer waitUntilCompleted];

    uint16_t result[4] = {0};
    [output getBytes:result bytesPerRow:sizeof(result)
              fromRegion:MTLRegionMake2D(0, 0, 1, 1) mipmapLevel:0];
    fprintf(stderr,
        "METAL_SOURCE_PROBE halfSaturationDispatch status=%lu "
        "errorDomain=%s errorCode=%ld description=%s "
        "rgba=0x%04x,0x%04x,0x%04x,0x%04x\n",
        (unsigned long)commandBuffer.status,
        commandBuffer.error ? commandBuffer.error.domain.UTF8String : "(nil)",
        (long)(commandBuffer.error ? commandBuffer.error.code : 0),
        commandBuffer.error
            ? commandBuffer.error.localizedDescription.UTF8String : "(nil)",
        result[0], result[1], result[2], result[3]);
    return commandBuffer.status == MTLCommandBufferStatusCompleted &&
           result[0] == UINT16_C(0x7bff) &&
           result[1] == UINT16_C(0xfbff) &&
           result[2] == UINT16_C(0x3c00) && result[3] == 0;
}

// Minimal chroot-side witness for the MTLCompilerService bridge.  It avoids
// WindowServer, Electron and Aquarium so one source compile can be tested
// without a GPU-process restart loop or sustained compositor load.
int main(void) {
    @autoreleasepool {
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        fprintf(stderr, "METAL_SOURCE_PROBE device=%p class=%s name=%s\n",
                device,
                device ? object_getClassName(device) : "(nil)",
                device ? device.name.UTF8String : "(nil)");
        if (!device) return 2;

        DumpNewEventImplementation(device);

        // Load a precompiled library through the same macOS Metal client and
        // native iOS AGX device used by WindowServer.  This is a bounded
        // compatibility witness for cross-OS system metallibs: it never
        // substitutes a function or retries a failed pipeline.  The two
        // descriptors differ only in color attachment 0, matching the
        // runtime-confirmed CoreAnimation success (private format 550) and
        // Dock desktop-effect failure (RGBA8Unorm / value 70).
        const char *libraryPath = getenv("MACWS_METAL_PROBE_LIBRARY_PATH");
        if (libraryPath && libraryPath[0]) {
            NSError *libraryError = nil;
            NSURL *libraryURL = [NSURL fileURLWithPath:
                [NSString stringWithUTF8String:libraryPath]];
            id<MTLLibrary> library = nil;
            if (getenv("MACWS_METAL_PROBE_LIBRARY_DATA")) {
                NSData *libraryBytes = [NSData dataWithContentsOfURL:libraryURL
                                                              options:0
                                                                error:&libraryError];
                dispatch_data_t libraryData = libraryBytes
                    ? dispatch_data_create(
                          libraryBytes.bytes, libraryBytes.length,
                          dispatch_get_global_queue(
                              QOS_CLASS_DEFAULT, 0),
                          ^{ (void)libraryBytes; })
                    : NULL;
                if (libraryData) {
                    library = [device newLibraryWithData:libraryData
                                                   error:&libraryError];
                }
            } else {
                library = [device newLibraryWithURL:libraryURL
                                             error:&libraryError];
            }
            NSArray<NSString *> *functionNames = library.functionNames;
            if (getenv("MACWS_METAL_PROBE_DUMP_LIBRARY_METHODS")) {
                for (Class cls = object_getClass(library); cls;
                     cls = class_getSuperclass(cls)) {
                    unsigned int methodCount = 0;
                    Method *methods = class_copyMethodList(cls, &methodCount);
                    for (unsigned int methodIndex = 0;
                         methodIndex < methodCount; methodIndex++) {
                        SEL selector = method_getName(methods[methodIndex]);
                        const char *selectorName = sel_getName(selector);
                        if (selectorName && strstr(selectorName, "Function")) {
                            fprintf(stderr,
                                "METAL_SOURCE_PROBE libraryMethod class=%s "
                                "selector=%s types=%s imp=%p\n",
                                class_getName(cls), selectorName,
                                method_getTypeEncoding(methods[methodIndex]),
                                method_getImplementation(methods[methodIndex]));
                        }
                    }
                    free(methods);
                }
            }
            fprintf(stderr,
                    "METAL_SOURCE_PROBE precompiledPath=%s library=%p "
                    "class=%s functions=%lu errorDomain=%s errorCode=%ld "
                    "description=%s\n",
                    libraryPath, library,
                    library ? object_getClassName(library) : "(nil)",
                    (unsigned long)functionNames.count,
                    libraryError ? libraryError.domain.UTF8String : "(nil)",
                    (long)(libraryError ? libraryError.code : 0),
                    libraryError
                        ? libraryError.localizedDescription.UTF8String : "(nil)");
            if (!library) return 7;

            // Descriptor-based compute replay for one byte-exact external
            // MTLB.  Stray's UE4 path fails at this public API only after the
            // library has loaded, so a library-load witness alone is not
            // sufficient.  Forward the supplied function unchanged and
            // report the real AGX result/error; never retry or substitute.
            const char *computeFunctionText =
                getenv("MACWS_METAL_PROBE_COMPUTE_FUNCTION");
            if (computeFunctionText && computeFunctionText[0]) {
                NSString *computeFunctionName =
                    [NSString stringWithUTF8String:computeFunctionText];
                id<MTLFunction> computeFunction =
                    [library newFunctionWithName:computeFunctionName];
                MTLComputePipelineDescriptor *computeDescriptor =
                    [MTLComputePipelineDescriptor new];
                computeDescriptor.label = computeFunctionName;
                computeDescriptor.computeFunction = computeFunction;
                MTLAutoreleasedComputePipelineReflection computeReflection =
                    nil;
                NSError *computeError = nil;
                id<MTLComputePipelineState> computePipeline = computeFunction
                    ? [device
                        newComputePipelineStateWithDescriptor:computeDescriptor
                        options:MTLPipelineOptionNone
                        reflection:&computeReflection
                        error:&computeError]
                    : nil;
                fprintf(stderr,
                        "METAL_SOURCE_PROBE computeReplay function=%s/%p "
                        "pipeline=%p class=%s reflection=%p errorDomain=%s "
                        "errorCode=%ld description=%s userInfo=%s\n",
                        computeFunctionText, computeFunction,
                        computePipeline,
                        computePipeline
                            ? object_getClassName(computePipeline) : "(nil)",
                        computeReflection,
                        computeError ? computeError.domain.UTF8String : "(nil)",
                        (long)(computeError ? computeError.code : 0),
                        computeError
                            ? computeError.localizedDescription.UTF8String
                            : "(nil)",
                        computeError
                            ? computeError.userInfo.description.UTF8String
                            : "(nil)");
                if (computePipeline &&
                    getenv("MACWS_METAL_PROBE_STRAY_SHADOW_DISPATCH") &&
                    strcmp(computeFunctionText,
                           "Main_000054ef_c016cab8") == 0) {
                    return RunStrayShadowHalfFloatWitness(
                        device, computePipeline) ? 0 : 14;
                }
                if (computePipeline &&
                    getenv("MACWS_METAL_PROBE_HALF_SATURATION_DISPATCH")) {
                    return RunHalfSaturationWitness(
                        device, computePipeline) ? 0 : 15;
                }
                return computePipeline ? 0 : 13;
            }

            // Generic precompiled-pipeline replay.  Real clients such as
            // Unreal load the vertex and fragment AIR from distinct MTLB
            // blobs, so the older QuartzCore-only probe below cannot replay
            // their descriptors.  Keep this entirely descriptor-driven: it
            // reports the concrete driver's result and never substitutes a
            // function, pipeline or error.
            const char *vertexFunctionText =
                getenv("MACWS_METAL_PROBE_VERTEX_FUNCTION");
            if (vertexFunctionText && vertexFunctionText[0]) {
                const char *fragmentLibraryPath =
                    getenv("MACWS_METAL_PROBE_FRAGMENT_LIBRARY_PATH");
                id<MTLLibrary> fragmentLibrary = library;
                NSError *fragmentLibraryError = nil;
                if (fragmentLibraryPath && fragmentLibraryPath[0] &&
                    strcmp(fragmentLibraryPath, libraryPath) != 0) {
                    NSURL *fragmentLibraryURL = [NSURL fileURLWithPath:
                        [NSString stringWithUTF8String:fragmentLibraryPath]];
                    fragmentLibrary = [device
                        newLibraryWithURL:fragmentLibraryURL
                                   error:&fragmentLibraryError];
                }

                const char *fragmentFunctionText =
                    getenv("MACWS_METAL_PROBE_FRAGMENT_FUNCTION");
                NSString *vertexFunctionName =
                    [NSString stringWithUTF8String:vertexFunctionText];
                NSString *fragmentFunctionName =
                    fragmentFunctionText && fragmentFunctionText[0]
                        ? [NSString stringWithUTF8String:fragmentFunctionText]
                        : nil;
                id<MTLFunction> replayVertex =
                    [library newFunctionWithName:vertexFunctionName];
                id<MTLFunction> replayFragment =
                    fragmentFunctionName && fragmentLibrary
                        ? [fragmentLibrary
                            newFunctionWithName:fragmentFunctionName]
                        : nil;
                // Diagnostic-only specialization: Office's Metal2D shaders
                // declare a hasStencil function constant. A direct pipeline
                // build from their unspecialized functions is invalid even
                // when the library itself loads correctly. Replay the real
                // public specialization API with zero-valued constants so
                // this probe can distinguish that contract from an AGX
                // compiler or render-pipeline failure.
                if (getenv("MACWS_METAL_PROBE_SPECIALIZE_FUNCTIONS")) {
                    id<MTLFunction> functions[] = {
                        replayVertex, replayFragment
                    };
                    id<MTLLibrary> libraries[] = {
                        library, fragmentLibrary
                    };
                    NSString *names[] = {
                        vertexFunctionName, fragmentFunctionName
                    };
                    for (NSUInteger index = 0; index < 2; index++) {
                        if (!functions[index] || !names[index]) continue;
                        NSDictionary<NSString *, MTLFunctionConstant *>
                            *constants =
                                functions[index].functionConstantsDictionary;
                        if (!constants.count) continue;
                        MTLFunctionConstantValues *values =
                            [MTLFunctionConstantValues new];
                        uint8_t zeroValue[256] = {0};
                        for (NSString *constantName in constants) {
                            MTLFunctionConstant *constant =
                                constants[constantName];
                            [values setConstantValue:zeroValue
                                               type:constant.type
                                           withName:constantName];
                        }
                        NSError *specializationError = nil;
                        functions[index] = [libraries[index]
                            newFunctionWithName:names[index]
                                   constantValues:values
                                            error:&specializationError];
                        fprintf(stderr,
                            "METAL_SOURCE_PROBE replaySpecialization "
                            "name=%s constants=%lu function=%p "
                            "errorDomain=%s errorCode=%ld description=%s\n",
                            names[index].UTF8String,
                            (unsigned long)constants.count,
                            functions[index],
                            specializationError
                                ? specializationError.domain.UTF8String
                                : "(nil)",
                            (long)(specializationError
                                ? specializationError.code : 0),
                            specializationError
                                ? specializationError.localizedDescription
                                    .UTF8String : "(nil)");
                    }
                    replayVertex = functions[0];
                    replayFragment = functions[1];
                }
                fprintf(stderr,
                    "METAL_SOURCE_PROBE replayFunctions vertexLibrary=%p "
                        "fragmentPath=%s fragmentLibrary=%p vertex=%s/%p "
                        "fragment=%s/%p fragmentLibraryErrorDomain=%s "
                        "fragmentLibraryErrorCode=%ld description=%s\n",
                        library,
                        fragmentLibraryPath ? fragmentLibraryPath : "(same)",
                        fragmentLibrary,
                        vertexFunctionText, replayVertex,
                        fragmentFunctionText ? fragmentFunctionText : "(nil)",
                        replayFragment,
                        fragmentLibraryError
                            ? fragmentLibraryError.domain.UTF8String : "(nil)",
                        (long)(fragmentLibraryError
                            ? fragmentLibraryError.code : 0),
                        fragmentLibraryError
                            ? fragmentLibraryError.localizedDescription.UTF8String
                            : "(nil)");
                if (!replayVertex ||
                    (fragmentFunctionName && !replayFragment)) return 11;

                const char *colorText =
                    getenv("MACWS_METAL_PROBE_COLOR0_FORMAT");
                const char *colorFormatsText =
                    getenv("MACWS_METAL_PROBE_COLOR_FORMATS");
                const char *depthText =
                    getenv("MACWS_METAL_PROBE_DEPTH_FORMAT");
                const char *stencilText =
                    getenv("MACWS_METAL_PROBE_STENCIL_FORMAT");
                const char *sampleText =
                    getenv("MACWS_METAL_PROBE_SAMPLE_COUNT");
                NSUInteger colorFormat = colorText
                    ? (NSUInteger)strtoull(colorText, NULL, 0) : 0;
                NSUInteger depthFormat = depthText
                    ? (NSUInteger)strtoull(depthText, NULL, 0) : 0;
                NSUInteger stencilFormat = stencilText
                    ? (NSUInteger)strtoull(stencilText, NULL, 0) : 0;
                NSUInteger sampleCount = sampleText
                    ? (NSUInteger)strtoull(sampleText, NULL, 0) : 1;
                if (sampleCount == 0) sampleCount = 1;

                MTLRenderPipelineDescriptor *replayDescriptor =
                    [MTLRenderPipelineDescriptor new];
                replayDescriptor.label = @"MacWS exact pipeline replay";
                replayDescriptor.vertexFunction = replayVertex;
                replayDescriptor.fragmentFunction = replayFragment;
                MTLVertexDescriptor *vertexDescriptor =
                    [MTLVertexDescriptor vertexDescriptor];
                if (!ConfigureVertexDescriptorFromEnvironment(
                        vertexDescriptor)) return 13;
                replayDescriptor.vertexDescriptor = vertexDescriptor;
                replayDescriptor.colorAttachments[0].pixelFormat =
                    (MTLPixelFormat)colorFormat;
                if (colorFormatsText && colorFormatsText[0]) {
                    char *colorFormats = strdup(colorFormatsText);
                    char *colorSave = NULL;
                    for (char *token = strtok_r(
                             colorFormats, ",", &colorSave);
                         token;
                         token = strtok_r(NULL, ",", &colorSave)) {
                        unsigned long index = 0, format = 0;
                        if (sscanf(token, "%lu:%lu", &index, &format) != 2 ||
                            index >= 8) {
                            fprintf(stderr,
                                "METAL_SOURCE_PROBE invalidColorFormat=%s\n",
                                token);
                            free(colorFormats);
                            return 13;
                        }
                        replayDescriptor.colorAttachments[index].pixelFormat =
                            (MTLPixelFormat)format;
                    }
                    free(colorFormats);
                }
                replayDescriptor.depthAttachmentPixelFormat =
                    (MTLPixelFormat)depthFormat;
                replayDescriptor.stencilAttachmentPixelFormat =
                    (MTLPixelFormat)stencilFormat;
                replayDescriptor.sampleCount = sampleCount;
                const char *pipelinePauseText =
                    getenv("MACWS_METAL_PROBE_PIPELINE_PAUSE_SECONDS");
                if (pipelinePauseText) {
                    unsigned long pauseSeconds =
                        strtoul(pipelinePauseText, NULL, 10);
                    if (pauseSeconds > 120) pauseSeconds = 120;
                    if (pauseSeconds) {
                        fprintf(stderr,
                            "METAL_SOURCE_PROBE pipelinePauseSeconds=%lu "
                            "pid=%d\n",
                            pauseSeconds, getpid());
                        sleep((unsigned)pauseSeconds);
                    }
                }
                const char *repeatText =
                    getenv("MACWS_METAL_PROBE_PIPELINE_REPEAT");
                unsigned long repeatCount = repeatText
                    ? strtoul(repeatText, NULL, 10) : 1;
                if (repeatCount == 0) repeatCount = 1;
                if (repeatCount > 8) repeatCount = 8;
                const char *betweenPauseText = getenv(
                    "MACWS_METAL_PROBE_BETWEEN_PIPELINE_PAUSE_SECONDS");
                unsigned long betweenPauseSeconds = betweenPauseText
                    ? strtoul(betweenPauseText, NULL, 10) : 0;
                if (betweenPauseSeconds > 300)
                    betweenPauseSeconds = 300;
                BOOL allReplayPipelinesBuilt = YES;
                for (unsigned long replayIndex = 0;
                     replayIndex < repeatCount; replayIndex++) {
                    NSError *replayError = nil;
                    id<MTLRenderPipelineState> replayPipeline = [device
                        newRenderPipelineStateWithDescriptor:replayDescriptor
                                                       error:&replayError];
                    fprintf(stderr,
                            "METAL_SOURCE_PROBE replayPipeline index=%lu/%lu "
                            "color0=%lu depth=%lu stencil=%lu samples=%lu "
                            "pipeline=%p class=%s errorDomain=%s "
                            "errorCode=%ld description=%s userInfo=%s\n",
                            replayIndex + 1, repeatCount,
                            (unsigned long)colorFormat,
                            (unsigned long)depthFormat,
                            (unsigned long)stencilFormat,
                            (unsigned long)sampleCount,
                            replayPipeline,
                            replayPipeline
                                ? object_getClassName(replayPipeline) : "(nil)",
                            replayError
                                ? replayError.domain.UTF8String : "(nil)",
                            (long)(replayError ? replayError.code : 0),
                            replayError
                                ? replayError.localizedDescription.UTF8String
                                : "(nil)",
                            replayError
                                ? replayError.userInfo.description.UTF8String
                                : "(nil)");
                    if (!replayPipeline) allReplayPipelinesBuilt = NO;
                    if (replayIndex + 1 < repeatCount &&
                        betweenPauseSeconds) {
                        fprintf(stderr,
                            "METAL_SOURCE_PROBE betweenPipelinePauseSeconds="
                            "%lu pid=%d completed=%lu/%lu\n",
                            betweenPauseSeconds, getpid(), replayIndex + 1,
                            repeatCount);
                        sleep((unsigned)betweenPauseSeconds);
                    }
                }
                return allReplayPipelinesBuilt ? 0 : 12;
            }
            BOOL dumpFunctionNames =
                getenv("MACWS_METAL_PROBE_DUMP_FUNCTIONS") != NULL;
            BOOL dumpFunctionRequirements =
                getenv("MACWS_METAL_PROBE_DUMP_FUNCTION_REQUIREMENTS") !=
                NULL;
            if (dumpFunctionNames || dumpFunctionRequirements) {
                for (NSString *functionName in functionNames) {
                    if (dumpFunctionNames) {
                        fprintf(stderr,
                                "METAL_SOURCE_PROBE libraryFunction name=%s\n",
                                functionName.UTF8String);
                    }
                    if (dumpFunctionRequirements) {
                        DumpFunctionSpecializationRequirement(
                            [library newFunctionWithName:functionName]);
                    }
                }
            }

            id<MTLFunction> vertex =
                [library newFunctionWithName:@"fixed_vert_lph_spc"];
            id<MTLFunction> fragment =
                [library newFunctionWithName:@"fixed_frag_lph_cpf"];
            id<MTLFunction> genericVertex =
                [library newFunctionWithName:@"fixed_vert_lph_gen"];
            DumpFunctionMethods(vertex);
            DumpFunctionSpecializationRequirement(vertex);
            DumpFunctionSpecializationRequirement(genericVertex);
            DumpFunctionSpecializationRequirement(fragment);
            fprintf(stderr,
                    "METAL_SOURCE_PROBE precompiledFunctions vertex=%p "
                    "fragment=%p hasVertexName=%d hasFragmentName=%d\n",
                    vertex, fragment,
                    [functionNames containsObject:@"fixed_vert_lph_spc"],
                    [functionNames containsObject:@"fixed_frag_lph_cpf"]);
            if (!vertex || !fragment) return 8;

            if (!getenv("MACWS_METAL_PROBE_PIPELINE")) return 0;

            id<MTLFunction> genericFunctions[] = { vertex, fragment };
            NSString *genericNames[] = {
                @"fixed_vert_lph_spc", @"fixed_frag_lph_cpf"
            };
            id<MTLFunction> specializedFunctions[] = { nil, nil };
            for (NSUInteger functionIndex = 0; functionIndex < 2;
                 functionIndex++) {
                NSDictionary<NSString *, MTLFunctionConstant *> *constants =
                    genericFunctions[functionIndex].functionConstantsDictionary;
                MTLFunctionConstantValues *values =
                    [MTLFunctionConstantValues new];
                uint8_t zeroValue[256] = {0};
                for (NSString *constantName in constants) {
                    MTLFunctionConstant *constant = constants[constantName];
                    [values setConstantValue:zeroValue type:constant.type
                                    withName:constantName];
                }
                NSError *specializationError = nil;
                specializedFunctions[functionIndex] =
                    [library newFunctionWithName:genericNames[functionIndex]
                                   constantValues:values
                                            error:&specializationError];
                fprintf(stderr,
                        "METAL_SOURCE_PROBE specializedFunction name=%s "
                        "constants=%lu function=%p class=%s errorDomain=%s "
                        "errorCode=%ld description=%s\n",
                        genericNames[functionIndex].UTF8String,
                        (unsigned long)constants.count,
                        specializedFunctions[functionIndex],
                        specializedFunctions[functionIndex]
                            ? object_getClassName(
                                specializedFunctions[functionIndex])
                            : "(nil)",
                        specializationError
                            ? specializationError.domain.UTF8String : "(nil)",
                        (long)(specializationError
                            ? specializationError.code : 0),
                        specializationError
                            ? specializationError.localizedDescription.UTF8String
                            : "(nil)");
            }
            vertex = specializedFunctions[0];
            fragment = specializedFunctions[1];
            if (!vertex || !fragment) return 9;

            const NSUInteger color0Formats[] = { 550, 70 };
            BOOL allPipelinesBuilt = YES;
            for (NSUInteger index = 0;
                 index < sizeof(color0Formats) / sizeof(color0Formats[0]);
                 index++) {
                MTLRenderPipelineDescriptor *descriptor =
                    [MTLRenderPipelineDescriptor new];
                descriptor.label = [NSString stringWithFormat:
                    @"MacWS precompiled probe color0=%lu",
                    (unsigned long)color0Formats[index]];
                descriptor.vertexFunction = vertex;
                descriptor.fragmentFunction = fragment;
                descriptor.colorAttachments[0].pixelFormat =
                    color0Formats[index];
                descriptor.colorAttachments[1].pixelFormat =
                    MTLPixelFormatRGBA16Float;
                NSError *pipelineError = nil;
                id<MTLRenderPipelineState> pipeline =
                    [device newRenderPipelineStateWithDescriptor:descriptor
                                                           error:&pipelineError];
                fprintf(stderr,
                        "METAL_SOURCE_PROBE precompiledPipeline color0=%lu "
                        "color1=%lu pipeline=%p class=%s errorDomain=%s "
                        "errorCode=%ld description=%s\n",
                        (unsigned long)color0Formats[index],
                        (unsigned long)MTLPixelFormatRGBA16Float,
                        pipeline,
                        pipeline ? object_getClassName(pipeline) : "(nil)",
                        pipelineError ? pipelineError.domain.UTF8String : "(nil)",
                        (long)(pipelineError ? pipelineError.code : 0),
                        pipelineError
                            ? pipelineError.localizedDescription.UTF8String
                            : "(nil)");
                if (!pipeline) allPipelinesBuilt = NO;
            }
            return allPipelinesBuilt ? 0 : 10;
        }

        // Optional bounded LLDB attach window.  Pause only after Metal and the
        // concrete AGX/IOGPU classes are loaded, but before either event
        // constructor is called.  This is a read-only diagnostic aid; normal
        // probe invocations do not set the variable and remain unchanged.
        const char *pauseText = getenv("MACWS_METAL_PROBE_PAUSE_SECONDS");
        if (pauseText) {
            unsigned long pauseSeconds = strtoul(pauseText, NULL, 10);
            if (pauseSeconds > 120) pauseSeconds = 120;
            if (pauseSeconds) {
                fprintf(stderr,
                        "METAL_SOURCE_PROBE lldbPauseSeconds=%lu pid=%d\n",
                        pauseSeconds, getpid());
                sleep((unsigned)pauseSeconds);
            }
        }

        // Chromium/ANGLE uses MTLSharedEvent for EGL fences before the
        // Aquarium draw loop starts.  Keep this as an independent witness:
        // a nil event must be diagnosed at creation time instead of being
        // handed to -encodeSignalEvent:value:, where AGX's superclass raises
        // an NSArray nil-insertion exception.
        if (!getenv("MACWS_METAL_PROBE_SOURCE_ONLY")) {
            id<MTLSharedEvent> sharedEvent = nil;
            if ([device respondsToSelector:@selector(newSharedEvent)])
                sharedEvent = [device newSharedEvent];
            fprintf(stderr,
                    "METAL_SOURCE_PROBE sharedEvent=%p class=%s signaledValue=%llu\n",
                    sharedEvent,
                    sharedEvent ? object_getClassName(sharedEvent) : "(nil)",
                    (unsigned long long)(sharedEvent
                        ? sharedEvent.signaledValue : 0));

        // Chromium 148's actual libGLESv2 binary calls the older private
        // -newEvent selector (RE-confirmed at arm64 file offset 0x2db888),
        // not public -newSharedEvent.  Compare the two on the same device.
            SEL newEventSelector = sel_registerName("newEvent");
            BOOL respondsToNewEvent =
                [device respondsToSelector:newEventSelector];
            id legacyEvent = respondsToNewEvent
                ? ((id (*)(id, SEL))objc_msgSend)(device, newEventSelector)
                : nil;
            fprintf(stderr,
                    "METAL_SOURCE_PROBE newEvent responds=%d event=%p class=%s\n",
                    respondsToNewEvent, legacyEvent,
                    legacyEvent ? object_getClassName(legacyEvent) : "(nil)");

            // Cross-version control for Stray's forward cross-queue event
            // dependency.  The paired iOS-native probe commits this exact
            // topology in well under one millisecond.  Exercise it through
            // the chroot's unmodified macOS Metal/IOGPU producer so we can
            // distinguish queue-submit translation from game workload.
            const char *eventQueuesMode =
                getenv("MACWS_METAL_PROBE_EVENT_QUEUES");
            if (eventQueuesMode &&
                strcmp(eventQueuesMode, "cycle") == 0) {
                id eventA0 = respondsToNewEvent
                    ? ((id (*)(id, SEL))objc_msgSend)(device,
                                                      newEventSelector)
                    : nil;
                id eventB0 = respondsToNewEvent
                    ? ((id (*)(id, SEL))objc_msgSend)(device,
                                                      newEventSelector)
                    : nil;
                id eventA1 = respondsToNewEvent
                    ? ((id (*)(id, SEL))objc_msgSend)(device,
                                                      newEventSelector)
                    : nil;
                id eventB1 = respondsToNewEvent
                    ? ((id (*)(id, SEL))objc_msgSend)(device,
                                                      newEventSelector)
                    : nil;
                SEL setBarrierSelector = sel_registerName(
                    "setEnableBarrier:");
                if (eventB1 &&
                    [eventB1 respondsToSelector:setBarrierSelector]) {
                    IMP method = [eventB1
                        methodForSelector:setBarrierSelector];
                    ((void (*)(id, SEL, BOOL))method)(
                        eventB1, setBarrierSelector, NO);
                }
                id<MTLCommandQueue> queueA = [device newCommandQueue];
                id<MTLCommandQueue> queueB = [device newCommandQueue];
                id<MTLCommandBuffer> bufferA = [queueA commandBuffer];
                id<MTLCommandBuffer> bufferB = [queueB commandBuffer];
                BOOL setupOK = eventA0 && eventB0 && eventA1 && eventB1 &&
                    queueA && queueB && bufferA && bufferB;
                struct timespec before = {0}, after = {0};
                if (setupOK) {
                    [bufferA encodeSignalEvent:(id<MTLEvent>)eventA0
                                         value:32];
                    [bufferA encodeWaitForEvent:(id<MTLEvent>)eventB0
                                           value:64];
                    [bufferA encodeSignalEvent:(id<MTLEvent>)eventA1
                                         value:32];
                    [bufferA encodeWaitForEvent:(id<MTLEvent>)eventB1
                                           value:32];
                    [bufferB encodeWaitForEvent:(id<MTLEvent>)eventA0
                                           value:32];
                    [bufferB encodeSignalEvent:(id<MTLEvent>)eventB0
                                         value:64];
                    [bufferB encodeWaitForEvent:(id<MTLEvent>)eventA1
                                           value:32];
                    [bufferB encodeSignalEvent:(id<MTLEvent>)eventB1
                                         value:32];
                    clock_gettime(CLOCK_MONOTONIC, &before);
                    [bufferA commit];
                    [bufferB commit];
                    [bufferA waitUntilCompleted];
                    [bufferB waitUntilCompleted];
                    clock_gettime(CLOCK_MONOTONIC, &after);
                }
                double wallMilliseconds =
                    (double)(after.tv_sec - before.tv_sec) * 1000.0 +
                    (double)(after.tv_nsec - before.tv_nsec) / 1000000.0;
                BOOL cycleOK = setupOK &&
                    bufferA.status == MTLCommandBufferStatusCompleted &&
                    bufferB.status == MTLCommandBufferStatusCompleted &&
                    bufferA.error == nil && bufferB.error == nil;
                fprintf(stderr,
                    "METAL_SOURCE_PROBE eventCycle setup=%s result=%s "
                    "aStatus=%ld bStatus=%ld wallMilliseconds=%.3f "
                    "aGPU=%.6f..%.6f bGPU=%.6f..%.6f "
                    "aError=%s bError=%s\n",
                    setupOK ? "YES" : "NO", cycleOK ? "PASS" : "FAIL",
                    (long)bufferA.status, (long)bufferB.status,
                    wallMilliseconds,
                    bufferA.GPUStartTime, bufferA.GPUEndTime,
                    bufferB.GPUStartTime, bufferB.GPUEndTime,
                    bufferA.error.localizedDescription.UTF8String
                        ?: "(nil)",
                    bufferB.error.localizedDescription.UTF8String
                        ?: "(nil)");
                return cycleOK ? 0 : 8;
            }
            if (eventQueuesMode) {
                id secondLegacyEvent = respondsToNewEvent
                    ? ((id (*)(id, SEL))objc_msgSend)(device,
                                                      newEventSelector)
                    : nil;
                SEL setBarrierSelector = sel_registerName(
                    "setEnableBarrier:");
                if (secondLegacyEvent &&
                    [secondLegacyEvent respondsToSelector:setBarrierSelector]) {
                    IMP method = [secondLegacyEvent
                        methodForSelector:setBarrierSelector];
                    ((void (*)(id, SEL, BOOL))method)(
                        secondLegacyEvent, setBarrierSelector, NO);
                }
                id<MTLCommandQueue> waitQueue = [device newCommandQueue];
                id<MTLCommandQueue> signalQueue = [device newCommandQueue];
                id<MTLCommandBuffer> waitBuffer = [waitQueue commandBuffer];
                id<MTLCommandBuffer> signalBuffer =
                    [signalQueue commandBuffer];
                BOOL setupOK = legacyEvent && secondLegacyEvent &&
                    waitQueue && signalQueue && waitBuffer && signalBuffer;
                struct timespec before = {0}, after = {0};
                if (setupOK) {
                    [waitBuffer encodeWaitForEvent:(id<MTLEvent>)legacyEvent
                                             value:64];
                    [waitBuffer
                        encodeWaitForEvent:(id<MTLEvent>)secondLegacyEvent
                                    value:32];
                    [signalBuffer
                        encodeSignalEvent:(id<MTLEvent>)legacyEvent value:64];
                    [signalBuffer
                        encodeSignalEvent:(id<MTLEvent>)secondLegacyEvent
                                    value:32];
                    clock_gettime(CLOCK_MONOTONIC, &before);
                    [waitBuffer commit];
                    [signalBuffer commit];
                    [waitBuffer waitUntilCompleted];
                    [signalBuffer waitUntilCompleted];
                    clock_gettime(CLOCK_MONOTONIC, &after);
                }
                double wallMilliseconds =
                    (double)(after.tv_sec - before.tv_sec) * 1000.0 +
                    (double)(after.tv_nsec - before.tv_nsec) / 1000000.0;
                BOOL queuesOK = setupOK &&
                    waitBuffer.status == MTLCommandBufferStatusCompleted &&
                    signalBuffer.status == MTLCommandBufferStatusCompleted &&
                    waitBuffer.error == nil && signalBuffer.error == nil;
                fprintf(stderr,
                    "METAL_SOURCE_PROBE eventQueues setup=%s result=%s "
                    "waitStatus=%ld signalStatus=%ld wallMilliseconds=%.3f "
                    "waitGPU=%.6f..%.6f signalGPU=%.6f..%.6f "
                    "waitError=%s signalError=%s\n",
                    setupOK ? "YES" : "NO", queuesOK ? "PASS" : "FAIL",
                    (long)waitBuffer.status, (long)signalBuffer.status,
                    wallMilliseconds,
                    waitBuffer.GPUStartTime, waitBuffer.GPUEndTime,
                    signalBuffer.GPUStartTime, signalBuffer.GPUEndTime,
                    waitBuffer.error.localizedDescription.UTF8String
                        ?: "(nil)",
                    signalBuffer.error.localizedDescription.UTF8String
                        ?: "(nil)");
                return queuesOK ? 0 : 7;
            }

            BOOL sharedEventCommandsOK = NO;
            if (sharedEvent) {
                id<MTLCommandQueue> queue = [device newCommandQueue];
                id<MTLCommandBuffer> signalBuffer = [queue commandBuffer];
                id<MTLCommandBuffer> waitBuffer = [queue commandBuffer];
                @try {
                    [signalBuffer encodeSignalEvent:sharedEvent value:1];
                    [waitBuffer encodeWaitForEvent:sharedEvent value:1];
                    [signalBuffer commit];
                    [waitBuffer commit];
                    [waitBuffer waitUntilCompleted];
                    fprintf(stderr,
                            "METAL_SOURCE_PROBE sharedEventCommands "
                            "signalStatus=%ld waitStatus=%ld eventValue=%llu "
                            "signalError=%s waitError=%s\n",
                            (long)signalBuffer.status,
                            (long)waitBuffer.status,
                            (unsigned long long)sharedEvent.signaledValue,
                            signalBuffer.error
                                ? signalBuffer.error.localizedDescription.UTF8String
                                : "(nil)",
                            waitBuffer.error
                                ? waitBuffer.error.localizedDescription.UTF8String
                                : "(nil)");
                    sharedEventCommandsOK =
                        signalBuffer.status ==
                            MTLCommandBufferStatusCompleted &&
                        waitBuffer.status ==
                            MTLCommandBufferStatusCompleted &&
                        sharedEvent.signaledValue == 1 &&
                        signalBuffer.error == nil && waitBuffer.error == nil;
                } @catch (NSException *exception) {
                    fprintf(stderr,
                            "METAL_SOURCE_PROBE sharedEventCommand exception=%s "
                            "reason=%s\n",
                            exception.name.UTF8String,
                            exception.reason.UTF8String);
                }
            }

        // The iOS-native event ABI witness must not depend on the separate
        // chroot MTLCompilerService experiment below.  In particular, a
        // native iOS process has no reason to request or validate a macOS AIR
        // target.  This opt-in endpoint gives the event probe a strict exit
        // status after both the public shared-event commands and the private
        // -newEvent constructor have been exercised.
            if (getenv("MACWS_METAL_PROBE_EVENT_ONLY")) {
                BOOL eventOnlyOK = legacyEvent != nil &&
                    sharedEventCommandsOK;
                fprintf(stderr,
                        "METAL_SOURCE_PROBE eventOnlyResult=%s\n",
                        eventOnlyOK ? "PASS" : "FAIL");
            // Force ARC to run both event destructors while libmachook's
            // IOKit interposer and stderr are still live.  Returning from
            // main can let process teardown reclaim VM mappings before the
            // external-method result is observable in the probe log.
#if __has_feature(objc_arc)
                legacyEvent = nil;
                sharedEvent = nil;
#else
                [legacyEvent release];
                [sharedEvent release];
#endif
                fprintf(stderr,
                        "METAL_SOURCE_PROBE eventOnlyReleased=YES\n");
                return eventOnlyOK ? 0 : 5;
            }
        }

        // Include a per-process comment so each invocation proves a fresh XPC
        // compilation instead of accepting a persistent source-cache hit.
        // An optional source file lets the same bounded probe replay an exact
        // MSL program captured from a real client (for example ANGLE's YUV
        // conversion shader) without starting Electron or WindowServer.  The
        // compiler and Metal loader still consume the unmodified source after
        // the harmless cache-busting comment; no result is synthesized.
        const char *sourcePath = getenv("MACWS_METAL_PROBE_SOURCE_PATH");
        const char *functionText = getenv("MACWS_METAL_PROBE_FUNCTION");
        NSString *functionName = functionText && functionText[0]
            ? [NSString stringWithUTF8String:functionText]
            : (sourcePath ? @"main0" : @"macws_probe");
        NSString *source = nil;
        if (sourcePath && sourcePath[0]) {
            NSError *readError = nil;
            NSString *captured = [NSString
                stringWithContentsOfFile:[NSString stringWithUTF8String:sourcePath]
                                encoding:NSUTF8StringEncoding
                                   error:&readError];
            fprintf(stderr,
                    "METAL_SOURCE_PROBE sourcePath=%s bytes=%lu readError=%s\n",
                    sourcePath, (unsigned long)captured.length,
                    readError
                        ? readError.localizedDescription.UTF8String : "(nil)");
            if (!captured) return 6;
            if (getenv("MACWS_METAL_PROBE_EXACT_SOURCE")) {
                // Deterministic replay for a source captured at the failing
                // public Metal boundary.  Do not add the ordinary cache-bust
                // comment: its hash and byte length are part of the witness.
                source = captured;
            } else {
                source = [NSString stringWithFormat:
                    @"// macws source probe pid=%d\n%@", getpid(), captured];
            }
        } else {
            source = [NSString stringWithFormat:
                @"// macws source probe pid=%d\n"
                 "#include <metal_stdlib>\n"
                 "using namespace metal;\n"
                 "kernel void macws_probe(device uint *out [[buffer(0)]], "
                 "uint tid [[thread_position_in_grid]]) { out[tid] = tid + 1; }\n",
                 getpid()];
        }
        MTLCompileOptions *options = [MTLCompileOptions new];
        options.languageVersion = MTLLanguageVersion3_0;
        NSError *error = nil;
        id<MTLLibrary> library = [device newLibraryWithSource:source
                                                      options:options
                                                        error:&error];
        fprintf(stderr,
                "METAL_SOURCE_PROBE library=%p class=%s errorDomain=%s "
                "errorCode=%ld description=%s\n",
                library,
                library ? object_getClassName(library) : "(nil)",
                error ? error.domain.UTF8String : "(nil)",
                (long)(error ? error.code : 0),
                error ? error.localizedDescription.UTF8String : "(nil)");
        if (!library) return 3;
        const char *serializePath =
            getenv("MACWS_METAL_PROBE_SERIALIZE_LIBRARY_PATH");
        if (serializePath && serializePath[0]) {
            SEL serializeSelector = sel_registerName("serializeToURL:error:");
            NSError *serializeError = nil;
            NSURL *serializeURL = [NSURL fileURLWithPath:
                [NSString stringWithUTF8String:serializePath]];
            BOOL serialized = [library respondsToSelector:serializeSelector] &&
                ((BOOL (*)(id, SEL, NSURL *, NSError **))objc_msgSend)(
                    library, serializeSelector, serializeURL, &serializeError);
            fprintf(stderr,
                "METAL_SOURCE_PROBE serialize path=%s result=%d "
                "errorDomain=%s errorCode=%ld description=%s\n",
                serializePath, serialized,
                serializeError ? serializeError.domain.UTF8String : "(nil)",
                (long)(serializeError ? serializeError.code : 0),
                serializeError
                    ? serializeError.localizedDescription.UTF8String : "(nil)");
            if (!serialized) return 16;
        }
        if (getenv("MACWS_METAL_PROBE_DUMP_LIBRARY_ALL_METHODS")) {
            for (Class cls = object_getClass(library); cls;
                 cls = class_getSuperclass(cls)) {
                unsigned int methodCount = 0;
                Method *methods = class_copyMethodList(cls, &methodCount);
                for (unsigned int methodIndex = 0;
                     methods && methodIndex < methodCount; methodIndex++) {
                    fprintf(stderr,
                        "METAL_SOURCE_PROBE libraryAllMethod class=%s "
                        "selector=%s types=%s imp=%p\n",
                        class_getName(cls),
                        sel_getName(method_getName(methods[methodIndex])),
                        method_getTypeEncoding(methods[methodIndex]),
                        method_getImplementation(methods[methodIndex]));
                }
                free(methods);
            }
        }

        id<MTLFunction> function = [library newFunctionWithName:functionName];
        fprintf(stderr,
                "METAL_SOURCE_PROBE functionName=%s function=%p class=%s\n",
                functionName.UTF8String,
                function,
                function ? object_getClassName(function) : "(nil)");
        return function ? 0 : 4;
    }
}

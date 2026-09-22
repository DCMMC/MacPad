@import Foundation;
@import Metal;

#import <dlfcn.h>
#import <objc/runtime.h>
#import <ptrauth.h>
#import <math.h>
#import <stdio.h>
#import <unistd.h>

static void PrintPointer(const char *label, const void *pointer) {
    Dl_info info = {0};
    const void *stripped = ptrauth_strip(pointer, ptrauth_key_function_pointer);
    if (stripped && dladdr(stripped, &info)) {
        fprintf(stderr,
                "ABI-PROBE pointer=%s value=%p image=%s base=%p symbol=%s "
                "symbolAddress=%p\n",
                label, stripped, info.dli_fname ?: "(nil)", info.dli_fbase,
                info.dli_sname ?: "(nil)", info.dli_saddr);
    } else {
        fprintf(stderr, "ABI-PROBE pointer=%s value=%p image=(unknown)\n",
                label, stripped);
    }
}

static void PrintClassLayout(Class cls) {
    if (!cls) return;
    fprintf(stderr, "ABI-PROBE class=%s instanceSize=%zu superclass=%s\n",
            class_getName(cls), class_getInstanceSize(cls),
            class_getSuperclass(cls)
                ? class_getName(class_getSuperclass(cls)) : "(nil)");
    unsigned int count = 0;
    Ivar *ivars = class_copyIvarList(cls, &count);
    for (unsigned int index = 0; index < count; index++) {
        fprintf(stderr,
                "ABI-PROBE ivar class=%s name=%s type=%s offset=%td\n",
                class_getName(cls), ivar_getName(ivars[index]) ?: "(nil)",
                ivar_getTypeEncoding(ivars[index]) ?: "(nil)",
                ivar_getOffset(ivars[index]));
    }
    free(ivars);
}

static void PrintHierarchy(id object, const char *label) {
    fprintf(stderr, "ABI-PROBE object=%s value=%p class=%s\n", label,
            (__bridge void *)object,
            object ? class_getName(object_getClass(object)) : "(nil)");
    for (Class cls = object_getClass(object); cls;
         cls = class_getSuperclass(cls)) {
        PrintClassLayout(cls);
    }
}

static void PrintMethod(Class cls, const char *selectorName) {
    SEL selector = sel_registerName(selectorName);
    Method method = cls ? class_getInstanceMethod(cls, selector) : NULL;
    fprintf(stderr,
            "ABI-PROBE method class=%s selector=%s present=%s encoding=%s\n",
            cls ? class_getName(cls) : "(nil)", selectorName,
            method ? "YES" : "NO",
            method ? method_getTypeEncoding(method) : "(nil)");
    if (method) PrintPointer(selectorName, method_getImplementation(method));
}

static int RunDepthClear(id<MTLDevice> device, id<MTLCommandQueue> queue,
                         double clearDepth) {
    const NSUInteger width = 32;
    const NSUInteger height = 32;
    const NSUInteger bytesPerRow = 256;
    MTLTextureDescriptor *descriptor =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:
            MTLPixelFormatDepth32Float_Stencil8
            width:width height:height mipmapped:NO];
    descriptor.storageMode = MTLStorageModePrivate;
    descriptor.usage = MTLTextureUsageRenderTarget;
    id<MTLTexture> texture = [device newTextureWithDescriptor:descriptor];
    id<MTLBuffer> output = [device
        newBufferWithLength:bytesPerRow * height
                    options:MTLResourceStorageModeShared];
    if (!texture || !output) {
        fprintf(stderr,
                "ABI-PROBE depth-clear=%.3f allocation-failed "
                "texture=%p buffer=%p\n",
                clearDepth, (__bridge void *)texture,
                (__bridge void *)output);
        return 10;
    }
    memset(output.contents, 0x5a, output.length);

    id<MTLCommandBuffer> command = [queue commandBuffer];
    MTLRenderPassDescriptor *pass =
        [MTLRenderPassDescriptor renderPassDescriptor];
    pass.depthAttachment.texture = texture;
    pass.depthAttachment.loadAction = MTLLoadActionClear;
    pass.depthAttachment.storeAction = MTLStoreActionStore;
    pass.depthAttachment.clearDepth = clearDepth;
    pass.stencilAttachment.texture = texture;
    pass.stencilAttachment.loadAction = MTLLoadActionClear;
    pass.stencilAttachment.storeAction = MTLStoreActionStore;
    pass.stencilAttachment.clearStencil = 0;
    id<MTLRenderCommandEncoder> render =
        [command renderCommandEncoderWithDescriptor:pass];
    [render endEncoding];
    id<MTLBlitCommandEncoder> blit = [command blitCommandEncoder];
    [blit copyFromTexture:texture sourceSlice:0 sourceLevel:0
              sourceOrigin:MTLOriginMake(0, 0, 0)
                sourceSize:MTLSizeMake(width, height, 1)
                  toBuffer:output destinationOffset:0
       destinationBytesPerRow:bytesPerRow
     destinationBytesPerImage:bytesPerRow * height];
    [blit endEncoding];
    [command commit];
    [command waitUntilCompleted];

    size_t matches = 0;
    size_t finite = 0;
    float minimum = INFINITY;
    float maximum = -INFINITY;
    for (NSUInteger y = 0; y < height; y++) {
        const float *row = (const float *)(
            (const uint8_t *)output.contents + y * bytesPerRow);
        for (NSUInteger x = 0; x < width; x++) {
            float value = row[x];
            if (isfinite(value)) {
                finite++;
                minimum = fminf(minimum, value);
                maximum = fmaxf(maximum, value);
            }
            if (fabsf(value - (float)clearDepth) < 1e-6f) matches++;
        }
    }
    fprintf(stderr,
            "ABI-PROBE depth-clear=%.3f status=%ld error=%s "
            "min=%a max=%a finite=%zu match=%zu/%lu\n",
            clearDepth, (long)command.status,
            command.error.description.UTF8String ?: "nil", minimum, maximum,
            finite, matches, (unsigned long)(width * height));
    return command.error || matches != width * height ? 11 : 0;
}

int main(void) {
    @autoreleasepool {
        fprintf(stderr, "ABI-PROBE begin pid=%d\n", getpid());
        MTLRenderPassDescriptor *pass = [MTLRenderPassDescriptor renderPassDescriptor];
        MTLRenderPassColorAttachmentDescriptor *color = pass.colorAttachments[0];
        MTLRenderPassDepthAttachmentDescriptor *depth = pass.depthAttachment;
        MTLRenderPassStencilAttachmentDescriptor *stencil = pass.stencilAttachment;
        depth.loadAction = MTLLoadActionClear;
        depth.storeAction = MTLStoreActionStore;
        depth.clearDepth = 0.125;
        color.loadAction = MTLLoadActionClear;
        color.storeAction = MTLStoreActionStore;
        color.clearColor = MTLClearColorMake(0.25, 0.5, 0.75, 1.0);

        PrintHierarchy(pass, "renderPass");
        PrintHierarchy(color, "color0");
        PrintHierarchy(depth, "depth");
        PrintHierarchy(stencil, "stencil");
        fprintf(stderr,
                "ABI-PROBE values depthLoad=%lu depthStore=%lu clearDepth=%.17g "
                "colorLoad=%lu colorStore=%lu clearColor=%.17g,%.17g,%.17g,%.17g\n",
                (unsigned long)depth.loadAction,
                (unsigned long)depth.storeAction, depth.clearDepth,
                (unsigned long)color.loadAction,
                (unsigned long)color.storeAction,
                color.clearColor.red, color.clearColor.green,
                color.clearColor.blue, color.clearColor.alpha);

        Class depthClass = object_getClass(depth);
        PrintMethod(depthClass, "texture");
        PrintMethod(depthClass, "loadAction");
        PrintMethod(depthClass, "storeAction");
        PrintMethod(depthClass, "clearDepth");

        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        id<MTLCommandQueue> queue = [device newCommandQueue];
        id<MTLCommandBuffer> commandBuffer = [queue commandBuffer];
        fprintf(stderr,
                "ABI-PROBE metal device=%p/%s queue=%p/%s commandBuffer=%p/%s\n",
                (__bridge void *)device,
                device ? class_getName(object_getClass(device)) : "(nil)",
                (__bridge void *)queue,
                queue ? class_getName(object_getClass(queue)) : "(nil)",
                (__bridge void *)commandBuffer,
                commandBuffer ? class_getName(object_getClass(commandBuffer)) : "(nil)");
        PrintMethod(object_getClass(commandBuffer),
                    "renderCommandEncoderWithDescriptor:");
        int clearZero = device && queue
            ? RunDepthClear(device, queue, 0.0) : 2;
        int clearOne = device && queue
            ? RunDepthClear(device, queue, 1.0) : 2;
        fprintf(stderr, "ABI-PROBE end\n");
        if (clearZero || clearOne) return clearZero ?: clearOne;
    }
    return 0;
}

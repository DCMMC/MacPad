// Bounded MetalFX spatial-scaler witness for the macOS chroot.
//
// This deliberately encodes one small frame and reports every public API
// result.  It is a diagnostic executable, not a production fallback: a
// translated default.metallib route must already be installed by the caller.

@import Foundation;
@import Metal;
@import MetalFX;

#import <objc/runtime.h>
#include <stdio.h>

static const char *MacWSClassName(id object) {
    return object ? object_getClassName(object) : "(nil)";
}

int main(void) {
    @autoreleasepool {
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        fprintf(stderr,
                "METALFX_SPATIAL_PROBE stage=device object=%p class=%s "
                "name=%s\n",
                (__bridge void *)device, MacWSClassName(device),
                device ? device.name.UTF8String : "(nil)");
        if (!device) return 2;

        BOOL supported = [MTLFXSpatialScalerDescriptor supportsDevice:device];
        fprintf(stderr,
                "METALFX_SPATIAL_PROBE stage=support supported=%d\n",
                supported);
        if (!supported) return 3;

        const NSUInteger inputWidth = 320;
        const NSUInteger inputHeight = 180;
        const NSUInteger outputWidth = 640;
        const NSUInteger outputHeight = 360;
        const MTLPixelFormat format = MTLPixelFormatRGBA16Float;

        MTLFXSpatialScalerDescriptor *descriptor =
            [MTLFXSpatialScalerDescriptor new];
        descriptor.colorTextureFormat = format;
        descriptor.outputTextureFormat = format;
        descriptor.inputWidth = inputWidth;
        descriptor.inputHeight = inputHeight;
        descriptor.outputWidth = outputWidth;
        descriptor.outputHeight = outputHeight;
        descriptor.colorProcessingMode =
            MTLFXSpatialScalerColorProcessingModeLinear;
        fprintf(stderr,
                "METALFX_SPATIAL_PROBE stage=create-scaler input=%lux%lu "
                "output=%lux%lu pixelFormat=%lu\n",
                (unsigned long)inputWidth, (unsigned long)inputHeight,
                (unsigned long)outputWidth, (unsigned long)outputHeight,
                (unsigned long)format);

        id<MTLFXSpatialScaler> scaler =
            [descriptor newSpatialScalerWithDevice:device];
        fprintf(stderr,
                "METALFX_SPATIAL_PROBE stage=scaler object=%p class=%s\n",
                (__bridge void *)scaler, MacWSClassName(scaler));
        if (!scaler) return 4;

        MTLTextureDescriptor *inputDescriptor =
            [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:format
                                                               width:inputWidth
                                                              height:inputHeight
                                                           mipmapped:NO];
        inputDescriptor.storageMode = MTLStorageModePrivate;
        inputDescriptor.usage = scaler.colorTextureUsage;
        id<MTLTexture> inputTexture =
            [device newTextureWithDescriptor:inputDescriptor];

        MTLTextureDescriptor *outputDescriptor =
            [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:format
                                                               width:outputWidth
                                                              height:outputHeight
                                                           mipmapped:NO];
        outputDescriptor.storageMode = MTLStorageModePrivate;
        outputDescriptor.usage = scaler.outputTextureUsage;
        id<MTLTexture> outputTexture =
            [device newTextureWithDescriptor:outputDescriptor];
        fprintf(stderr,
                "METALFX_SPATIAL_PROBE stage=textures input=%p inputClass=%s "
                "inputUsage=%#lx output=%p outputClass=%s outputUsage=%#lx\n",
                (__bridge void *)inputTexture, MacWSClassName(inputTexture),
                (unsigned long)inputDescriptor.usage,
                (__bridge void *)outputTexture, MacWSClassName(outputTexture),
                (unsigned long)outputDescriptor.usage);
        if (!inputTexture || !outputTexture) return 5;

        id<MTLCommandQueue> queue = [device newCommandQueue];
        id<MTLCommandBuffer> commandBuffer = [queue commandBuffer];
        fprintf(stderr,
                "METALFX_SPATIAL_PROBE stage=command queue=%p queueClass=%s "
                "buffer=%p bufferClass=%s\n",
                (__bridge void *)queue, MacWSClassName(queue),
                (__bridge void *)commandBuffer, MacWSClassName(commandBuffer));
        if (!queue || !commandBuffer) return 6;

        scaler.colorTexture = inputTexture;
        scaler.outputTexture = outputTexture;
        scaler.inputContentWidth = inputWidth;
        scaler.inputContentHeight = inputHeight;
        fprintf(stderr, "METALFX_SPATIAL_PROBE stage=encode-begin\n");
        [scaler encodeToCommandBuffer:commandBuffer];
        fprintf(stderr, "METALFX_SPATIAL_PROBE stage=encode-end\n");
        [commandBuffer commit];
        [commandBuffer waitUntilCompleted];
        fprintf(stderr,
                "METALFX_SPATIAL_PROBE stage=complete status=%lu "
                "errorDomain=%s errorCode=%ld description=%s\n",
                (unsigned long)commandBuffer.status,
                commandBuffer.error
                    ? commandBuffer.error.domain.UTF8String : "(nil)",
                (long)(commandBuffer.error ? commandBuffer.error.code : 0),
                commandBuffer.error
                    ? commandBuffer.error.localizedDescription.UTF8String
                    : "(nil)");
        return commandBuffer.status == MTLCommandBufferStatusCompleted ? 0 : 7;
    }
}

@import Darwin;
@import ImageIO;
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <QuartzCore/QuartzCore.h>
#import <simd/simd.h>
#include <dlfcn.h>
#import "IOMobileFramebuffer.h"

#define kUTTypePNG CFSTR("public.png")
#define USE_HW_FORMAT 1

BOOL CGImageWriteToFile(CGImageRef image, NSString *path) {
    CFURLRef url = (__bridge CFURLRef)[NSURL fileURLWithPath:path];
    CGImageDestinationRef destination = CGImageDestinationCreateWithURL(url, kUTTypePNG, 1, NULL);
    if (!destination) {
        NSLog(@"Failed to create CGImageDestination for %@", path);
        return NO;
    }

    CGImageDestinationAddImage(destination, image, nil);

    if (!CGImageDestinationFinalize(destination)) {
        NSLog(@"Failed to write image to %@", path);
        CFRelease(destination);
        return NO;
    }

    CFRelease(destination);
    return YES;
}

typedef struct {
    vector_float4 position;
    vector_float4 color;
} Vertex;

@interface MetalRenderer : NSObject
@property(retain) id<MTLDevice> device;
@property(retain) id<MTLCommandQueue> commandQueue;
@property(retain) id<MTLRenderPipelineState> pipelineState;
@property IOSurfaceRef surface;
@property(retain) id<MTLTexture> surfaceTexture;
@end

@implementation MetalRenderer

- (instancetype)init {
    NSLog(@"### debugbydcmmc TestMetalIOSurface init start");
    self = [super init];
    CFPreferencesSetAppValue((const CFStringRef)@"EnableSimApple5", (__bridge CFPropertyListRef)@(YES), (const CFStringRef)@"com.apple.Metal");
    
    _device = MTLCreateSystemDefaultDevice();
    if (!_device) {
        NSLog(@"ERROR: no Metal device");
        return nil;
    }
    _commandQueue = [_device newCommandQueue];
    if (!_commandQueue) NSLog(@"ERROR: no command queue");
    
    NSError *error = nil;
    NSString *source = @" \
#include <metal_stdlib> \n \
using namespace metal; \n \
 \n \
struct Vertex { \n \
    float4 position; \n \
    float4 color; \n \
}; \n \
 \n \
struct VertexOut { \n \
    float4 position [[position]]; \n \
    float4 color; \n \
}; \n \
    \n \
vertex VertexOut vertex_main(uint vertex_id [[vertex_id]], \n \
                             const device Vertex *vertices [[buffer(0)]]) { \n \
    VertexOut out; \n \
    out.position = vertices[vertex_id].position; \n \
    out.color = vertices[vertex_id].color; \n \
    return out; \n \
} \n \
 \n \
fragment float4 fragment_main(VertexOut in [[stage_in]]) { \n \
    return in.color; \n \
}";
    id<MTLLibrary> library = [_device newLibraryWithSource:source options:nil error:&error];
    if (!library) {
        NSLog(@"Error compiling shaders: %@", error);
        return nil;
    }
    
    // Create pipeline
    MTLRenderPipelineDescriptor *desc = [[MTLRenderPipelineDescriptor alloc] init];
    desc.vertexFunction = [library newFunctionWithName:@"vertex_main"];
    desc.fragmentFunction = [library newFunctionWithName:@"fragment_main"];
#if USE_HW_FORMAT
    desc.colorAttachments[0].pixelFormat = MTLPixelFormatBGRA10_XR;
#else
    desc.colorAttachments[0].pixelFormat = MTLPixelFormatBGRA8Unorm;
#endif
    
    _pipelineState = [_device newRenderPipelineStateWithDescriptor:desc error:&error];
    if (error) {
        NSLog(@"Pipeline error: %@", error);
    }
    
    CGSize screenSize;
    IOMobileFramebufferRef fbConn;
    IOMobileFramebufferGetMainDisplay(&fbConn);
    IOMobileFramebufferGetDisplaySize(fbConn, &screenSize);
    [self createIOSurfaceBackedTexture:screenSize.width height:screenSize.height];
    NSLog(@"### debugbydcmmc TestMetalIOSurface init done");
    return self;
}

- (void)createIOSurfaceBackedTexture:(size_t)width height:(size_t)height {
    int tileWidth = 8;
    int tileHeight = 16;
    int widthLonger = ((width-1) & ~(tileWidth-1))+tileWidth;
    int heightLonger = ((height-1) & ~(tileHeight-1))+tileHeight;
    int bytesPerElement = 8;
    size_t bytesPerRow = widthLonger * bytesPerElement;
    size_t size = widthLonger * heightLonger * bytesPerElement;
    size_t totalBytes = size + 0x20000;
    // NSDictionary *surfaceProps = @{
    //     @"IOSurfaceWidth": @(width),
    //     @"IOSurfaceHeight": @(height),
    //     @"IOSurfaceBytesPerElement": @(4),
    NSDictionary *surfaceProps = @{
        @"IOSurfaceAllocSize": @(totalBytes),
        @"IOSurfaceCacheMode": @1024,
        @"IOSurfaceWidth": @(width),
        @"IOSurfaceHeight": @(height),
        @"IOSurfaceMapCacheAttribute": @0,
        @"IOSurfaceMemoryRegion": @"PurpleGfxMem",
        @"IOSurfacePixelSizeCastingAllowed": @0,
        @"IOSurfaceBytesPerElement": @(bytesPerElement),
#if USE_HW_FORMAT
        @"IOSurfacePixelFormat": @((uint32_t)'&w4a'),
        @"IOSurfacePlaneInfo": @[
            @{
                @"IOSurfacePlaneWidth": @(width),
                @"IOSurfacePlaneHeight": @(height),
                @"IOSurfacePlaneBytesPerRow": @(bytesPerRow),
                @"IOSurfacePlaneOffset": @0,
                @"IOSurfacePlaneSize": @(totalBytes),
                
                @"IOSurfaceAddressFormat": @3,
                @"IOSurfacePlaneBytesPerCompressedTileHeader": @2,
                @"IOSurfacePlaneBytesPerElement": @(bytesPerElement),
                @"IOSurfacePlaneCompressedTileDataRegionOffset": @0,
                @"IOSurfacePlaneCompressedTileHeaderRegionOffset": @(size),
                @"IOSurfacePlaneCompressedTileHeight": @(tileHeight),
                @"IOSurfacePlaneCompressedTileWidth": @(tileWidth),
                @"IOSurfacePlaneCompressionType": @2,
                @"IOSurfacePlaneHeightInCompressedTiles": @(heightLonger / tileHeight),
                @"IOSurfacePlaneWidthInCompressedTiles": @(widthLonger / tileWidth),
            }
        ],
#else
        @"IOSurfacePixelFormat": @((uint32_t)'BGRA'),
#endif
    };
    NSLog(@"### debugbydcmmc TestMetalIOSurface IOSurfaceCreate before %@", surfaceProps);
    _surface = IOSurfaceCreate((__bridge CFDictionaryRef)surfaceProps);
    // after IOSurfaceCreate
    if (!_surface) {
        NSLog(@"ERROR: IOSurfaceCreate returned NULL");
    } else {
        NSLog(@"IOSurface created: %p width=%u height=%u bytesPerElement=%zu",
            _surface,
            (unsigned)IOSurfaceGetWidth(_surface),
            (unsigned)IOSurfaceGetHeight(_surface),
            IOSurfaceGetElementWidth(_surface) /* if available */);
    }
    IOSurfaceLock(_surface, 0, NULL);
    void *base = IOSurfaceGetBaseAddress(_surface);
    size_t bpr = IOSurfaceGetBytesPerRow(_surface);
    size_t w = IOSurfaceGetWidth(_surface);
    size_t h = IOSurfaceGetHeight(_surface);
    size_t alloc = IOSurfaceGetAllocSize ? IOSurfaceGetAllocSize(_surface) : 0; // if available
    NSLog(@"IOSurface base=%p bpr=%zu w=%zu h=%zu alloc=%zu", base, bpr, w, h, alloc);
    IOSurfaceUnlock(_surface, 0, NULL);

    IOSurfaceLock(_surface, 0, NULL);
    uint8_t *p = IOSurfaceGetBaseAddress(_surface);
    NSLog(@"first 16 bytes: %02x %02x %02x %02x ...", p[0], p[1], p[2], p[3]);
    bpr = IOSurfaceGetBytesPerRow(_surface);
    h = IOSurfaceGetHeight(_surface);
    uint8_t *last = p + bpr*(h-1);
    NSLog(@"last row first bytes: %02x %02x %02x %02x", last[0], last[1], last[2], last[3]);
    IOSurfaceUnlock(_surface, 0, NULL);


    
    MTLTextureDescriptor *texDesc = [[MTLTextureDescriptor alloc] init];
    texDesc.textureType = MTLTextureType2D;
#if USE_HW_FORMAT
    texDesc.pixelFormat = MTLPixelFormatBGRA10_XR;
#else
    texDesc.pixelFormat = MTLPixelFormatBGRA8Unorm;
#endif
    texDesc.width = (NSUInteger)width;
    texDesc.height = (NSUInteger)height;
    texDesc.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
    
    _surfaceTexture = [_device newTextureWithDescriptor:texDesc iosurface:_surface plane:0];
    if (!_surfaceTexture) {
        NSLog(@"ERROR: newTextureWithDescriptor:iosurface: returned nil");
    } else {
        NSLog(@"surfaceTexture created: %@ fmt=%lu size=%zux%zu bytesPerRow=%zu",
            _surfaceTexture,
            _surfaceTexture.pixelFormat,
            _surfaceTexture.width,
            _surfaceTexture.height,
            IOSurfaceGetBytesPerRow(_surface));
    }
    NSLog(@"### debugbydcmmc TestMetalIOSurface createIOSurfaceBackedTexture done");
}

- (void)renderToIOSurface {
    NSLog(@"### debugbydcmmc TestMetalIOSurface renderToIOSurface start");
    Vertex vertices[] = {
        {{-0.8, -0.8, 0, 1}, {1, 0, 0, 1}},
        {{ 0.8, -0.8, 0, 1}, {0, 1, 0, 1}},
        {{ 0.0,  0.8, 0, 1}, {0, 0, 1, 1}},
    };
    
    id<MTLBuffer> vertexBuffer = [_device newBufferWithBytes:vertices
                                                      length:sizeof(vertices)
                                                     options:MTLResourceStorageModeShared];
    
    MTLRenderPassDescriptor *passDesc = [MTLRenderPassDescriptor renderPassDescriptor];
    passDesc.colorAttachments[0].texture = _surfaceTexture;
    passDesc.colorAttachments[0].loadAction = MTLLoadActionClear;
    passDesc.colorAttachments[0].clearColor = MTLClearColorMake(0, 0, 0, 1);
    passDesc.colorAttachments[0].storeAction = MTLStoreActionStore;
    
    id<MTLCommandBuffer> cmdBuffer = [_commandQueue commandBuffer];
    id<MTLRenderCommandEncoder> encoder = [cmdBuffer renderCommandEncoderWithDescriptor:passDesc];
    [encoder setRenderPipelineState:_pipelineState];
    [encoder setVertexBuffer:vertexBuffer offset:0 atIndex:0];
    [encoder drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
    [encoder endEncoding];
    [cmdBuffer addCompletedHandler:^(id<MTLCommandBuffer> cb){
        if (cb.error) {
            NSLog(@"CommandBuffer error: %@", cb.error);
            NSLog(@"CB error userInfo: %@", cb.error.userInfo);
        } else {
            NSLog(@"CommandBuffer completed OK, status=%ld", (long)cb.status);
        }
    }];
    [cmdBuffer commit];
    [cmdBuffer waitUntilCompleted];
    
    int token;
    CGRect frame = CGRectMake(0, 0, 0, 0);
    IOMobileFramebufferRef fbConn;
    IOMobileFramebufferGetMainDisplay(&fbConn);
    IOMobileFramebufferGetDisplaySize(fbConn, &frame.size);
    int r;
    r = IOMobileFramebufferSwapBegin(fbConn, &token);
    NSLog(@"IOMobileFramebufferSwapBegin status=%d", r);
    r = IOMobileFramebufferSwapSetLayer(fbConn, 0, _surface, frame, frame, 0);
    NSLog(@"IOMobileFramebufferSwapSetLayer status=%d", r);
    r = IOMobileFramebufferSwapEnd(fbConn);
    NSLog(@"IOMobileFramebufferSwapEnd status=%d", r);
    NSLog(@"### debugbydcmmc TestMetalIOSurface renderToIOSurface done");
}

@end

int main(int argc, const char * argv[]) {
    @autoreleasepool {
        MetalRenderer *renderer = [[MetalRenderer alloc] init];
        [renderer renderToIOSurface];
        sleep(10);
    }

    return 0;
}

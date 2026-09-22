@import Darwin;
@import Foundation;
@import Metal;
@import MetalPerformanceShaders;
@import IOSurface;
#include <rootless.h>
#include <xpc/xpc.h>

@interface MTLTextureDescriptorInternal : MTLTextureDescriptor
@end
%hook MTLTextureDescriptorInternal
- (MTLStorageMode)storageMode {
    MTLStorageMode mode = %orig;
    if(mode == 1) { // MTLStorageModeManaged
        self.storageMode = MTLStorageModeShared;
        return MTLStorageModeShared;
    }
    return mode;
}
%end

// ─── Path C: tile-pipeline blur forwarding via XPC + MPSImageGaussianBlur ───
// chroot WS's MTLSim driver intentionally rejects tile-pipeline creation
// (MTLReportFailure stub) → QuartzCore's BlurState::tile_downsample produces
// no output → vibrancy panels are black. We can't transfer an iOS
// MTLRenderPipelineState across processes (device-bound object), so instead
// forward the entire DOWNSAMPLE BLUR operation: the client passes source +
// destination IOSurfaces (which CAN cross process boundaries via mach
// ports), the host wraps them as iOS Metal textures and runs
// MPSImageGaussianBlur (which is a graphics-equivalent fallback that does
// the same Gaussian downsample without needing tile rendering).
//
// XPC message:
//   "op"           = "blur"
//   "source_port"  = mach send-right for source IOSurface
//   "dest_port"    = mach send-right for destination IOSurface
//   "radius"       = double, Gaussian sigma (default 8)
// Reply:
//   "result"       = "ok" | "no_iosurface" | "no_device" | "mps_unavailable"
//
// Listens on `com.macwsguide.blur` (the WS plist's MachServices block
// publishes the endpoint at launchd time).
static id<MTLDevice> gBlurDevice = nil;
static id<MTLCommandQueue> gBlurQueue = nil;
static dispatch_queue_t gBlurDQ = NULL;

static void blur_serve(xpc_object_t event) {
    if (xpc_get_type(event) != XPC_TYPE_DICTIONARY) return;
    const char *op = xpc_dictionary_get_string(event, "op");
    if (!op || strcmp(op, "blur") != 0) return;

    mach_port_t srcPort = xpc_dictionary_copy_mach_send(event, "source_port");
    mach_port_t dstPort = xpc_dictionary_copy_mach_send(event, "dest_port");
    double radius = xpc_dictionary_get_double(event, "radius");
    if (radius <= 0) radius = 8.0;

    IOSurfaceRef srcSurf = IOSurfaceLookupFromMachPort(srcPort);
    IOSurfaceRef dstSurf = IOSurfaceLookupFromMachPort(dstPort);

    xpc_connection_t peer = xpc_dictionary_get_remote_connection(event);
    xpc_object_t r = xpc_dictionary_create_reply(event);
    const char *result = "ok";

    if (!srcSurf || !dstSurf) {
        result = "no_iosurface";
    } else {
        if (!gBlurDevice) {
            gBlurDevice = MTLCreateSystemDefaultDevice();
            if (gBlurDevice) gBlurQueue = [gBlurDevice newCommandQueue];
        }
        if (!gBlurDevice) {
            result = "no_device";
        } else {
            @autoreleasepool {
                MTLTextureDescriptor *td = [MTLTextureDescriptor new];
                td.pixelFormat = MTLPixelFormatBGRA8Unorm;
                td.width = IOSurfaceGetWidth(srcSurf);
                td.height = IOSurfaceGetHeight(srcSurf);
                td.usage = MTLTextureUsageShaderRead | MTLTextureUsageShaderWrite;
                id<MTLTexture> srcTex = [gBlurDevice newTextureWithDescriptor:td iosurface:srcSurf plane:0];
                td.width = IOSurfaceGetWidth(dstSurf);
                td.height = IOSurfaceGetHeight(dstSurf);
                id<MTLTexture> dstTex = [gBlurDevice newTextureWithDescriptor:td iosurface:dstSurf plane:0];
                if (srcTex && dstTex) {
                    id<MTLCommandBuffer> cb = [gBlurQueue commandBuffer];
                    MPSImageGaussianBlur *blur = [[MPSImageGaussianBlur alloc] initWithDevice:gBlurDevice sigma:(float)radius];
                    [blur encodeToCommandBuffer:cb sourceTexture:srcTex destinationTexture:dstTex];
                    [cb commit];
                    [cb waitUntilCompleted];
                } else {
                    result = "mps_unavailable";
                }
            }
        }
    }

    if (srcSurf) CFRelease(srcSurf);
    if (dstSurf) CFRelease(dstSurf);
    if (srcPort != MACH_PORT_NULL) mach_port_deallocate(mach_task_self(), srcPort);
    if (dstPort != MACH_PORT_NULL) mach_port_deallocate(mach_task_self(), dstPort);

    if (r && peer) {
        xpc_dictionary_set_string(r, "result", result);
        xpc_connection_send_message(peer, r);
    }
}

static void install_blur_listener(void) {
    gBlurDQ = dispatch_queue_create("com.macwsguide.blur", DISPATCH_QUEUE_SERIAL);
    xpc_connection_t (*createMach)(const char *, dispatch_queue_t, uint64_t) =
        dlsym(RTLD_DEFAULT, "xpc_connection_create_mach_service");
    if (!createMach) {
        NSLog(@"#### blur-listener: xpc_connection_create_mach_service missing");
        return;
    }
    xpc_connection_t l = createMach("com.macwsguide.blur", gBlurDQ, XPC_CONNECTION_MACH_SERVICE_LISTENER);
    if (!l) {
        NSLog(@"#### blur-listener: createMach returned NULL");
        return;
    }
    xpc_connection_set_event_handler(l, ^(xpc_object_t peer) {
        if (xpc_get_type(peer) != XPC_TYPE_CONNECTION) return;
        xpc_connection_set_event_handler((xpc_connection_t)peer, ^(xpc_object_t event) {
            blur_serve(event);
        });
        xpc_connection_resume((xpc_connection_t)peer);
    });
    xpc_connection_resume(l);
    NSLog(@"#### blur-listener: published com.macwsguide.blur");
}

// decompiled from MTLSimDriverHost.xpc with some modifications
xpc_connection_t xpc_connection_create_listener(const char* name, dispatch_queue_t queue);
xpc_connection_t xpc_connection_create_mach_service(const char *name, dispatch_queue_t targetq, uint64_t flags);
int main(int argc, const char **argv, const char **envp) {
    xpc_object_t (*xpc_connection_create_mach_service)(const char *name, dispatch_queue_t targetq, uint64_t flags) = dlsym(RTLD_DEFAULT, "xpc_connection_create_mach_service");
    xpc_connection_t peerConnection = xpc_connection_create_mach_service("com.apple.metal.simulator", dispatch_get_main_queue(), XPC_CONNECTION_MACH_SERVICE_LISTENER);
    // NSLog(@"#### debugbydcmmc MTLSimDriverHost.xpc main before dispatch_async");
    dispatch_async(dispatch_get_main_queue(), ^{
        char frameworkPath[PATH_MAX];
        void *debug_handle = dlopen("/var/mnt/rootfs/var/jb/usr/macOS/Frameworks/MetalSerializer.framework/MetalSerializer", RTLD_GLOBAL);
        NSCAssert(debug_handle, @"Failed to load MetalSerializer framework: %s", dlerror());
        snprintf(frameworkPath, sizeof(frameworkPath), "%s/MTLSimImplementation.framework/MTLSimImplementation", JBROOT_PATH("/usr/macOS/Frameworks"));
        void *handle = dlopen(frameworkPath, RTLD_GLOBAL);
        NSCAssert(handle, @"Failed to load MTLSimImplementation framework: %s", dlerror());
        void (*init_with_xpc_connection)(xpc_connection_t, uint64_t, uint64_t) = dlsym(handle, "init_with_xpc_connection");
        // NSLog(@"#### debugbydcmmc MTLSimDriverHost.xpc main before init_with_xpc_connection");
        init_with_xpc_connection(peerConnection, MTLCreateSystemDefaultDevice().registryID, 0LL);
        // NSLog(@"#### debugbydcmmc MTLSimDriverHost.xpc main after init_with_xpc_connection");
        install_blur_listener();
    });
    dispatch_main();
}

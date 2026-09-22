// agxprobe — standalone headless Metal probe for safely iterating the AGX (arm64e)
// GPU path WITHOUT involving WindowServer. A wrong GPU/IOKit value crashes only this
// throwaway CLI, never the compositor (which crash-loops and spikes load -> reboots).
//
// Staged: argv[1] = max stage (default 3 = up to-but-not-including SUBMISSION, which is
// the only stage that can hang the GPU hardware). Stages:
//   1 device, 2 buffer (heap/new_resource), 3 command queue, 4 commit+readback (RISKY).
//
// Build (on Mac, cross-compile) — see misc build invocation. Run in chroot via run_bash.sh
// (libmachook auto-inserted). arm64 slice -> MTLSimDevice (sanity baseline, fully works);
// arm64e slice -> AGXG13GDevice (the path we are fixing).
@import Foundation;
@import Metal;
@import IOSurface;
#import <stdio.h>
#import <stdlib.h>
#import <signal.h>
#import <string.h>
#import <unistd.h>
#import <objc/message.h>
#import <objc/runtime.h>

static void AGXProbePrintResourceAddresses(id resource, const char *label) {
    SEL gpuSel = sel_registerName("gpuAddress");
    SEL virtualSel = sel_registerName("virtualAddress");
    SEL resourceIDSel = sel_registerName("resourceID");
    uint64_t gpu = [resource respondsToSelector:gpuSel]
        ? ((uint64_t (*)(id, SEL))objc_msgSend)(resource, gpuSel) : 0;
    uintptr_t virtualAddress = [resource respondsToSelector:virtualSel]
        ? ((uintptr_t (*)(id, SEL))objc_msgSend)(resource, virtualSel) : 0;
    uint32_t resourceID = [resource respondsToSelector:resourceIDSel]
        ? ((uint32_t (*)(id, SEL))objc_msgSend)(resource, resourceIDSel) : 0;
    fprintf(stderr,
        "AGXPROBE [%s] class=%s gpuAddress=0x%llx virtualAddress=0x%llx "
        "resourceID=0x%x contents=%p\n",
        label, object_getClassName(resource), (unsigned long long)gpu,
        (unsigned long long)virtualAddress, resourceID,
        [resource respondsToSelector:@selector(contents)]
            ? ((void *(*)(id, SEL))objc_msgSend)(resource, @selector(contents))
            : NULL);
}

int main(int argc, char **argv) {
    int maxStage = (argc > 1) ? atoi(argv[1]) : 3;
    fprintf(stderr, "AGXPROBE start, maxStage=%d, arch=%s\n", maxStage,
#if defined(__arm64e__)
        "arm64e"
#elif defined(__arm64__)
        "arm64"
#else
        "?"
#endif
    );

    @autoreleasepool {
        // Stage 1: device
        id<MTLDevice> dev = MTLCreateSystemDefaultDevice();
        fprintf(stderr, "AGXPROBE [1] device=%p name=%s\n", (void*)dev, dev ? [[dev name] UTF8String] : "NIL");
        if (!dev) { fprintf(stderr, "AGXPROBE FAIL stage1 (no device)\n"); return 1; }
        // RE-only hold point: by now Metal has loaded AGXMetal13_3 and created
        // the native device, but no resource or command submission has begun.
        // SIGSTOP makes the short-lived probe attachable by the on-device LLDB.
        // Default behavior is unchanged when AGXPROBE_HOLD is absent.
        if (getenv("AGXPROBE_HOLD")) {
            fprintf(stderr, "AGXPROBE HOLD after stage1 (pid=%d)\n", getpid());
            raise(SIGSTOP);
        }
        // LLDB-only texture-initializer probe.  This deliberately stops before
        // command submission: it exists to inspect AGXTexture's CPU/GPU address
        // binding and device bind-table invariants without risking a GPU hang.
        // Select exactly one constructor with AGXPROBE_TEXTURE_INIT=plain or
        // =iosurface.  The normal staged probe is unchanged when the variable
        // is absent.
        const char *textureInit = getenv("AGXPROBE_TEXTURE_INIT");
        if (textureInit && strcmp(textureInit, "plain") == 0) {
            MTLTextureDescriptor *td =
                [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                                                   width:64
                                                                  height:64
                                                               mipmapped:NO];
            td.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
            td.storageMode = MTLStorageModeShared;
            fprintf(stderr, "AGXPROBE [TEX-INIT] creating plain texture\n");
            id<MTLTexture> texture = [dev newTextureWithDescriptor:td];
            fprintf(stderr, "AGXPROBE [TEX-INIT] plain texture=%p\n", (void *)texture);
        } else if (textureInit && strcmp(textureInit, "iosurface") == 0) {
            NSDictionary *props = @{
                (id)kIOSurfaceWidth: @64,
                (id)kIOSurfaceHeight: @64,
                (id)kIOSurfaceBytesPerElement: @4,
                (id)kIOSurfacePixelFormat: @((unsigned int)'BGRA'),
            };
            IOSurfaceRef surface = IOSurfaceCreate((CFDictionaryRef)props);
            MTLTextureDescriptor *td =
                [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                                                   width:64
                                                                  height:64
                                                               mipmapped:NO];
            td.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
            td.storageMode = MTLStorageModeShared;
            fprintf(stderr, "AGXPROBE [TEX-INIT] creating IOSurface texture surface=%p\n",
                    (void *)surface);
            id<MTLTexture> texture = surface
                ? [dev newTextureWithDescriptor:td iosurface:surface plane:0]
                : nil;
            fprintf(stderr, "AGXPROBE [TEX-INIT] IOSurface texture=%p\n", (void *)texture);
            if (surface) CFRelease(surface);
        }
        if (maxStage < 2) { fprintf(stderr, "AGXPROBE OK (stopped after stage1)\n"); return 0; }

        // Stage 2: buffer -> triggers AGX heap allocateImpl -> new_resource
        id<MTLBuffer> buf = [dev newBufferWithLength:4096 options:MTLResourceStorageModeShared];
        fprintf(stderr, "AGXPROBE [2] buffer=%p len=%lu\n", (void*)buf, buf ? (unsigned long)[buf length] : 0);
        if (!buf) { fprintf(stderr, "AGXPROBE FAIL stage2 (no buffer)\n"); return 2; }
        AGXProbePrintResourceAddresses(buf, "2-addresses");
        if (maxStage < 3) { fprintf(stderr, "AGXPROBE OK (stopped after stage2)\n"); return 0; }

        // Stage 3: command queue (sel 0x8->0x7, the 0x408 struct)
        id<MTLCommandQueue> q = [dev newCommandQueue];
        fprintf(stderr, "AGXPROBE [3] queue=%p\n", (void*)q);
        if (!q) { fprintf(stderr, "AGXPROBE FAIL stage3 (no queue)\n"); return 3; }
        if (maxStage < 4) { fprintf(stderr, "AGXPROBE OK (stopped after stage3 — resource/queue creation all succeeded)\n"); return 0; }

        // Stage 4: command buffer + blit fill + COMMIT + readback. SUBMISSION = GPU hardware
        // work; a malformed command buffer can hang the GPU. Only run with explicit arg>=4.
        unsigned char *p = (unsigned char *)[buf contents];
        p[0] = 0x00; p[4095] = 0x00;
        id<MTLCommandBuffer> cb = [q commandBuffer];
        fprintf(stderr, "AGXPROBE [4a] cmdbuf=%p\n", (void*)cb);
        id<MTLBlitCommandEncoder> blit = [cb blitCommandEncoder];
        fprintf(stderr, "AGXPROBE [4b] blit=%p\n", (void*)blit);
        [blit fillBuffer:buf range:NSMakeRange(0, 4096) value:0xAB];
        [blit endEncoding];
        fprintf(stderr, "AGXPROBE [4c] committing...\n");
        [cb commit];
        [cb waitUntilCompleted];
        fprintf(stderr, "AGXPROBE [4d] status=%ld error=%s\n", (long)[cb status],
                [cb error] ? [[[cb error] localizedDescription] UTF8String] : "none");
        fprintf(stderr, "AGXPROBE [4e] readback[0]=0x%02x [4095]=0x%02x (expect 0xab if GPU ran)\n", p[0], p[4095]);
        if (p[0] != 0xAB || p[4095] != 0xAB) {
            fprintf(stderr, "AGXPROBE FAIL stage4 (GPU did not fill buffer)\n");
            return 4;
        }
        fprintf(stderr, "AGXPROBE [4] OK — GPU executed a command buffer\n");
        if (maxStage < 5) { fprintf(stderr, "AGXPROBE OK (stopped after stage4)\n"); return 0; }

        // Stage 5: the WINDOW-CONTENT render path — an IOSurface-backed MTLTexture, render
        // into it via a render pass (clear to red), read back. This is what AppKit/CAMetalLayer
        // does for window content; if THIS fails on arm64e, that's why Terminal content is black.
        const int W = 64, H = 64;
        NSDictionary *iosProps = @{
            (id)kIOSurfaceWidth: @(W),
            (id)kIOSurfaceHeight: @(H),
            (id)kIOSurfaceBytesPerElement: @4,
            (id)kIOSurfacePixelFormat: @((unsigned int)'BGRA'),
        };
        IOSurfaceRef ios = IOSurfaceCreate((CFDictionaryRef)iosProps);
        fprintf(stderr, "AGXPROBE [5a] IOSurface=%p (%dx%d)\n", (void*)ios, W, H);
        if (!ios) { fprintf(stderr, "AGXPROBE FAIL stage5a (no IOSurface)\n"); return 5; }

        MTLTextureDescriptor *td = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:W height:H mipmapped:NO];
        td.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
        td.storageMode = MTLStorageModeShared;
        id<MTLTexture> tex = [dev newTextureWithDescriptor:td iosurface:ios plane:0];
        fprintf(stderr, "AGXPROBE [5b] IOSurface-backed texture=%p\n", (void*)tex);
        if (!tex) { fprintf(stderr, "AGXPROBE FAIL stage5b (no IOSurface-backed texture — THIS is the likely black-content cause)\n"); CFRelease(ios); return 5; }

        MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];
        rp.colorAttachments[0].texture = tex;
        rp.colorAttachments[0].loadAction = MTLLoadActionClear;
        rp.colorAttachments[0].storeAction = MTLStoreActionStore;
        rp.colorAttachments[0].clearColor = MTLClearColorMake(1.0, 0.0, 0.0, 1.0); // red
        id<MTLCommandBuffer> cb2 = [q commandBuffer];
        id<MTLRenderCommandEncoder> rce = [cb2 renderCommandEncoderWithDescriptor:rp];
        fprintf(stderr, "AGXPROBE [5c] renderEncoder=%p\n", (void*)rce);
        if (!rce) { fprintf(stderr, "AGXPROBE FAIL stage5c (no render encoder — tile/render path unsupported)\n"); CFRelease(ios); return 5; }
        [rce endEncoding];
        [cb2 commit];
        [cb2 waitUntilCompleted];
        fprintf(stderr, "AGXPROBE [5d] render status=%ld error=%s\n", (long)[cb2 status],
                [cb2 error] ? [[[cb2 error] localizedDescription] UTF8String] : "none");
        IOSurfaceLock(ios, kIOSurfaceLockReadOnly, NULL);
        unsigned char *pix = (unsigned char *)IOSurfaceGetBaseAddress(ios);
        fprintf(stderr, "AGXPROBE [5e] pixel[0] BGRA = %02x %02x %02x %02x (expect red: 00 00 ff ff)\n", pix[0], pix[1], pix[2], pix[3]);
        int isRed = (pix[2] > 0xC0 && pix[0] < 0x40 && pix[1] < 0x40);
        IOSurfaceUnlock(ios, kIOSurfaceLockReadOnly, NULL);
        CFRelease(ios);
        if (isRed) { fprintf(stderr, "AGXPROBE OK — IOSurface RENDER PATH WORKS (content rendering is fine!)\n"); return 0; }
        fprintf(stderr, "AGXPROBE FAIL stage5 (render did not reach IOSurface — black-content root cause)\n");
        return 5;
    }
}

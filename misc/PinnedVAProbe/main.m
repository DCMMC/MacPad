// PinnedVAProbe — empirically validate whether iOS IOGPU's pinned-VA selector
// chain actually places a buffer at a chosen GPU virtual address.
//
// Background:
//   iOS 16.3 IOGPU.dylib defines
//     -[IOGPUMetalBuffer initWithDevice:pointer:length:options:sysMemSize:
//                        gpuAddress:args:argsSize:deallocator:]
//   (string at IOGPU+0x1eec7a20c). The same selector chain is used by
//   IOGPUMetalSuballocatorAllocate around 0x1eec62310 via descriptor+setters.
//
//   We want to know: if we pass a chosen gpuAddress in, does the resulting
//   buffer report that same VA from -gpuAddress? If yes -> Path C2 viable
//   (iOS Metal honors guest-chosen GPU VAs end-to-end). If no -> the input
//   is purely bookkeeping and the framework picks the VA itself.
//
// Run from iOS shell (NOT inside the macOS chroot), arm64 only:
//   sudo /var/jb/usr/macOS/bin/PinnedVAProbe [requestedVA_hex]
//
// argv[1] default = 0x1158048000  (the same fault VA agxprobe hits when
//   running under chroot — relevant to the AGX-direct investigation).

@import Foundation;
@import Metal;
#include <dlfcn.h>
#include <objc/runtime.h>
#include <objc/message.h>
#include <stdint.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

// IOGPUMetalBuffer's init signature, reconstructed from the iOS 16.3 string:
//   -[IOGPUMetalBuffer initWithDevice:pointer:length:options:sysMemSize:
//                      gpuAddress:args:argsSize:deallocator:]
//
// We declare a typed function pointer for objc_msgSend so the ARM64 ABI passes
// our args correctly. The trailing `deallocator:` is an objc block; we pass NULL.
typedef id (*IOGPUMetalBufferInitFn)(
    id self, SEL _cmd,
    id<MTLDevice> device,
    void *pointer,
    uint64_t length,
    uint64_t options,
    uint64_t sysMemSize,
    uint64_t gpuAddress,
    void *args,
    uint64_t argsSize,
    id deallocator
);

typedef uint64_t (*GpuAddressGetterFn)(id self, SEL _cmd);

static void hex_dump_args(const void *args, size_t n) {
    const uint8_t *b = (const uint8_t *)args;
    for (size_t i = 0; i < n; i++) {
        fprintf(stderr, "%02x ", b[i]);
        if ((i & 15) == 15) fprintf(stderr, "\n  ");
    }
    if (n & 15) fprintf(stderr, "\n");
}

int main(int argc, char **argv) {
    uint64_t requestedVA = 0x1158048000ULL;
    if (argc > 1) {
        requestedVA = strtoull(argv[1], NULL, 0);
    }

    fprintf(stderr, "PINPROBE start, requestedVA = 0x%llx\n", requestedVA);

    @autoreleasepool {
        // ---- Step 1: get an MTLDevice. On iOS-native this is the real AGX.
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        if (!device) {
            fprintf(stderr, "PINPROBE FAIL: no MTLDevice\n");
            return 1;
        }
        fprintf(stderr, "PINPROBE device = %s (%p) class=%s\n",
            [[device name] UTF8String], (void*)device, object_getClassName(device));

        // iOS Metal often hands back a *MTLToolsObject* / debugger-wrapped proxy.
        // The internal IOGPU init paths gate on isKindOfClass: against an internal
        // type, so we likely need the underlying device. Try a few unwrap selectors.
        id realDevice = device;
        SEL unwrapSels[] = {
            sel_registerName("_realDevice"),
            sel_registerName("_targetDevice"),
            sel_registerName("realDevice"),
            sel_registerName("_internalDevice"),
        };
        for (size_t i = 0; i < sizeof(unwrapSels)/sizeof(unwrapSels[0]); i++) {
            if ([device respondsToSelector:unwrapSels[i]]) {
                id r = ((id (*)(id, SEL))objc_msgSend)(device, unwrapSels[i]);
                fprintf(stderr, "PINPROBE unwrap[%s] -> %p class=%s\n",
                    sel_getName(unwrapSels[i]), (void*)r,
                    r ? object_getClassName(r) : "(nil)");
                if (r && r != device) { realDevice = r; break; }
            }
        }
        fprintf(stderr, "PINPROBE realDevice = %p class=%s\n",
            (void*)realDevice, object_getClassName(realDevice));

        // ---- Step 2: resolve IOGPUMetalBuffer class.
        // IOGPU.framework is linked into Metal.framework via dependencies, so by
        // the time we created an MTLDevice it's loaded. We don't need dlopen,
        // but try one as a safety net in case lazy-loading skipped it.
        Class kBuf = objc_getClass("IOGPUMetalBuffer");
        // Also try the iOS-specific G13 family subclass — that's what plain
        // newBufferWithLength: returns on iOS, and setBuffer: may type-check.
        Class kFamilyBuf = objc_getClass("AGXG13GFamilyBuffer");
        fprintf(stderr, "PINPROBE IOGPUMetalBuffer=%p AGXG13GFamilyBuffer=%p\n",
            (void*)kBuf, (void*)kFamilyBuf);
        if (kFamilyBuf) {
            // Climb the class hierarchy
            for (Class c = kFamilyBuf; c; c = class_getSuperclass(c)) {
                fprintf(stderr, "PINPROBE   hierarchy: %s\n", class_getName(c));
            }
        }
        // Prefer the family subclass if available — that matches setBuffer's
        // type expectation
        Class kBufClass = kFamilyBuf ? kFamilyBuf : kBuf;
        if (!kBuf) {
            // Try a few common framework paths. iOS 16 has IOGPU bundled at:
            //   /System/Library/PrivateFrameworks/IOGPU.framework/IOGPU
            // but on jailbroken iPad13,6 / iOS 16.3 path is the same.
            const char *paths[] = {
                "/System/Library/PrivateFrameworks/IOGPU.framework/IOGPU",
                "/System/Library/Frameworks/Metal.framework/Metal",
                NULL,
            };
            for (int i = 0; paths[i]; i++) {
                void *h = dlopen(paths[i], RTLD_NOW | RTLD_NOLOAD);
                if (!h) h = dlopen(paths[i], RTLD_NOW);
                fprintf(stderr, "PINPROBE dlopen %s -> %p\n", paths[i], h);
            }
            kBuf = objc_getClass("IOGPUMetalBuffer");
            kFamilyBuf = objc_getClass("AGXG13GFamilyBuffer");
            kBufClass = kFamilyBuf ? kFamilyBuf : kBuf;
        }
        if (!kBufClass) {
            fprintf(stderr, "PINPROBE FAIL: neither IOGPUMetalBuffer nor AGXG13GFamilyBuffer is loaded\n");
            return 2;
        }

        // ---- Step 3: confirm the init selector exists on the class.
        SEL initSel = sel_registerName(
            "initWithDevice:pointer:length:options:sysMemSize:"
            "gpuAddress:args:argsSize:deallocator:");
        if (![kBufClass instancesRespondToSelector:initSel]) {
            fprintf(stderr, "PINPROBE FAIL: class %s does not respond to expected init selector\n",
                class_getName(kBufClass));
            return 3;
        }
        fprintf(stderr, "PINPROBE init selector is responded to by %s\n", class_getName(kBufClass));

        // Also check AGXBuffer's pinnedGPULocation: init — this is the
        // higher-level entry point on macOS-crash-stack research. If it
        // exists on iOS too, prefer it because it likely fully initializes
        // AGXBuffer-layer ivars that the lower IOGPUMetalBuffer init doesn't.
        SEL altInitSels[] = {
            sel_registerName("initWithDevice:length:options:isSuballocDisabled:pinnedGPULocation:"),
            sel_registerName("initWithDevice:length:options:isSuballocDisabled:resourceInArgs:pinnedGPULocation:"),
            sel_registerName("initWithDevice:bytes:length:options:deallocator:pinnedGPUAddress:"),
        };
        for (size_t i = 0; i < sizeof(altInitSels)/sizeof(altInitSels[0]); i++) {
            BOOL r = [kBufClass instancesRespondToSelector:altInitSels[i]];
            fprintf(stderr, "PINPROBE alt selector [%s]: %s\n",
                sel_getName(altInitSels[i]), r ? "RESPONDS" : "no");
        }
        // Also try on AGXBuffer directly (the proper home of these selectors per crash stacks)
        Class kAGXBuf = objc_getClass("AGXBuffer");
        if (kAGXBuf) {
            for (size_t i = 0; i < sizeof(altInitSels)/sizeof(altInitSels[0]); i++) {
                BOOL r = [kAGXBuf instancesRespondToSelector:altInitSels[i]];
                fprintf(stderr, "PINPROBE alt on AGXBuffer [%s]: %s\n",
                    sel_getName(altInitSels[i]), r ? "RESPONDS" : "no");
            }
        }

        // Debug-only hold after Metal/IOGPU/AGX are loaded but before the
        // explicit pinned allocation.  This lets the project LLDB tracer
        // resolve the already-loaded IOKit symbols and capture the exact
        // resource-create args generated by pinnedGPULocation:.  Normal runs
        // are unchanged.
        if (getenv("PINPROBE_HOLD_BEFORE_PIN")) {
            fprintf(stderr,
                "PINPROBE HOLD before pinned init (pid=%d)\n", getpid());
            raise(SIGSTOP);
        }

        // ---- Step 4: build args buffer.
        // The IOGPU "args" struct's shape is unknown — IOGPUNewResourceArgs-like.
        // First attempt: zero buffer big enough to look like a sane resource args
        // (256B is generous; real one is probably < 128B). If init aborts, we'll
        // need to inspect IOGPUMetalSuballocatorAllocate's setter sequence to
        // recover the layout — this is the fallback plan if step 5 fails.
        uint8_t args_buf[256] = {0};

        // Step 5: invoke the init.
        // Allocate uninitialized instance of the family subclass — so setBuffer's
        // type check (if any) accepts the buffer.
        id raw = ((id (*)(Class, SEL))objc_msgSend)(kBufClass, sel_registerName("alloc"));
        if (!raw) {
            fprintf(stderr, "PINPROBE FAIL: +alloc returned nil\n");
            return 4;
        }
        fprintf(stderr, "PINPROBE alloc (%s) -> %p\n", class_getName(kBufClass), (void*)raw);

        // We want the buffer to be size 0x1000 (one page), shared storage, with our chosen VA.
        // Decompile of -[IOGPUMetalBuffer init...]: with pointer==NULL, the init checks
        // sysMemSize >= length; if not it aborts. So we need sysMemSize >= length.
        // The init also zeroes args[0..0x50) itself — args is an OUTPUT, must be >= 0x50 bytes.
        const uint64_t bufLen = 0x1000;

        // PREFER: AGXBuffer's pinnedGPULocation: init if available — this is the
        // canonical entry point per macOS crash-stack research; it initializes
        // AGXBuffer-layer ivars that IOGPUMetalBuffer.init alone doesn't.
        SEL pinLoc1Sel = sel_registerName(
            "initWithDevice:length:options:isSuballocDisabled:pinnedGPULocation:");
        id buf = nil;
        BOOL usedPinLoc = NO;
        if ([kBufClass instancesRespondToSelector:pinLoc1Sel]) {
            // Signature inference from selector + macOS PG usage:
            //   - device:        id  (x2)
            //   - length:        u64 (x3)
            //   - options:       u64 (x4)
            //   - isSuballocDisabled: BOOL  (x5)
            //   - pinnedGPULocation:  u64* (pointer to VA?) or u64 (VA itself)? (x6)
            // pinnedGPULocation: usually means a pointer-shape, given the name.
            // First try pass-by-pointer (location = address of a u64 holding the VA)
            uint64_t pinLocStorage = requestedVA;
            typedef id (*PinLoc1Fn)(id, SEL, id, uint64_t, uint64_t, BOOL, uint64_t*);
            PinLoc1Fn pinLoc1 = (PinLoc1Fn)objc_msgSend;
            buf = pinLoc1(raw, pinLoc1Sel, realDevice, bufLen,
                          MTLResourceStorageModeShared, NO, &pinLocStorage);
            fprintf(stderr, "PINPROBE pinnedGPULocation: (ptr) returned %p; storage after=0x%llx\n",
                (void*)buf, pinLocStorage);
            usedPinLoc = (buf != nil);
            if (!buf) {
                // Re-+alloc — the failed init may have consumed self
                raw = ((id (*)(Class, SEL))objc_msgSend)(kBufClass, sel_registerName("alloc"));
                fprintf(stderr, "PINPROBE re-alloc -> %p\n", (void*)raw);
                // Try pass-by-value
                typedef id (*PinLoc1ValFn)(id, SEL, id, uint64_t, uint64_t, BOOL, uint64_t);
                PinLoc1ValFn pinLoc1Val = (PinLoc1ValFn)objc_msgSend;
                buf = pinLoc1Val(raw, pinLoc1Sel, realDevice, bufLen,
                                 MTLResourceStorageModeShared, NO, requestedVA);
                fprintf(stderr, "PINPROBE pinnedGPULocation: (val) returned %p\n", (void*)buf);
                usedPinLoc = (buf != nil);
            }
        }

        // Fallback: original IOGPUMetalBuffer-level init that we already know works for blit
        if (!buf) {
            if (raw == nil) raw = ((id (*)(Class, SEL))objc_msgSend)(kBufClass, sel_registerName("alloc"));
            fprintf(stderr, "PINPROBE falling back to IOGPUMetalBuffer-level init\n");
            IOGPUMetalBufferInitFn initFn = (IOGPUMetalBufferInitFn)objc_msgSend;
            buf = initFn(
                raw, initSel,
                realDevice,                    // use unwrapped underlying device
                NULL,                          // pointer (NULL → framework allocates host backing of sysMemSize)
                bufLen,                        // length
                MTLResourceStorageModeShared,  // options
                bufLen,                        // sysMemSize (must be >= length when pointer==NULL)
                requestedVA,                   // gpuAddress  <-- the thing we're testing
                args_buf,                      // args (output, init zeroes first 0x50 bytes itself)
                sizeof(args_buf),              // argsSize
                nil                            // deallocator
            );
        }
        fprintf(stderr, "PINPROBE final buf=%p (used pinnedGPULocation init: %s)\n",
            (void*)buf, usedPinLoc ? "YES" : "NO");

        fprintf(stderr, "PINPROBE init returned %p\n", (void*)buf);
        if (!buf) {
            fprintf(stderr, "PINPROBE init returned nil. Args buffer was:\n  ");
            hex_dump_args(args_buf, sizeof(args_buf));
            return 5;
        }

        // ---- Step 6: read back gpuAddress and compare.
        SEL gaSel = sel_registerName("gpuAddress");
        if (![buf respondsToSelector:gaSel]) {
            fprintf(stderr, "PINPROBE FAIL: buffer doesn't respond to -gpuAddress\n");
            return 6;
        }
        GpuAddressGetterFn ga = (GpuAddressGetterFn)objc_msgSend;
        uint64_t reportedVA = ga(buf, gaSel);

        fprintf(stderr,
            "PINPROBE RESULT: requested=0x%llx  reported=0x%llx  match=%s\n",
            requestedVA, reportedVA,
            (requestedVA == reportedVA) ? "YES" : "NO");

        // Also dump a couple of other Metal-side accessors for sanity.
        if ([buf respondsToSelector:@selector(length)]) {
            fprintf(stderr, "PINPROBE buf.length = %llu\n",
                (uint64_t)((NSUInteger (*)(id, SEL))objc_msgSend)(buf, @selector(length)));
        }
        if ([buf respondsToSelector:@selector(contents)]) {
            fprintf(stderr, "PINPROBE buf.contents = %p\n",
                ((void* (*)(id, SEL))objc_msgSend)(buf, @selector(contents)));
        }

        // If reportedVA == requestedVA, write a small marker into the contents
        // so an external observer (e.g. a follow-up agxprobe stage) can
        // see that the page is real backing memory.
        if (requestedVA == reportedVA) {
            void *contents = ((void* (*)(id, SEL))objc_msgSend)(buf, @selector(contents));
            if (contents) {
                memset(contents, 0xAB, 16);
                fprintf(stderr, "PINPROBE wrote 0xAB marker to contents — buffer is live (CPU-side)\n");
            }

            // ---- Stage 2: GPU access test ----
            // Allocation success !=> GPU can read/write this VA. The pinned VA
            // might be only symbolic; the real GPU page table may diverge.
            // To prove GPU access, submit a tiny compute shader that writes
            // a known pattern through this buffer and then read it back via
            // buf.contents. If the contents change visibly, GPU translation
            // through the pinned VA works.
            fprintf(stderr, "\nPINPROBE === Stage 2: GPU access test ===\n");

            // Wipe contents so we can tell GPU writes apart from our marker
            if (contents) memset(contents, 0, 16);

            // Tiny shader: fill *(device uint*)buf with 0xDEADBEEF.
            NSError *err = nil;
            NSString *src =
                @"#include <metal_stdlib>\n"
                @"using namespace metal;\n"
                @"kernel void marker(device uint* out [[buffer(0)]],\n"
                @"                   uint tid [[thread_position_in_grid]]) {\n"
                @"  if (tid == 0) {\n"
                @"    out[0] = 0xDEADBEEF;\n"
                @"    out[1] = 0xCAFEBABE;\n"
                @"  }\n"
                @"}\n";
            id<MTLLibrary> lib = [device newLibraryWithSource:src options:nil error:&err];
            if (!lib) {
                fprintf(stderr, "PINPROBE stage2 FAIL: shader compile error: %s\n",
                    err ? [[err description] UTF8String] : "(no err)");
                return 8;
            }
            id<MTLFunction> fn = [lib newFunctionWithName:@"marker"];
            id<MTLComputePipelineState> ps =
                [device newComputePipelineStateWithFunction:fn error:&err];
            if (!ps) {
                fprintf(stderr, "PINPROBE stage2 FAIL: pipeline error: %s\n",
                    err ? [[err description] UTF8String] : "(no err)");
                return 9;
            }
            id<MTLCommandQueue> q = [device newCommandQueue];
            id<MTLCommandBuffer> cb = [q commandBuffer];
            id<MTLComputeCommandEncoder> enc = [cb computeCommandEncoder];
            [enc setComputePipelineState:ps];
            // Set our pinned buffer as the compute output
            [enc setBuffer:(id<MTLBuffer>)buf offset:0 atIndex:0];
            MTLSize grid = MTLSizeMake(1, 1, 1);
            MTLSize tg   = MTLSizeMake(1, 1, 1);
            [enc dispatchThreadgroups:grid threadsPerThreadgroup:tg];
            [enc endEncoding];
            [cb commit];
            [cb waitUntilCompleted];

            MTLCommandBufferStatus st = [cb status];
            fprintf(stderr, "PINPROBE stage2 cb.status = %ld (4=Completed, 5=Error)\n", (long)st);
            if (st == MTLCommandBufferStatusError) {
                NSError *e2 = [cb error];
                fprintf(stderr, "PINPROBE stage2 cb.error = %s\n",
                    e2 ? [[e2 description] UTF8String] : "(no err)");
                return 10;
            }
            if (st != MTLCommandBufferStatusCompleted) {
                fprintf(stderr, "PINPROBE stage2 unexpected status\n");
                return 11;
            }

            // Re-read contents — did the GPU actually write here?
            uint32_t w0 = ((volatile uint32_t*)contents)[0];
            uint32_t w1 = ((volatile uint32_t*)contents)[1];
            fprintf(stderr, "PINPROBE stage2 contents after GPU write: [0]=0x%08x [1]=0x%08x\n", w0, w1);

            // ---- Stage 2c: GPU-side blit to a known buffer ----
            // CRITICAL: [buf contents] for the pinned buffer might be UNRELIABLE
            // (the `pointer=NULL` init path may not initialize contents to the
            // real backing). To disambiguate, ask the GPU itself to copy the
            // pinned buffer's first bytes into a plain buffer we trust.
            // If the plain buffer reads back our marker, GPU access to pinned VA worked.
            fprintf(stderr, "\nPINPROBE === Stage 2c: blit pinned -> plain to verify GPU side ===\n");
            id<MTLBuffer> sinkBuf = [device newBufferWithLength:64 options:MTLResourceStorageModeShared];
            memset([sinkBuf contents], 0xEE, 64);  // sentinel so we can tell apart "untouched" from "written 0"

            id<MTLCommandBuffer> cb3 = [q commandBuffer];
            id<MTLBlitCommandEncoder> bl = [cb3 blitCommandEncoder];
            [bl copyFromBuffer:(id<MTLBuffer>)buf sourceOffset:0
                      toBuffer:sinkBuf destinationOffset:0 size:16];
            [bl endEncoding];
            [cb3 commit];
            [cb3 waitUntilCompleted];
            fprintf(stderr, "PINPROBE stage2c cb.status = %ld\n", (long)[cb3 status]);
            if ([cb3 status] == MTLCommandBufferStatusError) {
                NSError *e3 = [cb3 error];
                fprintf(stderr, "PINPROBE stage2c blit error: %s\n",
                    e3 ? [[e3 description] UTF8String] : "(no err)");
            }
            uint32_t s0 = ((volatile uint32_t*)[sinkBuf contents])[0];
            uint32_t s1 = ((volatile uint32_t*)[sinkBuf contents])[1];
            uint32_t s2 = ((volatile uint32_t*)[sinkBuf contents])[2];
            fprintf(stderr, "PINPROBE stage2c sinkBuf (via GPU blit from pinned buf): [0]=0x%08x [1]=0x%08x [2]=0x%08x\n", s0, s1, s2);
            if (s0 == 0xDEADBEEF && s1 == 0xCAFEBABE) {
                fprintf(stderr, "PINPROBE stage2c RESULT: GPU writes through pinned VA DID land; [buf contents] is unreliable for pinned buffers but GPU page table IS WORKING\n");
            } else if (s0 == 0xEEEEEEEE) {
                fprintf(stderr, "PINPROBE stage2c RESULT: blit didn't write — pinned buffer not reachable as GPU source either, or blit failed silently\n");
            } else {
                fprintf(stderr, "PINPROBE stage2c RESULT: weird value, neither marker nor sentinel: pinned VA reads garbage\n");
            }

            // ---- Stage 2d: blit a known marker FROM plain buf INTO pinned buf ----
            // The pinned buffer might be discardable or read-only.
            // Bypass setBuffer: entirely: stage a marker in a plain buffer,
            // then blit it INTO the pinned buffer, then blit OUT to a sink.
            fprintf(stderr, "\nPINPROBE === Stage 2d: blit-into pinned + blit-out ===\n");
            id<MTLBuffer> srcBuf = [device newBufferWithLength:64 options:MTLResourceStorageModeShared];
            ((volatile uint32_t*)[srcBuf contents])[0] = 0xFEEDFACE;
            ((volatile uint32_t*)[srcBuf contents])[1] = 0xBADDCAFE;
            id<MTLBuffer> sink2 = [device newBufferWithLength:64 options:MTLResourceStorageModeShared];
            memset([sink2 contents], 0xEE, 64);

            id<MTLCommandBuffer> cb4 = [q commandBuffer];
            id<MTLBlitCommandEncoder> bl2 = [cb4 blitCommandEncoder];
            // srcBuf -> pinned buf
            [bl2 copyFromBuffer:srcBuf sourceOffset:0
                      toBuffer:(id<MTLBuffer>)buf destinationOffset:0 size:16];
            // pinned buf -> sink2
            [bl2 copyFromBuffer:(id<MTLBuffer>)buf sourceOffset:0
                      toBuffer:sink2 destinationOffset:0 size:16];
            [bl2 endEncoding];
            [cb4 commit];
            [cb4 waitUntilCompleted];
            fprintf(stderr, "PINPROBE stage2d cb.status = %ld\n", (long)[cb4 status]);
            uint32_t r0 = ((volatile uint32_t*)[sink2 contents])[0];
            uint32_t r1 = ((volatile uint32_t*)[sink2 contents])[1];
            uint32_t p0 = ((volatile uint32_t*)contents)[0];  // also re-check buf.contents
            uint32_t p1 = ((volatile uint32_t*)contents)[1];
            fprintf(stderr, "PINPROBE stage2d sink2 (blit-roundtrip via pinned): [0]=0x%08x [1]=0x%08x\n", r0, r1);
            fprintf(stderr, "PINPROBE stage2d buf.contents AFTER blit-into: [0]=0x%08x [1]=0x%08x\n", p0, p1);
            if (r0 == 0xFEEDFACE && r1 == 0xBADDCAFE) {
                fprintf(stderr, "PINPROBE stage2d ROUND-TRIP WORKS via blit — pinned buf is a real GPU-accessible page\n");
                if (p0 == 0xFEEDFACE && p1 == 0xBADDCAFE) {
                    fprintf(stderr, "PINPROBE stage2d  ALSO buf.contents is now consistent with GPU side — pinned VA is fully working\n");
                } else {
                    fprintf(stderr, "PINPROBE stage2d  BUT buf.contents diverges — host-vs-GPU memory aliasing is broken for pinned bufs\n");
                }
            } else {
                fprintf(stderr, "PINPROBE stage2d ROUND-TRIP FAILS — pinned buffer is GPU-discardable/symbolic\n");
            }

            // ---- Stage 2b: control with plain newBufferWithLength ----
            // If the pinned buffer didn't get the writes, was it because the shader
            // didn't run at all, or because the pinned VA doesn't map back to contents?
            // Repeat the kernel on a plain MTLBuffer to disambiguate.
            fprintf(stderr, "\nPINPROBE === Stage 2b: control with plain newBufferWithLength ===\n");
            id<MTLBuffer> plainBuf = [device newBufferWithLength:bufLen options:MTLResourceStorageModeShared];
            fprintf(stderr, "PINPROBE plainBuf class=%s gpuAddress=0x%llx contents=%p\n",
                object_getClassName(plainBuf),
                ((uint64_t (*)(id, SEL))objc_msgSend)(plainBuf, gaSel),
                [plainBuf contents]);
            fprintf(stderr, "PINPROBE   (pinned buf class was %s)\n", object_getClassName(buf));
            memset([plainBuf contents], 0, 16);
            id<MTLCommandBuffer> cb2 = [q commandBuffer];
            id<MTLComputeCommandEncoder> enc2 = [cb2 computeCommandEncoder];
            [enc2 setComputePipelineState:ps];
            [enc2 setBuffer:plainBuf offset:0 atIndex:0];
            [enc2 dispatchThreadgroups:grid threadsPerThreadgroup:tg];
            [enc2 endEncoding];
            [cb2 commit];
            [cb2 waitUntilCompleted];
            fprintf(stderr, "PINPROBE stage2b cb.status = %ld\n", (long)[cb2 status]);
            uint32_t c0 = ((volatile uint32_t*)[plainBuf contents])[0];
            uint32_t c1 = ((volatile uint32_t*)[plainBuf contents])[1];
            fprintf(stderr, "PINPROBE stage2b plainBuf after GPU write: [0]=0x%08x [1]=0x%08x\n", c0, c1);
            if (c0 == 0xDEADBEEF && c1 == 0xCAFEBABE) {
                fprintf(stderr, "PINPROBE stage2b CONTROL OK: shader ran correctly on plain buffer\n");
                if (w0 == 0xDEADBEEF && w1 == 0xCAFEBABE) {
                    fprintf(stderr,
                        "PINPROBE stage2b CONTROL CONFIRMS pinned result: "
                        "both pinned and plain buffers received the GPU writes\n");
                } else {
                    fprintf(stderr,
                        "PINPROBE stage2b CONTROL ONLY: shader setup works, "
                        "but the pinned buffer did not receive the same writes\n");
                }
            } else {
                fprintf(stderr, "PINPROBE stage2b CONTROL FAIL: shader doesn't write even to plain buffer — test setup broken\n");
            }

            if (w0 == 0xDEADBEEF && w1 == 0xCAFEBABE) {
                fprintf(stderr, "PINPROBE stage2 OK: GPU SUCCESSFULLY wrote through pinned VA 0x%llx\n", reportedVA);
                fprintf(stderr, "PINPROBE FINAL: PINNED VA HAS A REAL GPU PAGE TABLE ENTRY\n");
                return 0;
            } else {
                fprintf(stderr, "PINPROBE stage2 NO-OP: GPU did not write through this VA — pin is symbolic only\n");
                return 12;
            }
        } else {
            fprintf(stderr, "PINPROBE NO-MATCH: pinned VA is bookkeeping only (framework picked its own VA)\n");
            return 7;
        }
    }
}

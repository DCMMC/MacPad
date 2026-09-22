// mtl_keepalive — keeps an MTLCompilerService XPC instance alive long
// enough to attach lldb and verify our MTLCompilerBypassOSCheck tweak's
// renamer patch was applied.
//
// Strategy: in a loop, ask MTLDevice to compile a small MSL source whose
// AIR will exercise the same `air.fract.v3f16` rename path the chroot
// WindowServer hits at pipeline-build time. Each compile triggers an
// XPC round-trip to MTLCompilerService; if we space them out and never
// release the device, MTLCompilerService stays up between calls. The
// kernel for the test deliberately uses `fract` on `half3` so the AIR
// emitted by Apple's Metal frontend contains `air.fract.v3f16`, the
// exact intrinsic our patch is supposed to make benign by stopping the
// `agx.` prepend in `AGCLLVMUserObject::linkMetalRuntime`.
//
// Usage:
//   sudo /var/jb/usr/bin/mtl_keepalive          # default 10s sleep, infinite loop
//   sudo /var/jb/usr/bin/mtl_keepalive 2 5      # sleep 2s, 5 iterations
//
// While running, in another shell:
//   sudo lldb -p $(pgrep MTLCompilerService | head -1)
// then in lldb:
//   image lookup -n _AIRNTGetVersion        # find the anchor
//   memory read --size 4 --count 1 <anchor - 0x1259a4>
// expected unpatched: BL (0x94xxxxxx)
// expected patched:   0xd503201f (NOP)

#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <signal.h>

static volatile sig_atomic_t g_stop = 0;
static void on_sigint(int s) { (void)s; g_stop = 1; }

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        signal(SIGINT, on_sigint);

        double sleep_s = (argc >= 2) ? atof(argv[1]) : 10.0;
        long iters    = (argc >= 3) ? atol(argv[2]) : -1; // -1 == infinite

        id<MTLDevice> dev = MTLCreateSystemDefaultDevice();
        if (!dev) {
            fprintf(stderr, "MTLCreateSystemDefaultDevice returned nil\n");
            return 1;
        }
        printf("[mtl_keepalive] device=%s pid=%d sleep=%.1fs iters=%ld\n",
               [[dev name] UTF8String], getpid(), sleep_s, iters);
        fflush(stdout);

        // MSL source picked to exercise the same AIR intrinsic the
        // chroot CA pipelines hit. `fract(half3)` is the operand that
        // produces `air.fract.v3f16` after Apple's metal-frontend pass;
        // wrapping it in a fragment that returns a float4 keeps the
        // pipeline build path realistic.
        NSString *src =
            @"#include <metal_stdlib>\n"
            @"using namespace metal;\n"
            @"struct V { float4 pos [[position]]; half3 c; };\n"
            @"vertex V vs(uint vid [[vertex_id]]) {\n"
            @"    float2 p = float2((vid & 1u) ? 3.0 : -1.0,\n"
            @"                       (vid & 2u) ? 3.0 : -1.0);\n"
            @"    V o; o.pos = float4(p, 0, 1);\n"
            @"    o.c = half3(half(p.x) * 1.5h, half(p.y) * 1.5h, 0.5h);\n"
            @"    return o;\n"
            @"}\n"
            @"fragment float4 fs(V in [[stage_in]]) {\n"
            @"    half3 f = fract(in.c);\n"      // ← air.fract.v3f16
            @"    return float4(float3(f), 1.0);\n"
            @"}\n";

        MTLCompileOptions *opts = [[MTLCompileOptions alloc] init];
        for (long i = 0; iters < 0 || i < iters; i++) {
            if (g_stop) break;
            NSError *err = nil;
            id<MTLLibrary> lib =
                [dev newLibraryWithSource:src options:opts error:&err];
            printf("[mtl_keepalive] iter=%ld lib=%p err=%s\n",
                   i, (__bridge void *)lib,
                   err ? [[err description] UTF8String] : "(none)");
            if (lib) {
                id<MTLFunction> vf = [lib newFunctionWithName:@"vs"];
                id<MTLFunction> ff = [lib newFunctionWithName:@"fs"];
                MTLRenderPipelineDescriptor *d =
                    [[MTLRenderPipelineDescriptor alloc] init];
                d.vertexFunction   = vf;
                d.fragmentFunction = ff;
                d.colorAttachments[0].pixelFormat = MTLPixelFormatBGRA8Unorm;
                NSError *perr = nil;
                id<MTLRenderPipelineState> ps =
                    [dev newRenderPipelineStateWithDescriptor:d error:&perr];
                printf("[mtl_keepalive] iter=%ld ps=%p err=%s\n",
                       i, (__bridge void *)ps,
                       perr ? [[perr description] UTF8String] : "(none)");
            }
            fflush(stdout);
            if (g_stop) break;
            usleep((useconds_t)(sleep_s * 1e6));
        }
        printf("[mtl_keepalive] done\n");
    }
    return 0;
}

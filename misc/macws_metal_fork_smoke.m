// Standalone, bounded native-Metal/fork regression test. Run in the macOS
// chroot with the candidate libmachook injected, not in an existing user app.
// Build (either arm64 or arm64e):
// xcrun clang -arch arm64e -mmacosx-version-min=13.0 -Wall -Wextra -Werror \
//   -framework Foundation -framework Metal misc/macws_metal_fork_smoke.m -o /tmp/macws_metal_fork_smoke
// Sign/trust the result with the project entitlements before running on iPad.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <dlfcn.h>
#include <ptrauth.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <sys/wait.h>
#include <unistd.h>

static uint32_t superclassAuthenticationInstruction(void) {
    const uint32_t *code = ptrauth_strip(
        dlsym(RTLD_DEFAULT, "objc_msgSendSuper2"), ptrauth_key_function_pointer);
    return code ? code[4] : 0;
}

int main(void) {
    setvbuf(stdout, NULL, _IONBF, 0);
    // Bounds driver/command completion too. This process owns no user data.
    alarm(15);
    @autoreleasepool {
        uint32_t before = superclassAuthenticationInstruction();
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        uint32_t after = superclassAuthenticationInstruction();
        printf("probe-pid=%d device=%s superclass-auth-before=%#x after=%#x\n",
               getpid(), device ? device.name.UTF8String : "nil", before, after);
        if (!device) return 2;

        MTLTextureDescriptor *desc = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:
            MTLPixelFormatBGRA8Unorm width:4 height:4 mipmapped:NO];
        desc.usage = MTLTextureUsageRenderTarget;
        desc.storageMode = MTLStorageModeShared;
        id<MTLTexture> texture = [device newTextureWithDescriptor:desc];
        id<MTLCommandQueue> queue = [device newCommandQueue];
        id<MTLCommandBuffer> command = [queue commandBuffer];
        if (!texture || !queue || !command) return 3;
        MTLRenderPassDescriptor *pass = [MTLRenderPassDescriptor renderPassDescriptor];
        pass.colorAttachments[0].texture = texture;
        pass.colorAttachments[0].loadAction = MTLLoadActionClear;
        pass.colorAttachments[0].storeAction = MTLStoreActionStore;
        pass.colorAttachments[0].clearColor = MTLClearColorMake(0.25, 0.5, 0.75, 1);
        id<MTLRenderCommandEncoder> encoder = [command renderCommandEncoderWithDescriptor:pass];
        if (!encoder) return 3;
        [encoder endEncoding];
        [command commit];
        [command waitUntilCompleted];
        printf("command-status=%lu command-error=%s\n", (unsigned long)command.status,
               command.error ? command.error.description.UTF8String : "none");
        if (command.status != MTLCommandBufferStatusCompleted) return 3;
        uint8_t pixels[4 * 4 * 4] = {0};
        [texture getBytes:pixels bytesPerRow:16 fromRegion:MTLRegionMake2D(0, 0, 4, 4)
              mipmapLevel:0];
        printf("gpu-clear-bgra=%u,%u,%u,%u\n", pixels[0], pixels[1], pixels[2], pixels[3]);
        for (unsigned i = 0; i < sizeof(pixels); i += 4) {
            if (pixels[i] < 190 || pixels[i] > 192 || pixels[i + 1] < 127 ||
                pixels[i + 1] > 129 || pixels[i + 2] < 63 || pixels[i + 2] > 65 ||
                pixels[i + 3] != 255) return 3;
        }

        pid_t child = fork();
        if (child == 0) {
            static const char reached[] = "fork-child-reached-main\n";
            write(STDOUT_FILENO, reached, sizeof(reached) - 1);
            _exit(0);
        }
        if (child < 0) return 4;
        int status = 0;
        // Stop the global alarm while a child exists so timeout cleanup can
        // reap exactly this owned child instead of leaving an orphan.
        alarm(0);
        pid_t waited = 0;
        for (unsigned attempt = 0; attempt < 200; attempt++) {
            waited = waitpid(child, &status, WNOHANG);
            if (waited != 0) break;
            usleep(10000);
        }
        if (waited == 0) {
            kill(child, SIGKILL);
            waited = waitpid(child, &status, 0);
        }
        printf("fork-pid=%d wait-pid=%d status=%#x exit=%d signal=%d\n",
               child, waited, status, WIFEXITED(status) ? WEXITSTATUS(status) : -1,
               WIFSIGNALED(status) ? WTERMSIG(status) : -1);
        // iPadOS 16's libobjc authenticates superclass with AUTDA x16,x17.
        return before == 0xdac11a30u && after == before && waited == child &&
               WIFEXITED(status) && WEXITSTATUS(status) == 0 ? 0 : 4;
    }
}

// Explicitly invoked, one-page ownership/aliasing diagnostic; no UI or flags.
// The callback deliberately records rather than unmaps: this lets the observer
// report a premature callback without itself dereferencing unmapped memory.
// Build -fno-objc-arc with Foundation and Metal. No production linkage.
// Usage: probe [managed] | probe allocation-only [managed]. Allocation-only
// verifies CPU ownership, not GPU visibility; it never creates a command queue.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <TargetConditionals.h>
#include <mach-o/dyld.h>
#include <mach-o/loader.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

static _Atomic unsigned callbacks;
static _Atomic unsigned callback_mismatch;

static void provenance(void) {
    for (uint32_t i = 0; i < _dyld_image_count(); i++) {
        const char *name = _dyld_get_image_name(i);
        const struct mach_header_64 *header = (const void *)_dyld_get_image_header(i);
        if (!name || !strstr(name, "libmachook") || header->magic != MH_MAGIC_64)
            continue;
        const uint8_t *cursor = (const void *)(header + 1);
        printf("library=%s subtype=%#x", name, (unsigned)header->cpusubtype);
        for (uint32_t j = 0; j < header->ncmds; j++) {
            const struct load_command *command = (const void *)cursor;
            if (command->cmd == LC_UUID) {
                const struct uuid_command *uuid = (const void *)cursor;
                printf(" uuid=");
                for (unsigned k = 0; k < 16; k++) printf("%02x", uuid->uuid[k]);
            }
            cursor += command->cmdsize;
        }
        puts("");
    }
}

int main(int argc, char **argv) {
    alarm(10);
    setvbuf(stdout, NULL, _IONBF, 0);
    BOOL allocationOnly = argc >= 2 && strcmp(argv[1], "allocation-only") == 0;
    int storageArgument = allocationOnly ? 2 : 1;
    BOOL managed = argc == storageArgument + 1 &&
                   strcmp(argv[storageArgument], "managed") == 0;
    if (argc > storageArgument + 1 ||
        (argc == storageArgument + 1 && !managed)) return 2;
#if !TARGET_OS_OSX
    if (managed) return 2; // Do not send macOS-only options to native iOS.
#endif
    provenance();
    printf("NOCOPY mode=%s gpu-submit=%s\n",
           allocationOnly ? "allocation-only" : "full",
           allocationOnly ? "disabled" : "enabled");
    size_t length = (size_t)getpagesize();
    if (length < 4096 || length > 65536) return 2;
    uint8_t *original = mmap(NULL, length, PROT_READ | PROT_WRITE,
                            MAP_ANON | MAP_PRIVATE, -1, 0);
    if (original == MAP_FAILED) return 3;
    memset(original, 0x25, length);
    BOOL alias = NO, lateWrite = NO, validStorage = NO;
    BOOL gpuWrite = NO, validCommand = NO;
    unsigned premature = 0, callbacksBeforeRelease = 0;
    @autoreleasepool {
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        id<MTLBuffer> buffer = [device newBufferWithBytesNoCopy:original
            length:length options:(managed ? (1UL << MTLResourceStorageModeShift)
                                           : MTLResourceStorageModeShared)
            deallocator:^(void *pointer, NSUInteger size) {
                if (pointer != original || size != length)
                    atomic_fetch_add(&callback_mismatch, 1);
                atomic_fetch_add(&callbacks, 1);
            }];
        if (!buffer || !buffer.contents) {
            printf("NOCOPY allocation-failed buffer=%p\n", (void *)buffer);
            [buffer release]; [device release];
            munmap(original, length);
            return 4;
        }
        premature = atomic_load(&callbacks);
        alias = buffer.contents == original;
        validStorage = (NSUInteger)buffer.storageMode ==
                       (managed ? 1UL : (NSUInteger)MTLStorageModeShared);
        original[0] = 0x67;
#if TARGET_OS_OSX
        if (!allocationOnly && buffer.storageMode == MTLStorageModeManaged)
            [buffer didModifyRange:NSMakeRange(0, 1)];
#endif
        lateWrite = ((uint8_t *)buffer.contents)[0] == 0x67;
        printf("NOCOPY length=%zu original=%p contents=%p alias=%d "
               "cpu-late-write=%d premature-callbacks=%u requested=%s actual=%lu "
               "storage-match=%d\n",
               length, original, buffer.contents, alias, lateWrite, premature,
               managed ? "managed" : "shared", (unsigned long)buffer.storageMode,
               validStorage);
        id<MTLCommandQueue> queue = nil;
        if (!allocationOnly) {
            queue = [device newCommandQueue];
            id<MTLCommandBuffer> command = [queue commandBuffer];
            id<MTLBlitCommandEncoder> blit = [command blitCommandEncoder];
            [blit fillBuffer:buffer range:NSMakeRange(0, length) value:0x9b];
#if TARGET_OS_OSX
            if (buffer.storageMode == MTLStorageModeManaged)
                [blit synchronizeResource:buffer];
#endif
            [blit endEncoding];
            [command commit];
            [command waitUntilCompleted];
            validCommand = command.status == MTLCommandBufferStatusCompleted;
            unsigned originalMismatches = 0, bufferMismatches = 0;
            for (size_t i = 0; i < length; i++) {
                originalMismatches += original[i] != 0x9b;
                bufferMismatches += ((uint8_t *)buffer.contents)[i] != 0x9b;
            }
            gpuWrite = originalMismatches == 0 && bufferMismatches == 0;
            printf("NOCOPY status=%lu error=%s gpu-original-mismatch=%u/%zu "
                   "gpu-buffer-mismatch=%u/%zu callbacks-before-release=%u\n",
                   (unsigned long)command.status,
                   command.error ? command.error.description.UTF8String : "none",
                   originalMismatches, length, bufferMismatches, length,
                   atomic_load(&callbacks));
        }
        callbacksBeforeRelease = atomic_load(&callbacks);
        printf("NOCOPY callbacks-before-buffer-release=%u\n", callbacksBeforeRelease);
        [buffer release];
        [queue release];
        [device release];
    }
    unsigned finalCallbacks = atomic_load(&callbacks);
    printf("NOCOPY callbacks-after-drain=%u callback-mismatch=%u\n",
           finalCallbacks, atomic_load(&callback_mismatch));
    munmap(original, length);
    BOOL ownershipPassed = alias && lateWrite && validStorage &&
                  premature == 0 && finalCallbacks == 1 &&
                  callbacksBeforeRelease == 0 &&
                  atomic_load(&callback_mismatch) == 0;
    if (allocationOnly) {
        printf("NOCOPY allocation-only=%s gpu-contract=NOT-TESTED\n",
               ownershipPassed ? "PASS" : "FAIL");
        return ownershipPassed ? 0 : 1;
    }
    BOOL passed = ownershipPassed && gpuWrite && validCommand;
    printf("NOCOPY contract=%s\n", passed ? "PASS" : "FAIL");
    return passed ? 0 : 1;
}

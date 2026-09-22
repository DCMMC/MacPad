// Explicit native-iOS diagnostic, confined to its own process and one page.
// Describe first. --observe may temporarily replace a verified Objective-C
// method-table entry, call the original unchanged, and restore that entry.
// No GPU submission, no macOS wire replay, no user process/heap inspection.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <objc/runtime.h>
#include <dlfcn.h>
#include <errno.h>
#include <mach/mach.h>
#include <mach-o/loader.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

typedef id (*resource_init_fn)(id, SEL, id, NSUInteger, const void *, uint32_t);
static resource_init_fn original_init;
static unsigned observations;
static unsigned callbacks;

static id observed_init(id self, SEL selector, id device, NSUInteger options,
                       const void *arguments, uint32_t size) {
    int before_errno = errno;
    unsigned sample = ++observations;
    const char *class_name = object_getClassName(self);
    unsigned char snapshot[104] = {0};
    size_t copied = arguments && size ? MIN(size, sizeof(snapshot)) : 0;
    if (sample <= 8 && copied) memcpy(snapshot, arguments, copied);
    errno = before_errno;
    id result = original_init(self, selector, device, options, arguments, size);
    int result_errno = errno;
    if (sample <= 8) {
        printf("NATIVE-RESOURCE sample=%u class=%s options=%#lx argsSize=%lu "
               "snapshotSize=%zu result=%p words=", sample, class_name,
               (unsigned long)options, (unsigned long)size, copied, result);
        for (size_t offset = 0; offset < copied; offset += 8) {
            uint64_t word = 0;
            memcpy(&word, snapshot + offset, MIN((size_t)8, copied - offset));
            printf("+%02zx:%016llx ", offset, (unsigned long long)word);
        }
        puts("");
        printf("NATIVE-RESOURCE-AFTER sample=%u argsSize=%u words=", sample, size);
        for (size_t offset = 0; offset < copied; offset += 8) {
            uint64_t word = 0;
            memcpy(&word, (const unsigned char *)arguments + offset,
                   MIN((size_t)8, copied - offset));
            printf("+%02zx:%016llx ", offset, (unsigned long long)word);
        }
        puts("");
    }
    errno = result_errno;
    return result;
}

static BOOL exact_type(Method method, unsigned index, const char *expected) {
    char *type = method_copyArgumentType(method, index);
    const char *value = type;
    if (value && *value == 'r') value++; // const does not change this pointer ABI
    BOOL matches = value && strcmp(value, expected) == 0;
    free(type);
    return matches;
}

static void describe(Method method, unsigned code_size) {
    IMP implementation = method_getImplementation(method);
    Dl_info image = {0};
    (void)dladdr((const void *)implementation, &image);
    printf("NATIVE-METHOD selector=%s types=%s imp=%p owner=%s offset=%#lx uuid=",
        sel_getName(method_getName(method)), method_getTypeEncoding(method),
        implementation, image.dli_fname ?: "unknown",
        image.dli_fbase ? (unsigned long)((uintptr_t)implementation -
                                         (uintptr_t)image.dli_fbase) : 0);
    const struct mach_header_64 *header = image.dli_fbase;
    if (header && header->magic == MH_MAGIC_64 && header->ncmds < 2048 &&
        header->sizeofcmds <= 256 * 1024) {
        const uint8_t *cursor = (const void *)(header + 1);
        const uint8_t *end = cursor + header->sizeofcmds;
        for (uint32_t i = 0; i < header->ncmds; i++) {
            if ((size_t)(end - cursor) < sizeof(struct load_command)) break;
            const struct load_command *command = (const void *)cursor;
            if (command->cmdsize < sizeof(*command) || command->cmdsize > (size_t)(end - cursor)) break;
            if (command->cmd == LC_UUID && command->cmdsize >= sizeof(struct uuid_command)) {
                const struct uuid_command *uuid = (const void *)cursor;
                for (unsigned j = 0; j < 16; j++) printf("%02x", uuid->uuid[j]);
            }
            cursor += command->cmdsize;
        }
    }
    puts("");
    // Executable code only, in this process. Not a shared-cache extraction.
    const uint8_t *code = (const void *)implementation;
    printf("NATIVE-METHOD code-bytes=%u hex=", code_size);
    for (unsigned i = 0; i < code_size; i++) printf("%02x", code[i]);
    puts("");
}

static int describe_resource_create(Method method) {
    Dl_info owner = {0}, resource_owner = {0};
    if (!dladdr((const void *)method_getImplementation(method), &owner)) return 77;
    void *function = dlsym(RTLD_DEFAULT, "IOGPUResourceCreate");
    if (!function || !dladdr(function, &resource_owner) ||
        owner.dli_fbase != resource_owner.dli_fbase ||
        (uintptr_t)function - (uintptr_t)owner.dli_fbase != 0x6040) {
        printf("NATIVE-RESOURCE-CREATE refuse unexpected function=%p\n", function);
        return 77;
    }
    printf("NATIVE-RESOURCE-CREATE address=%p owner=%s offset=0x6040 code-bytes=2048 hex=",
           function, resource_owner.dli_fname);
    const uint8_t *code = function;
    for (unsigned i = 0; i < 2048; i++) printf("%02x", code[i]);
    puts("");
    // Decode only the BL observed in this exact producer. Resolve its target
    // through dyld before reading a small executable prefix; never call it.
    uint32_t call = 0;
    memcpy(&call, code + 0xe8, sizeof(call));
    if ((call & 0xfc000000) != 0x94000000) return 77;
    int64_t displacement = (int64_t)(int32_t)(call << 6) >> 4;
    const void *target = code + 0xe8 + displacement;
    Dl_info target_owner = {0}, public_owner = {0};
    void *public_method = dlsym(RTLD_DEFAULT, "IOConnectCallMethod");
    (void)dladdr(target, &target_owner);
    if (public_method) (void)dladdr(public_method, &public_owner);
    // Shared-cache branch islands may not belong to a dyld image. In that case
    // require an actual readable/executable region in this task before reading.
    vm_address_t region = (vm_address_t)target;
    vm_size_t region_size = 0;
    vm_region_basic_info_data_64_t info = {0};
    mach_msg_type_number_t count = VM_REGION_BASIC_INFO_COUNT_64;
    mach_port_t object = MACH_PORT_NULL;
    kern_return_t region_result = vm_region_64(mach_task_self(), &region, &region_size,
        VM_REGION_BASIC_INFO_64, (vm_region_info_t)&info, &count, &object);
    if (object != MACH_PORT_NULL) mach_port_deallocate(mach_task_self(), object);
    BOOL readable = region_result == KERN_SUCCESS && region <= (uintptr_t)target &&
        region_size >= 64 && (uintptr_t)target - region <= region_size - 64 &&
        (info.protection & (VM_PROT_READ | VM_PROT_EXECUTE)) == (VM_PROT_READ | VM_PROT_EXECUTE);
    printf("NATIVE-RESOURCE-CALL target=%p owner=%s symbol=%s offset=%#lx "
           "public-method=%p public-owner=%s exact-public=%d code-bytes=64 hex=",
           target, target_owner.dli_fname ?: "unknown", target_owner.dli_sname ?: "unknown",
           target_owner.dli_fbase ? (unsigned long)((uintptr_t)target - (uintptr_t)target_owner.dli_fbase) : 0,
           public_method, public_owner.dli_fname ?: "unknown", target == public_method);
    const uint8_t *target_code = target;
    if (readable) for (unsigned i = 0; i < 64; i++) printf("%02x", target_code[i]);
    puts("");
    printf("NATIVE-RESOURCE-CALL region-result=%d executable=%d public-offset=%#lx\n",
           region_result, readable, public_owner.dli_fbase ?
           (unsigned long)((uintptr_t)public_method - (uintptr_t)public_owner.dli_fbase) : 0);
    return readable ? 0 : 77;
}

int main(int argc, char **argv) {
    alarm(10);
    setvbuf(stdout, NULL, _IONBF, 0);
    BOOL observe = argc == 2 && strcmp(argv[1], "--observe") == 0;
    BOOL resource_create = argc == 2 && strcmp(argv[1], "--resource-create") == 0;
    if (argc > 2 || (argc == 2 && !observe && !resource_create)) return 64;
    @autoreleasepool {
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        Class resource = objc_getClass("IOGPUMetalResource");
        SEL selector = sel_registerName("initWithDevice:options:args:argsSize:");
        Method method = resource ? class_getInstanceMethod(resource, selector) : NULL;
        if (!device || !method) {
            printf("NATIVE-METHOD unavailable device=%p class=%p method=%p\n", device, resource, method);
            [device release]; return 77;
        }
        describe(method, resource_create ? 64 : 2048);
        if (resource_create) {
            int result = describe_resource_create(method);
            [device release]; return result;
        }
        if (!observe) { [device release]; return 0; }
        char *result_type = method_copyReturnType(method);
        BOOL compatible = result_type && strcmp(result_type, "@") == 0 &&
            method_getNumberOfArguments(method) == 6 &&
            exact_type(method, 0, "@") && exact_type(method, 1, ":") &&
            exact_type(method, 2, "@") && exact_type(method, 3, "Q") &&
            exact_type(method, 4, "^{IOGPUNewResourceArgs={IOGPUNewResourceData=IISSSSCCCCIQQQ(?={?=QQQI}{?=IIII[2Q]})}}") &&
            exact_type(method, 5, "I");
        free(result_type);
        if (!compatible) { puts("NATIVE-METHOD refuse-unverified-types"); [device release]; return 77; }
        size_t length = (size_t)getpagesize();
        if (length < 4096 || length > 65536) { [device release]; return 77; }
        void *bytes = mmap(NULL, length, PROT_READ | PROT_WRITE, MAP_ANON | MAP_PRIVATE, -1, 0);
        if (bytes == MAP_FAILED) { [device release]; return 1; }
        memset(bytes, 0x25, length);
        original_init = (resource_init_fn)method_getImplementation(method);
        method_setImplementation(method, (IMP)observed_init);
        id<MTLBuffer> buffer = nil;
        @try {
            buffer = [device newBufferWithBytesNoCopy:bytes length:length
                options:MTLResourceStorageModeShared deallocator:^(void *p, NSUInteger size) {
                    printf("NATIVE-DEALLOC pointer=%p size=%lu expected=%d\n", p,
                           (unsigned long)size, p == bytes && size == length);
                    callbacks++;
                }];
        } @finally {
            method_setImplementation(method, (IMP)original_init);
            puts("NATIVE-METHOD restored=1");
        }
        BOOL alias = buffer && buffer.contents == bytes;
        printf("NATIVE-NOCOPY original=%p length=%zu buffer=%p contents=%p "
               "alias=%d callbacks-before-release=%u observations=%u\n",
               bytes, length, buffer, buffer.contents, alias, callbacks, observations);
        [buffer release]; [device release];
        munmap(bytes, length);
        return alias && callbacks == 1 ? 0 : 1;
    }
}

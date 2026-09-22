// Isolated native compiler replay; never injected into a live app/service.
// The optional target-context experiment calls Apple's real Triple builder
// with the captured inputs' explicit macOS or Catalyst platform. No
// loader/compiler check is bypassed.
#import <Foundation/Foundation.h>
#include <dlfcn.h>
#include <fcntl.h>
#include <mach/mach.h>
#include <mach-o/loader.h>
#include <ptrauth.h>
#include <stdio.h>
#include <sys/stat.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

// RE-confirmed libGPUCompilerImpl 41d2f0618da83cfa8c4ac3e9d7009604:
// getDefaultTargetTriple(Optional<PlatformType>) has a 48-byte indirect
// return in x8, Optional presence at x0 bits 32..39, platform in w0.
// This opaque value is constructed/destroyed only by Apple's own code.
typedef struct { uint64_t storage[6]; } TripleABI;
typedef TripleABI (*TripleFn)(uint64_t);
static TripleFn originalTriple;
static uint32_t TargetPlatform;
typedef struct {
    const char *left;
    uintptr_t right;
    uint8_t leftKind, rightKind, padding[6];
} TwineABI;
static void (*SetTriple)(TripleABI *, const TwineABI *);
extern int csops(pid_t, unsigned, void *, size_t);

static BOOL AuthorizeOwnDiagnosticCode(void) {
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) return NO;
    struct timeval deadline = {2, 0};
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &deadline, sizeof(deadline));
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &deadline, sizeof(deadline));
    struct sockaddr_un address = {.sun_family = AF_UNIX};
    strlcpy(address.sun_path, "/var/mnt/rootfs/tmp/autosignd.sock", sizeof(address.sun_path));
    char reply[8] = {0};
    BOOL ok = !connect(fd, (void *)&address, sizeof(address)) &&
        write(fd, "DEBUG\n", 6) == 6 && read(fd, reply, 7) >= 3 && !memcmp(reply, "OK\n", 3);
    close(fd);
    uint32_t flags = 0;
    ok = ok && !csops(getpid(), 0, &flags, sizeof(flags)) && (flags & 0x10000000);
    fprintf(stderr, "DAG-REPLAY own-jit=%d csflags=%#x\n", ok, flags);
    return ok;
}
static TripleABI TargetTriple(uint64_t platform) {
    if (!platform && TargetPlatform == UINT32_MAX && SetTriple) {
        TripleABI result = originalTriple(platform);
        const TwineABI text = {.left = "air64-apple-macosx13.4.0", .leftKind = 3, .rightKind = 1};
        SetTriple(&result, &text);
        fprintf(stderr, "DAG-REPLAY real-setTriple=air64-apple-macosx13.4.0\n");
        return result;
    }
    uint64_t requested = platform ? platform : (UINT64_C(1) << 32) | TargetPlatform;
    fprintf(stderr, "DAG-REPLAY target optional=%#llx -> %#llx\n", platform, requested);
    return originalTriple(requested);
}

static BOOL HasUUID(const void *function, const uint8_t expected[16]) {
    Dl_info info = {0};
    if (!dladdr(ptrauth_strip(function, ptrauth_key_function_pointer), &info)) return NO;
    const struct mach_header_64 *header = info.dli_fbase;
    if (!header || header->magic != MH_MAGIC_64 || header->sizeofcmds > 1048576) return NO;
    const uint8_t *cursor = (const void *)(header + 1), *end = cursor + header->sizeofcmds;
    for (uint32_t i = 0; i < header->ncmds && cursor + sizeof(struct load_command) <= end; i++) {
        const struct load_command *cmd = (const void *)cursor;
        if (cmd->cmdsize < sizeof(*cmd) || cmd->cmdsize > (size_t)(end - cursor)) return NO;
        if (cmd->cmd == LC_UUID && cmd->cmdsize >= sizeof(struct uuid_command))
            return !memcmp(((const struct uuid_command *)cmd)->uuid, expected, 16);
        cursor += cmd->cmdsize;
    }
    return NO;
}

int main(int argc, char **argv) {
    if (argc == 2 && !strcmp(argv[1], "--triple-bytes")) {
        alarm(5);
        void *gpu = dlopen("/System/Library/PrivateFrameworks/GPUCompiler.framework/Libraries/libGPUCompilerImpl.dylib", RTLD_NOW | RTLD_LOCAL);
        void *target = dlsym(gpu, "_ZN7metalfe11GPUCompiler22getDefaultTargetTripleEN4llvm8OptionalINS1_5MachO12PlatformTypeEEE");
        const uint8_t uuid[16] = {0x41,0xd2,0xf0,0x61,0x8d,0xa8,0x3c,0xfa,0x8c,0x4a,0xc3,0xe9,0xd7,0x00,0x96,0x04};
        if (!target || !HasUUID(target, uuid)) return 68;
        const uint8_t *entry = ptrauth_strip(target, ptrauth_key_function_pointer);
        Dl_info info = {0};
        if (!dladdr(entry, &info)) return 68;
        printf("DAG-REPLAY triple image=%s offset=%#lx\n", info.dli_fname,
            (unsigned long)(entry - (const uint8_t *)info.dli_fbase));
        for (unsigned index = 0; index < 512; ++index) printf("%02x", entry[index]);
        printf("\n");
        const char *names[] = {
            "_ZN4llvm6TripleC1ERKNS_5TwineE", "_ZN4llvm6TripleC2ERKNS_5TwineE",
            "_ZN4llvm6Triple9setTripleERKNS_5TwineE",
            "_ZN4llvm6Triple9setTripleENS_9StringRefE",
            "_ZN4llvm6Triple9normalizeENS_9StringRefE",
            "_ZNK4llvm5Twine3strEv",
        };
        for (unsigned index = 0; index < sizeof(names) / sizeof(names[0]); ++index) {
            void *symbol = dlsym(gpu, names[index]);
            Dl_info resolved = {0};
            if (symbol) dladdr(ptrauth_strip(symbol, ptrauth_key_function_pointer), &resolved);
            printf("DAG-REPLAY symbol=%s address=%p image=%s offset=%#lx\n",
                names[index], symbol, resolved.dli_fname ?: "nil",
                symbol ? (unsigned long)((uintptr_t)ptrauth_strip(symbol, ptrauth_key_function_pointer) - (uintptr_t)resolved.dli_fbase) : 0);
            if (symbol && (index == 2 || index == 5)) {
                const uint8_t *instructions = ptrauth_strip(symbol, ptrauth_key_function_pointer);
                for (unsigned byte = 0; byte < 256; ++byte) printf("%02x", instructions[byte]);
                printf("\n");
                const struct mach_header_64 *header = resolved.dli_fbase;
                const uint8_t *cursor = (const void *)(header + 1);
                for (unsigned load = 0; load < header->ncmds; ++load) {
                    const struct load_command *command = (const void *)cursor;
                    if (command->cmd == LC_UUID) {
                        const uint8_t *uuidBytes = ((const struct uuid_command *)command)->uuid;
                        printf("DAG-REPLAY symbol-uuid=");
                        for (unsigned byte = 0; byte < 16; ++byte) printf("%02x", uuidBytes[byte]);
                        printf("\n");
                    }
                    cursor += command->cmdsize;
                }
            }
        }
        const uint8_t *callee = (const uint8_t *)info.dli_fbase + 0x4e2f030;
        Dl_info resolved = {0};
        if (dladdr(callee, &resolved)) {
            printf("DAG-REPLAY constructor image=%s name=%s offset=%#lx\n",
                resolved.dli_fname ?: "nil", resolved.dli_sname ?: "nil",
                (unsigned long)(callee - (const uint8_t *)resolved.dli_fbase));
            for (unsigned index = 0; index < 192; ++index) printf("%02x", callee[index]);
            printf("\n");
        }
        return 0;
    }
    if (argc != 3 && argc != 4) return 64;
    if (argc == 4) {
        if (!strcmp(argv[3], "--catalyst-context")) TargetPlatform = 6;
        else if (!strcmp(argv[3], "--macos-context")) TargetPlatform = 1;
        else if (!strcmp(argv[3], "--macos-triple")) TargetPlatform = UINT32_MAX;
        else return 64;
    }
    alarm(30);
    @autoreleasepool {
        NSData *request = [NSData dataWithContentsOfFile:[NSString stringWithUTF8String:argv[1]]];
        if (request.length < 16 || request.length > 4*1048576 ||
            memcmp(request.bytes, " gad", 4)) return 65;
        int output = open(argv[2], O_WRONLY | O_CREAT | O_EXCL, 0600);
        if (output < 0) return 66;
        void *compiler = dlopen("/System/Library/PrivateFrameworks/MTLCompiler.framework/MTLCompiler", RTLD_NOW | RTLD_LOCAL);
        void *(*create)(void) = dlsym(compiler, "MTLCodeGenServiceCreate");
        void (*destroy)(void *) = dlsym(compiler, "MTLCodeGenServiceDestroy");
        void (*build)(void *, unsigned, unsigned, const void *, size_t,
                      void (^)(unsigned, const void *, size_t, const char *)) =
            dlsym(compiler, "MTLCodeGenServiceBuildRequest");
        if (!create || !destroy || !build) { close(output); unlink(argv[2]); return 67; }
        if (argc == 4) {
            if (!AuthorizeOwnDiagnosticCode()) { close(output); unlink(argv[2]); return 68; }
            void *gpu = dlopen("/System/Library/PrivateFrameworks/GPUCompiler.framework/Libraries/libGPUCompilerImpl.dylib", RTLD_NOW | RTLD_LOCAL);
            void *target = dlsym(gpu, "_ZN7metalfe11GPUCompiler22getDefaultTargetTripleEN4llvm8OptionalINS1_5MachO12PlatformTypeEEE");
            const uint8_t uuid[16] = {0x41,0xd2,0xf0,0x61,0x8d,0xa8,0x3c,0xfa,0x8c,0x4a,0xc3,0xe9,0xd7,0x00,0x96,0x04};
            void *substrate = dlopen("/var/jb/usr/lib/libsubstrate.dylib", RTLD_NOW | RTLD_LOCAL);
            void (*hook)(void *, void *, void **) = dlsym(substrate, "MSHookFunction");
            if (!target || !hook || !HasUUID(target, uuid)) {
                fprintf(stderr, "DAG-REPLAY target experiment unavailable target=%p hook=%p\n", target, hook);
                close(output); unlink(argv[2]); return 68;
            }
            if (TargetPlatform == UINT32_MAX) {
                SetTriple = dlsym(gpu, "_ZN4llvm6Triple9setTripleERKNS_5TwineE");
                const uint8_t llvmUUID[16] = {0x3c,0x9d,0x9d,0x6c,0xcc,0x92,0x32,0x6a,0x91,0x1c,0x14,0x1b,0xc9,0x6d,0xc8,0xbe};
                const uint32_t setterPrologue[] = {0xd503237f, 0xd10143ff, 0xa9034ff4, 0xa9047bfd};
                if (!SetTriple || !HasUUID(SetTriple, llvmUUID) ||
                    memcmp(ptrauth_strip(SetTriple, ptrauth_key_function_pointer), setterPrologue, sizeof(setterPrologue))) {
                    close(output); unlink(argv[2]); return 68;
                }
            }
            hook(target, (void *)TargetTriple, (void **)&originalTriple);
            if (!originalTriple) { close(output); unlink(argv[2]); return 69; }
            // ElleKit's far-target hook copy leaves this page non-executable
            // in the arm64 diagnostic executable (runtime crash 14:40:28,
            // instruction permission fault at the neighbouring ctor). Finish
            // the normal code-patch RX transition, without touching any check.
            kern_return_t protection = vm_protect(mach_task_self(),
                (vm_address_t)ptrauth_strip(target, ptrauth_key_function_pointer),
                32, false, VM_PROT_READ | VM_PROT_EXECUTE);
            fprintf(stderr, "DAG-REPLAY hook restore-rx=%d\n", protection);
            if (protection) { close(output); unlink(argv[2]); return 69; }
        }
        void *service = create();
        __block int status = 70;
        build(service, UINT32_MAX, 14, request.bytes, request.length,
            ^(unsigned result, const void *bytes, size_t length, const char *error) {
                fprintf(stderr, "DAG-REPLAY result=%u length=%zu error=%s\n", result, length, error ?: "nil");
                status = result ? 71 : 0;
                if (!result && bytes && length <= 4*1048576) {
                    size_t written = 0;
                    while (written < length) {
                        ssize_t n = write(output, (const uint8_t *)bytes + written, length - written);
                        if (n <= 0) { status = 72; break; }
                        written += n;
                    }
                } else status = 71;
            });
        destroy(service);
        close(output);
        if (status) unlink(argv[2]);
        return status;
    }
}

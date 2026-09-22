// Standalone diagnostic injection. Never linked into production libmachook.
// RE: macOS 13.4 Metal UUID 2BAB169C-42DA-36E3-955A-F30B709EC2AD,
// MTLSetShaderCachePath +0x983e4 takes NSString*, calls UTF8String, then
// setShaderCacheMainFolder +0x463d0. That stock setter refuses late changes.
// getCacheMainFolder +0x4ad74 returns the override unchanged, before confstr.
// No GPU/device creation, cache deletion, method hooks, or return-value changes.
#import <Foundation/Foundation.h>
#include <dlfcn.h>
#include <errno.h>
#include <mach-o/loader.h>
#include <ptrauth.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

typedef void (*SetCachePathFn)(NSString *);
typedef NSString *(*GetCachePathFn)(void);
static int scope_status = 70;
static char scope_directory[256];

static BOOL MetalIdentity(const void *function) {
    Dl_info info = {0};
    function = ptrauth_strip(function, ptrauth_key_function_pointer);
    if (!function || !dladdr(function, &info) || !info.dli_fbase ||
        !info.dli_fname || !strstr(info.dli_fname, "/Metal.framework/")) return NO;
    const struct mach_header_64 *header = info.dli_fbase;
    if (header->magic != MH_MAGIC_64 || header->sizeofcmds > 262144 ||
        header->ncmds > 2048) return NO;
    const uint8_t expected[16] = {0x2b,0xab,0x16,0x9c,0x42,0xda,0x36,0xe3,
                                 0x95,0x5a,0xf3,0x0b,0x70,0x9e,0xc2,0xad};
    const uint8_t *cursor = (const void *)(header + 1);
    const uint8_t *limit = cursor + header->sizeofcmds;
    for (uint32_t i = 0; i < header->ncmds; ++i) {
        if ((size_t)(limit - cursor) < sizeof(struct load_command)) return NO;
        const struct load_command *command = (const void *)cursor;
        if (command->cmdsize < sizeof(*command) || command->cmdsize >
                (size_t)(limit - cursor)) return NO;
        if (command->cmd == LC_UUID) {
            return command->cmdsize >= sizeof(struct uuid_command) &&
                !memcmp(((const struct uuid_command *)command)->uuid,
                        expected, sizeof(expected));
        }
        cursor += command->cmdsize;
    }
    return NO;
}

__attribute__((constructor)) static void InstallOwnedCacheScope(void) {
    const int saved_errno = errno;
    @autoreleasepool {
        // Foundation is linked; Metal is already a dependency of the test app.
        // dlopen loads metadata only in the standalone no-device control.
        void *metal = dlopen("/System/Library/Frameworks/Metal.framework/Metal",
                             RTLD_LAZY | RTLD_LOCAL);
        SetCachePathFn set_path = metal ? dlsym(metal, "MTLSetShaderCachePath") : NULL;
        GetCachePathFn get_path = metal ? dlsym(metal, "MTLGetShaderCachePath") : NULL;
        if (!set_path || !get_path || !MetalIdentity((const void *)set_path) ||
            !MetalIdentity((const void *)get_path)) {
            fprintf(stderr, "METAL-CACHE-SCOPE ERROR unsupported Metal identity/API\n");
        } else {
            snprintf(scope_directory, sizeof(scope_directory),
                     "/private/tmp/macws-metal-cache-scope-%d-XXXXXX", getpid());
            if (!mkdtemp(scope_directory)) {
                fprintf(stderr, "METAL-CACHE-SCOPE ERROR mkdtemp errno=%d\n", errno);
            } else {
                NSString *path = [[NSString alloc] initWithUTF8String:scope_directory];
                set_path(path);
                NSString *actual = get_path();
                struct stat st = {0};
                BOOL valid = [actual isKindOfClass:[NSString class]] &&
                    [actual isEqualToString:path] &&
                    !lstat(scope_directory, &st) && S_ISDIR(st.st_mode) &&
                    st.st_uid == geteuid() && (st.st_mode & 0777) == 0700;
                scope_status = valid ? 0 : 70;
                fprintf(stderr,
                    "METAL-CACHE-SCOPE %s pid=%d requested=%s actual=%s "
                    "inode=%llu no-gpu=yes no-shared-cache-mutation=yes\n",
                    valid ? "READY" : "ERROR late-or-rejected", getpid(),
                    scope_directory, actual ? actual.UTF8String : "(nil)",
                    (unsigned long long)st.st_ino);
            }
        }
        // Preserve the framework lifetime; the program's Metal paths use it.
    }
    errno = saved_errno;
}

#ifdef MACWS_METAL_CACHE_SCOPE_STANDALONE
int main(void) {
    fprintf(stderr, "METAL-CACHE-SCOPE-CONTROL status=%d path=%s\n",
            scope_status, scope_directory);
    return scope_status;
}
#endif

// One-shot diagnostic in the macOS chroot. No task attachment, dyld debugger
// subscription, memory writes, or whole-cache extraction. Cache addresses are
// meaningful only in the same boot; compare UUIDs before using an offset.
#include <dlfcn.h>
#include <mach-o/dyld.h>
#include <mach-o/loader.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char **argv) {
    if (argc < 2 || argc > 100) return 64;
    alarm(15);
    const char *libraries[] = {
        "/System/Library/Frameworks/AppKit.framework/AppKit",
        "/System/Library/Frameworks/QuartzCore.framework/QuartzCore",
        "/System/Library/PrivateFrameworks/CoreUI.framework/CoreUI",
        "/System/Library/Frameworks/SwiftUI.framework/SwiftUI",
    };
    for (unsigned i = 0; i < sizeof(libraries) / sizeof(*libraries); ++i)
        if (!dlopen(libraries[i], RTLD_LAZY | RTLD_LOCAL))
            fprintf(stderr, "load %s: %s\n", libraries[i], dlerror());
    for (int i = 1; i < argc; ++i) {
        char *end;
        uint64_t address = strtoull(argv[i], &end, 0);
        if (!address || *end) return 64;
        Dl_info info = {0};
        if (!dladdr((void *)(uintptr_t)address, &info)) {
            printf("%s unresolved\n", argv[i]);
            continue;
        }
        printf("%s image=%s base=%p offset=0x%llx symbol=%s+0x%llx\n",
            argv[i], info.dli_fname, info.dli_fbase,
            (unsigned long long)(address - (uintptr_t)info.dli_fbase),
            info.dli_sname ?: "?",
            (unsigned long long)(info.dli_saddr ? address - (uintptr_t)info.dli_saddr : 0));
        const struct mach_header_64 *header = info.dli_fbase;
        for (uint32_t j = 0; j < _dyld_image_count(); ++j)
            if (_dyld_get_image_header(j) == (const struct mach_header *)header)
                printf("slide=0x%llx unslid=0x%llx\n",
                    (unsigned long long)_dyld_get_image_vmaddr_slide(j),
                    (unsigned long long)(address - _dyld_get_image_vmaddr_slide(j)));
        if (header->magic != MH_MAGIC_64 || header->sizeofcmds > 1024 * 1024) continue;
        const unsigned char *cursor = (const void *)(header + 1);
        const unsigned char *limit = cursor + header->sizeofcmds;
        for (unsigned j = 0; j < header->ncmds && cursor + sizeof(struct load_command) <= limit; ++j) {
            const struct load_command *cmd = (const void *)cursor;
            if (cmd->cmdsize < sizeof(*cmd) || cmd->cmdsize > (size_t)(limit - cursor)) break;
            if (cmd->cmd == LC_UUID && cmd->cmdsize >= sizeof(struct uuid_command)) {
                const struct uuid_command *uuid = (const void *)cursor;
                printf("uuid=");
                for (unsigned k = 0; k < 16; ++k) printf("%02x", uuid->uuid[k]);
                putchar('\n');
            }
            cursor += cmd->cmdsize;
        }
    }
    return 0;
}

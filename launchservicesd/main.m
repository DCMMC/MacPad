//
//  launchservicesd.m
//  
//
//  Created by Duy Tran on 3/8/25.
//

@import Darwin;
@import MachO;
#include <stdio.h>
#include <string.h>

static bool path_has_suffix(const char *path, const char *suffix) {
    if (!path || !suffix) return false;
    size_t path_length = strlen(path);
    size_t suffix_length = strlen(suffix);
    return path_length >= suffix_length &&
        strcmp(path + path_length - suffix_length, suffix) == 0;
}

void *dlopen_entry_point(const char *path, int flags) {
    void *handle = dlopen(path, flags);
    if(!handle) {
        fprintf(stderr, "launchservicesd loader: dlopen failed: %s\n",
            dlerror());
        return NULL;
    }

    const struct mach_header_64 *header = NULL;
    uint32_t image_count = _dyld_image_count();
    for (uint32_t index = 0; index < image_count; ++index) {
        const char *image_name = _dyld_get_image_name(index);
        if (path_has_suffix(image_name, "/launchservicesd.dylib")) {
            header = (const struct mach_header_64 *)
                _dyld_get_image_header(index);
            break;
        }
    }
    if (!header || header->magic != MH_MAGIC_64) {
        fprintf(stderr,
            "launchservicesd loader: payload image header was not found\n");
        return NULL;
    }

    uint64_t entryoff = 0;
    const uint8_t *commands_begin = (const uint8_t *)header +
        sizeof(struct mach_header_64);
    const uint8_t *commands_end = commands_begin + header->sizeofcmds;
    const uint8_t *imageHeaderPtr = commands_begin;
    for(uint32_t i = 0; i < header->ncmds; ++i) {
        if (imageHeaderPtr + sizeof(struct load_command) > commands_end) break;
        const struct load_command *command =
            (const struct load_command *)imageHeaderPtr;
        if (command->cmdsize < sizeof(struct load_command) ||
            imageHeaderPtr + command->cmdsize > commands_end) break;
        if(command->cmd == LC_MAIN) {
            if (command->cmdsize < sizeof(struct entry_point_command)) break;
            const struct entry_point_command *ucmd =
                (const struct entry_point_command *)imageHeaderPtr;
            entryoff = ucmd->entryoff;
            break;
        }
        imageHeaderPtr += command->cmdsize;
    }
    if (entryoff == 0) {
        fprintf(stderr,
            "launchservicesd loader: payload has no valid LC_MAIN entry\n");
        return NULL;
    }
    return (void *)header + entryoff;
}

int main(int argc, const char **argv, const char **envp, const char **apple) {
    int( *original_main)(int argc, const char **argv, const char **envp, const char **apple) = dlopen_entry_point("@loader_path/launchservicesd.dylib", RTLD_GLOBAL);
    if (!original_main) return EXIT_FAILURE;
    __attribute__((musttail))return original_main(argc, argv, envp, apple);
}

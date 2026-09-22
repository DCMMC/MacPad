#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <mach-o/dyld.h>
#include <mach-o/loader.h>
#include <mach-o/nlist.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

typedef unsigned (*macws_translate_fn)(
    unsigned sequence, unsigned char *commands, size_t *commands_length,
    unsigned char *segments, size_t *segments_length);

static const char *basename_of(const char *path) {
    const char *slash = path ? strrchr(path, '/') : NULL;
    return slash ? slash + 1 : path;
}

static void *map_readonly(const char *path, size_t *length_out) {
    int fd = open(path, O_RDONLY);
    if (fd < 0) {
        fprintf(stderr, "open %s: %s\n", path, strerror(errno));
        return NULL;
    }
    struct stat status = {0};
    if (fstat(fd, &status) != 0 || status.st_size <= 0) {
        fprintf(stderr, "stat %s: %s\n", path, strerror(errno));
        close(fd);
        return NULL;
    }
    size_t length = (size_t)status.st_size;
    void *mapping = mmap(NULL, length, PROT_READ, MAP_PRIVATE, fd, 0);
    close(fd);
    if (mapping == MAP_FAILED) {
        fprintf(stderr, "mmap %s: %s\n", path, strerror(errno));
        return NULL;
    }
    *length_out = length;
    return mapping;
}

static uint64_t symbol_value_in_macho(
    const void *mapping, size_t length, const char *symbol_name) {
    if (!mapping || length < sizeof(struct mach_header_64)) return 0;
    const struct mach_header_64 *header = mapping;
    if (header->magic != MH_MAGIC_64 ||
        sizeof(*header) + header->sizeofcmds > length) {
        return 0;
    }

    const struct symtab_command *symtab = NULL;
    const uint8_t *command_bytes = (const uint8_t *)(header + 1);
    for (uint32_t index = 0; index < header->ncmds; index++) {
        const struct load_command *command =
            (const struct load_command *)command_bytes;
        size_t command_offset = (size_t)(command_bytes - (const uint8_t *)mapping);
        if (command_offset + sizeof(*command) > length ||
            command->cmdsize < sizeof(*command) ||
            command_offset + command->cmdsize > length) {
            return 0;
        }
        if (command->cmd == LC_SYMTAB &&
            command->cmdsize >= sizeof(struct symtab_command)) {
            symtab = (const struct symtab_command *)command;
        }
        command_bytes += command->cmdsize;
    }
    if (!symtab ||
        (size_t)symtab->symoff +
            (size_t)symtab->nsyms * sizeof(struct nlist_64) > length ||
        (size_t)symtab->stroff + symtab->strsize > length) {
        return 0;
    }

    const struct nlist_64 *symbols =
        (const struct nlist_64 *)((const uint8_t *)mapping + symtab->symoff);
    const char *strings = (const char *)mapping + symtab->stroff;
    for (uint32_t index = 0; index < symtab->nsyms; index++) {
        uint32_t string_offset = symbols[index].n_un.n_strx;
        if (string_offset >= symtab->strsize) continue;
        const char *name = strings + string_offset;
        size_t available = symtab->strsize - string_offset;
        if (!memchr(name, '\0', available)) continue;
        if (strcmp(name, symbol_name) == 0) return symbols[index].n_value;
    }
    return 0;
}

static intptr_t loaded_image_slide(const char *image_basename) {
    for (uint32_t index = 0; index < _dyld_image_count(); index++) {
        const char *name = _dyld_get_image_name(index);
        if (name && strcmp(basename_of(name), image_basename) == 0) {
            return _dyld_get_image_vmaddr_slide(index);
        }
    }
    return INTPTR_MIN;
}

static unsigned char *copy_file(const char *path, size_t *length_out) {
    size_t length = 0;
    void *mapping = map_readonly(path, &length);
    if (!mapping) return NULL;
    unsigned char *copy = malloc(length);
    if (copy) memcpy(copy, mapping, length);
    munmap(mapping, length);
    if (!copy) fprintf(stderr, "malloc %zu for %s failed\n", length, path);
    *length_out = copy ? length : 0;
    return copy;
}

int main(int argc, char **argv) {
    if (argc != 5) {
        fprintf(stderr,
            "usage: %s <loaded-dylib-file> <local-symbol> <kcmd> <segments>\n",
            argv[0]);
        return 64;
    }

    size_t dylib_length = 0;
    void *dylib = map_readonly(argv[1], &dylib_length);
    if (!dylib) return 1;
    uint64_t symbol_value = symbol_value_in_macho(dylib, dylib_length, argv[2]);
    munmap(dylib, dylib_length);
    if (!symbol_value) {
        fprintf(stderr, "symbol not found: %s\n", argv[2]);
        return 2;
    }

    const char *loaded_basename = basename_of(argv[1]);
    intptr_t slide = loaded_image_slide(loaded_basename);
    if (slide == INTPTR_MIN &&
        strcmp(loaded_basename, "libmachook_arm64.dylib") != 0) {
        loaded_basename = "libmachook_arm64.dylib";
        slide = loaded_image_slide(loaded_basename);
    }
    if (slide == INTPTR_MIN) {
        fprintf(stderr, "loaded image not found: %s\n", loaded_basename);
        return 3;
    }
    macws_translate_fn translate =
        (macws_translate_fn)(uintptr_t)(symbol_value + (uint64_t)slide);

    size_t commands_length = 0;
    size_t segments_length = 0;
    unsigned char *commands = copy_file(argv[3], &commands_length);
    unsigned char *segments = copy_file(argv[4], &segments_length);
    if (!commands || !segments) {
        free(commands);
        free(segments);
        return 4;
    }

    size_t original_commands_length = commands_length;
    size_t original_segments_length = segments_length;
    unsigned fixed = translate(
        0xfeed, commands, &commands_length, segments, &segments_length);
    printf(
        "symbol=%s value=%#" PRIx64 " slide=%#" PRIxPTR
        " runtime=%p fixed=%u kcmd=%#zx->%#zx segments=%#zx->%#zx\n",
        argv[2], symbol_value, (uintptr_t)slide, (void *)translate, fixed,
        original_commands_length, commands_length,
        original_segments_length, segments_length);
    if (commands_length >= 8) {
        printf("kcmd type=%#x span=%#x\n",
               *(uint32_t *)(commands + 0), *(uint32_t *)(commands + 4));
    }
    if (segments_length >= 0x20) {
        printf("list count=%u encoded=%#x firstRange=%#x..%#x\n",
               *(uint32_t *)(segments + 0x08),
               *(uint32_t *)(segments + 0x0c),
               *(uint32_t *)(segments + 0x18),
               *(uint32_t *)(segments + 0x1c));
    }

    free(commands);
    free(segments);
    return fixed ? 0 : 5;
}

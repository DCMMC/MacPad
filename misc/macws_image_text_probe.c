// Standalone, bounded runtime RE witness. Copies only executable __TEXT
// mappings of one explicitly named loaded image, never a whole dyld cache.
// No attachment, hooks or memory writes. Output must not exist beforehand.
#include <dlfcn.h>
#include <fcntl.h>
#include <mach/mach.h>
#include <mach-o/dyld.h>
#include <mach-o/loader.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char **argv) {
    if (argc != 3 && argc != 4) return 64;
    int exports = argc == 4 && !strcmp(argv[3], "--exports");
    unsigned long rangeOffset = 0, rangeLength = 0;
    if (argc == 4 && !exports &&
        (sscanf(argv[3], "%lx:%lx", &rangeOffset, &rangeLength) != 2 ||
         !rangeLength || rangeLength > 32768)) return 64;
    alarm(20);
    if (!dlopen(argv[1], RTLD_LAZY | RTLD_LOCAL)) {
        fprintf(stderr, "dlopen: %s\n", dlerror()); return 65;
    }
    for (uint32_t i = 0; i < _dyld_image_count(); i++) {
        if (strcmp(_dyld_get_image_name(i), argv[1])) continue;
        const struct mach_header_64 *header = (const void *)_dyld_get_image_header(i);
        if (header->magic != MH_MAGIC_64 || header->sizeofcmds > 1048576) return 66;
        const unsigned char *cursor = (const void *)(header + 1);
        const unsigned char *end = cursor + header->sizeofcmds;
        intptr_t slide = _dyld_get_image_vmaddr_slide(i);
        uintptr_t linkeditBase = 0;
        uint32_t exportOffset = 0, exportSize = 0;
        printf("IMAGE path=%s base=%p slide=%#lx\n", argv[1], header, (unsigned long)slide);
        for (uint32_t j = 0; j < header->ncmds && cursor + sizeof(struct load_command) <= end; j++) {
            const struct load_command *cmd = (const void *)cursor;
            if (cmd->cmdsize < sizeof(*cmd) || cmd->cmdsize > (size_t)(end - cursor)) return 66;
            if (cmd->cmd == LC_UUID && cmd->cmdsize >= sizeof(struct uuid_command)) {
                const struct uuid_command *uuid = (const void *)cursor;
                printf("UUID ");
                for (unsigned k = 0; k < 16; k++) printf("%02x", uuid->uuid[k]);
                putchar('\n');
            }
            if (cmd->cmd == LC_DYLD_INFO_ONLY && cmd->cmdsize >= sizeof(struct dyld_info_command)) {
                const struct dyld_info_command *dyld = (const void *)cursor;
                exportOffset = dyld->export_off; exportSize = dyld->export_size;
            }
            if (cmd->cmd == LC_DYLD_EXPORTS_TRIE && cmd->cmdsize >= sizeof(struct linkedit_data_command)) {
                const struct linkedit_data_command *dyld = (const void *)cursor;
                exportOffset = dyld->dataoff; exportSize = dyld->datasize;
            }
            if (cmd->cmd == LC_SEGMENT_64 && cmd->cmdsize >= sizeof(struct segment_command_64)) {
                const struct segment_command_64 *seg = (const void *)cursor;
                printf("SEG name=%.16s vm=%#llx size=%#llx prot=%u\n", seg->segname,
                       seg->vmaddr, seg->vmsize, seg->initprot);
                if (!strncmp(seg->segname, "__LINKEDIT", 16))
                    linkeditBase = seg->vmaddr + slide - seg->fileoff;
                size_t textLength = rangeLength ? rangeLength : (size_t)seg->vmsize;
                if (!exports && !strncmp(seg->segname, "__TEXT", 16) && textLength &&
                    textLength <= 4*1048576 && rangeOffset <= seg->vmsize &&
                    textLength <= seg->vmsize - rangeOffset) {
                    unsigned char *bytes = malloc(textLength);
                    vm_size_t copied = 0;
                    uintptr_t address = seg->vmaddr + slide + rangeOffset;
                    if (!bytes || vm_read_overwrite(mach_task_self(), address,
                        textLength, (vm_address_t)bytes, &copied) || copied != textLength) return 67;
                    int fd = open(argv[2], O_WRONLY | O_CREAT | O_EXCL, 0600);
                    if (fd < 0) return 68;
                    size_t written = 0;
                    while (written < copied) {
                        ssize_t n = write(fd, bytes + written, copied - written);
                        if (n <= 0) { close(fd); unlink(argv[2]); return 69; }
                        written += (size_t)n;
                    }
                    close(fd);
                    printf("TEXT address=%#lx size=%#lx output=%s\n", (unsigned long)address, (unsigned long)copied, argv[2]);
                    // Resolve direct-call targets from the actual live cache,
                    // preserving local C++ symbol names absent from exports.
                    for (size_t k = 0; k + 4 <= copied; k += 4) {
                        uint32_t insn;
                        memcpy(&insn, bytes + k, 4);
                        if ((insn & 0xfc000000) != 0x94000000) continue;
                        int32_t displacement = ((int32_t)(insn << 6)) >> 4;
                        uintptr_t target = address + k + displacement;
                        Dl_info symbol = {0};
                        if (dladdr((void *)target, &symbol) && symbol.dli_sname)
                            printf("CALL offset=%#zx target=%#lx symbol-base=%p symbol=%s\n",
                                k, (unsigned long)target, symbol.dli_saddr, symbol.dli_sname);
                    }
                    free(bytes);
                }
            }
            cursor += cmd->cmdsize;
        }
        if (exports) {
            if (!linkeditBase || !exportSize || exportSize > 4*1048576) return 71;
            unsigned char *bytes = malloc(exportSize);
            vm_size_t copied = 0;
            if (!bytes || vm_read_overwrite(mach_task_self(), linkeditBase + exportOffset,
                exportSize, (vm_address_t)bytes, &copied) || copied != exportSize) return 72;
            int fd = open(argv[2], O_WRONLY | O_CREAT | O_EXCL, 0600);
            if (fd < 0) return 73;
            size_t written = 0;
            while (written < copied) {
                ssize_t n = write(fd, bytes + written, copied - written);
                if (n <= 0) { close(fd); unlink(argv[2]); return 74; }
                written += (size_t)n;
            }
            close(fd); free(bytes);
            printf("EXPORTS size=%u output=%s\n", exportSize, argv[2]);
        }
        return 0;
    }
    return 70;
}

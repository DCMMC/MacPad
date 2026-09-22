// Read-only bounded identity witness for known MacWS processes/images.
// No ptrace, thread suspension, target writes, environment or heap dump.
// UUID identifies the build; __text SHA-256 identifies instruction bytes only,
// not an entire mapped file. Legitimate runtime hooks may change that hash.
#include <CommonCrypto/CommonDigest.h>
#include <errno.h>
#include <limits.h>
#include <mach/mach.h>
#include <mach-o/loader.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

extern int proc_pidpath(int, void *, uint32_t);
extern kern_return_t mach_vm_read_overwrite(vm_map_t, mach_vm_address_t,
    mach_vm_size_t, mach_vm_address_t, mach_vm_size_t *);
#define MAX_IMAGES 4096u
#define MAX_COMMAND_BYTES (256u * 1024u)
#define MAX_TEXT_BYTES (8u * 1024u * 1024u)
static const char *const processes[] = {
    "MTLCompilerService", "WindowServer", "MacWSHost", "SpringBoard",
    "runningboardd", "macwsinputd", "macwsdisplayd", "macwshostd",
    "macwsaudiooutd", "macwsallocd", "autosignd", "OSXvnc-server",
    // Explicit representative consumers for installed-vs-mapped acceptance.
    // Still inspect only the allowlisted image headers/text below, never
    // application documents, heap, stack, environment, or arbitrary images.
    "Terminal", "bash", "Finder", "Preview", "Weather", "Electron",
    "iconservicesagent", "com.apple.quicklook.ThumbnailsAgent", NULL
};
static const char *const images[] = {
    "MTLCompilerService", "MTLCompilerBypassOSCheck.dylib", "WindowServer",
    "MacWSHost", "MacWSWindowing.dylib", "MacWSCatalystLaunch.dylib",
    "macwsinputd", "macwsdisplayd", "macwshostd", "macwsaudiooutd",
    "macwsallocd", "autosignd", "OSXvnc-server", "libmachook.dylib",
    "libmachook_arm64.dylib", NULL
};
static const char *base_name(const char *path) {
    const char *slash = strrchr(path, '/');
    return slash ? slash + 1 : path;
}
static int allowed(const char *value, const char *const *list) {
    for (unsigned i = 0; list[i]; i++) if (!strcmp(value, list[i])) return 1;
    return 0;
}
static int read_remote(task_t task, uint64_t address, void *buffer, size_t size) {
    mach_vm_size_t copied = 0;
    if (!address || !size || address > UINT64_MAX - size) return 0;
    kern_return_t kr = mach_vm_read_overwrite(task, address, size,
        (mach_vm_address_t)buffer, &copied);
    return kr == KERN_SUCCESS && copied == size;
}
static int image_identity(task_t task, uint64_t address, const char *name) {
    struct mach_header_64 header;
    if (!read_remote(task, address, &header, sizeof(header)) ||
        header.magic != MH_MAGIC_64 || !header.ncmds || header.ncmds > 2048 ||
        !header.sizeofcmds || header.sizeofcmds > MAX_COMMAND_BYTES ||
        address > UINT64_MAX - sizeof(header)) return 0;
    unsigned char *commands = malloc(header.sizeofcmds);
    if (!commands) return 0;
    if (!read_remote(task, address + sizeof(header), commands, header.sizeofcmds)) {
        free(commands); return 0;
    }
    size_t offset = 0;
    uint64_t preferred_base = 0, text_address = 0, text_size = 0;
    uint8_t uuid[16] = {0};
    int have_base = 0, have_uuid = 0;
    for (uint32_t i = 0; i < header.ncmds; i++) {
        if (offset > header.sizeofcmds ||
            header.sizeofcmds - offset < sizeof(struct load_command)) goto fail;
        const struct load_command *command = (const void *)(commands + offset);
        if (command->cmdsize < sizeof(*command) || (command->cmdsize & 3) ||
            command->cmdsize > header.sizeofcmds - offset) goto fail;
        if (command->cmd == LC_UUID) {
            if (command->cmdsize < sizeof(struct uuid_command) || have_uuid) goto fail;
            memcpy(uuid, ((const struct uuid_command *)command)->uuid, sizeof(uuid));
            have_uuid = 1;
        } else if (command->cmd == LC_SEGMENT_64) {
            if (command->cmdsize < sizeof(struct segment_command_64)) goto fail;
            const struct segment_command_64 *segment = (const void *)command;
            if (segment->nsects > (command->cmdsize - sizeof(*segment)) /
                                  sizeof(struct section_64)) goto fail;
            if (!strncmp(segment->segname, "__TEXT", 16) && !segment->fileoff) {
                preferred_base = segment->vmaddr; have_base = 1;
            }
            const struct section_64 *sections = (const void *)(segment + 1);
            for (uint32_t s = 0; s < segment->nsects; s++) {
                const struct section_64 *section = &sections[s];
                if (strncmp(section->sectname, "__text", 16)) continue;
                if (text_size || !(segment->initprot & VM_PROT_EXECUTE) ||
                    !section->size || section->size > MAX_TEXT_BYTES ||
                    section->addr < segment->vmaddr ||
                    section->addr - segment->vmaddr > segment->vmsize ||
                    section->size > segment->vmsize - (section->addr - segment->vmaddr)) goto fail;
                text_address = section->addr; text_size = section->size;
            }
        }
        offset += command->cmdsize;
    }
    if (!have_base || !have_uuid || !text_size || text_address < preferred_base ||
        address > UINT64_MAX - (text_address - preferred_base)) goto fail;
    text_address = address + (text_address - preferred_base);
    if (text_address > UINT64_MAX - text_size) goto fail;
    CC_SHA256_CTX context;
    CC_SHA256_Init(&context);
    unsigned char bytes[32768], hash[CC_SHA256_DIGEST_LENGTH];
    for (uint64_t done = 0; done < text_size;) {
        size_t count = text_size - done > sizeof(bytes) ? sizeof(bytes) : (size_t)(text_size - done);
        if (!read_remote(task, text_address + done, bytes, count)) goto fail;
        CC_SHA256_Update(&context, bytes, (CC_LONG)count);
        done += count;
    }
    CC_SHA256_Final(hash, &context);
    printf("image=%s base=0x%llx cpu=%u subtype=%u uuid=", name,
        (unsigned long long)address, (unsigned)header.cputype, (unsigned)header.cpusubtype);
    for (unsigned i = 0; i < sizeof(uuid); i++) printf("%02x", uuid[i]);
    printf(" text-address=0x%llx text-bytes=%llu text-sha256=",
        (unsigned long long)text_address, (unsigned long long)text_size);
    for (unsigned i = 0; i < sizeof(hash); i++) printf("%02x", hash[i]);
    putchar('\n');
    free(commands); return 1;
fail:
    free(commands); return 0;
}
int main(int argc, char **argv) {
    if (argc < 3 || argc > 10) return 64;
    char *end = NULL;
    long number = strtol(argv[1], &end, 10);
    if (!*argv[1] || *end || number <= 1 || number > INT_MAX) return 64;
    for (int i = 2; i < argc; i++) if (!allowed(argv[i], images)) return 64;
    alarm(15);
    char executable[4096] = {0};
    if (proc_pidpath((int)number, executable, sizeof(executable)) <= 0) {
        fprintf(stderr, "process-path unavailable pid=%ld errno=%d\n", number, errno);
        return 77;
    }
    if (!allowed(base_name(executable), processes)) {
        fprintf(stderr, "process outside allowlist pid=%ld\n", number);
        return 77;
    }
    task_t task = MACH_PORT_NULL;
    kern_return_t kr = task_for_pid(mach_task_self(), (pid_t)number, &task);
    printf("pid=%ld process=%s task-for-pid=%d\n", number, base_name(executable), kr);
    if (kr != KERN_SUCCESS) return 77;
    task_dyld_info_data_t dyld;
    mach_msg_type_number_t count = TASK_DYLD_INFO_COUNT;
    kr = task_info(task, TASK_DYLD_INFO, (task_info_t)&dyld, &count);
    struct { uint32_t version, count; uint64_t array; } info, after;
    struct remote_image { uint64_t address, path, modified; } *list = NULL;
    int status = 70;
    if (kr || dyld.all_image_info_format != TASK_DYLD_ALL_IMAGE_INFO_64 ||
        !read_remote(task, dyld.all_image_info_addr, &info, sizeof(info)) ||
        !info.count || info.count > MAX_IMAGES || !info.array) goto done;
    list = calloc(info.count, sizeof(*list));
    if (!list || !read_remote(task, info.array, list, info.count * sizeof(*list))) goto done;
    unsigned matched = 0;
    for (uint32_t i = 0; i < info.count; i++) {
        char path[1024] = {0};
        if (!read_remote(task, list[i].path, path, sizeof(path) - 1)) continue;
        const char *name = base_name(path);
        for (int a = 2; a < argc; a++) {
            if (!strcmp(name, argv[a])) {
                if (!image_identity(task, list[i].address, name)) goto done;
                matched++; break;
            }
        }
    }
    if (!read_remote(task, dyld.all_image_info_addr, &after, sizeof(after)) ||
        memcmp(&info, &after, sizeof(info))) {
        fprintf(stderr, "image-list-changed; retry snapshot\n"); goto done;
    }
    printf("image-count=%u matched=%u requested=%d no-suspend=yes\n", info.count, matched, argc - 2);
    status = matched == (unsigned)(argc - 2) ? 0 : 75;
done:
    free(list);
    mach_port_deallocate(mach_task_self(), task);
    return status;
}

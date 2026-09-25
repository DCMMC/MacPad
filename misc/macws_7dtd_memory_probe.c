// Read-only, bounded memory witness for the separately signed 7DTD arm64
// rehost. This tool never suspends the target and cannot write target memory.
#include <errno.h>
#include <limits.h>
#include <mach/mach.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

extern int proc_pidpath(int, void *, uint32_t);
extern kern_return_t mach_vm_read_overwrite(
    vm_map_t, mach_vm_address_t, mach_vm_size_t, mach_vm_address_t,
    mach_vm_size_t *);

static int has_suffix(const char *value, const char *suffix) {
    size_t value_length = strlen(value);
    size_t suffix_length = strlen(suffix);
    return value_length >= suffix_length &&
        strcmp(value + value_length - suffix_length, suffix) == 0;
}

int main(int argc, char **argv) {
    if (argc != 4) return 64;
    char *end = NULL;
    long pid_number = strtol(argv[1], &end, 10);
    if (!argv[1][0] || *end || pid_number <= 1 || pid_number > INT_MAX)
        return 64;
    end = NULL;
    unsigned long long address_number = strtoull(argv[2], &end, 0);
    if (!argv[2][0] || *end || !address_number) return 64;
    end = NULL;
    unsigned long length_number = strtoul(argv[3], &end, 0);
    if (!argv[3][0] || *end || !length_number || length_number > 4096 ||
        address_number > UINT64_MAX - length_number) return 64;

    char path[4096] = {0};
    static const char suffix[] =
        "/7DaysToDie-ARM.app/Contents/MacOS/7 Days To Die";
    if (proc_pidpath((int)pid_number, path, sizeof(path)) <= 0 ||
        !has_suffix(path, suffix)) {
        fprintf(stderr, "target rejected pid=%ld path=%s errno=%d\n",
                pid_number, path, errno);
        return 77;
    }

    task_t task = MACH_PORT_NULL;
    kern_return_t kr = task_for_pid(mach_task_self(), (pid_t)pid_number,
                                    &task);
    if (kr != KERN_SUCCESS) {
        fprintf(stderr, "task_for_pid=%#x\n", kr);
        return 77;
    }

    uint8_t bytes[4096] = {0};
    mach_vm_size_t copied = 0;
    kr = mach_vm_read_overwrite(
        task, (mach_vm_address_t)address_number,
        (mach_vm_size_t)length_number, (mach_vm_address_t)bytes, &copied);
    mach_port_deallocate(mach_task_self(), task);
    printf("pid=%ld path=%s address=%#llx requested=%lu result=%#x copied=%llu\n",
           pid_number, path, address_number, length_number, kr,
           (unsigned long long)copied);
    if (kr != KERN_SUCCESS || copied != length_number) return 75;
    for (size_t offset = 0; offset < copied; offset += 16) {
        printf("%016llx:", address_number + offset);
        size_t count = copied - offset < 16 ? copied - offset : 16;
        for (size_t index = 0; index < count; index++)
            printf(" %02x", bytes[offset + index]);
        putchar('\n');
    }
    return 0;
}

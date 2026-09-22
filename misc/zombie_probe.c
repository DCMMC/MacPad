#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/proc_info.h>

// libproc is part of libSystem on the target, but the public iPhoneOS SDK
// omits libproc.h.  Keep the diagnostic's declaration identical to the macOS
// SDK rather than depending on a private header copied onto the device.
extern int proc_pidinfo(int pid, int flavor, uint64_t arg, void *buffer,
                        int buffersize);

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: zombie_probe PID...\n");
        return 64;
    }
    for (int index = 1; index < argc; index++) {
        char *end = NULL;
        long raw = strtol(argv[index], &end, 10);
        if (!end || *end || raw <= 1 || raw > 999999) {
            fprintf(stderr, "invalid pid: %s\n", argv[index]);
            continue;
        }
        struct proc_bsdinfo info = {0};
        errno = 0;
        int size = proc_pidinfo((int)raw, PROC_PIDTBSDINFO, 0, &info,
                                sizeof(info));
        printf("pid=%ld size=%d errno=%d status=%u ppid=%u pgid=%u "
               "comm=%s name=%s\n",
               raw, size, errno, info.pbi_status, info.pbi_ppid,
               info.pbi_pgid, info.pbi_comm, info.pbi_name);
    }
    return 0;
}

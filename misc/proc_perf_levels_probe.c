// Read XNU's cumulative per-performance-level counters for every live thread
// in an existing process. This is a read-only witness for deciding whether an
// iOS launchd coalition is restricted to efficiency cores.

#include <errno.h>
#include <inttypes.h>
#include <mach/mach_time.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/sysctl.h>
#include <unistd.h>

#define PROC_PIDLISTTHREADIDS 28
#define PROC_PIDTHREADCOUNTS 34
#define MAX_THREAD_IDS 4096

// libproc is present on iOS but its public SDK omits libproc.h.  Keep the one
// stable C entry point used by this read-only probe local instead of importing
// macOS SDK headers into an iOS translation unit.
extern int proc_pidinfo(int pid, int flavor, uint64_t arg, void *buffer,
                        int buffersize);

struct perf_count {
    uint64_t instructions;
    uint64_t cycles;
    uint64_t user_time_mach;
    uint64_t system_time_mach;
    uint64_t energy_nj;
};

struct thread_counts {
    uint16_t length;
    uint16_t reserved0;
    uint32_t reserved1;
    struct perf_count counts[];
};

static void add_count(struct perf_count *total, const struct perf_count *value) {
    total->instructions += value->instructions;
    total->cycles += value->cycles;
    total->user_time_mach += value->user_time_mach;
    total->system_time_mach += value->system_time_mach;
    total->energy_nj += value->energy_nj;
}

int main(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "usage: %s pid\n", argv[0]);
        return 2;
    }
    char *end = NULL;
    long parsed = strtol(argv[1], &end, 10);
    if (parsed <= 0 || end == argv[1] || *end != '\0') {
        fprintf(stderr, "invalid pid: %s\n", argv[1]);
        return 2;
    }
    pid_t pid = (pid_t)parsed;

    uint32_t level_count = 0;
    size_t level_count_size = sizeof(level_count);
    if (sysctlbyname("hw.nperflevels", &level_count, &level_count_size,
                     NULL, 0) != 0 || level_count == 0 || level_count > 16) {
        perror("sysctl hw.nperflevels");
        return 1;
    }
    mach_timebase_info_data_t timebase = {0};
    if (mach_timebase_info(&timebase) != KERN_SUCCESS ||
        timebase.numer == 0 || timebase.denom == 0) {
        fprintf(stderr, "mach_timebase_info failed\n");
        return 1;
    }

    uint64_t *thread_ids = calloc(MAX_THREAD_IDS, sizeof(*thread_ids));
    struct perf_count *totals = calloc(level_count, sizeof(*totals));
    size_t count_bytes = sizeof(struct thread_counts) +
        level_count * sizeof(struct perf_count);
    struct thread_counts *counts = calloc(1, count_bytes);
    if (thread_ids == NULL || totals == NULL || counts == NULL) {
        fprintf(stderr, "allocation failed\n");
        return 1;
    }

    errno = 0;
    int thread_bytes = proc_pidinfo(pid, PROC_PIDLISTTHREADIDS, 0, thread_ids,
                                    MAX_THREAD_IDS * sizeof(*thread_ids));
    if (thread_bytes <= 0) {
        fprintf(stderr, "PROC_PIDLISTTHREADIDS pid=%d status=%d errno=%d\n",
                pid, thread_bytes, errno);
        return 1;
    }

    unsigned listed = (unsigned)thread_bytes / sizeof(*thread_ids);
    unsigned read = 0;
    unsigned failed = 0;
    for (unsigned i = 0; i < listed; i++) {
        memset(counts, 0, count_bytes);
        errno = 0;
        int status = proc_pidinfo(pid, PROC_PIDTHREADCOUNTS, thread_ids[i],
                                  counts, (int)count_bytes);
        if (status <= 0) {
            failed++;
            continue;
        }
        uint32_t available = counts->length < level_count
            ? counts->length : level_count;
        for (uint32_t level = 0; level < available; level++) {
            add_count(&totals[level], &counts->counts[level]);
        }
        read++;
    }

    printf("process_perf_levels pid=%d listed_threads=%u read_threads=%u "
           "failed_threads=%u levels=%u timebase_numer=%u "
           "timebase_denom=%u\n", pid, listed, read, failed, level_count,
           timebase.numer, timebase.denom);
    for (uint32_t level = 0; level < level_count; level++) {
        char key[64] = {0};
        char name[64] = "unknown";
        snprintf(key, sizeof(key), "hw.perflevel%u.name", level);
        size_t name_size = sizeof(name);
        if (sysctlbyname(key, name, &name_size, NULL, 0) != 0) {
            snprintf(name, sizeof(name), "level-%u", level);
        }
        printf("perf_level index=%u name=%s instructions=%" PRIu64
               " cycles=%" PRIu64 " user_time_mach=%" PRIu64
               " system_time_mach=%" PRIu64 " energy_nj=%" PRIu64 "\n",
               level, name, totals[level].instructions, totals[level].cycles,
               totals[level].user_time_mach, totals[level].system_time_mach,
               totals[level].energy_nj);
    }

    free(counts);
    free(totals);
    free(thread_ids);
    return read == 0 ? 1 : 0;
}

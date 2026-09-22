#include <errno.h>
#include <inttypes.h>
#include <libproc.h>
#include <mach/thread_info.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

struct thread_snapshot {
    uint64_t id;
    uint64_t user_time;
    uint64_t system_time;
    int cpu_usage;
    int run_state;
    char name[MAXTHREADNAMESIZE];
};

static int compare_thread_id(const void *left, const void *right) {
    const struct thread_snapshot *a = left;
    const struct thread_snapshot *b = right;
    return a->id < b->id ? -1 : a->id > b->id;
}

static struct thread_snapshot *take_snapshot(pid_t pid, size_t *count_out) {
    // PROC_PIDLISTTHREADS does not implement the NULL-buffer size query on
    // Ventura.  A task cannot approach this bound in the workloads this probe
    // targets, and a full buffer is rejected below instead of silently
    // truncating the result.
    size_t capacity = 4096;
    uint64_t *identifiers = calloc(capacity, sizeof(*identifiers));
    struct thread_snapshot *snapshot =
        calloc(capacity, sizeof(*snapshot));
    if (!identifiers || !snapshot) {
        fprintf(stderr, "allocation failed\n");
        free(identifiers);
        free(snapshot);
        return NULL;
    }

    int bytes = proc_pidinfo(pid, PROC_PIDLISTTHREADS, 0, identifiers,
                             (int)(capacity * sizeof(*identifiers)));
    if (bytes <= 0) {
        fprintf(stderr, "proc_pidinfo(PROC_PIDLISTTHREADS): %s\n",
                strerror(errno));
        free(identifiers);
        free(snapshot);
        return NULL;
    }
    if ((size_t)bytes == capacity * sizeof(*identifiers)) {
        fprintf(stderr, "thread list reached the %zu-entry probe limit\n",
                capacity);
        free(identifiers);
        free(snapshot);
        return NULL;
    }

    size_t identifier_count = (size_t)bytes / sizeof(*identifiers);
    size_t count = 0;
    for (size_t index = 0; index < identifier_count; index++) {
        struct proc_threadinfo info;
        memset(&info, 0, sizeof(info));
        int info_bytes = proc_pidinfo(pid, PROC_PIDTHREADINFO,
                                      identifiers[index], &info,
                                      sizeof(info));
        if (info_bytes != sizeof(info)) continue;

        snapshot[count].id = identifiers[index];
        snapshot[count].user_time = info.pth_user_time;
        snapshot[count].system_time = info.pth_system_time;
        snapshot[count].cpu_usage = info.pth_cpu_usage;
        snapshot[count].run_state = info.pth_run_state;
        snprintf(snapshot[count].name, sizeof(snapshot[count].name), "%s",
                 info.pth_name[0] ? info.pth_name : "(unnamed)");
        count++;
    }
    free(identifiers);
    qsort(snapshot, count, sizeof(*snapshot), compare_thread_id);
    *count_out = count;
    return snapshot;
}

static const struct thread_snapshot *find_thread(
        const struct thread_snapshot *snapshot, size_t count, uint64_t id) {
    struct thread_snapshot key = {.id = id};
    return bsearch(&key, snapshot, count, sizeof(*snapshot),
                   compare_thread_id);
}

int main(int argc, char **argv) {
    if (argc < 2 || argc > 3) {
        fprintf(stderr, "usage: %s pid [seconds]\n", argv[0]);
        return 64;
    }
    char *end = NULL;
    long parsed_pid = strtol(argv[1], &end, 10);
    if (!end || *end || parsed_pid <= 0) {
        fprintf(stderr, "invalid pid: %s\n", argv[1]);
        return 64;
    }
    unsigned interval = 3;
    if (argc == 3) {
        end = NULL;
        unsigned long parsed_interval = strtoul(argv[2], &end, 10);
        if (!end || *end || parsed_interval == 0 || parsed_interval > 60) {
            fprintf(stderr, "invalid interval: %s\n", argv[2]);
            return 64;
        }
        interval = (unsigned)parsed_interval;
    }

    size_t before_count = 0;
    struct thread_snapshot *before =
        take_snapshot((pid_t)parsed_pid, &before_count);
    if (!before) return 1;
    sleep(interval);

    size_t after_count = 0;
    struct thread_snapshot *after =
        take_snapshot((pid_t)parsed_pid, &after_count);
    if (!after) {
        free(before);
        return 1;
    }

    printf("thread_id          cpu_ms  user_ms system_ms cpu_pct state name\n");
    for (size_t index = 0; index < after_count; index++) {
        const struct thread_snapshot *old =
            find_thread(before, before_count, after[index].id);
        if (!old) continue;
        uint64_t user_delta = after[index].user_time - old->user_time;
        uint64_t system_delta =
            after[index].system_time - old->system_time;
        uint64_t total_delta = user_delta + system_delta;
        if (total_delta < 1000000) continue;
        printf("%-18" PRIu64 " %7.1f %8.1f %9.1f %7.1f %5d %s\n",
               after[index].id, total_delta / 1000000.0,
               user_delta / 1000000.0, system_delta / 1000000.0,
               100.0 * after[index].cpu_usage / TH_USAGE_SCALE,
               after[index].run_state,
               after[index].name);
    }

    free(after);
    free(before);
    return 0;
}

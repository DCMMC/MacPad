// Fixed-work scalar floating-point probe for separating the iPad/M1 hardware
// execution rate from V8 and WebGL overhead.  Build one arm64 macOS Mach-O and
// run that exact file on both machines.  The LCG implements Aquarium's
// pseudoRandom recurrence with explicit truncation so clang emits the same
// fdiv/frintz-style remainder sequence observed in V8's JIT code.

#include <errno.h>
#include <inttypes.h>
#include <libproc.h>
#include <math.h>
#include <mach/mach.h>
#include <mach/task_policy.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/sysctl.h>
#include <time.h>
#include <unistd.h>

extern int __proc_info(int callnum, int pid, int flavor, uint64_t arg,
                       void *buffer, int buffersize);

#define LCG_ITERATIONS UINT64_C(100000000)
#define TRIG_ITERATIONS UINT64_C(10000000)
#define LCG_ROUNDS 6
#define PROC_PIDTHREADCOUNTS 34

// Private in the SDK but implemented by iOS 16's XNU
// (xnu-8792.61.2/bsd/kern/sys_recount.c). Keep this local ABI definition in
// sync with bsd/sys/proc_info_private.h.
struct macws_proc_threadcounts_data {
    uint64_t instructions;
    uint64_t cycles;
    uint64_t user_time_mach;
    uint64_t system_time_mach;
    uint64_t energy_nj;
};

struct macws_proc_threadcounts {
    uint16_t length;
    uint16_t reserved0;
    uint32_t reserved1;
    struct macws_proc_threadcounts_data counts[];
};

struct perf_level_snapshot {
    int status;
    int error;
    uint32_t level_count;
    uint16_t returned_count;
    struct macws_proc_threadcounts_data *counts;
};

static volatile double g_seed = 0.0;
static volatile double g_range = 4294967296.0;

static void print_process_policy(void) {
    struct proc_bsdinfo bsd = {0};
    int bsd_bytes = proc_pidinfo(getpid(), PROC_PIDTBSDINFO, 0, &bsd,
                                 sizeof(bsd));
    task_category_policy_data_t category = {0};
    mach_msg_type_number_t category_count = TASK_CATEGORY_POLICY_COUNT;
    boolean_t category_default = FALSE;
    kern_return_t category_result = task_policy_get(
        mach_task_self(), TASK_CATEGORY_POLICY,
        (task_policy_t)&category, &category_count, &category_default);
    struct task_qos_policy base_qos = {0};
    mach_msg_type_number_t qos_count = TASK_QOS_POLICY_COUNT;
    boolean_t qos_default = FALSE;
    kern_return_t qos_result = task_policy_get(
        mach_task_self(), TASK_BASE_QOS_POLICY,
        (task_policy_t)&base_qos, &qos_count, &qos_default);
    task_latency_qos_t latency = 0;
    mach_msg_type_number_t latency_count = 1;
    boolean_t latency_default = FALSE;
    kern_return_t latency_result = task_policy_get(
        mach_task_self(), TASK_BASE_LATENCY_QOS_POLICY,
        (task_policy_t)&latency, &latency_count, &latency_default);
    task_throughput_qos_t throughput = 0;
    mach_msg_type_number_t throughput_count = 1;
    boolean_t throughput_default = FALSE;
    kern_return_t throughput_result = task_policy_get(
        mach_task_self(), TASK_BASE_THROUGHPUT_QOS_POLICY,
        (task_policy_t)&throughput, &throughput_count, &throughput_default);
    uint32_t foreground_hardware_reason = 99;
    errno = 0;
    int foreground_hardware_result = __proc_info(
        0xc, getpid(), 0, 0, &foreground_hardware_reason,
        sizeof(foreground_hardware_reason));
    int foreground_hardware_errno = errno;
    printf("policy pid=%d ppid=%d bsd_bytes=%d pbi_flags=%#x "
           "category_kr=%#x role=%d category_default=%d "
           "base_qos_kr=%#x latency=%#x throughput=%#x qos_default=%d "
           "latency_kr=%#x latency_base=%#x latency_default=%d "
           "throughput_kr=%#x throughput_base=%#x throughput_default=%d "
           "foreground_hw_result=%d foreground_hw_errno=%d "
           "foreground_hw_reason=%u\n",
           getpid(), getppid(), bsd_bytes, bsd.pbi_flags, category_result,
           category.role, category_default, qos_result,
           base_qos.task_latency_qos_tier,
           base_qos.task_throughput_qos_tier, qos_default, latency_result,
           latency, latency_default, throughput_result, throughput,
           throughput_default, foreground_hardware_result,
           foreground_hardware_errno, foreground_hardware_reason);
}

static uint64_t monotonic_ns(void) {
    struct timespec value = {0};
    clock_gettime(CLOCK_MONOTONIC_RAW, &value);
    return (uint64_t)value.tv_sec * UINT64_C(1000000000) +
        (uint64_t)value.tv_nsec;
}

static uint64_t thread_cpu_ns(void) {
    struct timespec value = {0};
    clock_gettime(CLOCK_THREAD_CPUTIME_ID, &value);
    return (uint64_t)value.tv_sec * UINT64_C(1000000000) +
        (uint64_t)value.tv_nsec;
}

static struct perf_level_snapshot take_perf_level_snapshot(void) {
    struct perf_level_snapshot snapshot = {0};
    size_t count_size = sizeof(snapshot.level_count);
    if (sysctlbyname("hw.nperflevels", &snapshot.level_count, &count_size,
                     NULL, 0) != 0 || snapshot.level_count == 0 ||
        snapshot.level_count > 16) {
        snapshot.status = -1;
        snapshot.error = errno;
        return snapshot;
    }

    size_t bytes = sizeof(struct macws_proc_threadcounts) +
        snapshot.level_count * sizeof(struct macws_proc_threadcounts_data);
    struct macws_proc_threadcounts *raw = calloc(1, bytes);
    if (raw == NULL) {
        snapshot.status = -1;
        snapshot.error = ENOMEM;
        return snapshot;
    }

    uint64_t thread_id = 0;
    int thread_id_status = pthread_threadid_np(NULL, &thread_id);
    if (thread_id_status != 0) {
        snapshot.status = -1;
        snapshot.error = thread_id_status;
        free(raw);
        return snapshot;
    }

    errno = 0;
    snapshot.status = proc_pidinfo(getpid(), PROC_PIDTHREADCOUNTS, thread_id,
                                   raw, (int)bytes);
    snapshot.error = errno;
    if (snapshot.status > 0) {
        snapshot.returned_count = raw->length;
        snapshot.counts = calloc(snapshot.level_count,
                                 sizeof(*snapshot.counts));
        if (snapshot.counts != NULL) {
            uint32_t available = raw->length < snapshot.level_count
                ? raw->length : snapshot.level_count;
            memcpy(snapshot.counts, raw->counts,
                   available * sizeof(*snapshot.counts));
        } else {
            snapshot.status = -1;
            snapshot.error = ENOMEM;
        }
    }
    free(raw);
    return snapshot;
}

static void free_perf_level_snapshot(struct perf_level_snapshot *snapshot) {
    free(snapshot->counts);
    snapshot->counts = NULL;
}

static void print_perf_level_delta(
    unsigned round, const struct perf_level_snapshot *before,
    const struct perf_level_snapshot *after) {
    if (before->status <= 0 || after->status <= 0 ||
        before->counts == NULL || after->counts == NULL ||
        before->level_count != after->level_count) {
        printf("perf_levels round=%u before_status=%d before_errno=%d "
               "after_status=%d after_errno=%d before_levels=%u "
               "after_levels=%u before_returned=%u after_returned=%u\n",
               round, before->status, before->error, after->status,
               after->error, before->level_count, after->level_count,
               before->returned_count, after->returned_count);
        return;
    }

    for (uint32_t i = 0; i < after->level_count; i++) {
        char key[64] = {0};
        char name[64] = "unknown";
        snprintf(key, sizeof(key), "hw.perflevel%u.name", i);
        size_t name_size = sizeof(name);
        if (sysctlbyname(key, name, &name_size, NULL, 0) != 0) {
            snprintf(name, sizeof(name), "level-%u", i);
        }
        const struct macws_proc_threadcounts_data *a = &after->counts[i];
        const struct macws_proc_threadcounts_data *b = &before->counts[i];
        printf("perf_level round=%u index=%u name=%s instructions=%" PRIu64
               " cycles=%" PRIu64 " user_time_mach=%" PRIu64
               " system_time_mach=%" PRIu64 " energy_nj=%" PRIu64 "\n",
               round, i, name, a->instructions - b->instructions,
               a->cycles - b->cycles, a->user_time_mach - b->user_time_mach,
               a->system_time_mach - b->system_time_mach,
               a->energy_nj - b->energy_nj);
    }
}

__attribute__((noinline))
static double aquarium_lcg(uint64_t iterations) {
    // Keep the divisor a runtime value. V8's optimized Aquarium function uses
    // `fdiv`; allowing clang to fold 2^32 into a reciprocal would benchmark a
    // different instruction dependency chain.
    const double range = g_range;
    double seed = g_seed;
    for (uint64_t i = 0; i < iterations; i++) {
        double product = 134775813.0 * seed + 1.0;
        seed = product - __builtin_trunc(product / range) * range;
    }
    g_seed = seed;
    return seed;
}

__attribute__((noinline))
static double aquarium_trig(uint64_t iterations) {
    double value = g_seed * 0x1p-32 + 0.125;
    for (uint64_t i = 0; i < iterations; i++) {
        value = sin(value + (double)i * 0.000001) +
            cos(value - (double)i * 0.0000007);
    }
    g_seed = value;
    return value;
}

static void run_lcg(unsigned round) {
    g_seed = 0.0;
    struct perf_level_snapshot perf_before = take_perf_level_snapshot();
    uint64_t wall_started = monotonic_ns();
    uint64_t cpu_started = thread_cpu_ns();
    double checksum = aquarium_lcg(LCG_ITERATIONS);
    uint64_t cpu_ended = thread_cpu_ns();
    uint64_t wall_ended = monotonic_ns();
    double wall_seconds =
        (double)(wall_ended - wall_started) / 1000000000.0;
    double cpu_seconds =
        (double)(cpu_ended - cpu_started) / 1000000000.0;
    printf("lcg round=%u iterations=%" PRIu64 " wall_seconds=%.9f "
           "thread_cpu_seconds=%.9f million_per_wall_second=%.3f "
           "checksum=%.0f\n", round, LCG_ITERATIONS, wall_seconds,
           cpu_seconds, (double)LCG_ITERATIONS / wall_seconds / 1000000.0,
           checksum);
    struct perf_level_snapshot perf_after = take_perf_level_snapshot();
    print_perf_level_delta(round, &perf_before, &perf_after);
    free_perf_level_snapshot(&perf_before);
    free_perf_level_snapshot(&perf_after);
}

static void run_trig(void) {
    g_seed = 0.0;
    uint64_t wall_started = monotonic_ns();
    uint64_t cpu_started = thread_cpu_ns();
    double checksum = aquarium_trig(TRIG_ITERATIONS);
    uint64_t cpu_ended = thread_cpu_ns();
    uint64_t wall_ended = monotonic_ns();
    double wall_seconds =
        (double)(wall_ended - wall_started) / 1000000000.0;
    double cpu_seconds =
        (double)(cpu_ended - cpu_started) / 1000000000.0;
    printf("trig iterations=%" PRIu64 " wall_seconds=%.9f "
           "thread_cpu_seconds=%.9f million_per_wall_second=%.3f "
           "checksum=%.17g\n", TRIG_ITERATIONS, wall_seconds, cpu_seconds,
           (double)TRIG_ITERATIONS / wall_seconds / 1000000.0, checksum);
}

int main(int argc, char **argv) {
    setvbuf(stdout, NULL, _IONBF, 0);
    print_process_policy();
    int relative_priority = 0;
    qos_class_t initial_qos = QOS_CLASS_UNSPECIFIED;
    (void)pthread_get_qos_class_np(
        pthread_self(), &initial_qos, &relative_priority);
    int qos_result = 0;
    if (argc == 2 && strcmp(argv[1], "--force-interactive-qos") == 0) {
        qos_result = pthread_set_qos_class_self_np(
            QOS_CLASS_USER_INTERACTIVE, 0);
    }
    int active_relative_priority = 0;
    qos_class_t active_qos = QOS_CLASS_UNSPECIFIED;
    (void)pthread_get_qos_class_np(
        pthread_self(), &active_qos, &active_relative_priority);
    printf("qos initial=%#x initial_relative=%d requested_interactive=%d "
           "set_result=%d active=%#x active_relative=%d\n",
           initial_qos, relative_priority, argc == 2, qos_result, active_qos,
           active_relative_priority);
    for (unsigned round = 1; round <= LCG_ROUNDS; round++) run_lcg(round);
    run_trig();
    return 0;
}

// Read-only iOS-native task-policy witness for a live macOS chroot process.
// Pair with proc_perf_levels_probe while the benchmark worker is running.
#include <errno.h>
#include <mach/mach.h>
#include <mach/task_policy.h>
#include <mach/thread_policy.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

extern int proc_pidinfo(int pid, int flavor, uint64_t arg, void *buffer,
                        int buffersize);

// iPhoneOS16.5.sdk omits this read-only private flavor. The two integer_t
// fields and flavor 9 match XNU's osfmk/mach/thread_policy_private.h.
#define MACWS_THREAD_QOS_POLICY 9
struct macws_thread_qos_policy {
    integer_t qos_tier;
    integer_t tier_importance;
};

int main(int argc, char **argv) {
    if (argc != 2) return 2;
    char *end = NULL;
    long value = strtol(argv[1], &end, 10);
    if (value <= 1 || !end || *end) return 2;
    pid_t pid = (pid_t)value;
    mach_port_t task = MACH_PORT_NULL;
    kern_return_t task_kr = task_for_pid(mach_task_self(), pid, &task);
    if (task_kr != KERN_SUCCESS) {
        printf("target pid=%d task_for_pid=%#x\n", pid, task_kr);
        return 1;
    }
    // PROC_PIDTBSDINFO is flavor 3. Its first field is the stable
    // uint32_t pbi_flags; this SDK omits the private structure header.
    uint32_t bsd[64] = {0};
    int bsd_bytes = proc_pidinfo(pid, 3, 0, bsd, sizeof(bsd));
    printf("target pid=%d bsd_bytes=%d pbi_flags=%#x\n",
           pid, bsd_bytes, bsd[0]);
    // XNU's PROC_PIDCOALITIONINFO private flavor (20) returns the coalition
    // IDs as an array of uint64_t. Probe with a generously sized buffer so
    // this remains read-only even if a newer kernel extends the structure.
    uint64_t coalitions[16] = {0};
    int coalition_bytes = proc_pidinfo(pid, 20, 0, coalitions,
                                       sizeof(coalitions));
    printf("target pid=%d coalition_bytes=%d resource=%llu jetsam=%llu\n",
           pid, coalition_bytes, (unsigned long long)coalitions[0],
           (unsigned long long)coalitions[1]);
    task_category_policy_data_t category = {0};
    mach_msg_type_number_t category_count = TASK_CATEGORY_POLICY_COUNT;
    boolean_t category_default = FALSE;
    kern_return_t category_kr = task_policy_get(
        task, TASK_CATEGORY_POLICY, (task_policy_t)&category,
        &category_count, &category_default);
    struct task_qos_policy qos = {0};
    mach_msg_type_number_t qos_count = TASK_QOS_POLICY_COUNT;
    boolean_t qos_default = FALSE;
    kern_return_t qos_kr = task_policy_get(
        task, TASK_BASE_QOS_POLICY, (task_policy_t)&qos,
        &qos_count, &qos_default);
    task_latency_qos_t latency = 0;
    mach_msg_type_number_t latency_count = 1;
    boolean_t latency_default = FALSE;
    kern_return_t latency_kr = task_policy_get(
        task, TASK_BASE_LATENCY_QOS_POLICY, (task_policy_t)&latency,
        &latency_count, &latency_default);
    task_throughput_qos_t throughput = 0;
    mach_msg_type_number_t throughput_count = 1;
    boolean_t throughput_default = FALSE;
    kern_return_t throughput_kr = task_policy_get(
        task, TASK_BASE_THROUGHPUT_QOS_POLICY, (task_policy_t)&throughput,
        &throughput_count, &throughput_default);
    printf("target pid=%d category_kr=%#x role=%d category_default=%d "
           "qos_kr=%#x latency=%#x throughput=%#x qos_default=%d "
           "latency_kr=%#x latency_base=%#x latency_default=%d "
           "throughput_kr=%#x throughput_base=%#x throughput_default=%d\n",
           pid, category_kr, category.role, category_default,
           qos_kr, qos.task_latency_qos_tier,
           qos.task_throughput_qos_tier, qos_default,
           latency_kr, latency, latency_default,
           throughput_kr, throughput, throughput_default);
    thread_act_array_t threads = NULL;
    mach_msg_type_number_t thread_count = 0;
    kern_return_t threads_kr = task_threads(task, &threads, &thread_count);
    printf("target pid=%d threads_kr=%#x thread_count=%u\n",
           pid, threads_kr, thread_count);
    if (threads_kr == KERN_SUCCESS) {
        for (mach_msg_type_number_t index = 0; index < thread_count;
             index++) {
            struct macws_thread_qos_policy thread_qos = {0};
            mach_msg_type_number_t count = 2;
            boolean_t get_default = FALSE;
            kern_return_t kr = thread_policy_get(
                threads[index], MACWS_THREAD_QOS_POLICY,
                (thread_policy_t)&thread_qos, &count, &get_default);
            printf("thread index=%u qos_kr=%#x tier=%d importance=%d "
                   "default=%d\n", index, kr, thread_qos.qos_tier,
                   thread_qos.tier_importance, get_default);
            mach_port_deallocate(mach_task_self(), threads[index]);
        }
        vm_deallocate(mach_task_self(), (vm_address_t)threads,
                      thread_count * sizeof(*threads));
    }
    mach_port_deallocate(mach_task_self(), task);
    return 0;
}

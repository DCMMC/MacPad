// Diagnostic only. Samples one exact System Settings process without libproc's
// dyld notification subscription (a cold stock-sample run ended in dyld EXC_GUARD).
// The target is suspended only for thread_get_state and resumed BEFORE reads.
// Frame-pointer unwinding after resume is best-effort; PC/LR are register
// witnesses. No hooks, executable modifications, or memory writes.
#include <mach/mach.h>
#include <mach/arm/thread_status.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

extern int proc_pidpath(int, void *, uint32_t);
static thread_t HeldThread;
static void Deadline(int signum) {
    (void)signum;
    if (HeldThread) thread_resume(HeldThread);
    _exit(124);
}
static uint64_t Address(uint64_t value) { return value & 0x0000000fffffffffull; }

int main(int argc, const char **argv) {
    if (argc != 2) return 64;
    char *end = NULL;
    long number = strtol(argv[1], &end, 10);
    if (!end || *end || number <= 1 || number > INT32_MAX) return 64;
    char path[4096] = {0};
    if (proc_pidpath((int)number, path, sizeof(path)) <= 0 ||
        (strcmp(path, "/System/Applications/System Settings.app/Contents/MacOS/System Settings") &&
         strcmp(path, "/private/var/mnt/rootfs/System/Applications/System Settings.app/Contents/MacOS/System Settings"))) return 77;
    signal(SIGALRM, Deadline);
    signal(SIGTERM, Deadline);
    signal(SIGINT, Deadline);
    alarm(12);
    task_t task = MACH_PORT_NULL;
    kern_return_t kr = task_for_pid(mach_task_self(), (int)number, &task);
    if (kr != KERN_SUCCESS) { fprintf(stderr, "task_for_pid=%d\n", kr); return 77; }
    thread_act_array_t threads = NULL;
    mach_msg_type_number_t count = 0;
    kr = task_threads(task, &threads, &count);
    if (kr != KERN_SUCCESS || !count) return 2;
    thread_t main = MACH_PORT_NULL;
    uint64_t identifier = UINT64_MAX;
    for (unsigned i = 0; i < count; i++) {
        thread_identifier_info_data_t info = {0};
        mach_msg_type_number_t n = THREAD_IDENTIFIER_INFO_COUNT;
        if (thread_info(threads[i], THREAD_IDENTIFIER_INFO, (thread_info_t)&info, &n) == KERN_SUCCESS && info.thread_id < identifier) {
            if (main) mach_port_deallocate(mach_task_self(), main);
            main = threads[i];
            identifier = info.thread_id;
        } else mach_port_deallocate(mach_task_self(), threads[i]);
    }
    vm_deallocate(mach_task_self(), (vm_address_t)threads, count * sizeof(thread_t));
    if (!main) return 2;
    printf("settings-stack pid=%ld thread=%llu unwind=best-effort dyld-subscription=NO\n", number, (unsigned long long)identifier);
    fflush(stdout);
    for (unsigned sample = 0; sample < 40; sample++) {
        arm_thread_state64_t state = {0};
        mach_msg_type_number_t n = ARM_THREAD_STATE64_COUNT;
        // Block cleanup signals across this short suspend/resume pair so a
        // signal cannot race the held-port publication or double-resume.
        sigset_t block, prior;
        sigemptyset(&block); sigaddset(&block, SIGALRM);
        sigaddset(&block, SIGTERM); sigaddset(&block, SIGINT);
        sigprocmask(SIG_BLOCK, &block, &prior);
        kr = thread_suspend(main);
        if (kr == KERN_SUCCESS) {
            HeldThread = main;
            kr = thread_get_state(main, ARM_THREAD_STATE64, (thread_state_t)&state, &n);
            kern_return_t resumed = thread_resume(main);
            HeldThread = MACH_PORT_NULL;
            if (resumed != KERN_SUCCESS) kr = resumed;
        }
        sigprocmask(SIG_SETMASK, &prior, NULL);
        if (kr != KERN_SUCCESS) { printf("sample=%u state-error=%d\n", sample, kr); break; }
        printf("sample=%u pc=0x%llx lr=0x%llx frames=", sample,
            (unsigned long long)Address(arm_thread_state64_get_pc(state)),
            (unsigned long long)Address(arm_thread_state64_get_lr(state)));
        uint64_t frame = Address(arm_thread_state64_get_fp(state));
        for (unsigned j = 0; j < 24 && frame >= 0x100000000 && !(frame & 15); j++) {
            uint64_t pair[2] = {0}; vm_size_t read = 0;
            if (vm_read_overwrite(task, (vm_address_t)frame, sizeof(pair), (vm_address_t)pair, &read) != KERN_SUCCESS || read != sizeof(pair)) break;
            printf("0x%llx,", (unsigned long long)Address(pair[1]));
            uint64_t next = Address(pair[0]);
            if (next <= frame || next - frame > 1024 * 1024) break;
            frame = next;
        }
        putchar('\n'); fflush(stdout);
        usleep(200000);
    }
    mach_port_deallocate(mach_task_self(), main);
    mach_port_deallocate(mach_task_self(), task);
    return 0;
}

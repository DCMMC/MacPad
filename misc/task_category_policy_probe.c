// Runtime probe for the iOS kernel's TASK_CATEGORY_POLICY behavior.
//
// Each role is tested in a fresh child because task roles are process state;
// reusing one process would make later results depend on earlier mutations.
// This is intentionally read/write only for the calling child task and does
// not claim that a successful role is appropriate for Chromium.

#include <mach/mach.h>
#include <mach/task_policy.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/wait.h>
#include <unistd.h>

static void read_policy(const char *phase) {
    task_category_policy_data_t policy = {0};
    mach_msg_type_number_t count = TASK_CATEGORY_POLICY_COUNT;
    boolean_t get_default = FALSE;
    kern_return_t kr = task_policy_get(
        mach_task_self(), TASK_CATEGORY_POLICY,
        (task_policy_t)&policy, &count, &get_default);
    fprintf(stderr,
        "TASKPOLICY %s pid=%d get=%#x role=%d count=%u default=%d\n",
        phase, getpid(), kr, policy.role, count, get_default);
}

static int test_role(int role) {
    read_policy("before");
    task_category_policy_data_t policy = {.role = (task_role_t)role};
    kern_return_t kr = task_policy_set(
        mach_task_self(), TASK_CATEGORY_POLICY,
        (task_policy_t)&policy, TASK_CATEGORY_POLICY_COUNT);
    fprintf(stderr,
        "TASKPOLICY set pid=%d role=%d flavor=%d count=%u kr=%#x\n",
        getpid(), role, TASK_CATEGORY_POLICY,
        TASK_CATEGORY_POLICY_COUNT, kr);
    read_policy("after");
    return kr == KERN_SUCCESS ? 0 : 1;
}

int main(void) {
    int failures = 0;
    for (int role = TASK_UNSPECIFIED;
         role <= TASK_DARWINBG_APPLICATION; role++) {
        pid_t child = fork();
        if (child == 0) _exit(test_role(role));
        if (child < 0) {
            perror("fork");
            return 2;
        }
        int status = 0;
        if (waitpid(child, &status, 0) != child ||
            !WIFEXITED(status) || WEXITSTATUS(status) != 0) {
            failures++;
        }
    }
    fprintf(stderr, "TASKPOLICY summary failures=%d\n", failures);
    return failures ? 1 : 0;
}

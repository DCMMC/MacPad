"""Exercise the actual GUI spawn boundary, not a copied policy expression."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'macwshostd/main.m').read_text()
START = SOURCE.index('static int SpawnMacOSApplication(')
FUNCTION = SOURCE[START:SOURCE.index('\n}', START) + 2]

PREFIX = r'''
#include <assert.h>
#include <errno.h>
#include <signal.h>
#include <spawn.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>
typedef bool BOOL;
extern char **environ;
#define MACWS_POSIX_SPAWN_PROC_TYPE_APP_DEFAULT 0x100
static int process_type_calls;
// Scheduling policy is checked separately. This harness leaves the native
// process-type attribute untouched and exercises real spawn/groups/signals.
static int test_set_process_type(posix_spawnattr_t *a, int value) {
    assert(a && value == MACWS_POSIX_SPAWN_PROC_TYPE_APP_DEFAULT);
    process_type_calls++;
    return 0;
}
#define posix_spawnattr_setprocesstype_np test_set_process_type
'''


@unittest.skipUnless(shutil.which('cc'), 'C compiler required')
class AppProcessGroupContract(unittest.TestCase):
    def run_program(self, program):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'app-spawn'
            subprocess.run(['cc', '-x', 'c', '-', '-O2', '-Wall', '-Wextra',
                            '-Werror', '-o', str(binary)], input=program,
                           text=True, capture_output=True, check=True)
            subprocess.run([str(binary)], capture_output=True, check=True,
                           timeout=10)

    def test_real_child_has_own_group_and_normal_interactive_signals(self):
        self.run_program(PREFIX + FUNCTION + r'''
int main(int argc, char **argv) {
    if (argc == 2 && !strcmp(argv[1], "child")) {
        assert(getpgrp() == getpid());
        sigset_t mask;
        assert(sigprocmask(SIG_BLOCK, NULL, &mask) == 0);
        assert(!sigismember(&mask, SIGINT));
        assert(!sigismember(&mask, SIGTERM));
        struct sigaction action;
        assert(sigaction(SIGINT, NULL, &action) == 0);
        assert(action.sa_handler == SIG_DFL);
        return 0;
    }
    const pid_t originalGroup = getpgrp();
    sigset_t blocked;
    sigemptyset(&blocked);
    sigaddset(&blocked, SIGINT);
    sigaddset(&blocked, SIGTERM);
    assert(sigprocmask(SIG_BLOCK, &blocked, NULL) == 0);
    assert(signal(SIGINT, SIG_IGN) != SIG_ERR);
    for (int application = 0; application < 2; application++) {
        pid_t child = -1;
        char *args[] = {argv[0], "child", NULL};
        assert(SpawnMacOSApplication(&child, argv[0], NULL, args,
                                     environ, application) == 0);
        assert(child > 1 && child != originalGroup);
        int status = -1;
        assert(waitpid(child, &status, 0) == child);
        assert(WIFEXITED(status) && WEXITSTATUS(status) == 0);
        assert(getpgrp() == originalGroup);
    }
    assert(process_type_calls == 1);
    return 0;
}
''')

    def test_group_setup_failure_does_not_spawn_or_change_scheduling(self):
        self.run_program(PREFIX + r'''
static int spawn_calls, destroys;
static int failing_group(posix_spawnattr_t *a, pid_t group) {
    assert(a && group == 0);
    return EPERM;
}
static int counted_destroy(posix_spawnattr_t *a) {
    destroys++;
    return posix_spawnattr_destroy(a);
}
static int unexpected_spawn(pid_t *p, const char *path,
    const posix_spawn_file_actions_t *actions,
    const posix_spawnattr_t *attributes, char *const args[], char *const env[]) {
    (void)p; (void)path; (void)actions; (void)attributes; (void)args; (void)env;
    spawn_calls++;
    return 0;
}
#define posix_spawnattr_setpgroup failing_group
#define posix_spawnattr_destroy counted_destroy
#define posix_spawn unexpected_spawn
''' + FUNCTION + r'''
int main(void) {
    pid_t child = -1;
    char *args[] = {"unused", NULL};
    assert(SpawnMacOSApplication(&child, "unused", NULL, args,
                                 environ, true) == EPERM);
    assert(child == -1 && spawn_calls == 0 && process_type_calls == 0);
    assert(destroys == 1);
    return 0;
}
''')


if __name__ == '__main__':
    unittest.main()

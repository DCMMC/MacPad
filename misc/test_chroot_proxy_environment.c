#include "macws_chroot_environment.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

static int stage, openFailure, queryFailure, closeCount;
static const char *answer;
static int unterminated;
static long kernel(long number, long a, long b, long c) {
    if (stage++ == 0) {
        assert(number == 5 && strcmp((const char *)a, "/var/mnt/rootfs") == 0);
        assert(b == 0x1100000 && c == 0);
        return openFailure ? -13 : 9;
    }
    if (number == 92) {
        assert(a == 9 && b == 50);
        if (queryFailure) return -2;
        if (unterminated) memset((void *)c, '/', MACWS_CHROOT_PATH_MAX);
        else strcpy((char *)c, answer);
        return 0;
    }
    assert(number == 6 && a == 9 && b == 0 && c == 0);
    closeCount++;
    return 0;
}
static int run(char *output) {
    stage = closeCount = 0;
    return MacWSBuildChrootRootEnvironment("/var/mnt/rootfs", output,
            MACWS_CHROOT_ROOT_ENV_SIZE, kernel);
}
int main(void) {
    char output[MACWS_CHROOT_ROOT_ENV_SIZE];
    answer = "/private/var/mnt/rootfs";
    assert(run(output));
    assert(strcmp(output, MACWS_CHROOT_ROOT_PREFIX "/private/var/mnt/rootfs") == 0);
    assert(stage == 3 && closeCount == 1);
    // Use the actual vnode answer, not a hard-coded spelling of the root.
    answer = "/private/var/mnt/another-root";
    assert(run(output));
    assert(strcmp(output, MACWS_CHROOT_ROOT_PREFIX "/private/var/mnt/another-root") == 0);
    openFailure = 1;
    assert(!run(output) && stage == 1 && closeCount == 0);
    openFailure = 0; queryFailure = 1;
    assert(!run(output) && stage == 3 && closeCount == 1);
    queryFailure = 0; answer = "/";
    assert(!run(output) && closeCount == 1);
    answer = "relative/path";
    assert(!run(output) && closeCount == 1);
    unterminated = 1;
    assert(!run(output) && closeCount == 1);
    stage = 0;
    assert(!MacWSBuildChrootRootEnvironment("/var/mnt/rootfs", output,
                                          sizeof(output) - 1, kernel));
    assert(!MacWSBuildChrootRootEnvironment(NULL, output, sizeof(output), kernel));
    assert(!MacWSBuildChrootRootEnvironment("relative", output, sizeof(output), kernel));
    assert(!MacWSBuildChrootRootEnvironment("/var/mnt/rootfs", NULL, sizeof(output), kernel));
    assert(stage == 0);
    puts("chroot canonical vnode metadata contract PASS");
    return 0;
}

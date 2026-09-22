// chroot_isolation_test.c — isolate whether `chroot()` syscall itself
// triggers the kernel state that breaks sel=0x9.
//
// Sequence:
//   1. open AGX UC + call sel=0x9 → should SUCCEED (proved earlier)
//   2. call chroot("/") (no-op chroot)
//   3. open another AGX UC + call sel=0x9 → does it fail?
//
// If step-3 succeeds → chroot syscall doesn't matter; something else (lambda,
// DYLD_INSERT, etc) is the gate
// If step-3 fails → chroot syscall directly puts kernel in bad state
//
// Run iOS-native (sudo): sudo /tmp/chroot_isolation_test
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <unistd.h>
#include <mach/mach.h>
#include <IOKit/IOKitLib.h>

static int try_sel9(const char *label) {
    io_service_t svc = IOServiceGetMatchingService(kIOMainPortDefault,
        IOServiceMatching("AGXAccelerator"));
    if (!svc) { fprintf(stderr, "[%s] no service\n", label); return -1; }
    io_connect_t conn = 0;
    kern_return_t kr = IOServiceOpen(svc, mach_task_self(), 1, &conn);
    IOObjectRelease(svc);
    if (kr) { fprintf(stderr, "[%s] IOServiceOpen kr=0x%x\n", label, kr); return -1; }

    unsigned char in_args[0x70] = {0};
    unsigned char out_args[0x50] = {0};
    *(uint64_t *)(in_args + 0x40) = 0x10000;
    size_t outSz = sizeof(out_args);
    kr = IOConnectCallMethod(conn, 0x9, NULL, 0, in_args, sizeof(in_args),
                             NULL, NULL, out_args, &outSz);
    fprintf(stderr, "[%s] sel=0x9 -> kr=0x%x %s\n",
        label, kr, kr == 0 ? "SUCCESS" : "FAIL");
    IOServiceClose(conn);
    return kr == 0 ? 0 : -1;
}

int main(int argc, char **argv) {
    const char *root = argc > 1 ? argv[1] : "/";
    fprintf(stderr, "step 1: sel=0x9 before chroot\n");
    try_sel9("before-chroot");

    fprintf(stderr, "\nstep 2: chroot(\"%s\") ...\n", root);
    if (chroot(root) < 0) {
        perror("chroot");
    } else {
        fprintf(stderr, "chroot(%s) succeeded\n", root);
    }

    fprintf(stderr, "\nstep 3: sel=0x9 after chroot to %s\n", root);
    try_sel9("after-chroot");

    return 0;
}

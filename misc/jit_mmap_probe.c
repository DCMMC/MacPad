// Compare Darwin's executable-memory reservation modes on iOS and in the
// macOS chroot.  This deliberately exercises the same 256 MiB / 16 KiB
// geometry used by V8's arm64 CodeRange.

#include <errno.h>
#include <inttypes.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <unistd.h>

#ifndef MAP_JIT
#define MAP_JIT 0x800
#endif
#ifndef MAP_NORESERVE
#define MAP_NORESERVE 0x40
#endif

#define CS_DEBUGGED 0x10000000u

extern int csops(pid_t pid, unsigned int ops, void *useraddr,
                 size_t usersize);
extern int ptrace(int request, pid_t pid, caddr_t address, int data);
extern void sys_icache_invalidate(void *start, size_t length);

static void execute_arm64_probe(const char *label, void *mapping) {
    if (mapping == MAP_FAILED) return;
    const uint32_t code[] = {
        0x52800540, // mov w0, #42
        0xd65f03c0, // ret
    };
    memcpy(mapping, code, sizeof(code));
    sys_icache_invalidate(mapping, sizeof(code));
    int (*function)(void) = mapping;
    printf(" %s()=%d", label, function());
}

static uint32_t code_signing_flags(void) {
    uint32_t flags = 0;
    if (csops(getpid(), 0, &flags, sizeof(flags)) != 0) {
        printf("csops failed: errno=%d (%s)\n", errno, strerror(errno));
    }
    return flags;
}

static void enable_debugged_jit(void) {
    if ((code_signing_flags() & CS_DEBUGGED) != 0) {
        return;
    }

    pid_t child = fork();
    if (child == 0) {
        (void)ptrace(0, 0, NULL, 0);
        _exit(0);
    }
    if (child < 0) {
        printf("fork failed: errno=%d (%s)\n", errno, strerror(errno));
        return;
    }
    while (waitpid(child, NULL, 0) < 0 && errno == EINTR) {
    }
}

static bool request_debugged_from_autosignd(void) {
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) return false;
    struct sockaddr_un address = {0};
    address.sun_family = AF_UNIX;
    const char *socket_path = access("/var/mnt/rootfs/tmp/autosignd.sock",
                                     F_OK) == 0
        ? "/var/mnt/rootfs/tmp/autosignd.sock"
        : "/tmp/autosignd.sock";
    strlcpy(address.sun_path, socket_path,
            sizeof(address.sun_path));
    bool ok = connect(fd, (struct sockaddr *)&address,
                      sizeof(address)) == 0 &&
        write(fd, "DEBUG\n", 6) == 6;
    char reply[8] = {0};
    ssize_t count = ok ? read(fd, reply, sizeof(reply) - 1) : -1;
    close(fd);
    return count > 0 && strncmp(reply, "OK\n", 3) == 0;
}

static void probe_with_fd(const char *label, size_t size, int protection,
                          int extra_flags, int fd, bool try_transitions) {
    const int flags = MAP_PRIVATE | MAP_ANON | MAP_NORESERVE | extra_flags;
    errno = 0;
    void *mapping = mmap(NULL, size, protection, flags, fd, 0);
    const int mmap_errno = errno;
    printf("%-24s size=0x%zx prot=0x%x flags=0x%x -> %p errno=%d (%s)",
           label, size, protection, flags, mapping, mmap_errno,
           strerror(mmap_errno));
    if (mapping == MAP_FAILED) {
        putchar('\n');
        return;
    }

    if (try_transitions) {
        errno = 0;
        int rw = mprotect(mapping, 0x4000, PROT_READ | PROT_WRITE);
        int rw_errno = errno;
        errno = 0;
        int rx = mprotect(mapping, 0x4000, PROT_READ | PROT_EXEC);
        int rx_errno = errno;
        printf(" mprotect(RW)=%d/%d(%s) mprotect(RX)=%d/%d(%s)", rw,
               rw_errno, strerror(rw_errno), rx, rx_errno,
               strerror(rx_errno));
    }
    putchar('\n');
    munmap(mapping, size);
}

static void probe(const char *label, size_t size, int protection,
                  int extra_flags, bool try_transitions) {
    probe_with_fd(label, size, protection, extra_flags, -1, try_transitions);
}

static void probe_debugged_rwx(void) {
    const size_t page = (size_t)getpagesize();
    const int flags = MAP_PRIVATE | MAP_ANON | MAP_NORESERVE;

    errno = 0;
    void *direct = mmap(NULL, page, PROT_READ | PROT_WRITE | PROT_EXEC,
                        flags, -1, 0);
    int direct_errno = errno;
    printf("%-24s size=0x%zx -> %p errno=%d (%s)",
           "rwx/no-jit", page, direct, direct_errno,
           strerror(direct_errno));
    if (direct != MAP_FAILED) {
        execute_arm64_probe("code", direct);
        munmap(direct, page);
    }
    putchar('\n');

    errno = 0;
    void *transition = mmap(NULL, page, PROT_NONE, flags, -1, 0);
    int map_errno = errno;
    int protect_result = -1;
    int protect_errno = 0;
    if (transition != MAP_FAILED) {
        errno = 0;
        protect_result = mprotect(
            transition, page, PROT_READ | PROT_WRITE | PROT_EXEC);
        protect_errno = errno;
    }
    printf("%-24s size=0x%zx map=%p/%d mprotect(RWX)=%d/%d(%s)",
           "none->rwx/no-jit", page, transition, map_errno,
           protect_result, protect_errno, strerror(protect_errno));
    if (transition != MAP_FAILED && protect_result == 0) {
        execute_arm64_probe("code", transition);
    }
    putchar('\n');
    if (transition != MAP_FAILED) munmap(transition, page);

    // Match the compatibility path used by macOS runtimes in the chroot:
    // reserve without MAP_JIT, make the page writable, publish generated
    // instructions, then remove write permission before the first fetch.
    errno = 0;
    void *wx_transition = mmap(NULL, page, PROT_NONE, flags, -1, 0);
    map_errno = errno;
    int rw_result = -1;
    int rw_errno = 0;
    int rx_result = -1;
    int rx_errno = 0;
    if (wx_transition != MAP_FAILED) {
        errno = 0;
        rw_result = mprotect(wx_transition, page,
                             PROT_READ | PROT_WRITE);
        rw_errno = errno;
        if (rw_result == 0) {
            const uint32_t code[] = {
                0x52800540, // mov w0, #42
                0xd65f03c0, // ret
            };
            memcpy(wx_transition, code, sizeof(code));
            sys_icache_invalidate(wx_transition, sizeof(code));
            errno = 0;
            rx_result = mprotect(wx_transition, page,
                                 PROT_READ | PROT_EXEC);
            rx_errno = errno;
        }
    }
    printf("%-24s size=0x%zx map=%p/%d mprotect(RW)=%d/%d(%s) "
           "mprotect(RX)=%d/%d(%s)",
           "none->rw->rx/no-jit", page, wx_transition, map_errno,
           rw_result, rw_errno, strerror(rw_errno), rx_result, rx_errno,
           strerror(rx_errno));
    if (wx_transition != MAP_FAILED && rw_result == 0 && rx_result == 0) {
        int (*function)(void) = wx_transition;
        printf(" code()=%d", function());
    }
    putchar('\n');
    if (wx_transition != MAP_FAILED) munmap(wx_transition, page);
}

int main(int argc, char **argv) {
    setvbuf(stdout, NULL, _IONBF, 0);
    uint32_t before = code_signing_flags();
    printf("pid=%d page_size=%d MAP_JIT=0x%x csflags_before=0x%08x\n",
           getpid(), getpagesize(), MAP_JIT, before);
    if (argc > 1 && strcmp(argv[1], "first-map-jit") == 0) {
        // V8 passes VM_MAKE_TAG(255) in mmap's fd slot.  Run this as the
        // process's first explicit MAP_JIT request so the result also rules
        // out the iOS single-JIT-region policy.
        probe_with_fd("first-rwx/MAP_JIT", 0x4000,
                      PROT_READ | PROT_WRITE | PROT_EXEC, MAP_JIT,
                      (int)0xff000000u, false);
        return 0;
    }
    if (argc > 1 && strcmp(argv[1], "autosignd") == 0) {
        printf("autosignd DEBUG reply=%s\n",
               request_debugged_from_autosignd() ? "OK" : "FAIL");
    } else if (argc > 1 && strcmp(argv[1], "external-debug") == 0) {
        // Give the operator a bounded window to run the jailbreak's real
        // `jbctl proc_set_debugged <pid>` path. This distinguishes that
        // authorization mechanism from the historical ptrace child trick.
        printf("waiting 5 seconds for external proc_set_debugged\n");
        sleep(5);
    } else {
        enable_debugged_jit();
    }
    uint32_t after = code_signing_flags();
    printf("csflags_after=0x%08x CS_DEBUGGED=%s\n", after,
           (after & CS_DEBUGGED) != 0 ? "yes" : "no");

    // Jailbreak JIT baseline: determine whether CS_DEBUGGED permits a normal
    // anonymous mapping to remain RWX without the Apple-only MAP_JIT MAC
    // policy.  Execute two instructions so page metadata alone cannot be
    // mistaken for working JIT.
    probe_debugged_rwx();

    const size_t code_range = 256ULL * 1024ULL * 1024ULL;
    probe("noaccess/no-jit", code_range, PROT_NONE, 0, true);
    probe("noaccess/MAP_JIT", code_range, PROT_NONE, MAP_JIT, true);
    probe("rw/MAP_JIT", code_range, PROT_READ | PROT_WRITE, MAP_JIT, true);
    probe("rwx/MAP_JIT", code_range,
          PROT_READ | PROT_WRITE | PROT_EXEC, MAP_JIT, true);
    probe("small-rwx/MAP_JIT", 0x4000,
          PROT_READ | PROT_WRITE | PROT_EXEC, MAP_JIT, true);
    return 0;
}

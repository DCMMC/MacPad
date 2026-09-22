// Validate a page-granular W^X adapter on iOS: generated code normally stays
// RX; a write fault while a trusted writer scope is active temporarily turns
// only the faulting page RW, after which the caller restores that page to RX.

#include <errno.h>
#include <mach/mach.h>
#include <stdbool.h>
#include <stdint.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

#define CS_DEBUGGED 0x10000000u

extern int csops(pid_t pid, unsigned int ops, void *useraddr,
                 size_t usersize);
extern int ptrace(int request, pid_t pid, caddr_t address, int data);
extern void sys_icache_invalidate(void *start, size_t length);

static void *g_page;
static size_t g_page_size;
static volatile sig_atomic_t g_writer_active;
static volatile sig_atomic_t g_write_faults;

static uint32_t code_signing_flags(void) {
    uint32_t flags = 0;
    (void)csops(getpid(), 0, &flags, sizeof(flags));
    return flags;
}

static void enable_debugged_jit(void) {
    if ((code_signing_flags() & CS_DEBUGGED) != 0) return;
    pid_t child = fork();
    if (child == 0) {
        (void)ptrace(0, 0, NULL, 0);
        _exit(0);
    }
    while (child > 0 && waitpid(child, NULL, 0) < 0 && errno == EINTR) {
    }
}

static void fault_handler(int signo, siginfo_t *info, void *context) {
    (void)context;
    uintptr_t address = info ? (uintptr_t)info->si_addr : 0;
    uintptr_t base = (uintptr_t)g_page;
    if (signo == SIGBUS && g_writer_active && address >= base &&
        address < base + g_page_size) {
        kern_return_t kr = vm_protect(mach_task_self(), base, g_page_size,
                                      false, VM_PROT_READ | VM_PROT_WRITE);
        if (kr == KERN_SUCCESS) {
            g_write_faults++;
            return;
        }
    }
    _exit(90);
}

int main(void) {
    setvbuf(stdout, NULL, _IONBF, 0);
    enable_debugged_jit();
    g_page_size = (size_t)getpagesize();
    g_page = mmap(NULL, g_page_size, PROT_READ | PROT_WRITE,
                  MAP_PRIVATE | MAP_ANON, -1, 0);
    if (g_page == MAP_FAILED) {
        printf("mmap failed errno=%d (%s)\n", errno, strerror(errno));
        return 1;
    }

    struct sigaction action;
    memset(&action, 0, sizeof(action));
    action.sa_sigaction = fault_handler;
    action.sa_flags = SA_SIGINFO | SA_RESTART;
    sigemptyset(&action.sa_mask);
    if (sigaction(SIGBUS, &action, NULL) != 0) return 2;

    uint32_t *code = g_page;
    code[0] = 0x52800540; // mov w0, #42
    code[1] = 0xd65f03c0; // ret
    sys_icache_invalidate(g_page, 8);
    if (mprotect(g_page, g_page_size, PROT_READ | PROT_EXEC) != 0) return 3;
    int (*function)(void) = g_page;
    int before = function();

    g_writer_active = 1;
    code[0] = 0x52800560; // mov w0, #43; faults once on the RX page
    g_writer_active = 0;
    if (mprotect(g_page, g_page_size, PROT_READ | PROT_EXEC) != 0) return 4;
    sys_icache_invalidate(g_page, 8);
    int after = function();

    printf("page=%p size=%#zx csflags=%#x before=%d after=%d "
           "write_faults=%d final=RX\n",
           g_page, g_page_size, code_signing_flags(), before, after,
           (int)g_write_faults);
    return before == 42 && after == 43 && g_write_faults == 1 ? 0 : 5;
}

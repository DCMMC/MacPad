// csflags_dump.c — show this process's csflags + a few task fields via Dopamine KRW.
// Compare two runs: one from iOS-native shell, one from chroot via run_bash.sh.
// Difference tells us which task-side field matters for the sel=0x9 gate.

#include <stdio.h>
#include <string.h>
#include <stdbool.h>
#include <stdint.h>
#include <unistd.h>
#include <dlfcn.h>

typedef struct ucred_dummy *cred_t;
typedef struct proc_dummy  *proc_t;

static uint64_t (*p_task_self)(void);
static uint64_t (*p_proc_self)(void);
static uint64_t (*p_proc_task)(uint64_t proc);
static uint64_t (*p_proc_ucred)(uint64_t proc);
static uint32_t (*p_proc_getcsflags)(uint64_t proc);
static uint64_t (*p_kread64)(uint64_t addr);
static uint32_t (*p_kread32)(uint64_t addr);
static int      (*p_jbclient_process_checkin)(char **r, char **u, char **s, bool *d);
static int      (*p_jbclient_initialize_primitives)(void);

#define CS_VALID                    0x00000001
#define CS_ADHOC                    0x00000002
#define CS_GET_TASK_ALLOW           0x00000004
#define CS_INSTALLER                0x00000008
#define CS_FORCED_LV                0x00000010
#define CS_INVALID_ALLOWED          0x00000020
#define CS_HARD                     0x00000100
#define CS_KILL                     0x00000200
#define CS_CHECK_EXPIRATION         0x00000400
#define CS_RESTRICT                 0x00000800
#define CS_ENFORCEMENT              0x00001000
#define CS_REQUIRE_LV               0x00002000
#define CS_ENTITLEMENTS_VALIDATED   0x00004000
#define CS_NVRAM_UNRESTRICTED       0x00008000
#define CS_RUNTIME                  0x00010000
#define CS_LINKER_SIGNED            0x00020000
#define CS_EXEC_SET_HARD            0x00100000
#define CS_EXEC_SET_KILL            0x00200000
#define CS_EXEC_SET_ENFORCEMENT     0x00400000
#define CS_EXEC_INHERIT_SIP         0x00800000
#define CS_KILLED                   0x01000000
#define CS_NO_UNTRUSTED_HELPERS     0x02000000
#define CS_PLATFORM_BINARY          0x04000000
#define CS_PLATFORM_PATH            0x08000000
#define CS_DEBUGGED                 0x10000000
#define CS_SIGNED                   0x20000000
#define CS_DEV_CODE                 0x40000000
#define CS_DATAVAULT_CONTROLLER     0x80000000

static void decode_csflags(uint32_t f) {
    fprintf(stderr, "  csflags = 0x%08x\n", f);
#define BIT(N) if (f & N) fprintf(stderr, "    %s\n", #N)
    BIT(CS_VALID); BIT(CS_ADHOC); BIT(CS_GET_TASK_ALLOW); BIT(CS_INSTALLER);
    BIT(CS_FORCED_LV); BIT(CS_INVALID_ALLOWED); BIT(CS_HARD); BIT(CS_KILL);
    BIT(CS_CHECK_EXPIRATION); BIT(CS_RESTRICT); BIT(CS_ENFORCEMENT);
    BIT(CS_REQUIRE_LV); BIT(CS_ENTITLEMENTS_VALIDATED); BIT(CS_NVRAM_UNRESTRICTED);
    BIT(CS_RUNTIME); BIT(CS_LINKER_SIGNED); BIT(CS_EXEC_SET_HARD);
    BIT(CS_EXEC_SET_KILL); BIT(CS_EXEC_SET_ENFORCEMENT); BIT(CS_EXEC_INHERIT_SIP);
    BIT(CS_KILLED); BIT(CS_NO_UNTRUSTED_HELPERS); BIT(CS_PLATFORM_BINARY);
    BIT(CS_PLATFORM_PATH); BIT(CS_DEBUGGED); BIT(CS_SIGNED); BIT(CS_DEV_CODE);
    BIT(CS_DATAVAULT_CONTROLLER);
#undef BIT
}

int main(int argc, char **argv) {
    fprintf(stderr, "=== csflags_dump pid=%d ===\n", getpid());
    void *lib = dlopen("/var/jb/basebin/libjailbreak.dylib", RTLD_NOW);
    if (!lib) { fprintf(stderr, "dlopen: %s\n", dlerror()); return 1; }
    p_task_self                 = dlsym(lib, "task_self");
    p_proc_self                 = dlsym(lib, "proc_self");
    p_proc_task                 = dlsym(lib, "proc_task");
    p_proc_ucred                = dlsym(lib, "proc_ucred");
    p_proc_getcsflags           = dlsym(lib, "proc_getcsflags");
    p_kread64                   = dlsym(lib, "kread64");
    p_kread32                   = dlsym(lib, "kread32");
    p_jbclient_process_checkin  = dlsym(lib, "jbclient_process_checkin");
    p_jbclient_initialize_primitives = dlsym(lib, "jbclient_initialize_primitives");

    if (p_jbclient_process_checkin) {
        char *root = NULL, *uuid = NULL, *sbox = NULL; bool dbg = false;
        int r = p_jbclient_process_checkin(&root, &uuid, &sbox, &dbg);
        fprintf(stderr, "checkin = %d  dbg=%d\n", r, (int)dbg);
    }
    if (p_jbclient_initialize_primitives) p_jbclient_initialize_primitives();

    uint64_t proc = p_proc_self ? p_proc_self() : 0;
    uint64_t task = p_task_self ? p_task_self() : 0;
    fprintf(stderr, "  proc = %#llx, task = %#llx\n",
        (unsigned long long)proc, (unsigned long long)task);

    if (proc && p_proc_getcsflags) {
        uint32_t f = p_proc_getcsflags(proc);
        decode_csflags(f);
    }

    if (proc && p_proc_ucred) {
        uint64_t cred = p_proc_ucred(proc);
        fprintf(stderr, "  ucred = %#llx\n", (unsigned long long)cred);
    }

    // dump task struct field range 0x500-0x600 to find chroot-vs-iOS diffs
    if (task && p_kread64) {
        fprintf(stderr, "task struct dump (offsets 0x500-0x600):\n");
        for (uint64_t off = 0x500; off < 0x600; off += 8) {
            uint64_t v = p_kread64(task + off);
            if (v) fprintf(stderr, "  task+%#5llx = %#llx\n",
                (unsigned long long)off, (unsigned long long)v);
        }
        // proc struct also — proc->p_flag, proc->p_csflags etc.
        if (proc && p_kread64) {
            fprintf(stderr, "proc struct dump (offsets 0x280-0x350):\n");
            for (uint64_t off = 0x280; off < 0x350; off += 8) {
                uint64_t v = p_kread64(proc + off);
                if (v) fprintf(stderr, "  proc+%#5llx = %#llx\n",
                    (unsigned long long)off, (unsigned long long)v);
            }
        }
    }
    return 0;
}

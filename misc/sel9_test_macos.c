// sel9_test_macos.c — minimal sel=0x9 heap-create test.
//
// Same kernel call as agx_iogpu_probe but compiled as a macOS arm64e binary
// so we can run it from INSIDE the chroot. Comparison:
//
//   iOS-native context (probe):  sel=0x9 type=0 size=0x10000 → SUCCESS
//   chroot context (this binary): ???
//
// If this also succeeds → kernel doesn't care about caller; chroot WS's args
//   shape is the actual blocker.
// If this fails with 0xe00002c2 → chroot task fails a kernel check; we need
//   to find which task field.

#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdlib.h>
#include <unistd.h>
#include <dlfcn.h>
#include <mach/mach.h>
#include <IOKit/IOKitLib.h>

#define CS_DEBUGGED 0x10000000U

static int (*g_csflags_set)(uint64_t, uint32_t);
static int (*g_csflags_clear)(uint64_t, uint32_t);
static uint64_t (*g_proc_self)(void);
static uint32_t (*g_getcsflags)(uint64_t);

static int load_libjb(void) {
    static int loaded = 0;
    if (loaded) return 0;
    void *lib = dlopen("/var/jb/basebin/libjailbreak.dylib", RTLD_NOW);
    if (!lib) { fprintf(stderr, "dlopen libjb failed: %s\n", dlerror()); return -1; }
    int (*p_checkin)(char **, char **, char **, bool *) = dlsym(lib, "jbclient_process_checkin");
    int (*p_init)(void) = dlsym(lib, "jbclient_initialize_primitives");
    g_csflags_set   = dlsym(lib, "proc_csflags_set");
    g_csflags_clear = dlsym(lib, "proc_csflags_clear");
    g_proc_self     = dlsym(lib, "proc_self");
    g_getcsflags    = dlsym(lib, "proc_getcsflags");
    if (p_checkin) { char *r=0,*u=0,*s=0; bool d=0; p_checkin(&r,&u,&s,&d); }
    if (p_init) p_init();
    loaded = 1;
    return 0;
}

static void csflags_modify(uint32_t add, uint32_t clear, const char *desc) {
    if (load_libjb() < 0 || !g_proc_self || !g_getcsflags) return;
    uint64_t proc = g_proc_self();
    uint32_t before = g_getcsflags(proc);
    if (clear && g_csflags_clear) g_csflags_clear(proc, clear);
    if (add && g_csflags_set) g_csflags_set(proc, add);
    uint32_t after = g_getcsflags(proc);
    fprintf(stderr, "csflags %s: 0x%08x -> 0x%08x\n", desc, before, after);
}

int main(int argc, char **argv) {
    // CRITICAL: libmachook translates macOS sel=0xa → iOS kernel sel=0x9 in
    // chroot context. Without libmachook (iOS-native context), sel=0x9 hits
    // the kernel directly as iOS sel=0x9 = new_resource. To exercise the
    // ACTUAL kernel call new_resource:
    //   - chroot binary should call sel=0xa (libmachook will translate)
    //   - iOS-native binary should call sel=0x9 (no translation)
    // Detect chroot env via the DYLD_INSERT_LIBRARIES presence.
    uint32_t SEL = 0x9;
    if (getenv("DYLD_INSERT_LIBRARIES")) {
        SEL = 0xa;
        fprintf(stderr, "[chroot detected] using sel=0xa (libmachook will → iOS 0x9)\n");
    } else {
        fprintf(stderr, "[iOS-native] using sel=0x9 (direct kernel call)\n");
    }
    fprintf(stderr, "sel9_test — running in pid %d arch=%s\n", getpid(),
#if __arm64e__
        "arm64e"
#elif __arm64__
        "arm64"
#else
        "?"
#endif
    );

    io_service_t svc = IOServiceGetMatchingService(kIOMainPortDefault,
        IOServiceMatching("AGXAccelerator"));
    if (!svc) {
        fprintf(stderr, "no AGXAccelerator service — am I in iOS or chroot?\n");
        return 1;
    }
    io_connect_t conn = 0;
    kern_return_t kr = IOServiceOpen(svc, mach_task_self(), 1, &conn);
    IOObjectRelease(svc);
    if (kr != 0) {
        fprintf(stderr, "IOServiceOpen(type=1) kr=0x%x\n", kr);
        // Try other types — chroot path uses 0x100001 which we mask to 1
        kr = IOServiceOpen(IOServiceGetMatchingService(kIOMainPortDefault,
            IOServiceMatching("AGXAccelerator")), mach_task_self(), 0x100001, &conn);
        fprintf(stderr, "IOServiceOpen(type=0x100001) kr=0x%x\n", kr);
        if (kr) return 1;
    }
    fprintf(stderr, "io_connect_t = 0x%x\n", conn);

    // 1) Same call as iOS-native probe that SUCCEEDED: type=0 heap, size=0x10000
    unsigned char in_args[0x70];
    unsigned char out_args[0x50];
    memset(in_args, 0, sizeof(in_args));
    memset(out_args, 0, sizeof(out_args));
    in_args[0x00] = 0x00;
    *(uint64_t *)(in_args + 0x40) = 0x10000;
    size_t outSz = sizeof(out_args);
    kr = IOConnectCallMethod(conn, SEL,
        NULL, 0,
        in_args, sizeof(in_args),
        NULL, NULL,
        out_args, &outSz);
    fprintf(stderr, "TEST-1 sel=0x9 type=0 size=0x10000 -> kr=0x%x  %s\n",
        kr, kr == 0 ? "SUCCESS" : "FAIL");

    // 2) inStructCnt slightly different — what the chroot's macOS path uses
    //    (inStructCnt = 0x60 instead of 0x70). The kernel framework decides
    //    inline-vs-OOL based on size, so this might take different path.
    memset(in_args, 0, sizeof(in_args));
    in_args[0x00] = 0x00;
    *(uint64_t *)(in_args + 0x40) = 0x10000;
    outSz = sizeof(out_args);
    kr = IOConnectCallMethod(conn, SEL,
        NULL, 0,
        in_args, 0x60,
        NULL, NULL,
        out_args, &outSz);
    fprintf(stderr, "TEST-2 sel=0x9 inSC=0x60 type=0 size=0x10000 -> kr=0x%x  %s\n",
        kr, kr == 0 ? "SUCCESS" : "FAIL");

    // 3) macOS Metal sends *outStructCnt = 0x50 initially. Our probe also
    //    sent 0x50. Re-try just to be explicit.
    memset(in_args, 0, sizeof(in_args));
    in_args[0x00] = 0x00;
    *(uint64_t *)(in_args + 0x40) = 0x10000;
    outSz = 0x50;
    kr = IOConnectCallMethod(conn, SEL,
        NULL, 0,
        in_args, 0x70,
        NULL, NULL,
        out_args, &outSz);
    fprintf(stderr, "TEST-3 sel=0x9 outSC=0x50 explicit -> kr=0x%x  %s\n",
        kr, kr == 0 ? "SUCCESS" : "FAIL");

    // 4) Same with outSC=0x10000 — matches what libmachook IOConnectCallMethod_new
    //    bumps it to. Sanity: from iOS-native this should still succeed; if it
    //    fails, libmachook's outCnt bump is what kills chroot calls.
    memset(in_args, 0, sizeof(in_args));
    unsigned char out_big[0x10000];
    in_args[0x00] = 0x00;
    *(uint64_t *)(in_args + 0x40) = 0x10000;
    outSz = sizeof(out_big);
    kr = IOConnectCallMethod(conn, SEL,
        NULL, 0,
        in_args, 0x70,
        NULL, NULL,
        out_big, &outSz);
    fprintf(stderr, "TEST-4 sel=0x9 outSC=0x10000 (libmachook-shape) -> kr=0x%x  %s\n",
        kr, kr == 0 ? "SUCCESS" : "FAIL");

    // Cycle through csflags experiments. After each modification, re-call
    // sel=0x9 and report. iOS-native CS_HARD | CS_KILL distinguishes its
    // csflags from chroot's. Try adding those.
    struct { uint32_t add, clear; const char *name; } experiments[] = {
        { 0,                              CS_DEBUGGED,           "clear CS_DEBUGGED" },
        { 0x100 | 0x200,                  0,                     "add CS_HARD|CS_KILL" },
        { 0x100 | 0x200,                  CS_DEBUGGED,           "add CS_HARD|CS_KILL + clear CS_DEBUGGED" },
        { 0x04000000,                     0,                     "add CS_PLATFORM_BINARY" },
        { 0x100 | 0x200 | 0x04000000,     CS_DEBUGGED,           "match-iOS-native + clear CS_DEBUGGED" },
    };
    for (int i = 0; i < (int)(sizeof(experiments)/sizeof(experiments[0])); i++) {
        fprintf(stderr, "\n--- experiment[%d]: %s ---\n", i+1, experiments[i].name);
        csflags_modify(experiments[i].add, experiments[i].clear, experiments[i].name);
        memset(in_args, 0, sizeof(in_args));
        in_args[0x00] = 0x00;
        *(uint64_t *)(in_args + 0x40) = 0x10000;
        outSz = sizeof(out_args);
        kr = IOConnectCallMethod(conn, SEL,
            NULL, 0,
            in_args, sizeof(in_args),
            NULL, NULL,
            out_args, &outSz);
        fprintf(stderr, "  sel=0x9 -> kr=0x%x  %s\n",
            kr, kr == 0 ? "*** SUCCESS ***" : "FAIL");
        if (kr == 0) break;
    }

    // === sel=0x7/0x8 verification (queue create + set_api_property) ===
    // chroot:    macOS sel=0x8 → iOS sel=0x7 = new_command_queue
    // iOS-native: call sel=0x7 directly
    uint32_t QSEL = getenv("DYLD_INSERT_LIBRARIES") ? 0x8 : 0x7;
    {
        unsigned char in_q[0x408] = {0};
        unsigned char out_q[0x10]  = {0};
        size_t outSz = sizeof(out_q);
        kr = IOConnectCallMethod(conn, QSEL,
            NULL, 0,
            in_q, sizeof(in_q),
            NULL, NULL,
            out_q, &outSz);
        fprintf(stderr, "TEST-QC sel=0x%x (kernel new_command_queue) -> kr=0x%x  %s\n",
            QSEL, kr, kr == 0 ? "SUCCESS" : "FAIL");
    }
    uint32_t PSEL = getenv("DYLD_INSERT_LIBRARIES") ? 0x7 : 0x6;
    {
        unsigned char in_p[0x408] = {0};
        unsigned char out_p[0x10]  = {0};
        size_t outSz = sizeof(out_p);
        kr = IOConnectCallMethod(conn, PSEL,
            NULL, 0,
            in_p, sizeof(in_p),
            NULL, NULL,
            out_p, &outSz);
        fprintf(stderr, "TEST-AP sel=0x%x (kernel set_api_property) -> kr=0x%x  %s\n",
            PSEL, kr, kr == 0 ? "SUCCESS" : "FAIL");
    }

    IOServiceClose(conn);
    return 0;
}

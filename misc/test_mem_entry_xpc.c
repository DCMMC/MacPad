// test_mem_entry_xpc.c — verify mach memory entry survives XPC transfer.
//
// Calls macwsallocd's `make-mem-entry` XPC op, receives an entry mach port,
// then tries to:
//   1. Use mach_vm_map() to map the entry into our task at any address →
//      success means the entry IS usable cross-task (good — we can build on
//      this for type=0x80 fix); failure (esp. EXC_GUARD-like) means dead end.
//   2. Read the first 64 bytes of the mapping (should be zero since the
//      helper memset'd it).
//
// Compile + run on iOS (NOT chroot):
//   clang -arch arm64 -isystem /var/jb/var/mobile/MacWSBootingGuide/vendor/ios-xpc \
//         /tmp/test_mem_entry_xpc.c -o /tmp/test_mem_entry
//   sudo ldid -S /tmp/test_mem_entry
//   sudo jbctl trustcache add $(ldid -arch arm64 -h /tmp/test_mem_entry | \
//        awk -F= '/CDHash/{print $2}')
//   sudo /tmp/test_mem_entry

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <dlfcn.h>
#include <mach/mach.h>
#include <xpc/xpc.h>

extern kern_return_t mach_vm_map(
    vm_map_t target_task, mach_vm_address_t *address, mach_vm_size_t size,
    mach_vm_offset_t mask, int flags, mach_port_t object,
    memory_object_offset_t offset, boolean_t copy,
    vm_prot_t cur_protection, vm_prot_t max_protection,
    vm_inherit_t inheritance);

#define VM_FLAGS_ANYWHERE 0x0001

int main(int argc, const char **argv) {
    fprintf(stderr, "test_mem_entry_xpc — pid %d\n", getpid());

    xpc_connection_t (*createMach)(const char *, dispatch_queue_t, uint64_t) =
        dlsym(RTLD_DEFAULT, "xpc_connection_create_mach_service");
    if (!createMach) { fprintf(stderr, "no createMach\n"); return 1; }

    xpc_connection_t c = createMach("com.macwsguide.alloc", NULL, 0);
    xpc_connection_set_event_handler(c, ^(xpc_object_t e) { (void)e; });
    xpc_connection_resume(c);

    fprintf(stderr, "sending make-mem-entry size=0x10000…\n");
    xpc_object_t req = xpc_dictionary_create(NULL, NULL, 0);
    xpc_dictionary_set_string(req, "op", "make-mem-entry");
    xpc_dictionary_set_uint64(req, "size", 0x10000);
    xpc_object_t reply = xpc_connection_send_message_with_reply_sync(c, req);
    if (!reply || xpc_get_type(reply) != XPC_TYPE_DICTIONARY) {
        fprintf(stderr, "NO REPLY (daemon crash?)\n"); return 2;
    }
    const char *result = xpc_dictionary_get_string(reply, "result");
    fprintf(stderr, "reply result=%s\n", result ?: "(null)");
    if (!result || strcmp(result, "ok") != 0) return 3;

    mach_port_t entry = xpc_dictionary_copy_mach_send(reply, "entry");
    uint64_t sz = xpc_dictionary_get_uint64(reply, "size");
    uint64_t helper_va = xpc_dictionary_get_uint64(reply, "helper_va");
    fprintf(stderr, "got entry port=%u size=%llu helper_va=%#llx\n",
            entry, sz, helper_va);
    if (entry == MACH_PORT_NULL) return 4;

    // Step 1 — try to map the entry into our task.
    mach_vm_address_t addr = 0;
    kern_return_t kr = mach_vm_map(
        mach_task_self(), &addr, (mach_vm_size_t)sz, 0,
        VM_FLAGS_ANYWHERE, entry, 0, FALSE,
        VM_PROT_READ | VM_PROT_WRITE, VM_PROT_READ | VM_PROT_WRITE,
        VM_INHERIT_NONE);
    fprintf(stderr, "mach_vm_map kr=%#x addr=%#llx\n", kr, (unsigned long long)addr);
    if (kr != KERN_SUCCESS) {
        fprintf(stderr, "ENTRY DOES NOT CROSS XPC TASK BOUNDARY — fix path dead\n");
        return 5;
    }

    // Step 2 — read first 64 bytes.
    fprintf(stderr, "first 64 bytes at %#llx:\n", (unsigned long long)addr);
    const uint8_t *p = (const uint8_t *)(uintptr_t)addr;
    for (int i = 0; i < 64; i += 16) {
        fprintf(stderr, "  %02x:", i);
        for (int j = 0; j < 16; j++) fprintf(stderr, " %02x", p[i + j]);
        fprintf(stderr, "\n");
    }
    fprintf(stderr, "MAP+READ OK — entry crosses XPC. Forward path is viable.\n");

    // Step 3 — write a known pattern, then ask the helper to read it back
    // (next iteration). For now just confirm we can write.
    *(uint64_t *)(uintptr_t)addr = 0xDEADBEEFCAFEBABEULL;
    fprintf(stderr, "wrote 0xDEADBEEFCAFEBABE @ %#llx; readback: %#llx\n",
        (unsigned long long)addr, *(const uint64_t *)(uintptr_t)addr);

    return 0;
}

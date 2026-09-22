// Diagnose large aligned virtual-address reservations on iOS and in the
// macOS chroot. Build the same source for both platforms and compare results.

#include <errno.h>
#include <inttypes.h>
#include <mach/mach.h>
#include <mach/mach_error.h>
#include <mach/vm_statistics.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>

// The iOS SDK intentionally makes <mach/mach_vm.h> unavailable to app
// builds, although the MIG entry points and 64-bit Mach VM types are present
// in libSystem. Declare the public ABI directly so this probe can compare an
// iOS-native process with the macOS-chroot process.
extern kern_return_t mach_vm_map(
    vm_map_t target_task, mach_vm_address_t *address, mach_vm_size_t size,
    mach_vm_offset_t mask, int flags, mem_entry_name_port_t object,
    memory_object_offset_t offset, boolean_t copy, vm_prot_t cur_protection,
    vm_prot_t max_protection, vm_inherit_t inheritance);
extern kern_return_t mach_vm_deallocate(vm_map_t target,
                                         mach_vm_address_t address,
                                         mach_vm_size_t size);

static const uint64_t kGiB = 1024ULL * 1024ULL * 1024ULL;

static void probe_mmap(uint64_t size, uintptr_t hint) {
    errno = 0;
    void *result = mmap((void *)hint, (size_t)size, PROT_NONE,
                        MAP_ANON | MAP_PRIVATE, VM_MAKE_TAG(253), 0);
    int saved_errno = errno;
    printf("mmap hint=0x%" PRIxPTR " size=%" PRIu64
           "GiB -> %p errno=%d (%s) aligned=%s\n",
           hint, size / kGiB, result, saved_errno, strerror(saved_errno),
           result != MAP_FAILED && ((uintptr_t)result & (size - 1)) == 0
               ? "yes"
               : "no");
    if (result != MAP_FAILED) {
        munmap(result, (size_t)size);
    }
}

static void probe_mach_vm_map(uint64_t size, uint64_t alignment) {
    mach_vm_address_t address = 0;
    mach_vm_offset_t mask = alignment - 1;
    int flags = VM_FLAGS_ANYWHERE | VM_MAKE_TAG(253);
    kern_return_t kr = mach_vm_map(
        mach_task_self(), &address, size, mask, flags, MACH_PORT_NULL, 0,
        FALSE, VM_PROT_NONE, VM_PROT_READ | VM_PROT_WRITE, VM_INHERIT_DEFAULT);
    printf("mach_vm_map size=%" PRIu64 "GiB align=%" PRIu64
           "GiB -> kr=0x%x (%s) address=0x%llx aligned=%s\n",
           size / kGiB, alignment / kGiB, kr, mach_error_string(kr), address,
           kr == KERN_SUCCESS && (address & mask) == 0 ? "yes" : "no");
    if (kr == KERN_SUCCESS) {
        mach_vm_deallocate(mach_task_self(), address, size);
    }
}

static void probe_adjacent_16g_segments(void) {
    mach_vm_address_t addresses[3] = {0, 0, 0};
    kern_return_t results[3] = {KERN_FAILURE, KERN_FAILURE, KERN_FAILURE};
    const mach_vm_size_t size = 16 * kGiB;
    const mach_vm_offset_t mask = size - 1;
    const int flags = VM_FLAGS_ANYWHERE | VM_MAKE_TAG(253);

    for (unsigned i = 0; i < 3; ++i) {
        results[i] = mach_vm_map(
            mach_task_self(), &addresses[i], size, mask, flags, MACH_PORT_NULL,
            0, FALSE, VM_PROT_NONE, VM_PROT_READ | VM_PROT_WRITE,
            VM_INHERIT_DEFAULT);
        printf("sequence16[%u] -> kr=0x%x address=0x%llx\n", i, results[i],
               addresses[i]);
    }
    printf("sequence16 adjacent01=%s adjacent12=%s pair32aligned01=%s "
           "pair32aligned12=%s\n",
           results[0] == KERN_SUCCESS && results[1] == KERN_SUCCESS &&
                   addresses[1] == addresses[0] + size
               ? "yes"
               : "no",
           results[1] == KERN_SUCCESS && results[2] == KERN_SUCCESS &&
                   addresses[2] == addresses[1] + size
               ? "yes"
               : "no",
           results[0] == KERN_SUCCESS && (addresses[0] & (32 * kGiB - 1)) == 0
               ? "yes"
               : "no",
           results[1] == KERN_SUCCESS && (addresses[1] & (32 * kGiB - 1)) == 0
               ? "yes"
               : "no");
    for (unsigned i = 0; i < 3; ++i) {
        if (results[i] == KERN_SUCCESS) {
            mach_vm_deallocate(mach_task_self(), addresses[i], size);
        }
    }
}

int main(void) {
    printf("page_size=%lu task=0x%x\n", (unsigned long)vm_page_size,
           mach_task_self());
    probe_mach_vm_map(32 * kGiB, 32 * kGiB);
    probe_mach_vm_map(16 * kGiB, 16 * kGiB);
    probe_mach_vm_map(8 * kGiB, 8 * kGiB);
    probe_adjacent_16g_segments();
    probe_mmap(32 * kGiB, 0x10000000000ULL);
    probe_mmap(32 * kGiB, 0);
    return 0;
}

/* VS Code 1.130 / Electron 42.6's default V8 PageAllocator unconditionally
 * over-allocates size+alignment-page. Its 4-GiB Oilpan cage consequently asks
 * mmap for ~12 GiB at 56 GiB, beyond this iPad's VA range, although the actual
 * 4-GiB aligned reservation fits. Add the same exact-size first attempt used
 * by Chromium's other page allocator, preserving all real cage checks.
 *
 * Only an exact framework UUID + constructor/thunk + already-ported cage
 * geometry + relocated vtable slot are accepted. Rebind DATA, not RX text:
 * Electron's fork children must retain the original executable mappings.
 */
#include "macws_electron_page_allocator.h"
#include <mach/mach.h>
#include <mach-o/dyld.h>
#include <mach-o/loader.h>
#include <stdlib.h>
#include <stdatomic.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#ifdef MACWS_ELECTRON_ALLOCATOR_PROBE
#define REPORT(...) dprintf(STDERR_FILENO, __VA_ARGS__)
#else
#define REPORT(...) ((void)0)
#endif

typedef void *(*MacWSPageAllocate)(void *, void *, size_t, size_t, int);
static _Atomic(MacWSPageAllocate) originalAllocate;

static void *reservePages(void *hint, size_t size) {
    void *result = mmap(hint, size, PROT_NONE,
                       MAP_PRIVATE | MAP_ANON | MAP_NORESERVE,
                       (int)0xff000000, 0);
    int savedError = errno;
    REPORT("[MacWSElectronPage] exact hint=%p size=%#zx result=%p errno=%d\n",
           hint, size, result, result == MAP_FAILED ? errno : 0);
    errno = savedError;
    return result;
}

static void *allocatePages(void *allocator, void *hint, size_t size,
                           size_t alignment, int permission) {
    void *result = NULL;
    if (MacWSElectronTryExactReservation(hint, size, alignment, permission,
            (size_t)getpagesize(), reservePages, munmap, &result)) return result;
    return atomic_load_explicit(&originalAllocate, memory_order_acquire)(
        allocator, hint, size, alignment, permission);
}

static void __attribute__((unused)) imageAdded(const struct mach_header *header, intptr_t slide) {
    (void)slide;
    if (atomic_load_explicit(&originalAllocate, memory_order_acquire) ||
        header->magic != MH_MAGIC_64 ||
        header->cputype != CPU_TYPE_ARM64) return;
    const struct mach_header_64 *h = (const void *)header;
    if (h->ncmds > 256 || h->sizeofcmds > 65536) return;
    static const unsigned char uuid[16] = {
        0x4c,0x4c,0x44,0x42,0x55,0x55,0x31,0x44,
        0xa1,0xa8,0x56,0x41,0x69,0xf3,0xff,0x00};
    bool matches = false, containsCode = false, containsSlot = false;
    const unsigned char *cursor = (const void *)(h + 1);
    const unsigned char *end = cursor + h->sizeofcmds;
    for (uint32_t i = 0; i < h->ncmds; ++i) {
        if ((size_t)(end - cursor) < sizeof(struct load_command)) return;
        const struct load_command *lc = (const void *)cursor;
        if (lc->cmdsize < sizeof(*lc) || lc->cmdsize > (size_t)(end-cursor)) return;
        if (lc->cmd == LC_UUID && lc->cmdsize == sizeof(struct uuid_command))
            matches = memcmp(((const struct uuid_command *)lc)->uuid, uuid, 16) == 0;
        if (lc->cmd == LC_SEGMENT_64 && lc->cmdsize >= sizeof(struct segment_command_64)) {
            const struct segment_command_64 *s = (const void *)lc;
            if (!strncmp(s->segname, "__TEXT", sizeof(s->segname)) && s->vmaddr == 0 &&
                s->vmsize >= 0x61d23b0 && (s->initprot & VM_PROT_EXECUTE)) containsCode = true;
            if (!strncmp(s->segname, "__DATA_CONST", sizeof(s->segname)) && s->vmaddr <= 0xac8b718 &&
                s->vmaddr <= UINT64_MAX - s->vmsize &&
                s->vmaddr + s->vmsize >= 0xac8b750) containsSlot = true;
        }
        cursor += lc->cmdsize;
    }
    if (!matches || !containsCode || !containsSlot) return;
    const unsigned char *base = (const void *)h;
    static const uint32_t thunk[6] = {
        0xaa0103e0,0xaa0203e1,0xaa0303e2,0xaa0403e3,0xd2800004,0x16a92a32};
    static const uint32_t ctor[7] = {
        0xa9be4ff4,0xa9017bfd,0x910043fd,0xaa0003f3,
        0xb00255c8,0x911c6108,0xf9000008};
    if (memcmp(base + 0x61d2398, thunk, sizeof(thunk)) ||
        memcmp(base + 0x61d2354, ctor, sizeof(ctor))) return;
    /* Verify the existing full PA/cppgc geometry port, not just its UUID. */
    if (*(const uint32_t *)(base + 0x1fbc118) != 0xd2c001c1 ||
        *(const uint32_t *)(base + 0x1fbc328) != 0xd2c00022 ||
        *(const uint32_t *)(base + 0x1fbc32c) != 0xd2c00043) return;
    MacWSPageAllocate *slot = (void *)(base + 0xac8b748);
    MacWSPageAllocate expected = (void *)(base + 0x61d2398);
    if (*slot != expected) return;
    vm_address_t region = (vm_address_t)slot;
    vm_size_t regionSize = 0;
    vm_region_basic_info_data_64_t info = {0};
    mach_msg_type_number_t count = VM_REGION_BASIC_INFO_COUNT_64;
    mach_port_t object = MACH_PORT_NULL;
    kern_return_t kr = vm_region_64(mach_task_self(), &region, &regionSize,
        VM_REGION_BASIC_INFO_64, (vm_region_info_t)&info, &count, &object);
    if (MACH_PORT_VALID(object)) mach_port_deallocate(mach_task_self(), object);
    if (kr != KERN_SUCCESS || count != VM_REGION_BASIC_INFO_COUNT_64 ||
        region > (uintptr_t)slot || regionSize < sizeof(*slot) ||
        (uintptr_t)slot - region > regionSize - sizeof(*slot) ||
        !(info.protection & VM_PROT_READ) || (info.protection & VM_PROT_EXECUTE)) return;
    size_t pageSize = (size_t)getpagesize();
    vm_address_t page = (uintptr_t)slot & ~(pageSize - 1);
    kr = vm_protect(mach_task_self(), page, pageSize, false,
        info.protection | VM_PROT_WRITE | VM_PROT_COPY);
    if (kr != KERN_SUCCESS) {
        dprintf(STDERR_FILENO, "[MacWSElectronPage] data page protection failed: %d\n", kr);
        return;
    }
    atomic_store_explicit(&originalAllocate, expected, memory_order_release);
    __atomic_store_n(slot, allocatePages, __ATOMIC_RELEASE);
    kr = vm_protect(mach_task_self(), page, pageSize, false, info.protection);
    if (kr != KERN_SUCCESS) {
        /* Do not knowingly publish an adapter with unrecovered permissions. */
        __atomic_store_n(slot, expected, __ATOMIC_RELEASE);
        kern_return_t retry = vm_protect(mach_task_self(), page, pageSize,
                                             false, info.protection);
        dprintf(STDERR_FILENO,
                "[MacWSElectronPage] data protection restore failed: %d; rollback=%d\n",
                kr, retry);
        return;
    }
    REPORT("[MacWSElectronPage] installed slot=%p original=%p restore=%d\n",
           slot, expected, kr);
}

__attribute__((constructor)) static void install(void) {
#if defined(__arm64e__)
    /* This UUID is arm64. Do not publish arm64e signed function pointers into
     * an arm64 vtable in any unsupported mixed-image process. */
    return;
#else
    const char *mode = getenv("ELECTRON_RUN_AS_NODE");
    const char *name = getprogname();
    if (!mode || strcmp(mode, "1") || !name ||
        (strcmp(name, "Electron") && strcmp(name, "Code Helper (Plugin)") &&
         strcmp(name, "Code Helper"))) return;
    _dyld_register_func_for_add_image(imageAdded);
#endif
}

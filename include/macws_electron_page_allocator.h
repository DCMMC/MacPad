#ifndef MACWS_ELECTRON_PAGE_ALLOCATOR_H
#define MACWS_ELECTRON_PAGE_ALLOCATOR_H

#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* A real, exact-size first attempt, not a substitute reservation. The caller
 * retains its original allocator for every unsupported or unsuccessful case.
 * Permission 0 is the verified v8::PageAllocator::kNoAccess enum. */
static inline bool MacWSElectronTryExactReservation(
        void *hint, size_t size, size_t alignment, int permission,
        size_t pageSize, void *(*reserve)(void *, size_t),
        int (*release)(void *, size_t), void **result) {
    uintptr_t address = (uintptr_t)hint;
    if (!result || !reserve || !release || permission != 0 || !address ||
        !size || !pageSize || (pageSize & (pageSize - 1)) ||
        alignment < pageSize || (alignment & (alignment - 1)) ||
        (address & (alignment - 1)) || (size & (pageSize - 1)) ||
        address > UINTPTR_MAX - size) return false;
    int savedError = errno;
    void *allocation = reserve(hint, size);
    if (allocation == (void *)(intptr_t)-1) {
        errno = savedError;
        return false;
    }
    if (allocation && ((uintptr_t)allocation & (alignment - 1)) == 0) {
        *result = allocation;
        errno = savedError;
        return true;
    }
    /* mmap without MAP_FIXED may choose a different, unaligned address.
     * Never return it as an aligned reservation, and never leak it before
     * falling back. A genuine unmap failure is an allocation failure. */
    if (release(allocation, size) != 0) {
        *result = NULL;
        return true;
    }
    errno = savedError;
    return false;
}

#endif

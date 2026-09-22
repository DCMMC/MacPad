#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <mach/mach.h>
#include <stdint.h>
#include <stdlib.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/uio.h>
#include <unistd.h>

// macOS libuv calls Darwin sendfile even for file-to-file copies, then uses
// its real pread/write fallback on ENOTSOCK. On this iPadOS kernel syscall
// 337 raises SIGSYS instead of returning an unsupported-operation error.
// Runtime: VSCode 13102, libuv-worker, sendfile+8, x16=0x151 (2026-09-19).
// RE: its actual Electron Framework +0x8cfea8..0x8cfee8 accepts EINVAL, EIO,
// ENOTSOCK and EXDEV, NOT ENOSYS. Validate real descriptor types, and implement
// the valid regular-file -> connected-stream operation with bounded I/O.
// No executable-page patch, feature flag, signal handler or success stub.

enum { MacWSSendfileChunk = 64 * 1024 };

// Kernel sendfile copies its metadata rather than dereferencing arbitrary
// caller addresses. Preserve metadata EFAULT without a process-wide signal
// handler or protection change. Darwin's final length copyout is best-effort
// and does not replace an existing error (or success) with a copyout error.
static int MacWSSendfileCopyIn(void *to, const void *from, size_t size) {
    vm_size_t copied = 0;
    kern_return_t result = vm_read_overwrite(mach_task_self(),
        (vm_address_t)(uintptr_t)from, size,
        (vm_address_t)(uintptr_t)to, &copied);
    if (result == KERN_SUCCESS && copied == size) return 0;
    errno = EFAULT;
    return -1;
}

static int MacWSSendfileCopyLength(off_t *to, off_t value) {
    kern_return_t result = vm_write(mach_task_self(),
        (vm_address_t)(uintptr_t)to,
        (vm_offset_t)(uintptr_t)&value, (mach_msg_type_number_t)sizeof(value));
    if (result == KERN_SUCCESS) return 0;
    errno = EFAULT;
    return -1;
}

static int MacWSSendfileVectors(int socketFD, const struct iovec *source,
                               int count, off_t *sent) {
    if (!source) return 0; // Darwin ignores the corresponding absent vector.
    if (count < 0 || count > IOV_MAX) { errno = EINVAL; return -1; }
    if (!count) return 0;
    struct iovec *vectors = malloc((size_t)count * sizeof(*vectors));
    if (!vectors) { errno = ENOMEM; return -1; }
    int result = -1, error = 0;
    if (MacWSSendfileCopyIn(vectors, source,
                          (size_t)count * sizeof(*vectors)) != 0) goto done;
    size_t remaining = 0;
    for (int i = 0; i < count; ++i) {
        if (vectors[i].iov_len > (size_t)SSIZE_MAX - remaining) {
            errno = EINVAL;
            goto done;
        }
        remaining += vectors[i].iov_len;
    }
    if (remaining > (uint64_t)INT64_MAX - (uint64_t)*sent) {
        errno = EOVERFLOW;
        goto done;
    }
    int first = 0;
    while (remaining) {
        // writev retains native payload-address checks and header/trailer
        // SIGPIPE semantics. Do not retry EINTR or consume EAGAIN: report the
        // exact prefix already accepted so callers can resume without repeats.
        ssize_t amount = writev(socketFD, vectors + first, count - first);
        if (amount < 0) goto done;
        if (!amount) { errno = EIO; goto done; }
        *sent += amount;
        remaining -= (size_t)amount;
        size_t advanced = (size_t)amount;
        while (first < count && advanced >= vectors[first].iov_len) {
            advanced -= vectors[first].iov_len;
            ++first;
        }
        if (advanced) {
            vectors[first].iov_base = (char *)vectors[first].iov_base + advanced;
            vectors[first].iov_len -= advanced;
        }
    }
    result = 0;
done:
    error = errno;
    free(vectors);
    errno = error;
    return result;
}

int MacWSSendfile(int inputFD, int socketFD, off_t offset, off_t *length,
                  struct sf_hdtr *headersAndTrailers, int flags) {
    off_t sent = 0, requested = 0;
    int result = -1, error = 0;
    unsigned char *buffer = NULL;
    struct sf_hdtr vectors = {0};
    struct stat file;

    int access = fcntl(inputFD, F_GETFL);
    if (access < 0) goto done;
    if ((access & O_ACCMODE) == O_WRONLY) { errno = EBADF; goto done; }
    if (fstat(inputFD, &file) != 0) goto done;
    if (!S_ISREG(file.st_mode)) { errno = ENOTSUP; goto done; }

    int type = 0;
    socklen_t typeLength = sizeof(type);
    // For regular-file output, this returns the real ENOTSOCK that macOS
    // callers already handle; it must not silently copy data or claim success.
    if (getsockopt(socketFD, SOL_SOCKET, SO_TYPE, &type, &typeLength) != 0)
        goto done;
    if (type != SOCK_STREAM) { errno = EINVAL; goto done; }
    struct sockaddr_storage peer;
    socklen_t peerLength = sizeof(peer);
    if (getpeername(socketFD, (struct sockaddr *)&peer, &peerLength) != 0) {
        // After SO_TYPE established a real stream socket, Darwin getpeername
        // reports EINVAL for an AF_UNIX peer that has closed. Stock macOS
        // sendfile reports ENOTCONN for this same state (socketpair reference
        // probe, 2026-09-19). Preserve every other error, including EBADF or
        // ENOTSOCK if another thread changed the descriptor in the meantime.
        if (errno == EINVAL) errno = ENOTCONN;
        goto done;
    }
    if (offset < 0 || !length || flags != 0) { errno = EINVAL; goto done; }
    if (MacWSSendfileCopyIn(&requested, length, sizeof(requested)) != 0)
        goto done;
    if (headersAndTrailers && MacWSSendfileCopyIn(&vectors, headersAndTrailers,
                                                 sizeof(vectors)) != 0)
        goto done;

    // XNU sends the full header first; len limits header+file, not trailers.
    // Stock macOS verified: 5-byte header, len=3 => header+trailer, no file;
    // len=8 => full header +3 file bytes+trailer; len=0 => through EOF.
    if (MacWSSendfileVectors(socketFD, vectors.headers, vectors.hdr_cnt,
                            &sent) != 0) goto done;
    if (fstat(inputFD, &file) != 0) goto done;
    off_t available = file.st_size > offset ? file.st_size - offset : 0;
    if (requested != 0) {
        off_t budget = requested > sent ? requested - sent : 0;
        if (available > budget) available = budget;
    }
    if ((uint64_t)available > (uint64_t)INT64_MAX - (uint64_t)sent) {
        errno = EOVERFLOW;
        goto done;
    }
    if (available) {
        buffer = malloc(MacWSSendfileChunk);
        if (!buffer) { errno = ENOMEM; goto done; }
    }
    while (available > 0) {
        size_t chunk = available < MacWSSendfileChunk ?
            (size_t)available : MacWSSendfileChunk;
        ssize_t readBytes = pread(inputFD, buffer, chunk, offset);
        if (readBytes < 0) goto done;
        if (!readBytes) break; // Concurrent truncation is ordinary EOF.
        size_t consumed = 0;
        while (consumed < (size_t)readBytes) {
            // sendfile's kernel file-data path does not raise SIGPIPE. Keep
            // the caller's socket options and signal disposition untouched.
            ssize_t amount = send(socketFD, buffer + consumed,
                (size_t)readBytes - consumed, MSG_NOSIGNAL);
            if (amount < 0) goto done;
            if (!amount) { errno = EIO; goto done; }
            consumed += (size_t)amount;
            sent += amount;
            offset += amount;
            available -= amount;
        }
    }
    if (MacWSSendfileVectors(socketFD, vectors.trailers, vectors.trl_cnt,
                            &sent) != 0) goto done;
    result = 0;
done:
    error = errno;
    free(buffer);
    if (length) (void)MacWSSendfileCopyLength(length, sent);
    errno = error;
    return result;
}

#ifndef MACWS_SENDFILE_UNIT_TEST
#include "interpose.h"
DYLD_INTERPOSE(MacWSSendfile, sendfile)
#endif

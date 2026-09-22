#ifndef MACWS_FILE_COPY_H
#define MACWS_FILE_COPY_H

#include <CommonCrypto/CommonDigest.h>
#include <errno.h>
#include <stdint.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

// File payloads are not inline XPC payloads. Stream at a fixed memory cost;
// reject mutation/truncation and verify the destination before publishing it.
// The caller owns both descriptors, exclusively created the destination, and
// removes it on failure. Destination must be O_RDWR, positioned at zero.
static inline int MacWSCopyStableRegularFile(int source, int destination,
                                            uint64_t *copiedBytes) {
    struct stat before = {0}, after = {0}, output = {0};
    if (fstat(source, &before) != 0) return -1;
    if (!S_ISREG(before.st_mode) || before.st_size < 0) {
        errno = EINVAL;
        return -1;
    }
    unsigned char buffer[128 * 1024];
    CC_SHA256_CTX inputHash, outputHash;
    CC_SHA256_Init(&inputHash);
    uint64_t total = 0;
    while (total < (uint64_t)before.st_size) {
        size_t wanted = sizeof(buffer);
        if ((uint64_t)before.st_size - total < wanted)
            wanted = (size_t)((uint64_t)before.st_size - total);
        ssize_t count = read(source, buffer, wanted);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) { if (count == 0) errno = EIO; return -1; }
        CC_SHA256_Update(&inputHash, buffer, (CC_LONG)count);
        for (ssize_t offset = 0; offset < count;) {
            ssize_t written = write(destination, buffer + offset,
                                    (size_t)(count - offset));
            if (written < 0 && errno == EINTR) continue;
            if (written <= 0) { if (written == 0) errno = EIO; return -1; }
            offset += written;
        }
        total += (uint64_t)count;
    }
    if (fstat(source, &after) != 0 || fstat(destination, &output) != 0)
        return -1;
    if (before.st_size != after.st_size || output.st_size != before.st_size ||
        before.st_mtimespec.tv_sec != after.st_mtimespec.tv_sec ||
        before.st_mtimespec.tv_nsec != after.st_mtimespec.tv_nsec ||
        before.st_ctimespec.tv_sec != after.st_ctimespec.tv_sec ||
        before.st_ctimespec.tv_nsec != after.st_ctimespec.tv_nsec) {
        errno = ESTALE;
        return -1;
    }
    if (fsync(destination) != 0 || lseek(destination, 0, SEEK_SET) < 0)
        return -1;
    CC_SHA256_Init(&outputHash);
    for (uint64_t checked = 0; checked < total;) {
        ssize_t count = read(destination, buffer, sizeof(buffer));
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) { if (count == 0) errno = EIO; return -1; }
        CC_SHA256_Update(&outputHash, buffer, (CC_LONG)count);
        checked += (uint64_t)count;
    }
    unsigned char inputDigest[CC_SHA256_DIGEST_LENGTH];
    unsigned char outputDigest[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256_Final(inputDigest, &inputHash);
    CC_SHA256_Final(outputDigest, &outputHash);
    if (memcmp(inputDigest, outputDigest, sizeof(inputDigest)) != 0) {
        errno = EIO;
        return -1;
    }
    if (copiedBytes) *copiedBytes = total;
    return 0;
}
#endif

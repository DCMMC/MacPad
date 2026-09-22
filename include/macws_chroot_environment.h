#ifndef MACWS_CHROOT_ENVIRONMENT_H
#define MACWS_CHROOT_ENVIRONMENT_H

#include <stddef.h>

// These are Darwin's BSD syscall / fcntl ABI values (iPhoneOS 16.5 SDK).
// The callback returns a negative errno on failure, including for open().
// A freestanding caller must translate carry, rather than mistake errno for
// an open descriptor and close a descriptor belonging to the launch context.
typedef long (*MacWSChrootCheckedSyscall)(long, long, long, long);
#define MACWS_CHROOT_PATH_MAX 1024
#define MACWS_CHROOT_ROOT_KEY "MACWS_CHROOT_HOST_ROOT"
#define MACWS_CHROOT_ROOT_PREFIX MACWS_CHROOT_ROOT_KEY "="
#define MACWS_CHROOT_ROOT_ENV_SIZE \
    (sizeof(MACWS_CHROOT_ROOT_PREFIX) - 1 + MACWS_CHROOT_PATH_MAX)

// Obtain the actual host namespace from the opened root vnode *before*
// chroot. Do not invent an alias, reuse inherited metadata from another root,
// or depend on libSystem starting up before the real XPC executable.
static inline int MacWSBuildChrootRootEnvironment(
        const char *root, char *output, size_t capacity,
        MacWSChrootCheckedSyscall call) {
    static const char prefix[] = MACWS_CHROOT_ROOT_PREFIX;
    if (!root || root[0] != '/' || !output ||
        capacity < MACWS_CHROOT_ROOT_ENV_SIZE || !call) return 0;
    for (size_t i = 0; i < sizeof(prefix) - 1; i++) output[i] = prefix[i];
    char *path = output + sizeof(prefix) - 1;
    // Fill nonzero so a malformed/nonterminating successful response is not
    // accepted merely because the caller's static storage started at zero.
    for (size_t i = 0; i < MACWS_CHROOT_PATH_MAX; i++) path[i] = (char)0x7f;
    long fd = call(5 /* open */, (long)root,
                   0x00100000 /* O_DIRECTORY */ | 0x01000000 /* O_CLOEXEC */,
                   0 /* O_RDONLY, mode */);
    if (fd < 0) return 0;
    long result = call(92 /* fcntl */, fd, 50 /* F_GETPATH */, (long)path);
    (void)call(6 /* close */, fd, 0, 0);
    if (result != 0 || path[0] != '/' || !path[1]) return 0;
    for (size_t i = 0; i < MACWS_CHROOT_PATH_MAX; i++)
        if (!path[i]) return 1;
    return 0;
}

#endif

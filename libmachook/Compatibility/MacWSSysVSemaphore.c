#include <errno.h>
#include <dlfcn.h>
#include <fcntl.h>
#include <semaphore.h>
#include <signal.h>
#include <stdlib.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/file.h>
#include <sys/ipc.h>
#include <sys/event.h>
#include <sys/sem.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include "interpose.h"

// macOS Steam 2026-08-04 uses a one-element System V semaphore as its
// cross-process named mutex. The iPadOS 16 kernel deliberately terminates a
// process that enters libsystem_kernel!semget with SIGSYS. Runtime evidence:
// steam_osx-2026-08-13-050712.ips, faulting frame semget+8. RE evidence:
// steam_osx arm64 +0x238a04 calls semget(key, 1, IPC_CREAT|IPC_EXCL|0660),
// followed by semctl(SETVAL); its lock/unlock sites use one sembuf with -1/+1.
//
// Preserve that synchronization invariant using a flock-protected state file
// and EVFILT_VNODE wakeups. An earlier adapter used POSIX named semaphores as
// its backing primitive, but Steam also creates several POSIX semaphores per
// CSharedMemStream and iPadOS's small global table then returned ENOSPC. Using
// one file per System V set removes that second global namespace dependency
// while retaining atomic counter updates and sleeping cross-process waits.
// The state record also supplies
// the System V query operations Steam uses (GETNCNT/GETZCNT/GETPID/GETVAL) and
// makes the public integer identifier deterministic across helper processes.
// This is an ABI adapter, not a success stub: waits block, IPC_NOWAIT returns
// EAGAIN, posts wake waiters, SETVAL changes the real semaphore count, and
// IPC_RMID removes both namespaces.

#define MACWS_SYSVSEM_MAGIC 0x4d575356u /* MWSV */
#define MACWS_SYSVSEM_VERSION 1u
#define MACWS_SYSVSEM_ID_TAG 0x20000000u
#define MACWS_SYSVSEM_ID_MASK 0x1fffffffu

typedef struct {
    uint32_t magic;
    uint32_t version;
    int32_t key;
    int32_t semid;
    int32_t value;
    int32_t lastPID;
    int32_t negativeWaiters;
    int32_t zeroWaiters;
    uint32_t mode;
    uint32_t reserved;
} MacWSSysVSemaphoreState;

static int MacWSSysVSemaphoreIDForKey(key_t key) {
    uint32_t value = (uint32_t)key;
    value ^= value >> 16;
    value *= 0x7feb352du;
    value ^= value >> 15;
    value *= 0x846ca68bu;
    value ^= value >> 16;
    return (int)(MACWS_SYSVSEM_ID_TAG | (value & MACWS_SYSVSEM_ID_MASK));
}

static void MacWSSysVSemaphoreNames(int semid, char *path, size_t pathSize,
                                    char *name, size_t nameSize) {
    snprintf(path, pathSize, "/tmp/.macws-sysvsem-%08x", (unsigned)semid);
    snprintf(name, nameSize, "/macws-sysvsem-%08x", (unsigned)semid);
}

static int MacWSSysVSemaphoreOpenState(int semid, int flags, mode_t mode,
                                       MacWSSysVSemaphoreState *state,
                                       int *createdOut) {
    char path[96];
    char name[64];
    MacWSSysVSemaphoreNames(semid, path, sizeof(path), name, sizeof(name));
    int fd = open(path, flags | O_CLOEXEC, mode);
    if (fd < 0) return -1;
    if (flock(fd, LOCK_EX) != 0) {
        int saved = errno;
        close(fd);
        errno = saved;
        return -1;
    }
    struct stat status;
    int created = fstat(fd, &status) == 0 && status.st_size == 0;
    if (!created) {
        ssize_t amount = pread(fd, state, sizeof(*state), 0);
        if (amount != (ssize_t)sizeof(*state) ||
            state->magic != MACWS_SYSVSEM_MAGIC ||
            state->version != MACWS_SYSVSEM_VERSION ||
            state->semid != semid) {
            flock(fd, LOCK_UN);
            close(fd);
            errno = EINVAL;
            return -1;
        }
    }
    if (createdOut) *createdOut = created;
    return fd;
}

static int MacWSSysVSemaphoreWriteState(
        int fd, const MacWSSysVSemaphoreState *state) {
    if (pwrite(fd, state, sizeof(*state), 0) != (ssize_t)sizeof(*state) ||
        ftruncate(fd, sizeof(*state)) != 0) return -1;
    return 0;
}

static void MacWSSysVSemaphoreCloseState(int fd) {
    int saved = errno;
    (void)flock(fd, LOCK_UN);
    close(fd);
    errno = saved;
}

static int MacWSSemgetImpl(key_t key, int count, int flags) {
    if (count < 0 || count > 1) {
        errno = count > 1 ? ENOSPC : EINVAL;
        return -1;
    }
    if (key == IPC_PRIVATE) {
        static uint32_t privateSequence;
        key = (key_t)(((uint32_t)getpid() << 12) ^
                      __atomic_add_fetch(&privateSequence, 1,
                                         __ATOMIC_RELAXED));
    }
    int semid = MacWSSysVSemaphoreIDForKey(key);
    int openFlags = O_RDWR;
    if (flags & IPC_CREAT) openFlags |= O_CREAT;
    if (flags & IPC_EXCL) openFlags |= O_EXCL;
    MacWSSysVSemaphoreState state = {0};
    int created = 0;
    int fd = MacWSSysVSemaphoreOpenState(
        semid, openFlags, (mode_t)(flags & 0777), &state, &created);
    if (fd < 0) return -1;
    if (created) {
        state.magic = MACWS_SYSVSEM_MAGIC;
        state.version = MACWS_SYSVSEM_VERSION;
        state.key = key;
        state.semid = semid;
        state.value = 0;
        state.lastPID = 0;
        state.mode = (uint32_t)(flags & 0777);
        if (MacWSSysVSemaphoreWriteState(fd, &state) != 0) {
            int saved = errno;
            MacWSSysVSemaphoreCloseState(fd);
            errno = saved;
            return -1;
        }
    } else if (state.key != key) {
        MacWSSysVSemaphoreCloseState(fd);
        errno = EEXIST;
        return -1;
    }
    MacWSSysVSemaphoreCloseState(fd);
    return semid;
}

// Bounded evidence collector for Steam's global-instance election. The
// interposer above is the iPadOS ABI implementation, so logging at this
// boundary records the exact key/flags/result Steam observes without changing
// the System V state machine. Keep it opt-in: global-instance construction is
// cold-path work, but production launches should still pay no formatting or
// symbolication cost.
static int MacWSSemget(key_t key, int count, int flags) {
    int result = MacWSSemgetImpl(key, count, flags);
    int savedError = errno;
    static unsigned diagnosticCount;
    if (getenv("MACWS_STEAM_SYSVSEM_DIAGNOSTICS") &&
        __atomic_fetch_add(&diagnosticCount, 1, __ATOMIC_RELAXED) < 64) {
        void *caller = __builtin_return_address(0);
        Dl_info image = {0};
        (void)dladdr(caller, &image);
        dprintf(STDERR_FILENO,
                "[MacWSSysVSem] pid=%d program=%s op=semget key=%#x "
                "count=%d flags=%#x result=%d errno=%d caller=%p "
                "image=%s offset=%#llx\n",
                getpid(), getprogname() ?: "?", (unsigned)key, count,
                flags, result, result < 0 ? savedError : 0, caller,
                image.dli_fname ?: "?",
                image.dli_fbase ? (unsigned long long)(
                    (uintptr_t)caller - (uintptr_t)image.dli_fbase) : 0);
    }
    errno = savedError;
    return result;
}

static int MacWSSemctl(int semid, int number, int command, ...) {
    if (number != 0) {
        errno = EINVAL;
        if (getenv("MACWS_STEAM_SYSVSEM_DIAGNOSTICS"))
            dprintf(STDERR_FILENO,
                    "[MacWSSysVSem] pid=%d program=%s op=semctl semid=%#x "
                    "number=%d command=%#x result=-1 errno=%d caller=%p\n",
                    getpid(), getprogname() ?: "?", (unsigned)semid,
                    number, command, errno, __builtin_return_address(0));
        return -1;
    }
    char path[96];
    char name[64];
    MacWSSysVSemaphoreNames(semid, path, sizeof(path), name, sizeof(name));
    MacWSSysVSemaphoreState state = {0};
    int fd = MacWSSysVSemaphoreOpenState(
        semid, O_RDWR, 0, &state, NULL);
    if (fd < 0) {
        int savedError = errno;
        if (getenv("MACWS_STEAM_SYSVSEM_DIAGNOSTICS"))
            dprintf(STDERR_FILENO,
                    "[MacWSSysVSem] pid=%d program=%s op=semctl semid=%#x "
                    "number=%d command=%#x result=-1 errno=%d caller=%p\n",
                    getpid(), getprogname() ?: "?", (unsigned)semid,
                    number, command, savedError,
                    __builtin_return_address(0));
        errno = savedError;
        return -1;
    }

    union semun argument = {0};
    if (command == SETVAL || command == SETALL || command == GETALL ||
        command == IPC_STAT || command == IPC_SET) {
        va_list arguments;
        va_start(arguments, command);
        argument = va_arg(arguments, union semun);
        va_end(arguments);
    }

    int result = 0;
    switch (command) {
        case GETNCNT: result = state.negativeWaiters; break;
        case GETZCNT: result = state.zeroWaiters; break;
        case GETPID: result = state.lastPID; break;
        case GETVAL: result = state.value; break;
        case GETALL:
            if (!argument.array) { errno = EFAULT; result = -1; }
            else argument.array[0] = (unsigned short)state.value;
            break;
        case SETVAL:
        case SETALL: {
            int value = command == SETVAL ? argument.val :
                (argument.array ? argument.array[0] : -1);
            if (value < 0) {
                errno = ERANGE;
                result = -1;
            } else {
                state.value = value;
                state.lastPID = getpid();
                result = MacWSSysVSemaphoreWriteState(fd, &state);
            }
            break;
        }
        case IPC_STAT:
            if (!argument.buf) {
                errno = EFAULT;
                result = -1;
            } else {
                memset(argument.buf, 0, sizeof(*argument.buf));
                argument.buf->sem_nsems = 1;
                argument.buf->sem_perm.mode = (mode_t)state.mode;
            }
            break;
        case IPC_SET:
            if (!argument.buf) {
                errno = EFAULT;
                result = -1;
            } else {
                state.mode = argument.buf->sem_perm.mode & 0777;
                result = MacWSSysVSemaphoreWriteState(fd, &state);
            }
            break;
        case IPC_RMID:
            result = 0;
            break;
        default:
            errno = EINVAL;
            result = -1;
            break;
    }
    MacWSSysVSemaphoreCloseState(fd);
    if (command == IPC_RMID && result == 0) (void)unlink(path);
    int savedError = errno;
    static unsigned diagnosticCount;
    if (getenv("MACWS_STEAM_SYSVSEM_DIAGNOSTICS") &&
        __atomic_fetch_add(&diagnosticCount, 1, __ATOMIC_RELAXED) < 128)
        dprintf(STDERR_FILENO,
                "[MacWSSysVSem] pid=%d program=%s op=semctl semid=%#x "
                "number=%d command=%#x result=%d errno=%d value=%d "
                "lastpid=%d caller=%p\n",
                getpid(), getprogname() ?: "?", (unsigned)semid, number,
                command, result, result < 0 ? savedError : 0, state.value,
                state.lastPID, __builtin_return_address(0));
    errno = savedError;
    return result;
}

static int MacWSSysVSemaphoreWaitForChange(
        int fd, MacWSSysVSemaphoreState *state) {
    int queue = kqueue();
    if (queue < 0) return -1;
    (void)fcntl(queue, F_SETFD, FD_CLOEXEC);
    struct kevent change;
    EV_SET(&change, fd, EVFILT_VNODE, EV_ADD | EV_CLEAR,
           NOTE_WRITE | NOTE_DELETE | NOTE_RENAME, 0, NULL);
    if (kevent(queue, &change, 1, NULL, 0, NULL) != 0) {
        int saved = errno;
        close(queue);
        errno = saved;
        return -1;
    }

    if (flock(fd, LOCK_UN) != 0) {
        int saved = errno;
        close(queue);
        errno = saved;
        return -1;
    }
    int waitResult;
    do {
        struct kevent event;
        waitResult = kevent(queue, NULL, 0, &event, 1, NULL);
    } while (waitResult < 0 && errno == EINTR);
    int saved = errno;
    close(queue);
    if (waitResult < 0) {
        errno = saved;
        return -1;
    }
    if (flock(fd, LOCK_EX) != 0) return -1;
    ssize_t amount = pread(fd, state, sizeof(*state), 0);
    if (amount != (ssize_t)sizeof(*state) ||
        state->magic != MACWS_SYSVSEM_MAGIC ||
        state->version != MACWS_SYSVSEM_VERSION) {
        errno = EIDRM;
        return -1;
    }
    return 0;
}

static int MacWSSysVSemaphoreWaitForZero(int *fdInOut,
                                         MacWSSysVSemaphoreState *state,
                                         int nowait) {
    int fd = *fdInOut;
    for (;;) {
        if (state->value == 0) {
            *fdInOut = fd;
            return 0;
        }
        if (nowait) {
            *fdInOut = fd;
            errno = EAGAIN;
            return -1;
        }
        state->zeroWaiters++;
        if (MacWSSysVSemaphoreWriteState(fd, state) != 0) {
            *fdInOut = fd;
            return -1;
        }
        if (MacWSSysVSemaphoreWaitForChange(fd, state) != 0) {
            *fdInOut = -1;
            close(fd);
            return -1;
        }
        if (state->zeroWaiters > 0) {
            state->zeroWaiters--;
            if (MacWSSysVSemaphoreWriteState(fd, state) != 0) {
                *fdInOut = fd;
                return -1;
            }
        }
    }
}

static int MacWSSemop(int semid, struct sembuf *operations,
                      size_t operationCount) {
    if (!operations || operationCount == 0) {
        errno = EINVAL;
        return -1;
    }
    MacWSSysVSemaphoreState state = {0};
    int fd = MacWSSysVSemaphoreOpenState(
        semid, O_RDWR, 0, &state, NULL);
    if (fd < 0) {
        int savedError = errno;
        if (getenv("MACWS_STEAM_SYSVSEM_DIAGNOSTICS"))
            dprintf(STDERR_FILENO,
                    "[MacWSSysVSem] pid=%d program=%s op=semop semid=%#x "
                    "count=%zu result=-1 errno=%d caller=%p\n",
                    getpid(), getprogname() ?: "?", (unsigned)semid,
                    operationCount, savedError,
                    __builtin_return_address(0));
        errno = savedError;
        return -1;
    }
    int result = 0;
    for (size_t index = 0; index < operationCount; index++) {
        struct sembuf operation = operations[index];
        if (operation.sem_num != 0) { errno = EFBIG; result = -1; break; }
        if (operation.sem_op == 0) {
            result = MacWSSysVSemaphoreWaitForZero(
                &fd, &state, (operation.sem_flg & IPC_NOWAIT) != 0);
            if (result != 0) break;
            continue;
        }
        if (operation.sem_op < 0) {
            int amount = -(int)operation.sem_op;
            int nowait = (operation.sem_flg & IPC_NOWAIT) != 0;
            while (state.value < amount) {
                if (nowait) {
                    errno = EAGAIN;
                    result = -1;
                    break;
                }
                state.negativeWaiters++;
                if (MacWSSysVSemaphoreWriteState(fd, &state) != 0 ||
                    MacWSSysVSemaphoreWaitForChange(fd, &state) != 0) {
                    result = -1;
                    break;
                }
                if (state.negativeWaiters > 0) {
                    state.negativeWaiters--;
                    if (MacWSSysVSemaphoreWriteState(fd, &state) != 0) {
                        result = -1;
                        break;
                    }
                }
            }
            if (result != 0) break;
            state.value -= amount;
            state.lastPID = getpid();
            if (MacWSSysVSemaphoreWriteState(fd, &state) != 0) {
                result = -1;
                break;
            }
            continue;
        }
        if (state.value > INT32_MAX - operation.sem_op) {
            errno = ERANGE;
            result = -1;
            break;
        }
        state.value += operation.sem_op;
        state.lastPID = getpid();
        if (MacWSSysVSemaphoreWriteState(fd, &state) != 0) {
            result = -1;
            break;
        }
    }
    int saved = errno;
    if (fd >= 0) MacWSSysVSemaphoreCloseState(fd);
    static unsigned diagnosticCount;
    if (getenv("MACWS_STEAM_SYSVSEM_DIAGNOSTICS") &&
        __atomic_fetch_add(&diagnosticCount, 1, __ATOMIC_RELAXED) < 128) {
        int firstOperation = operationCount > 0 ? operations[0].sem_op : 0;
        int firstFlags = operationCount > 0 ? operations[0].sem_flg : 0;
        dprintf(STDERR_FILENO,
                "[MacWSSysVSem] pid=%d program=%s op=semop semid=%#x "
                "count=%zu first_op=%d first_flags=%#x result=%d errno=%d "
                "value=%d lastpid=%d caller=%p\n",
                getpid(), getprogname() ?: "?", (unsigned)semid,
                operationCount, firstOperation, firstFlags, result,
                result < 0 ? saved : 0, state.value, state.lastPID,
                __builtin_return_address(0));
    }
    errno = saved;
    return result;
}

DYLD_INTERPOSE(MacWSSemget, semget)
DYLD_INTERPOSE(MacWSSemctl, semctl)
DYLD_INTERPOSE(MacWSSemop, semop)

@import Darwin;
#include <assert.h> 
#include <sys/socket.h>
#include <sys/un.h>
#define CS_DEBUGGED 0x10000000
int csops(pid_t pid, unsigned int ops, void *useraddr, size_t usersize);
int isJITEnabled() {
    int flags = 0;
    return csops(getpid(), 0, &flags, sizeof(flags)) == 0 &&
        (flags & CS_DEBUGGED) != 0;
}

// Ask the already-running iOS/root helper to mark this exact Unix-socket peer
// debugged.  This deliberately performs no fork in the injected process:
// libmachook constructors execute under dyld's image-initialization path, and
// a constructor-time fork previously left Steam CEF's later dlopen waiting on
// dyld's recursive API lock forever.  autosignd validates the request against
// LOCAL_PEERPID instead of trusting a client-supplied PID.
static int requestJITFromAutosignd(void) {
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) {
        dprintf(STDERR_FILENO,
                "#### MACWS-JIT authorization failed pid=%d program=%s "
                "stage=socket errno=%d\n",
                getpid(), getprogname() ?: "(unknown)", errno);
        return 0;
    }
    struct timeval timeout = { .tv_sec = 2, .tv_usec = 0 };
    (void)setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO,
                     &timeout, sizeof(timeout));
    (void)setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO,
                     &timeout, sizeof(timeout));
    struct sockaddr_un address = {0};
    address.sun_family = AF_UNIX;
    strlcpy(address.sun_path, "/tmp/autosignd.sock",
            sizeof(address.sun_path));
    int connected = connect(fd, (struct sockaddr *)&address,
                            sizeof(address)) == 0;
    int connectError = connected ? 0 : errno;
    char reply[8] = {0};
    ssize_t writeResult = connected ? write(fd, "DEBUG\n", 6) : -1;
    int writeError = writeResult == 6 ? 0 : errno;
    ssize_t readResult = writeResult == 6
        ? read(fd, reply, sizeof(reply) - 1) : -1;
    int readError = readResult > 0 ? 0 : errno;
    int ok = connected && writeResult == 6 && readResult > 0 &&
        strncmp(reply, "OK\n", 3) == 0;
    close(fd);
    if (!ok) {
        dprintf(STDERR_FILENO,
                "#### MACWS-JIT authorization failed pid=%d program=%s "
                "stage=autosignd connected=%d connect_errno=%d write=%zd "
                "write_errno=%d read=%zd read_errno=%d reply=%.*s\n",
                getpid(), getprogname() ?: "(unknown)", connected,
                connectError, writeResult, writeError, readResult, readError,
                readResult > 0 ? (int)readResult : 0, reply);
    }
    return ok;
}

void EnableJIT() {
    int requested = -1;
    if (!isJITEnabled()) {
        requested = requestJITFromAutosignd();
        for (unsigned int attempt = 0;
             requested && attempt < 250 && !isJITEnabled(); attempt++) {
            usleep(1000);
        }
    }
    if (!isJITEnabled()) {
        int flags = 0;
        errno = 0;
        int result = csops(getpid(), 0, &flags, sizeof(flags));
        int savedError = errno;
        dprintf(STDERR_FILENO,
                "#### MACWS-JIT authorization invariant failed pid=%d "
                "program=%s requested=%d csops=%d errno=%d flags=%#x\n",
                getpid(), getprogname() ?: "(unknown)", requested, result,
                savedError, flags);
    }
    assert(isJITEnabled());
}

@import Darwin;
@import Security;

#import "interpose.h"

#include <dlfcn.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdatomic.h>
#include <sys/stat.h>

typedef int (*MacWSGetEntropyFunction)(void *, size_t);

static MacWSGetEntropyFunction gMacWSRealGetEntropy;

// Diagnostic-only witness for platform random failures. Keep this outside the
// compatibility implementation: it is enabled explicitly for one launch and
// must never become a fallback that fabricates entropy or masks an OSStatus.
static OSStatus MacWSDiagnosticSecRandomCopyBytes(SecRandomRef random,
                                                   size_t count,
                                                   uint8_t *bytes) {
    OSStatus status = SecRandomCopyBytes(random, count, bytes);
    if (getenv("MACWS_RANDOM_DIAGNOSTICS")) {
        static _Atomic unsigned callCount = 0;
        unsigned call = atomic_fetch_add_explicit(
            &callCount, 1, memory_order_relaxed) + 1;
        fprintf(stderr,
                "[MacWSRandomDiagnostics] api=SecRandomCopyBytes "
                "call=%u pid=%d program=%s random=%p count=%zu "
                "bytes=%p status=%d return=%p\n",
                call, getpid(), getprogname() ?: "(unknown)", random, count,
                bytes, (int)status, __builtin_return_address(0));
        fflush(stderr);
    }
    return status;
}

DYLD_INTERPOSE(MacWSDiagnosticSecRandomCopyBytes, SecRandomCopyBytes)

static bool MacWSRandomDiagnosticsEnabled(void) {
    return getenv("MACWS_RANDOM_DIAGNOSTICS") != NULL;
}

static bool MacWSRandomDiagnosticPath(const char *path) {
    if (!path) return false;
    return strstr(path, "random") || strstr(path, "urandom") ||
        strstr(path, "ssl") || strstr(path, "cert") ||
        strstr(path, "pem");
}

static int MacWSDiagnosticGetEntropy(void *bytes, size_t count) {
    MacWSGetEntropyFunction realGetEntropy = gMacWSRealGetEntropy;
    if (!realGetEntropy) {
        errno = ENOSYS;
        return -1;
    }
    int result = realGetEntropy(bytes, count);
    int savedError = errno;
    fprintf(stderr,
            "[MacWSRandomDiagnostics] api=getentropy pid=%d program=%s "
            "count=%zu bytes=%p result=%d errno=%d return=%p\n",
            getpid(), getprogname() ?: "(unknown)", count, bytes, result,
            result < 0 ? savedError : 0, __builtin_return_address(0));
    fflush(stderr);
    errno = savedError;
    return result;
}

static void *MacWSDiagnosticDlsym(void *handle, const char *symbol) {
    void *result = dlsym(handle, symbol);
    int savedError = errno;
    if (MacWSRandomDiagnosticsEnabled() && symbol &&
        strcmp(symbol, "getentropy") == 0) {
        gMacWSRealGetEntropy = (MacWSGetEntropyFunction)result;
        fprintf(stderr,
                "[MacWSRandomDiagnostics] api=dlsym pid=%d program=%s "
                "symbol=getentropy resolved=%p return=%p\n",
                getpid(), getprogname() ?: "(unknown)", result,
                __builtin_return_address(0));
        fflush(stderr);
        if (result) result = (void *)&MacWSDiagnosticGetEntropy;
    }
    errno = savedError;
    return result;
}

static int MacWSDiagnosticOpen(const char *path, int flags, ...) {
    mode_t mode = 0;
    if (flags & O_CREAT) {
        va_list arguments;
        va_start(arguments, flags);
        mode = (mode_t)va_arg(arguments, int);
        va_end(arguments);
    }
    int descriptor = (flags & O_CREAT) ? open(path, flags, mode) :
        open(path, flags);
    int savedError = errno;
    if (MacWSRandomDiagnosticsEnabled() &&
        MacWSRandomDiagnosticPath(path)) {
        fprintf(stderr,
                "[MacWSRandomDiagnostics] api=open pid=%d program=%s "
                "path=%s flags=%#x result=%d errno=%d return=%p\n",
                getpid(), getprogname() ?: "(unknown)", path, flags,
                descriptor, descriptor < 0 ? savedError : 0,
                __builtin_return_address(0));
        fflush(stderr);
    }
    errno = savedError;
    return descriptor;
}

static ssize_t MacWSDiagnosticRead(int descriptor, void *bytes,
                                   size_t count) {
    ssize_t result = read(descriptor, bytes, count);
    int savedError = errno;
    if (MacWSRandomDiagnosticsEnabled()) {
        char linkPath[64] = {0};
        char targetPath[PATH_MAX] = {0};
        snprintf(linkPath, sizeof(linkPath), "/dev/fd/%d", descriptor);
        ssize_t targetLength = readlink(linkPath, targetPath,
                                        sizeof(targetPath) - 1);
        if (targetLength > 0 &&
            (!strcmp(targetPath, "/dev/random") ||
             !strcmp(targetPath, "/dev/urandom"))) {
            fprintf(stderr,
                    "[MacWSRandomDiagnostics] api=read pid=%d program=%s "
                    "fd=%d path=%s count=%zu result=%zd errno=%d "
                    "return=%p\n",
                    getpid(), getprogname() ?: "(unknown)", descriptor,
                    targetPath, count, result, result < 0 ? savedError : 0,
                    __builtin_return_address(0));
            fflush(stderr);
        }
    }
    errno = savedError;
    return result;
}

static FILE *MacWSDiagnosticFopen(const char *path, const char *mode) {
    FILE *stream = fopen(path, mode);
    int savedError = errno;
    if (MacWSRandomDiagnosticsEnabled() &&
        MacWSRandomDiagnosticPath(path)) {
        fprintf(stderr,
                "[MacWSRandomDiagnostics] api=fopen pid=%d program=%s "
                "path=%s mode=%s result=%p errno=%d return=%p\n",
                getpid(), getprogname() ?: "(unknown)", path ?: "(null)",
                mode ?: "(null)", stream, stream ? 0 : savedError,
                __builtin_return_address(0));
        fflush(stderr);
    }
    errno = savedError;
    return stream;
}

static int MacWSDiagnosticAccess(const char *path, int mode) {
    int result = access(path, mode);
    int savedError = errno;
    if (MacWSRandomDiagnosticsEnabled() &&
        MacWSRandomDiagnosticPath(path)) {
        fprintf(stderr,
                "[MacWSRandomDiagnostics] api=access pid=%d program=%s "
                "path=%s mode=%#x result=%d errno=%d return=%p\n",
                getpid(), getprogname() ?: "(unknown)", path ?: "(null)",
                mode, result, result < 0 ? savedError : 0,
                __builtin_return_address(0));
        fflush(stderr);
    }
    errno = savedError;
    return result;
}

static int MacWSDiagnosticStat(const char *path, struct stat *status) {
    int result = stat(path, status);
    int savedError = errno;
    if (MacWSRandomDiagnosticsEnabled() &&
        MacWSRandomDiagnosticPath(path)) {
        fprintf(stderr,
                "[MacWSRandomDiagnostics] api=stat pid=%d program=%s "
                "path=%s result=%d errno=%d return=%p\n",
                getpid(), getprogname() ?: "(unknown)", path ?: "(null)",
                result, result < 0 ? savedError : 0,
                __builtin_return_address(0));
        fflush(stderr);
    }
    errno = savedError;
    return result;
}

// OpenSSL 1.1's Darwin entropy collector resolves getentropy dynamically and
// falls back to /dev/urandom. Capturing both external boundaries is needed to
// distinguish a namespace/device failure from a DRBG-state failure. These
// interposers preserve the exact return values and bytes produced by XNU.
DYLD_INTERPOSE(MacWSDiagnosticOpen, open)
DYLD_INTERPOSE(MacWSDiagnosticRead, read)
DYLD_INTERPOSE(MacWSDiagnosticFopen, fopen)
DYLD_INTERPOSE(MacWSDiagnosticAccess, access)
DYLD_INTERPOSE(MacWSDiagnosticStat, stat)
DYLD_INTERPOSE(MacWSDiagnosticDlsym, dlsym)

// Temporary SpringBoard diagnostic: record the in-process result of loading
// MacWSWindowing through the same dyld namespace used by the tweak loader.

#include <dlfcn.h>
#include <fcntl.h>
#include <stdio.h>
#include <time.h>
#include <unistd.h>

static const char *const kTarget =
    "/var/jb/Library/MobileSubstrate/DynamicLibraries/MacWSWindowing.dylib";
static const char *const kLog =
    "/var/mobile/Library/Logs/MacWSWindowingLoaderProbe.log";

__attribute__((constructor)) static void MacWSWindowingLoaderProbe(void) {
    int fd = open(kLog, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0644);
    dlerror();
    void *image = dlopen(kTarget, RTLD_NOW | RTLD_LOCAL);
    const char *error = dlerror();
    struct timespec now = {0};
    clock_gettime(CLOCK_REALTIME, &now);
    if (fd >= 0) {
        dprintf(fd, "%lld.%03lld pid=%d target=%s image=%p error=%s\n",
                (long long)now.tv_sec,
                (long long)(now.tv_nsec / 1000000), getpid(), kTarget,
                image, error ? error : "none");
        close(fd);
    }
}

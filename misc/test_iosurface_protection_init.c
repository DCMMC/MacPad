// The test runner inserts the ACTUAL production readiness function below the
// mocks. This exercises its once/atomic publication, not a copied state machine.
#include <assert.h>
#include <dispatch/dispatch.h>
#include <pthread.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

typedef int BOOL;
typedef void *Class;
typedef void *Ivar;
#define NO 0
#define YES 1
typedef struct { void *dli_fbase; } Dl_info;
static unsigned char image[0x4000];
static int registered, validating, proceed, secondDone, mismatch;
static pthread_mutex_t mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t condition = PTHREAD_COND_INITIALIZER;
static ptrdiff_t g_macws_iosurface_impl_offset = -1;
static int g_macws_iosurface_protection_abi;
static const uint8_t g_macws_iosurface_protection_uuid[16];
static void IOSurfaceGetProtectionOptions(void) {}
static Class objc_getClass(const char *name) {
    assert(!strcmp(name, "IOSurface"));
    return registered ? image : NULL;
}
static Ivar class_getInstanceVariable(Class cls, const char *name) {
    assert(cls == image && !strcmp(name, "_impl"));
    return image;
}
static ptrdiff_t ivar_getOffset(Ivar impl) {
    assert(impl == image);
    return mismatch == 4 ? 16 : 8;
}
static int dladdr(void *fn, Dl_info *info) {
    assert(fn == (void *)IOSurfaceGetProtectionOptions);
    info->dli_fbase = image;
    return 1;
}
static int macws_macho_uuid_matches(const void *base, const uint8_t *uuid) {
    assert(base == image && uuid == g_macws_iosurface_protection_uuid);
    return mismatch != 2;
}
static int macws_real_sysctlbyname(const char *name, void *value, size_t *size,
                                  const void *replacement, size_t count) {
    assert(!strcmp(name, "kern.osversion") && !replacement && !count);
    assert(*size >= 6);
    pthread_mutex_lock(&mutex);
    validating = 1;
    pthread_cond_broadcast(&condition);
    while (!proceed) pthread_cond_wait(&condition, &mutex);
    pthread_mutex_unlock(&mutex);
    memcpy(value, mismatch == 1 ? "20X99" : "20D67", 6);
    *size = 6;
    return 0;
}

/* PRODUCTION_READINESS_FUNCTION */

static void *first(void *unused) {
    (void)unused;
    assert(macws_iosurface_protection_abi_ready());
    assert(g_macws_iosurface_impl_offset == 8);
    return NULL;
}
static void *second(void *unused) {
    (void)unused;
    BOOL result = macws_iosurface_protection_abi_ready();
    __atomic_store_n(&secondDone, 1, __ATOMIC_RELEASE);
    assert(result);
    assert(g_macws_iosurface_impl_offset == 8);
    return NULL;
}
int main(int argc, char **argv) {
    alarm(5);
    uint32_t getter[] = {0xf9406400, 0xd65f03c0};
    memcpy(image + 0x3df8, getter, sizeof(getter));
    if (argc == 2) {
        mismatch = argv[1][0] - '0';
        assert(mismatch >= 1 && mismatch <= 4);
        registered = proceed = 1;
        if (mismatch == 3) image[0x3df8] ^= 1;
        assert(!macws_iosurface_protection_abi_ready());
        assert(g_macws_iosurface_protection_abi == -1);
        assert(g_macws_iosurface_impl_offset == -1);
        assert(!macws_iosurface_protection_abi_ready());
        puts("IOSurface protection initialization: incompatible ABI rejected");
        return 0;
    }
    // Missing registration is retryable, not a permanently cached rejection.
    assert(!macws_iosurface_protection_abi_ready());
    assert(g_macws_iosurface_protection_abi == 0);
    registered = 1;
    pthread_t a, b;
    assert(!pthread_create(&a, NULL, first, NULL));
    pthread_mutex_lock(&mutex);
    while (!validating) pthread_cond_wait(&condition, &mutex);
    pthread_mutex_unlock(&mutex);
    assert(!pthread_create(&b, NULL, second, NULL));
    usleep(100000);
    // The second caller must wait for the first, not see transient -1 and use
    // the incompatible original getter while initialization is still running.
    assert(!__atomic_load_n(&secondDone, __ATOMIC_ACQUIRE));
    pthread_mutex_lock(&mutex);
    proceed = 1;
    pthread_cond_broadcast(&condition);
    pthread_mutex_unlock(&mutex);
    assert(!pthread_join(a, NULL));
    assert(!pthread_join(b, NULL));
    assert(g_macws_iosurface_protection_abi == 1);
    assert(macws_iosurface_protection_abi_ready());
    puts("IOSurface protection initialization: retry and publication PASS");
    return 0;
}

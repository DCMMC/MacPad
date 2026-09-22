// Query SpringBoard's actual screen-lock state from an iOS-native process.
//
// This intentionally resolves the private SpringBoardServices entry points at
// runtime so the probe can be built with the ordinary Procursus C toolchain.
// It is diagnostic-only and does not change lock or backlight state.

#include <dlfcn.h>
#include <mach/mach.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>

typedef mach_port_t (*springboard_port_fn)(void);
typedef void (*screen_lock_status_fn)(mach_port_t, bool *, bool *);
typedef uint32_t (*notify_register_check_fn)(const char *, int *);
typedef uint32_t (*notify_get_state_fn)(int, uint64_t *);
typedef uint32_t (*notify_cancel_fn)(int);

static void print_notify_state(void *notify_handle, const char *name) {
    notify_register_check_fn register_check =
        (notify_register_check_fn)dlsym(notify_handle,
                                        "notify_register_check");
    notify_get_state_fn get_state =
        (notify_get_state_fn)dlsym(notify_handle, "notify_get_state");
    notify_cancel_fn cancel =
        (notify_cancel_fn)dlsym(notify_handle, "notify_cancel");
    if (!register_check || !get_state || !cancel) return;

    int token = -1;
    uint64_t state = UINT64_MAX;
    uint32_t register_result = register_check(name, &token);
    uint32_t state_result = register_result == 0
        ? get_state(token, &state) : UINT32_MAX;
    printf("notify name=%s register=%u get=%u state=%llu\n",
           name, register_result, state_result,
           (unsigned long long)state);
    if (register_result == 0) cancel(token);
}

int main(void) {
    void *springboard = dlopen(
        "/System/Library/PrivateFrameworks/"
        "SpringBoardServices.framework/SpringBoardServices",
        RTLD_NOW | RTLD_LOCAL);
    if (!springboard) {
        fprintf(stderr, "SpringBoardServices dlopen failed: %s\n", dlerror());
        return 1;
    }

    springboard_port_fn server_port =
        (springboard_port_fn)dlsym(springboard,
                                   "SBSSpringBoardServerPort");
    screen_lock_status_fn lock_status =
        (screen_lock_status_fn)dlsym(springboard,
                                     "SBGetScreenLockStatus");
    if (!server_port || !lock_status) {
        fprintf(stderr,
                "SpringBoardServices symbols missing: port=%p status=%p\n",
                server_port, lock_status);
        return 2;
    }

    bool locked = false;
    bool passcode_enabled = false;
    mach_port_t port = server_port();
    lock_status(port, &locked, &passcode_enabled);
    printf("springboard port=%u locked=%u passcode_enabled=%u\n",
           port, locked, passcode_enabled);

    void *notify = dlopen("/usr/lib/system/libsystem_notify.dylib",
                          RTLD_NOW | RTLD_LOCAL);
    if (notify) {
        print_notify_state(notify, "com.apple.springboard.lockstate");
        print_notify_state(notify,
                           "com.apple.springboard.hasBlankedScreen");
        print_notify_state(notify, "com.apple.springboard.lockcomplete");
    }
    return 0;
}

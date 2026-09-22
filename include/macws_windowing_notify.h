#ifndef MACWS_WINDOWING_NOTIFY_H
#define MACWS_WINDOWING_NOTIFY_H

#include "macws_windowing_protocol.h"
#include <notify.h>
#include <pthread.h>
#include <string.h>
#include <sys/sysctl.h>
#include <sys/proc.h>

// notify(3)'s 64-bit state is explicitly intended for server-resource
// readiness. Keep the registration alive so reads do not lose that state;
// retry registration failures instead of caching a permanent false result.
static inline int MacWSWindowingStateTokenRecovering(int failedToken) {
    static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
    static int token = -1;
    pthread_mutex_lock(&lock);
    // Another thread may already have recovered this registration. Cancel
    // only the token whose read actually failed, never its replacement.
    if (failedToken >= 0 && token == failedToken) {
        notify_cancel(token);
        token = -1;
    }
    if (token < 0) {
        int candidate = -1;
        if (notify_register_check(MACWS_WINDOWING_STATE_NAME, &candidate) ==
            NOTIFY_STATUS_OK) token = candidate;
    }
    int result = token;
    pthread_mutex_unlock(&lock);
    return result;
}

static inline int MacWSWindowingStateToken(void) {
    return MacWSWindowingStateTokenRecovering(-1);
}

static inline bool MacWSWindowingLiveCapabilities(uint8_t required,
                                                 uint64_t *stateOut) {
    int token = MacWSWindowingStateToken();
    uint64_t state = 0;
    if (stateOut) *stateOut = 0;
    if (token < 0) return false;
    uint32_t status = notify_get_state(token, &state);
    if (status == NOTIFY_STATUS_INVALID_TOKEN ||
        status == NOTIFY_STATUS_SERVER_NOT_FOUND) {
        token = MacWSWindowingStateTokenRecovering(token);
        if (token < 0) return false;
        status = notify_get_state(token, &state);
    }
    if (status != NOTIFY_STATUS_OK) return false;
    if (stateOut) *stateOut = state;
    if (!MacWSWindowingStateSupports(state, required)) {
        notify_post(MACWS_WINDOWING_REFRESH_NAME);
        return false;
    }
    // A dead publisher or a recycled PID must not keep an upgrade looking
    // ready. This query is read-only; it never starts/restarts SpringBoard.
    struct kinfo_proc process = {0};
    size_t size = sizeof(process);
    int mib[] = {CTL_KERN, KERN_PROC, KERN_PROC_PID,
                 (int)MacWSWindowingPublisher(state)};
    bool live = sysctl(mib, 4, &process, &size, NULL, 0) == 0 && size != 0 &&
        strcmp(process.kp_proc.p_comm, "SpringBoard") == 0;
    if (!live) notify_post(MACWS_WINDOWING_REFRESH_NAME);
    return live;
}

#endif

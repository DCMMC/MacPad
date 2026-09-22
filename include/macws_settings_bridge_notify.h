#ifndef MACWS_SETTINGS_BRIDGE_NOTIFY_H
#define MACWS_SETTINGS_BRIDGE_NOTIFY_H

#include "macws_settings_bridge_protocol.h"
#include <notify.h>
#include <pthread.h>
#include <string.h>
#include <sys/sysctl.h>
#include <sys/proc.h>

// Hold the notify registration for the lifetime of each consumer/publisher.
// No file enables Settings: this is the installed hook's live capability.
static inline int MacWSSettingsBridgeStateTokenRecovering(int failedToken) {
    static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
    static int token = -1;
    pthread_mutex_lock(&lock);
    if (failedToken >= 0 && token == failedToken) {
        notify_cancel(token);
        token = -1;
    }
    if (token < 0) {
        int candidate = -1;
        if (notify_register_check(MACWS_SETTINGS_BRIDGE_STATE_NAME, &candidate) ==
            NOTIFY_STATUS_OK) token = candidate;
    }
    int result = token;
    pthread_mutex_unlock(&lock);
    return result;
}

static inline int MacWSSettingsBridgeStateToken(void) {
    return MacWSSettingsBridgeStateTokenRecovering(-1);
}

static inline bool MacWSSettingsBridgeLiveCapabilities(uint64_t *stateOut) {
    int token = MacWSSettingsBridgeStateToken();
    uint64_t state = 0;
    if (stateOut) *stateOut = 0;
    if (token < 0) return false;
    uint32_t status = notify_get_state(token, &state);
    if (status == NOTIFY_STATUS_INVALID_TOKEN ||
        status == NOTIFY_STATUS_SERVER_NOT_FOUND) {
        token = MacWSSettingsBridgeStateTokenRecovering(token);
        if (token < 0) return false;
        status = notify_get_state(token, &state);
    }
    if (status != NOTIFY_STATUS_OK) return false;
    if (stateOut) *stateOut = state;
    if (!MacWSSettingsBridgeStateSupports(state)) {
        notify_post(MACWS_SETTINGS_BRIDGE_REFRESH_NAME);
        return false;
    }
    struct kinfo_proc process = {0};
    size_t size = sizeof(process);
    int mib[] = {CTL_KERN, KERN_PROC, KERN_PROC_PID,
                 (int)MacWSSettingsBridgePublisher(state)};
    bool live = sysctl(mib, 4, &process, &size, NULL, 0) == 0 && size != 0 &&
        strcmp(process.kp_proc.p_comm, "runningboardd") == 0;
    if (!live) notify_post(MACWS_SETTINGS_BRIDGE_REFRESH_NAME);
    return live;
}

#endif

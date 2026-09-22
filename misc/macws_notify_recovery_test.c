// Host-side fault-injection test for the real inline capability clients.
// Build/run: cc -Wall -Wextra -Werror -Iinclude \
//   misc/macws_notify_recovery_test.c -o /tmp/macws_notify_recovery_test && \
//   /tmp/macws_notify_recovery_test
// No real notify channel is registered, written or cancelled by this test.
#include <assert.h>
#include <notify.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/sysctl.h>
#include <sys/proc.h>

static uint32_t TestRegister(const char *, int *);
static uint32_t TestCancel(int);
static uint32_t TestGetState(int, uint64_t *);
static uint32_t TestPost(const char *);
static int TestSysctl(int *, u_int, void *, size_t *, void *, size_t);

#define notify_register_check TestRegister
#define notify_cancel TestCancel
#define notify_get_state TestGetState
#define notify_post TestPost
#define sysctl TestSysctl
#include "macws_windowing_notify.h"
#include "macws_settings_bridge_notify.h"

typedef struct {
    unsigned registrations, cancels, reads, queries;
    unsigned failRegistration, failReads;
    uint32_t readFailure;
    int activeToken;
    uint64_t state;
    const char *processName;
    bool processExists;
} Channel;

static Channel channels[2];
static int nextToken = 100;
static int tokenChannel[256];

static unsigned ChannelForName(const char *name) {
    if (strcmp(name, MACWS_WINDOWING_STATE_NAME) == 0 ||
        strcmp(name, MACWS_WINDOWING_REFRESH_NAME) == 0) return 0;
    assert(strcmp(name, MACWS_SETTINGS_BRIDGE_STATE_NAME) == 0 ||
           strcmp(name, MACWS_SETTINGS_BRIDGE_REFRESH_NAME) == 0);
    return 1;
}

static uint32_t TestRegister(const char *name, int *token) {
    unsigned index = ChannelForName(name);
    Channel *channel = &channels[index];
    channel->registrations++;
    if (channel->failRegistration) {
        channel->failRegistration--;
        return NOTIFY_STATUS_SERVER_NOT_FOUND;
    }
    assert(nextToken < 256);
    *token = channel->activeToken = nextToken++;
    tokenChannel[*token] = (int)index;
    return NOTIFY_STATUS_OK;
}

static uint32_t TestCancel(int token) {
    assert(token >= 100 && token < nextToken);
    Channel *channel = &channels[tokenChannel[token]];
    // A stale error must never cancel a newer registration from another
    // caller. Catch that invariant at the actual cancellation boundary.
    assert(token == channel->activeToken);
    channel->activeToken = -1;
    channel->cancels++;
    return NOTIFY_STATUS_OK;
}

static uint32_t TestGetState(int token, uint64_t *state) {
    assert(token >= 100 && token < nextToken);
    Channel *channel = &channels[tokenChannel[token]];
    channel->reads++;
    if (channel->failReads) {
        channel->failReads--;
        return channel->readFailure;
    }
    if (token != channel->activeToken) return NOTIFY_STATUS_INVALID_TOKEN;
    *state = channel->state;
    return NOTIFY_STATUS_OK;
}

static uint32_t TestPost(const char *name) {
    channels[ChannelForName(name)].queries++;
    return NOTIFY_STATUS_OK;
}

static int TestSysctl(int *mib, u_int count, void *old, size_t *length,
                      void *replacement, size_t replacementLength) {
    assert(count == 4 && mib[0] == CTL_KERN && mib[1] == KERN_PROC &&
           mib[2] == KERN_PROC_PID && !replacement && replacementLength == 0);
    unsigned index = mib[3] == 381 ? 0 : 1;
    assert(mib[3] == (index == 0 ? 381 : 382));
    Channel *channel = &channels[index];
    if (!channel->processExists) {
        *length = 0;
        return 0;
    }
    assert(*length == sizeof(struct kinfo_proc));
    struct kinfo_proc *process = old;
    snprintf(process->kp_proc.p_comm, sizeof(process->kp_proc.p_comm),
             "%s", channel->processName);
    return 0;
}

static bool Read(unsigned index, uint64_t *state) {
    return index == 0
        ? MacWSWindowingLiveCapabilities(MacWSWindowingRequired, state)
        : MacWSSettingsBridgeLiveCapabilities(state);
}

static int Recover(unsigned index, int failedToken) {
    return index == 0 ? MacWSWindowingStateTokenRecovering(failedToken)
                     : MacWSSettingsBridgeStateTokenRecovering(failedToken);
}

static void CheckChannel(unsigned index) {
    Channel *channel = &channels[index];
    const char *expectedProcess = index == 0 ? "SpringBoard" : "runningboardd";
    channel->state = index == 0
        ? MacWSWindowingState(381, MacWSWindowingRequired)
        : MacWSSettingsBridgeState(382);
    channel->processName = expectedProcess;
    channel->processExists = true;
    channel->failRegistration = 1;
    uint64_t state = UINT64_MAX;
    assert(!Read(index, &state) && state == 0);
    assert(Read(index, &state) && state == channel->state);

    int oldToken = channel->activeToken;
    channel->readFailure = NOTIFY_STATUS_INVALID_TOKEN;
    channel->failReads = 1;
    assert(Read(index, &state));
    assert(channel->activeToken != oldToken && channel->cancels == 1);
    unsigned registrations = channel->registrations;
    assert(Recover(index, oldToken) == channel->activeToken);
    assert(channel->cancels == 1 && channel->registrations == registrations);

    unsigned reads = channel->reads;
    channel->readFailure = NOTIFY_STATUS_SERVER_NOT_FOUND;
    channel->failReads = 2;
    state = UINT64_MAX;
    assert(!Read(index, &state) && state == 0);
    assert(channel->reads == reads + 2);
    assert(channel->registrations == registrations + 1);
    assert(Read(index, &state));

    registrations = channel->registrations;
    channel->readFailure = NOTIFY_STATUS_NOT_AUTHORIZED;
    channel->failReads = 1;
    assert(!Read(index, &state) && state == 0);
    assert(channel->registrations == registrations);

    unsigned queries = channel->queries;
    channel->processExists = false;
    assert(!Read(index, &state) && channel->queries == ++queries);
    channel->processExists = true;
    channel->processName = "different-pid";
    assert(!Read(index, &state) && channel->queries == ++queries);
    channel->processName = expectedProcess;
    channel->state = 0;
    assert(!Read(index, &state) && channel->queries == ++queries);
}

int main(void) {
    CheckChannel(0);
    CheckChannel(1);
    puts("notify recovery: registration retry, bounded token recovery, "
         "concurrent replacement preservation, stale publisher refresh PASS");
    return 0;
}

#ifndef MACWS_STEAM_SEMAPHORE_PROTOCOL_H
#define MACWS_STEAM_SEMAPHORE_PROTOCOL_H

#include <stdint.h>

#define MACWS_STEAM_SEM_VERSION 23u
#define MACWS_STEAM_SEM_VALUE_MAX 0x7fffffffu
#define MACWS_STEAM_SEM_NAME_CAPACITY 112u
#define MACWS_STEAM_SEM_STATE_MAGIC 0x4d575345u /* MWSE */
#define MACWS_STEAM_SEM_STATE_VERSION 2u
#define MACWS_STEAM_SEM_WAITER_CAPACITY 64u
#define MACWS_STEAM_SEM_WAIT_MAGIC 0x4d575357u /* MWSW */
#define MACWS_STEAM_SEM_WAIT_SOCKET_PATH \
    "/private/tmp/macws_steam_sem_wait.sock"

typedef struct MacWSSteamSemaphoreWaitRequest {
    uint32_t magic;
    uint32_t version;
    uint32_t operation;
    uint32_t reserved;
    uint64_t generation;
    uint64_t waiter;
    uint64_t requestID;
    // Duration for WAIT_TIMED. Every other operation must send zero. Protocol
    // v23 accepts exactly the RE-confirmed overlay polling interval (10 ms).
    uint32_t timeoutMicroseconds;
    uint32_t reserved2;
} MacWSSteamSemaphoreWaitRequest;

typedef struct MacWSSteamSemaphoreWaitReply {
    uint32_t magic;
    uint32_t version;
    int32_t error;
    uint32_t value;
    uint64_t generation;
    uint64_t requestID;
} MacWSSteamSemaphoreWaitReply;

// Authoritative high-frequency state. macwshostd retains the creator file
// descriptor for the full generation and unlinks the name with POSIX
// semantics. Steam processes mutate the same kernel-backed vnode only through
// nonblocking flock + pread/pwrite; no cross-runtime mapped-memory coherence
// or fabricated semaphore token is assumed.
typedef struct MacWSSteamSemaphoreState {
    uint32_t magic;
    uint32_t version;
    uint32_t value;
    uint32_t revision;
    uint64_t brokerGeneration;
    uint32_t waiterCount;
    uint32_t reserved;
    char name[MACWS_STEAM_SEM_NAME_CAPACITY];
    uint64_t waiters[MACWS_STEAM_SEM_WAITER_CAPACITY];
    uint8_t waiterGranted[MACWS_STEAM_SEM_WAITER_CAPACITY];
} MacWSSteamSemaphoreState;

enum {
    MACWS_STEAM_SEM_SOCKET_WAIT_POLL = 1,
    MACWS_STEAM_SEM_SOCKET_TRYWAIT = 2,
    MACWS_STEAM_SEM_SOCKET_POST = 3,
    MACWS_STEAM_SEM_SOCKET_GETVALUE = 4,
    // The host retains this stream until the FIFO waiter receives a post.
    // The macOS client waits for EVFILT_READ; unlike a direct blocking read,
    // that readiness path is runtime-proven to wake across the chroot/iOS
    // boundary without a deadline poll.
    MACWS_STEAM_SEM_SOCKET_WAIT_BLOCK = 5,
    // Event-driven equivalent of sem_trywait + userspace deadline polling.
    // The broker consumes a real token immediately/on post, or returns
    // EAGAIN once timeoutMicroseconds expires. It never grants synthetically.
    MACWS_STEAM_SEM_SOCKET_WAIT_TIMED = 6,
};

#define MACWS_STEAM_SEM_SOCKET_FLAG_DIAGNOSTICS 0x1u

#define MACWS_STEAM_SEM_OP_OPEN "steam-sem-open"
#define MACWS_STEAM_SEM_OP_RECREATE "steam-sem-recreate"
#define MACWS_STEAM_SEM_OP_CLOSE "steam-sem-close"
#define MACWS_STEAM_SEM_OP_UNLINK "steam-sem-unlink"
#define MACWS_STEAM_SEM_OP_RESET "steam-sem-reset"
#define MACWS_STEAM_SEM_OP_DELAY "steam-sem-delay"
#define MACWS_STEAM_SEM_OP_TRYWAIT "steam-sem-trywait"
#define MACWS_STEAM_SEM_OP_WAIT_POLL "steam-sem-wait-poll"
#define MACWS_STEAM_SEM_OP_POST "steam-sem-post"
#define MACWS_STEAM_SEM_OP_GETVALUE "steam-sem-getvalue"
#define MACWS_STEAM_SEM_OP_REGISTER_WAIT "steam-sem-register-wait"
#define MACWS_STEAM_SEM_OP_NOTIFY "steam-sem-notify"
#define MACWS_STEAM_SEM_KEY_NAME "steam_sem_name"
#define MACWS_STEAM_SEM_KEY_FLAGS "steam_sem_flags"
#define MACWS_STEAM_SEM_KEY_VALUE "steam_sem_value"
#define MACWS_STEAM_SEM_KEY_GENERATION "steam_sem_generation"
#define MACWS_STEAM_SEM_KEY_CREATED "steam_sem_created"
#define MACWS_STEAM_SEM_KEY_EPOCH "steam_sem_epoch"
#define MACWS_STEAM_SEM_KEY_ERROR "steam_sem_error"
#define MACWS_STEAM_SEM_KEY_DIAGNOSTICS "steam_sem_diagnostics"
#define MACWS_STEAM_SEM_KEY_WAIT_PORT "steam_sem_wait_port"
#define MACWS_STEAM_SEM_KEY_WAITER "steam_sem_waiter"

#endif

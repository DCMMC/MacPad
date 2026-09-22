#include <dispatch/dispatch.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>
#include <xpc/xpc.h>

#include "macws_control_protocol.h"
#include "macws_steam_semaphore_protocol.h"
#include "macws_windowing_notify.h"
#include "macws_settings_bridge_notify.h"

// Procursus/Theos' public iOS 16 XPC shim intentionally declares only a
// small subset of libxpc.  Keep this witness buildable on-device without
// weakening its reply validation; the symbols below are exported by the
// platform libxpc and are present in Apple's complete SDK headers.
extern const struct _xpc_type_s _xpc_type_dictionary;
#ifndef XPC_TYPE_DICTIONARY
#define XPC_TYPE_DICTIONARY (&_xpc_type_dictionary)
#endif
xpc_type_t xpc_get_type(xpc_object_t object);
char *xpc_copy_description(xpc_object_t object);
bool xpc_dictionary_get_bool(xpc_object_t dictionary, const char *key);
int64_t xpc_dictionary_get_int64(xpc_object_t dictionary, const char *key);
void xpc_dictionary_set_int64(xpc_object_t dictionary, const char *key,
                              int64_t value);

enum {
    MacWSProbeMTLBHeaderSize = 88,
    MacWSProbeMTLBMaximumSize = 1024 * 1024,
};

static uint16_t read_u16(const uint8_t *bytes, size_t offset) {
    uint16_t value = 0;
    memcpy(&value, bytes + offset, sizeof(value));
    return value;
}

static uint64_t read_u64(const uint8_t *bytes, size_t offset) {
    uint64_t value = 0;
    memcpy(&value, bytes + offset, sizeof(value));
    return value;
}

static uint64_t fnv1a64(const uint8_t *bytes, size_t length) {
    uint64_t value = UINT64_C(1469598103934665603);
    for (size_t index = 0; index < length; index++) {
        value ^= bytes[index];
        value *= UINT64_C(1099511628211);
    }
    return value;
}

static bool validate_mtlb(const uint8_t *bytes, size_t length,
                          bool source, uint64_t expectedHash) {
    if (!bytes || length < MacWSProbeMTLBHeaderSize ||
        length > MacWSProbeMTLBMaximumSize ||
        memcmp(bytes, "MTLB", 4) != 0 || read_u64(bytes, 16) != length ||
        read_u16(bytes, 4) != UINT16_C(0x8001)) return false;
    uint8_t targetOS = bytes[11];
    if ((source && targetOS != 0x00 && targetOS != 0x81) ||
        (!source && targetOS != 0x86)) return false;
    static const size_t sectionOffsets[] = {24, 40, 56, 72};
    for (size_t index = 0;
         index < sizeof(sectionOffsets) / sizeof(sectionOffsets[0]); index++) {
        uint64_t offset = read_u64(bytes, sectionOffsets[index]);
        uint64_t size = read_u64(bytes, sectionOffsets[index] + 8);
        if (offset > length || size > length - offset ||
            ((index == 0 || index == 3) &&
             (offset < MacWSProbeMTLBHeaderSize || size == 0))) return false;
    }
    return !expectedHash || fnv1a64(bytes, length) == expectedHash;
}

static uint8_t *read_mtlb(const char *path, size_t *lengthOut,
                          uint64_t *hashOut) {
    *lengthOut = 0;
    *hashOut = 0;
    int descriptor = open(path, O_RDONLY | O_CLOEXEC);
    if (descriptor < 0) return NULL;
    struct stat status = {0};
    if (fstat(descriptor, &status) != 0 ||
        status.st_size < MacWSProbeMTLBHeaderSize ||
        status.st_size > MacWSProbeMTLBMaximumSize) {
        close(descriptor);
        errno = EINVAL;
        return NULL;
    }
    size_t length = (size_t)status.st_size;
    uint8_t *bytes = malloc(length);
    size_t consumed = 0;
    while (bytes && consumed < length) {
        ssize_t count = read(descriptor, bytes + consumed,
                             length - consumed);
        if (count > 0) consumed += (size_t)count;
        else if (count < 0 && errno == EINTR) continue;
        else break;
    }
    close(descriptor);
    uint64_t hash = consumed == length ? fnv1a64(bytes, length) : 0;
    if (consumed != length ||
        !validate_mtlb(bytes, length, true, hash)) {
        free(bytes);
        errno = EINVAL;
        return NULL;
    }
    *lengthOut = length;
    *hashOut = hash;
    return bytes;
}

static bool write_all(const char *path, const uint8_t *bytes, size_t length) {
    int descriptor = open(path, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC,
                          0600);
    if (descriptor < 0) return false;
    size_t written = 0;
    while (written < length) {
        ssize_t count = write(descriptor, bytes + written, length - written);
        if (count > 0) written += (size_t)count;
        else if (count < 0 && errno == EINTR) continue;
        else break;
    }
    bool ok = written == length && fsync(descriptor) == 0;
    int savedErrno = errno;
    if (close(descriptor) != 0 && ok) {
        ok = false;
        savedErrno = errno;
    }
    if (!ok) errno = savedErrno;
    return ok;
}

static bool descriptor_write_all(int descriptor, const void *bytes,
                                 size_t length) {
    const uint8_t *cursor = bytes;
    while (length != 0) {
        ssize_t amount = write(descriptor, cursor, length);
        if (amount < 0 && errno == EINTR) continue;
        if (amount <= 0) return false;
        cursor += (size_t)amount;
        length -= (size_t)amount;
    }
    return true;
}

static bool descriptor_read_all(int descriptor, void *bytes, size_t length) {
    uint8_t *cursor = bytes;
    while (length != 0) {
        ssize_t amount = read(descriptor, cursor, length);
        if (amount < 0 && errno == EINTR) continue;
        if (amount <= 0) return false;
        cursor += (size_t)amount;
        length -= (size_t)amount;
    }
    return true;
}

static double monotonic_milliseconds(void) {
    struct timespec now = {0};
    if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) return -1.0;
    return (double)now.tv_sec * 1000.0 +
        (double)now.tv_nsec / 1000000.0;
}

static int steam_semaphore_socket_operation(uint64_t generation,
                                            uint64_t requestID,
                                            uint32_t timeoutMicroseconds,
                                            double *elapsedMilliseconds) {
    int descriptor = socket(AF_UNIX, SOCK_STREAM, 0);
    if (descriptor < 0) return errno;
    struct sockaddr_un address = {0};
    address.sun_family = AF_UNIX;
    strlcpy(address.sun_path,
            "/var/mnt/rootfs" MACWS_STEAM_SEM_WAIT_SOCKET_PATH,
            sizeof(address.sun_path));
    if (connect(descriptor, (const struct sockaddr *)&address,
                sizeof(address)) != 0) {
        int error = errno;
        close(descriptor);
        return error;
    }
    uint64_t threadID = 0;
    if (pthread_threadid_np(NULL, &threadID) != 0) threadID = 1;
    MacWSSteamSemaphoreWaitRequest request = {
        .magic = MACWS_STEAM_SEM_WAIT_MAGIC,
        .version = MACWS_STEAM_SEM_VERSION,
        .operation = MACWS_STEAM_SEM_SOCKET_WAIT_TIMED,
        .reserved = MACWS_STEAM_SEM_SOCKET_FLAG_DIAGNOSTICS,
        .generation = generation,
        .waiter = ((uint64_t)(uint32_t)getpid() << 32) |
            (threadID & UINT64_C(0xffffffff)),
        .requestID = requestID,
        .timeoutMicroseconds = timeoutMicroseconds,
    };
    MacWSSteamSemaphoreWaitReply reply = {0};
    double began = monotonic_milliseconds();
    bool transferred = descriptor_write_all(
            descriptor, &request, sizeof(request)) &&
        descriptor_read_all(descriptor, &reply, sizeof(reply));
    double ended = monotonic_milliseconds();
    close(descriptor);
    if (elapsedMilliseconds) *elapsedMilliseconds = ended - began;
    if (!transferred || reply.magic != MACWS_STEAM_SEM_WAIT_MAGIC ||
        reply.version != MACWS_STEAM_SEM_VERSION ||
        reply.generation != generation || reply.requestID != requestID)
        return EPROTO;
    return reply.error;
}

static xpc_object_t steam_semaphore_xpc_request(
        xpc_connection_t connection, const char *operation, const char *name,
        int flags, uint64_t value, const char *epoch) {
    xpc_object_t request = xpc_dictionary_create(NULL, NULL, 0);
    xpc_dictionary_set_string(request, MACWS_CONTROL_KEY_OP, operation);
    if (name)
        xpc_dictionary_set_string(request, MACWS_STEAM_SEM_KEY_NAME, name);
    xpc_dictionary_set_int64(request, MACWS_STEAM_SEM_KEY_FLAGS, flags);
    xpc_dictionary_set_uint64(request, MACWS_STEAM_SEM_KEY_VALUE, value);
    if (epoch)
        xpc_dictionary_set_string(request, MACWS_STEAM_SEM_KEY_EPOCH, epoch);
    xpc_object_t reply = xpc_connection_send_message_with_reply_sync(
        connection, request);
    xpc_release(request);
    return reply;
}

static int run_steam_semaphore_timed_wait_selftest(
        xpc_connection_t connection) {
    char epoch[64] = {0};
    char name[64] = {0};
    snprintf(epoch, sizeof(epoch), "macws-probe-%d", getpid());
    snprintf(name, sizeof(name), "/BSem/MacWSProbe-%d", getpid());
    xpc_object_t reply = steam_semaphore_xpc_request(
        connection, MACWS_STEAM_SEM_OP_RESET, NULL, 0, 0, epoch);
    int error = reply ? (int)xpc_dictionary_get_int64(
        reply, MACWS_STEAM_SEM_KEY_ERROR) : EPROTO;
    if (reply) xpc_release(reply);
    if (error != 0) return error;
    reply = steam_semaphore_xpc_request(
        connection, MACWS_STEAM_SEM_OP_OPEN, name,
        O_CREAT | O_EXCL, 1, epoch);
    error = reply ? (int)xpc_dictionary_get_int64(
        reply, MACWS_STEAM_SEM_KEY_ERROR) : EPROTO;
    uint64_t generation = reply ? xpc_dictionary_get_uint64(
        reply, MACWS_STEAM_SEM_KEY_GENERATION) : 0;
    if (reply) xpc_release(reply);
    if (error != 0 || generation == 0) return error ?: EPROTO;

    double immediateMS = 0.0, timeoutMS = 0.0;
    int immediate = steam_semaphore_socket_operation(
        generation, ((uint64_t)(uint32_t)getpid() << 32) | 1,
        10000, &immediateMS);
    int timeout = steam_semaphore_socket_operation(
        generation, ((uint64_t)(uint32_t)getpid() << 32) | 2,
        10000, &timeoutMS);
    reply = steam_semaphore_xpc_request(
        connection, MACWS_STEAM_SEM_OP_UNLINK, name, 0, 0, epoch);
    if (reply) xpc_release(reply);
    bool valid = immediate == 0 && immediateMS >= 0.0 &&
        immediateMS < 10.0 && timeout == EAGAIN &&
        timeoutMS >= 8.0 && timeoutMS < 25.0;
    printf("steam-sem-timed-wait protocol=%u generation=%llu "
           "immediate-error=%d immediate-ms=%.3f "
           "timeout-error=%d timeout-ms=%.3f valid=%s\n",
           MACWS_STEAM_SEM_VERSION, (unsigned long long)generation,
           immediate, immediateMS, timeout, timeoutMS,
           valid ? "yes" : "no");
    return valid ? 0 : 71;
}

// Minimal on-device witness for the typed MacWS Host control service.  This
// deliberately exposes only the same fixed operations as the public protocol;
// it is not a shell bridge and cannot launch arbitrary commands.
int main(int argc, const char *argv[]) {
    const char *operation = argc > 1 ? argv[1] : MACWS_CONTROL_OP_STATUS;
    if (strcmp(operation, "windowing-status") == 0) {
        uint64_t state = 0;
        bool ready = MacWSWindowingLiveCapabilities(
            MacWSWindowingRequired, &state);
        printf("windowing ready=%s abi=%u pid=%u capabilities=0x%02x\n",
               ready ? "yes" : "no", (unsigned)((state >> 40) & 0xffu),
               MacWSWindowingPublisher(state),
               (unsigned)((state >> 32) & 0xffu));
        return ready ? 0 : 69;
    }
    if (strcmp(operation, "settings-bridge-status") == 0) {
        uint64_t state = 0;
        bool ready = MacWSSettingsBridgeLiveCapabilities(&state);
        printf("settings-bridge ready=%s abi=%u pid=%u capabilities=0x%02x\n",
               ready ? "yes" : "no", (unsigned)((state >> 40) & 0xffu),
               MacWSSettingsBridgePublisher(state),
               (unsigned)((state >> 32) & 0xffu));
        return ready ? 0 : 69;
    }
    uint8_t *metalSource = NULL;
    size_t metalSourceLength = 0;
    uint64_t metalSourceHash = 0;
    xpc_connection_t (*createMachService)(
        const char *, dispatch_queue_t, uint64_t) =
        dlsym(RTLD_DEFAULT, "xpc_connection_create_mach_service");
    xpc_connection_t connection = createMachService
        ? createMachService(
              MACWS_CONTROL_SERVICE,
              dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), 0)
        : NULL;
    if (!connection) {
        fprintf(stderr, "macws_control_probe: connection create failed\n");
        return 69;
    }
    xpc_connection_set_event_handler(connection, ^(xpc_object_t event) {
        (void)event;
    });
    xpc_connection_resume(connection);

    if (strcmp(operation, "steam-sem-timed-wait-selftest") == 0)
        return run_steam_semaphore_timed_wait_selftest(connection);

    xpc_object_t request = xpc_dictionary_create(NULL, NULL, 0);
    xpc_dictionary_set_string(request, MACWS_CONTROL_KEY_OP, operation);
    if (strcmp(operation, MACWS_CONTROL_OP_LAUNCH_APP) == 0) {
        if (argc != 3) {
            fprintf(stderr,
                    "usage: macws_control_probe launch-app APP_ID\n");
            return 64;
        }
        xpc_dictionary_set_string(request, MACWS_CONTROL_KEY_APP_ID, argv[2]);
    } else if (strcmp(operation, MACWS_CONTROL_OP_LAUNCH_PATH) == 0) {
        if (argc != 3) {
            fprintf(stderr,
                    "usage: macws_control_probe launch-path /absolute/App.app\n");
            return 64;
        }
        xpc_dictionary_set_string(request, MACWS_CONTROL_KEY_APP_PATH,
                                  argv[2]);
    } else if (strcmp(operation, MACWS_CONTROL_OP_OPEN_DOCUMENTS) == 0) {
        if (argc < 4) {
            fprintf(stderr,
                    "usage: macws_control_probe open-documents "
                    "/absolute/App.app /absolute/document [document...]\n");
            return 64;
        }
        xpc_dictionary_set_string(request, MACWS_CONTROL_KEY_APP_PATH,
                                  argv[2]);
        xpc_object_t documents = xpc_array_create(NULL, 0);
        for (int index = 3; index < argc; index++)
            xpc_array_set_string(documents, XPC_ARRAY_APPEND, argv[index]);
        xpc_dictionary_set_value(request,
                                 MACWS_CONTROL_KEY_DOCUMENT_PATHS,
                                 documents);
        xpc_release(documents);
    } else if (strcmp(operation, MACWS_CONTROL_OP_OPEN_WEB_URL) == 0) {
        if (argc != 3) {
            fprintf(stderr,
                    "usage: macws_control_probe open-web-url "
                    "https://example.com\n");
            return 64;
        }
        xpc_dictionary_set_string(request, MACWS_CONTROL_KEY_WEB_URL,
                                  argv[2]);
    } else if (strcmp(operation, MACWS_CONTROL_OP_REFRESH_DOCK) == 0) {
        if (argc != 3) {
            fprintf(stderr,
                    "usage: macws_control_probe refresh-dock TARGET_PID\n");
            return 64;
        }
        char *end = NULL;
        long value = strtol(argv[2], &end, 10);
        if (!end || *end != '\0' || value <= 1 || value > INT32_MAX) {
            fprintf(stderr, "invalid target pid: %s\n", argv[2]);
            return 64;
        }
        xpc_dictionary_set_int64(request, MACWS_CONTROL_KEY_TARGET_PID,
                                 (int64_t)value);
    } else if (strcmp(operation,
                      MACWS_CONTROL_OP_RETARGET_METAL_LIBRARY) == 0) {
        if (argc != 4) {
            fprintf(stderr,
                    "usage: macws_control_probe retarget-metal-library "
                    "SOURCE.mtlb OUTPUT.metallib\n");
            return 64;
        }
        metalSource = read_mtlb(argv[2], &metalSourceLength,
                                &metalSourceHash);
        if (!metalSource) {
            fprintf(stderr, "invalid MTLB source %s: %s\n", argv[2],
                    strerror(errno));
            return 65;
        }
        xpc_dictionary_set_uint64(request, MACWS_CONTROL_KEY_SOURCE_LENGTH,
                                  metalSourceLength);
        xpc_dictionary_set_uint64(request, MACWS_CONTROL_KEY_SOURCE_HASH,
                                  metalSourceHash);
        xpc_dictionary_set_data(request, MACWS_CONTROL_KEY_METAL_LIBRARY,
                                metalSource, metalSourceLength);
    }

    xpc_object_t reply = xpc_connection_send_message_with_reply_sync(
        connection, request);
    if (!reply || xpc_get_type(reply) != XPC_TYPE_DICTIONARY) {
        char *description = reply ? xpc_copy_description(reply) : NULL;
        fprintf(stderr, "macws_control_probe: invalid reply: %s\n",
                description ?: "<null>");
        free(description);
        free(metalSource);
        return 70;
    }
    bool ok = xpc_dictionary_get_bool(reply, "ok");
    const char *message = xpc_dictionary_get_string(reply, "message");
    long long launchedPID = xpc_dictionary_get_int64(
        reply, "launched_app_pid");
    printf("ok=%s launched-pid=%lld message=%s\n",
           ok ? "yes" : "no", launchedPID, message ?: "");
    if (strcmp(operation, MACWS_CONTROL_OP_STATUS) == 0) {
        const char *startupLog = xpc_dictionary_get_string(
            reply, "startup_log");
        printf("protocol=%llu rootfs=%s windowserver=%s busy=%s "
               "startup-retry=%s startup-log-bytes=%zu phase=%s error=%s\n",
               (unsigned long long)xpc_dictionary_get_uint64(
                   reply, "protocol_version"),
               xpc_dictionary_get_bool(reply, "rootfs_ready") ? "yes" : "no",
               xpc_dictionary_get_bool(reply, "windowserver_running")
                   ? "yes" : "no",
               xpc_dictionary_get_bool(reply, "busy") ? "yes" : "no",
               xpc_dictionary_get_bool(
                   reply, "startup_retry_available") ? "yes" : "no",
               startupLog ? strlen(startupLog) : 0,
               xpc_dictionary_get_string(reply, "phase") ?: "",
               xpc_dictionary_get_string(reply, "last_error") ?: "");
        const char *availabilityKeys[] = {
            "glassdemo_available", "terminal_available",
            "activity_monitor_available", "finder_available",
            "vscode_available", "system_settings_available",
            "maps_available", "amadine_available", "word_available",
            "excel_available", "powerpoint_available", "steam_available",
            "weather_available", "sublime_available",
        };
        printf("availability");
        for (size_t index = 0;
             index < sizeof(availabilityKeys) / sizeof(availabilityKeys[0]);
             index++) {
            printf(" %s=%s", availabilityKeys[index],
                   xpc_dictionary_get_bool(reply, availabilityKeys[index])
                       ? "yes" : "no");
        }
        printf("\n");
    } else if (strcmp(operation,
                      MACWS_CONTROL_OP_RETARGET_METAL_LIBRARY) == 0) {
        uint64_t echoedLength = xpc_dictionary_get_uint64(
            reply, MACWS_CONTROL_KEY_SOURCE_LENGTH);
        uint64_t echoedHash = xpc_dictionary_get_uint64(
            reply, MACWS_CONTROL_KEY_SOURCE_HASH);
        uint64_t replacementLength = xpc_dictionary_get_uint64(
            reply, MACWS_CONTROL_KEY_REPLACEMENT_LENGTH);
        uint64_t replacementHash = xpc_dictionary_get_uint64(
            reply, MACWS_CONTROL_KEY_REPLACEMENT_HASH);
        size_t receivedLength = 0;
        const uint8_t *received = xpc_dictionary_get_data(
            reply, MACWS_CONTROL_KEY_METAL_LIBRARY, &receivedLength);
        bool valid = ok && echoedLength == metalSourceLength &&
            echoedHash == metalSourceHash &&
            replacementLength == receivedLength &&
            validate_mtlb(received, receivedLength, false, replacementHash);
        printf("metal-retarget source=%zu/%016llx "
               "replacement=%zu/%016llx cache=%s elapsed-ms=%.3f "
               "validated=%s\n",
               metalSourceLength, (unsigned long long)metalSourceHash,
               receivedLength, (unsigned long long)replacementHash,
               xpc_dictionary_get_bool(reply, "cache_hit") ? "hit" :
                                                             "converted",
               xpc_dictionary_get_double(reply, "elapsed_ms"),
               valid ? "yes" : "no");
        if (!valid || !write_all(argv[3], received, receivedLength)) {
            fprintf(stderr, "retarget validation/write failed: %s\n",
                    strerror(errno));
            free(metalSource);
            return 74;
        }
    }
    free(metalSource);
    return ok ? 0 : 1;
}

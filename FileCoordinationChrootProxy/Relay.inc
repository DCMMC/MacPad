@import Darwin;
#include <dlfcn.h>
#include <fcntl.h>
#include <xpc/xpc.h>

#if !defined(MACWS_RELAY_PUBLIC_SERVICE) || \
    !defined(MACWS_RELAY_PRIVATE_SERVICE) || \
    !defined(MACWS_RELAY_LOG_PATH) || !defined(MACWS_RELAY_LOG_NAME)
#error Define the relay service names and log identity before including Relay.inc
#endif

static void MacWSRelayLog(const char *message) {
    int fd = open(MACWS_RELAY_LOG_PATH, O_WRONLY | O_CREAT | O_APPEND, 0644);
    if (fd < 0) return;
    dprintf(fd, "[%s pid=%d] %s\n", MACWS_RELAY_LOG_NAME, getpid(),
            message ?: "(null)");
    close(fd);
}

static xpc_object_t MacWSRelayDictionaryCopy(xpc_object_t source) {
    xpc_object_t copy = xpc_dictionary_create(NULL, NULL, 0);
    if (!copy) return NULL;
    xpc_dictionary_apply(source, ^bool(const char *key, xpc_object_t value) {
        xpc_dictionary_set_value(copy, key, value);
        return true;
    });
    return copy;
}

// Relay one stock XPC protocol across the otherwise incompatible macOS
// System-session -> iPadOS User-session lookup boundary. Each downstream
// connection owns a distinct upstream connection so filecoordinationd keeps
// its normal per-client lifecycle. Only public dictionary entries are copied;
// libxpc creates fresh reply rights on both legs.
int main(void) {
    dispatch_queue_t queue = dispatch_queue_create(
        "com.macwsguide.filecoordination-relay", DISPATCH_QUEUE_SERIAL);
    typedef xpc_connection_t (*create_mach_service_fn)(
        const char *, dispatch_queue_t, uint64_t);
    create_mach_service_fn createMach = (create_mach_service_fn)dlsym(
        RTLD_DEFAULT, "xpc_connection_create_mach_service");
    if (!createMach) {
        MacWSRelayLog("xpc_connection_create_mach_service unavailable");
        return 70;
    }

    xpc_connection_t listener = createMach(
        MACWS_RELAY_PUBLIC_SERVICE, queue,
        XPC_CONNECTION_MACH_SERVICE_LISTENER);
    if (!listener) {
        MacWSRelayLog("failed to create local listener");
        return 71;
    }
    xpc_connection_set_event_handler(listener, ^(xpc_object_t object) {
        if (xpc_get_type(object) != XPC_TYPE_CONNECTION) return;
        xpc_connection_t peer = (xpc_connection_t)object;
        xpc_connection_t upstream = createMach(
            MACWS_RELAY_PRIVATE_SERVICE, queue, 0);
        if (!upstream) {
            MacWSRelayLog("failed to create upstream connection");
            xpc_connection_cancel(peer);
            return;
        }

        xpc_connection_set_event_handler(upstream, ^(xpc_object_t event) {
            if (xpc_get_type(event) == XPC_TYPE_ERROR) {
                // A private upstream connection is invalidated when its
                // downstream application exits. That is the normal per-client
                // lifecycle, so reserve the persistent log for real failures.
                if (event != XPC_ERROR_CONNECTION_INVALID &&
                    event != XPC_ERROR_TERMINATION_IMMINENT) {
                    MacWSRelayLog(xpc_dictionary_get_string(
                        event, XPC_ERROR_KEY_DESCRIPTION));
                }
                xpc_connection_cancel(peer);
            }
        });
        xpc_connection_set_target_queue(peer, queue);
        xpc_connection_set_event_handler(peer, ^(xpc_object_t event) {
            if (xpc_get_type(event) == XPC_TYPE_ERROR) {
                xpc_connection_cancel(upstream);
                return;
            }
            if (xpc_get_type(event) != XPC_TYPE_DICTIONARY) return;
            xpc_object_t forwarded = MacWSRelayDictionaryCopy(event);
            if (!forwarded) {
                MacWSRelayLog("failed to copy request dictionary");
                xpc_connection_cancel(peer);
                return;
            }
            xpc_object_t clientReply = xpc_dictionary_create_reply(event);
            if (!clientReply) {
                xpc_connection_send_message(upstream, forwarded);
                return;
            }
            xpc_connection_send_message_with_reply(
                upstream, forwarded, queue, ^(xpc_object_t upstreamReply) {
                    if (xpc_get_type(upstreamReply) != XPC_TYPE_DICTIONARY) {
                        MacWSRelayLog("upstream returned non-dictionary reply");
                        xpc_connection_cancel(peer);
                        return;
                    }
                    xpc_dictionary_apply(upstreamReply,
                        ^bool(const char *key, xpc_object_t value) {
                            xpc_dictionary_set_value(clientReply, key, value);
                            return true;
                        });
                    xpc_connection_send_message(peer, clientReply);
                });
        });
        xpc_connection_resume(upstream);
        xpc_connection_resume(peer);
        MacWSRelayLog("accepted client");
    });
    xpc_connection_resume(listener);
    MacWSRelayLog("ready");
    dispatch_main();
}

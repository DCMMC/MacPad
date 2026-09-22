// Read-only integration probe for the single fullscreen DisplayStream owner.
//
// Build as an iOS command-line tool, sign, and run outside the chroot.  It
// opens two independent XPC clients, subscribes both to the one physical
// macOS desktop, releases every IOSurface lease, and exits.  macwsdisplayd's
// log must show workspace-handoff when the second subscription takes over.

// This probe does not launch or stop WindowServer and does not mutate its
// windows.  It exists to reproduce the stale-Scene ownership race without
// relying on UIKit/Stage Manager automation.

#import <Foundation/Foundation.h>

#include <dispatch/dispatch.h>
#include <dlfcn.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <xpc/xpc.h>

#include "../include/macws_stream_protocol.h"

static void SendHello(xpc_connection_t connection) {
    xpc_object_t request = xpc_dictionary_create(NULL, NULL, 0);
    xpc_dictionary_set_string(request, MACWS_STREAM_KEY_OP,
                              MACWS_STREAM_OP_HELLO);
    xpc_dictionary_set_uint64(request, MACWS_STREAM_KEY_PROTOCOL_VERSION,
                              MACWS_STREAM_VERSION);
    xpc_connection_send_message(connection, request);
    xpc_release(request);
}

static void SendFullscreenSubscription(xpc_connection_t connection) {
    xpc_object_t request = xpc_dictionary_create(NULL, NULL, 0);
    xpc_dictionary_set_string(request, MACWS_STREAM_KEY_OP,
                              MACWS_STREAM_OP_SUBSCRIBE);
    xpc_dictionary_set_uint64(request, MACWS_STREAM_KEY_MODE,
                              MacWSStreamModeFullscreen);
    xpc_dictionary_set_uint64(request, MACWS_STREAM_KEY_WINDOW_ID, 0);
    xpc_connection_send_message(connection, request);
    xpc_release(request);
}

static void ReleaseFrame(xpc_connection_t connection, uint64_t token) {
    if (token == 0) return;
    xpc_object_t request = xpc_dictionary_create(NULL, NULL, 0);
    xpc_dictionary_set_string(request, MACWS_STREAM_KEY_OP,
                              MACWS_STREAM_OP_RELEASE_FRAME);
    xpc_dictionary_set_uint64(request, MACWS_STREAM_KEY_LEASE_TOKEN, token);
    xpc_connection_send_message(connection, request);
    xpc_release(request);
}

static xpc_connection_t NewClient(const char *name,
                                  dispatch_queue_t queue) {
    xpc_connection_t (*createMachService)(const char *, dispatch_queue_t,
                                          uint64_t) =
        dlsym(RTLD_DEFAULT, "xpc_connection_create_mach_service");
    xpc_connection_t connection = createMachService
        ? createMachService(MACWS_STREAM_SERVICE, queue, 0) : NULL;
    if (!connection) return NULL;
    xpc_connection_set_event_handler(connection, ^(xpc_object_t event) {
        if (event == XPC_ERROR_CONNECTION_INVALID ||
            event == XPC_ERROR_CONNECTION_INTERRUPTED) {
            fprintf(stderr, "%s connection-error=%s\n", name,
                    event == XPC_ERROR_CONNECTION_INVALID
                        ? "invalid" : "interrupted");
            return;
        }
        if (xpc_get_type(event) != XPC_TYPE_DICTIONARY) return;
        const char *eventName = xpc_dictionary_get_string(
            event, MACWS_STREAM_KEY_EVENT);
        if (!eventName) return;
        if (strcmp(eventName, MACWS_STREAM_EVENT_FRAME) == 0) {
            uint64_t token = xpc_dictionary_get_uint64(
                event, MACWS_STREAM_KEY_LEASE_TOKEN);
            fprintf(stderr, "%s event=frame token=%llu\n", name,
                    (unsigned long long)token);
            ReleaseFrame(connection, token);
            return;
        }
        const char *message = xpc_dictionary_get_string(
            event, MACWS_STREAM_KEY_MESSAGE);
        fprintf(stderr, "%s event=%s message=%s\n", name, eventName,
                message ?: "");
    });
    xpc_connection_resume(connection);
    SendHello(connection);
    return connection;
}

int main(void) {
    @autoreleasepool {
        dispatch_queue_t queue = dispatch_queue_create(
            "com.macwsguide.display.handoff-probe", DISPATCH_QUEUE_SERIAL);
        xpc_connection_t first = NewClient("first", queue);
        xpc_connection_t second = NewClient("second", queue);
        if (!first || !second) {
            fprintf(stderr, "failed to create DisplayStream clients\n");
            return 2;
        }
        usleep(250000);
        SendFullscreenSubscription(first);
        usleep(750000);
        SendFullscreenSubscription(second);
        sleep(3);
        xpc_connection_cancel(first);
        xpc_connection_cancel(second);
        xpc_release(first);
        xpc_release(second);
        return 0;
    }
}

// Diagnostic client for the iOS launch proxy of macOS extensionkitservice.
//
// This deliberately does not fabricate a Settings extension reply.  It only
// registers the same concrete proxy bundle as libmachook and opens the real
// com.apple.extensionkitservice connection, giving a direct runtime witness
// for service-cache registration, xpcproxy SETEXEC and launch-context health.

@import Foundation;

#import <xpc/xpc.h>

#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

extern void xpc_add_bundle(char *path, int flags);
extern char *xpc_copy_description(xpc_object_t object);

int main(void) {
    @autoreleasepool {
        char path[] =
            "/var/jb/usr/macOS/Frameworks/ExtensionFoundation.framework/"
            "Versions/A/XPCServices/ExtensionKitProxy.xpc";
        xpc_add_bundle(path, 2);

        dispatch_semaphore_t event = dispatch_semaphore_create(0);
        xpc_connection_t connection = xpc_connection_create(
            "com.apple.extensionkitservice",
            dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0));
        if (!connection) {
            fprintf(stderr, "EXTENSIONKIT_PROBE connection-create=nil\n");
            return 2;
        }
        xpc_connection_set_event_handler(connection, ^(xpc_object_t object) {
            char *description = object ? xpc_copy_description(object) : NULL;
            fprintf(stderr,
                    "EXTENSIONKIT_PROBE event=%p description=%s\n",
                    object, description ?: "(null)");
            free(description);
            dispatch_semaphore_signal(event);
        });
        xpc_connection_resume(connection);

        xpc_object_t message = xpc_dictionary_create(NULL, NULL, 0);
        xpc_dictionary_set_string(message, "MACWSDiagnostic", "connect-only");
        xpc_connection_send_message(connection, message);
        fprintf(stderr, "EXTENSIONKIT_PROBE sent pid=%d path=%s\n",
                getpid(), path);
        (void)dispatch_semaphore_wait(
            event, dispatch_time(DISPATCH_TIME_NOW, 8 * NSEC_PER_SEC));
        const char *holdValue = getenv("MACWS_PROBE_HOLD_SECONDS");
        unsigned holdSeconds =
            holdValue ? (unsigned)strtoul(holdValue, NULL, 10) : 0;
        if (holdSeconds > 60) holdSeconds = 60;
        if (holdSeconds) sleep(holdSeconds);
        fprintf(stderr, "EXTENSIONKIT_PROBE complete\n");
    }
    return 0;
}

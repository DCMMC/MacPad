// iOS-native Keychain proxy for the exact Asphalt Catalyst identity.
// The helper owns no plaintext database: every operation terminates in the
// real iPadOS Security framework under the vendor's original access groups.

@import Foundation;
@import Security;

#include <dispatch/dispatch.h>
#include <dlfcn.h>
#include <xpc/xpc.h>

#include "macws_keychain_protocol.h"

// libproc.h is not shipped in every Theos SDK. proc_pidpath is part of
// libSystem on the deployment target and this declaration matches the public
// Darwin ABI used by the other iOS-side MacWS services.
extern int proc_pidpath(int pid, void *buffer, uint32_t buffersize);
#define MACWS_PROC_PATH_CAPACITY 4096

static NSString *const MacWSAllowedExecutable =
    @"/Applications/Asphalt.app/Contents/MacOS/Asphalt";
static NSString *const MacWSAllowedHostExecutable =
    @"/private/var/mnt/rootfs/Applications/Asphalt.app/Contents/MacOS/Asphalt";

static NSDictionary *MacWSCopyDictionary(xpc_object_t request,
                                         const char *key) {
    size_t length = 0;
    const void *bytes = xpc_dictionary_get_data(request, key, &length);
    if (!bytes || length == 0 || length > (1024 * 1024)) return nil;
    NSData *data = [NSData dataWithBytes:bytes length:length];
    NSError *error = nil;
    id value = [NSPropertyListSerialization
        propertyListWithData:data options:NSPropertyListMutableContainers
                      format:NULL error:&error];
    return !error && [value isKindOfClass:NSDictionary.class] ? value : nil;
}

static NSData *MacWSCopyPropertyListData(CFTypeRef value) {
    if (!value) return nil;
    NSError *error = nil;
    NSData *data = [NSPropertyListSerialization
        dataWithPropertyList:(__bridge id)value
                      format:NSPropertyListBinaryFormat_v1_0
                     options:0 error:&error];
    return error ? nil : data;
}

static BOOL MacWSQuerySupported(NSDictionary *query) {
    id itemClass = query[(__bridge id)kSecClass];
    if (![itemClass isEqual:(__bridge id)kSecClassGenericPassword]) return NO;
    // SecKeychainItemRef/SecKey persistent-reference objects are process- and
    // framework-instance-bound and cannot be serialized across this XPC
    // boundary. Asphalt's observed identity store requests data/attributes;
    // reject unsupported result contracts explicitly instead of returning an
    // apparently successful reply with a missing object.
    if ([query[(__bridge id)kSecReturnRef] boolValue] ||
        [query[(__bridge id)kSecReturnPersistentRef] boolValue]) return NO;
    id group = query[(__bridge id)kSecAttrAccessGroup];
    if (!group) return YES;
    return [group isEqual:@"A4QBZ46HAP.com.gameloft.asphalt9mac"] ||
        [group isEqual:@"A4QBZ46HAP.com.gameloft.SingleSignonGames"];
}

static BOOL MacWSAuthenticatedPeer(xpc_connection_t peer) {
    if (!peer || xpc_connection_get_euid(peer) != 501) return NO;
    // Apple marks this SPI unavailable to iOS source while libxpc still
    // exports it. Resolve it dynamically and fail closed if the running OS
    // ever removes it; accepting an unauthenticated Keychain client would be
    // worse than making Asphalt's login unavailable.
    static pid_t (*connectionGetPID)(xpc_connection_t);
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        connectionGetPID = (pid_t (*)(xpc_connection_t))dlsym(
            RTLD_DEFAULT, "xpc_connection_get_pid");
    });
    if (!connectionGetPID) return NO;
    pid_t pid = connectionGetPID(peer);
    char path[MACWS_PROC_PATH_CAPACITY] = {0};
    if (pid <= 1 || proc_pidpath(pid, path, sizeof(path)) <= 0) return NO;
    NSString *executable = [NSString stringWithUTF8String:path];
    return [executable isEqualToString:MacWSAllowedExecutable] ||
        [executable isEqualToString:MacWSAllowedHostExecutable];
}

static void MacWSServe(xpc_connection_t peer, xpc_object_t request) {
    xpc_object_t reply = xpc_dictionary_create_reply(request);
    if (!reply) return;
    OSStatus status = errSecParam;
    CFTypeRef result = NULL;
    uint64_t version = xpc_dictionary_get_uint64(
        request, MACWS_KEYCHAIN_KEY_VERSION);
    const char *operation = xpc_dictionary_get_string(
        request, MACWS_KEYCHAIN_KEY_OPERATION);
    NSDictionary *query = MacWSCopyDictionary(
        request, MACWS_KEYCHAIN_KEY_QUERY);
    NSDictionary *attributes = MacWSCopyDictionary(
        request, MACWS_KEYCHAIN_KEY_ATTRIBUTES);
    if (version != MACWS_KEYCHAIN_VERSION || !operation || !query ||
        !MacWSAuthenticatedPeer(peer)) {
        status = errSecAuthFailed;
    } else if (!MacWSQuerySupported(query)) {
        status = errSecParam;
    } else if (!strcmp(operation, MACWS_KEYCHAIN_OP_COPY)) {
        status = SecItemCopyMatching((__bridge CFDictionaryRef)query,
                                     &result);
    } else if (!strcmp(operation, MACWS_KEYCHAIN_OP_ADD)) {
        status = SecItemAdd((__bridge CFDictionaryRef)query, &result);
    } else if (!strcmp(operation, MACWS_KEYCHAIN_OP_UPDATE) && attributes) {
        status = SecItemUpdate((__bridge CFDictionaryRef)query,
                               (__bridge CFDictionaryRef)attributes);
    } else if (!strcmp(operation, MACWS_KEYCHAIN_OP_DELETE)) {
        status = SecItemDelete((__bridge CFDictionaryRef)query);
    }
    xpc_dictionary_set_int64(reply, MACWS_KEYCHAIN_KEY_STATUS, status);
    NSData *data = status == errSecSuccess
        ? MacWSCopyPropertyListData(result) : nil;
    if (data) xpc_dictionary_set_data(reply, MACWS_KEYCHAIN_KEY_RESULT,
                                      data.bytes, data.length);
    if (result) CFRelease(result);
    xpc_connection_send_message(peer, reply);
}

int main(void) {
    @autoreleasepool {
        xpc_connection_t (*createMach)(const char *, dispatch_queue_t,
                                       uint64_t) = dlsym(
            RTLD_DEFAULT, "xpc_connection_create_mach_service");
        if (!createMach) return 1;
        dispatch_queue_t queue = dispatch_queue_create(
            "com.macwsguide.keychain", DISPATCH_QUEUE_SERIAL);
        xpc_connection_t listener = createMach(
            MACWS_KEYCHAIN_SERVICE, queue,
            XPC_CONNECTION_MACH_SERVICE_LISTENER);
        if (!listener) return 1;
        xpc_connection_set_event_handler(listener, ^(xpc_object_t event) {
            if (xpc_get_type(event) != XPC_TYPE_CONNECTION) return;
            xpc_connection_t peer = (xpc_connection_t)event;
            xpc_connection_set_target_queue(peer, queue);
            xpc_connection_set_event_handler(peer, ^(xpc_object_t request) {
                if (xpc_get_type(request) == XPC_TYPE_DICTIONARY)
                    MacWSServe(peer, request);
            });
            xpc_connection_resume(peer);
        });
        xpc_connection_resume(listener);
        dispatch_main();
    }
}

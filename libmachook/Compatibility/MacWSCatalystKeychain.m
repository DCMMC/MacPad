@import Foundation;
@import Security;

#import "MacWSIdentityDiagnostics.h"
#import "interpose.h"

#include <dlfcn.h>
#include <xpc/xpc.h>

#include "macws_keychain_protocol.h"

static BOOL MacWSCatalystKeychainEnabled(void) {
    const char *enabled = getenv("MACWS_CATALYST_LOCAL_KEYCHAIN");
    const char *bundle = getenv("__CFBundleIdentifier");
    return enabled && !strcmp(enabled, "1") && bundle &&
        !strcmp(bundle, "com.gameloft.asphalt9mac");
}

static BOOL MacWSNativeKeychainUnavailable(OSStatus status) {
    // Runtime-confirmed on the uid-501 Catalyst session: legacy/default
    // requests fail with errSecMDSError because no CSSM/MDS user domain can
    // initialize, while data-protection requests fail with
    // errSecNotAvailable because Ventura's client cannot speak iPadOS secd.
    return status == errSecNotAvailable || status == errSecMDSError;
}

static NSData *MacWSPropertyListData(CFTypeRef value) {
    if (!value) return nil;
    NSError *error = nil;
    NSData *data = [NSPropertyListSerialization
        dataWithPropertyList:(__bridge id)value
                      format:NSPropertyListBinaryFormat_v1_0
                     options:0 error:&error];
    return error ? nil : data;
}

static CFTypeRef MacWSCopyPropertyList(xpc_object_t reply) {
    size_t length = 0;
    const void *bytes = xpc_dictionary_get_data(
        reply, MACWS_KEYCHAIN_KEY_RESULT, &length);
    if (!bytes || length == 0) return NULL;
    NSData *data = [NSData dataWithBytes:bytes length:length];
    NSError *error = nil;
    id value = [NSPropertyListSerialization
        propertyListWithData:data options:NSPropertyListImmutable
                      format:NULL error:&error];
    return error || !value ? NULL : CFBridgingRetain(value);
}

static xpc_connection_t MacWSCreateKeychainConnection(void) {
    static xpc_connection_t (*createMach)(const char *, dispatch_queue_t,
                                          uint64_t);
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        createMach = dlsym(
            RTLD_DEFAULT, "xpc_connection_create_mach_service");
    });
    if (!createMach) return NULL;
    xpc_connection_t connection = createMach(
        MACWS_KEYCHAIN_SERVICE,
        dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), 0);
    if (!connection) return NULL;
    xpc_connection_set_event_handler(connection,
        ^(xpc_object_t event) { (void)event; });
    xpc_connection_resume(connection);
    return connection;
}

static OSStatus MacWSProxyKeychainOperation(
        const char *operation, CFDictionaryRef query,
        CFDictionaryRef attributes, CFTypeRef *result) {
    NSData *queryData = MacWSPropertyListData(query);
    NSData *attributeData = MacWSPropertyListData(attributes);
    if (!queryData || (attributes && !attributeData)) return errSecParam;
    // Keychain operations are infrequent during startup/login. Use one
    // connection per synchronous transaction so a helper restart cannot leave
    // this process permanently attached to an invalid cached endpoint.
    xpc_connection_t connection = MacWSCreateKeychainConnection();
    if (!connection) return errSecNotAvailable;

    xpc_object_t request = xpc_dictionary_create(NULL, NULL, 0);
    xpc_dictionary_set_uint64(request, MACWS_KEYCHAIN_KEY_VERSION,
                              MACWS_KEYCHAIN_VERSION);
    xpc_dictionary_set_string(request, MACWS_KEYCHAIN_KEY_OPERATION,
                              operation);
    xpc_dictionary_set_data(request, MACWS_KEYCHAIN_KEY_QUERY,
                            queryData.bytes, queryData.length);
    if (attributeData) {
        xpc_dictionary_set_data(request, MACWS_KEYCHAIN_KEY_ATTRIBUTES,
                                attributeData.bytes, attributeData.length);
    }
    xpc_object_t reply = xpc_connection_send_message_with_reply_sync(
        connection, request);
    xpc_release(request);
    if (!reply || xpc_get_type(reply) != XPC_TYPE_DICTIONARY) {
        if (reply) xpc_release(reply);
        xpc_connection_cancel(connection);
        xpc_release(connection);
        return errSecNotAvailable;
    }
    OSStatus status = (OSStatus)xpc_dictionary_get_int64(
        reply, MACWS_KEYCHAIN_KEY_STATUS);
    if (status == errSecSuccess && result)
        *result = MacWSCopyPropertyList(reply);
    xpc_release(reply);
    xpc_connection_cancel(connection);
    xpc_release(connection);
    return status;
}

static OSStatus MacWSSecItemCopyMatching(CFDictionaryRef query,
                                         CFTypeRef *result) {
    OSStatus native = SecItemCopyMatching(query, result);
    OSStatus final = native;
    if (MacWSNativeKeychainUnavailable(native) &&
        MacWSCatalystKeychainEnabled()) {
        if (result) *result = NULL;
        final = MacWSProxyKeychainOperation(
            MACWS_KEYCHAIN_OP_COPY, query, NULL, result);
    }
    MacWSIdentityDiagnosticLogKeychainQuery(
        "SecItemCopyMatching", query, native, final,
        result ? *result : NULL, __builtin_return_address(0));
    return final;
}

static OSStatus MacWSSecItemAdd(CFDictionaryRef attributes,
                                CFTypeRef *result) {
    OSStatus native = SecItemAdd(attributes, result);
    OSStatus final = native;
    if (MacWSNativeKeychainUnavailable(native) &&
        MacWSCatalystKeychainEnabled()) {
        if (result) *result = NULL;
        final = MacWSProxyKeychainOperation(
            MACWS_KEYCHAIN_OP_ADD, attributes, NULL, result);
    }
    MacWSIdentityDiagnosticLogKeychainQuery(
        "SecItemAdd", attributes, native, final,
        result ? *result : NULL, __builtin_return_address(0));
    return final;
}

static OSStatus MacWSSecItemUpdate(CFDictionaryRef query,
                                   CFDictionaryRef attributes) {
    OSStatus native = SecItemUpdate(query, attributes);
    OSStatus final = native;
    if (MacWSNativeKeychainUnavailable(native) &&
        MacWSCatalystKeychainEnabled()) {
        final = MacWSProxyKeychainOperation(
            MACWS_KEYCHAIN_OP_UPDATE, query, attributes, NULL);
    }
    MacWSIdentityDiagnosticLogKeychainQuery(
        "SecItemUpdate", query, native, final, NULL,
        __builtin_return_address(0));
    return final;
}

static OSStatus MacWSSecItemDelete(CFDictionaryRef query) {
    OSStatus native = SecItemDelete(query);
    OSStatus final = native;
    if (MacWSNativeKeychainUnavailable(native) &&
        MacWSCatalystKeychainEnabled()) {
        final = MacWSProxyKeychainOperation(
            MACWS_KEYCHAIN_OP_DELETE, query, NULL, NULL);
    }
    MacWSIdentityDiagnosticLogKeychainQuery(
        "SecItemDelete", query, native, final, NULL,
        __builtin_return_address(0));
    return final;
}

DYLD_INTERPOSE(MacWSSecItemCopyMatching, SecItemCopyMatching)
DYLD_INTERPOSE(MacWSSecItemAdd, SecItemAdd)
DYLD_INTERPOSE(MacWSSecItemUpdate, SecItemUpdate)
DYLD_INTERPOSE(MacWSSecItemDelete, SecItemDelete)

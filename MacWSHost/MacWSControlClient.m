#import "MacWSControlClient.h"

#include <dispatch/dispatch.h>
#include <dlfcn.h>
#include <xpc/xpc.h>

#include "macws_control_protocol.h"

@interface MacWSControlClient ()
@property(nonatomic) xpc_connection_t connection;
@property(nonatomic) dispatch_queue_t connectionQueue;
@end

@implementation MacWSControlClient

- (instancetype)init {
    self = [super init];
    if (self) {
        _connectionQueue = dispatch_queue_create("com.macwsguide.host.client",
                                                  DISPATCH_QUEUE_SERIAL);
    }
    return self;
}

- (void)ensureConnection {
    if (self.connection) return;
    xpc_connection_t (*createMach)(const char *, dispatch_queue_t, uint64_t) =
        dlsym(RTLD_DEFAULT, "xpc_connection_create_mach_service");
    if (!createMach) return;
    xpc_connection_t connection = createMach(MACWS_CONTROL_SERVICE,
                                              self.connectionQueue, 0);
    if (!connection) return;
    __weak typeof(self) weakSelf = self;
    xpc_connection_set_event_handler(connection, ^(xpc_object_t event) {
        if (event == XPC_ERROR_CONNECTION_INVALID ||
            event == XPC_ERROR_CONNECTION_INTERRUPTED) {
            weakSelf.connection = nil;
        }
    });
    xpc_connection_resume(connection);
    self.connection = connection;
}

static NSString *MacWSString(xpc_object_t object, const char *key) {
    const char *value = xpc_dictionary_get_string(object, key);
    return value ? [NSString stringWithUTF8String:value] : @"";
}

static NSDictionary<NSString *, id> *MacWSDictionaryFromReply(xpc_object_t reply) {
    if (!reply || xpc_get_type(reply) != XPC_TYPE_DICTIONARY) {
        const char *description = reply ? xpc_copy_description(reply) : NULL;
        NSString *detail = description ? [NSString stringWithUTF8String:description] :
            @"控制服务没有响应";
        if (description) free((void *)description);
        return @{ @"ok": @NO, @"message": detail ?: @"控制服务没有响应",
                  @"connection_error": @YES };
    }
    NSMutableDictionary *result = [NSMutableDictionary dictionary];
    const char *boolKeys[] = {
        "ok", "busy", "rootfs_ready", "windowserver_running",
        "input_running", "frame_ready", "experimental_mode",
        MACWS_CONTROL_KEY_SYSTEM_INPUT_READY,
        "app_input_ready", "startup_retry_available",
        "glassdemo_available", "terminal_available",
        "activity_monitor_available", "finder_available",
        "vscode_available", "system_settings_available",
        "maps_available", "amadine_available", "word_available",
        "excel_available", "powerpoint_available", "asphalt_available",
        "steam_available", "weather_available", "sublime_available",
    };
    for (NSUInteger i = 0; i < sizeof(boolKeys) / sizeof(boolKeys[0]); i++) {
        xpc_object_t value = xpc_dictionary_get_value(reply, boolKeys[i]);
        if (value && xpc_get_type(value) == XPC_TYPE_BOOL)
            result[@(boolKeys[i])] = @(xpc_dictionary_get_bool(reply, boolKeys[i]));
    }
    const char *uintKeys[] = {
        "protocol_version", "frame_width", "frame_height", "frame_generation",
    };
    for (NSUInteger i = 0; i < sizeof(uintKeys) / sizeof(uintKeys[0]); i++) {
        xpc_object_t value = xpc_dictionary_get_value(reply, uintKeys[i]);
        if (value && xpc_get_type(value) == XPC_TYPE_UINT64)
            result[@(uintKeys[i])] = @(xpc_dictionary_get_uint64(reply, uintKeys[i]));
    }
    const char *intKeys[] = {
        "windowserver_pid", "input_pid", "active_app_pid",
        "launched_app_pid", MACWS_CONTROL_KEY_SYSTEM_INPUT_PID,
    };
    for (NSUInteger i = 0; i < sizeof(intKeys) / sizeof(intKeys[0]); i++) {
        xpc_object_t value = xpc_dictionary_get_value(reply, intKeys[i]);
        if (value && xpc_get_type(value) == XPC_TYPE_INT64)
            result[@(intKeys[i])] = @(xpc_dictionary_get_int64(reply, intKeys[i]));
    }
    const char *stringKeys[] = {
        "message", "phase", "last_error", "hostd_log",
        "safety_trip", "active_app_id", "windowserver_log", "input_log",
        "postinst_log", "startup_log",
    };
    for (NSUInteger i = 0; i < sizeof(stringKeys) / sizeof(stringKeys[0]); i++) {
        xpc_object_t value = xpc_dictionary_get_value(reply, stringKeys[i]);
        if (value && xpc_get_type(value) == XPC_TYPE_STRING)
            result[@(stringKeys[i])] = MacWSString(reply, stringKeys[i]);
    }
    return result;
}

- (void)performOperation:(NSString *)operation
               arguments:(NSDictionary<NSString *, id> *)arguments
              completion:(MacWSControlCompletion)completion {
    dispatch_async(self.connectionQueue, ^{
        [self ensureConnection];
        if (!self.connection) {
            dispatch_async(dispatch_get_main_queue(), ^{
                completion(@{ @"ok": @NO,
                              @"message": @"无法连接 root 控制服务",
                              @"connection_error": @YES });
            });
            return;
        }
        xpc_object_t request = xpc_dictionary_create(NULL, NULL, 0);
        xpc_dictionary_set_string(request, MACWS_CONTROL_KEY_OP,
                                  operation.UTF8String);
        [arguments enumerateKeysAndObjectsUsingBlock:^(NSString *key, id value, BOOL *stop) {
            (void)stop;
            if ([value isKindOfClass:NSString.class])
                xpc_dictionary_set_string(request, key.UTF8String,
                                          [value UTF8String]);
            else if ([key isEqualToString:@MACWS_CONTROL_KEY_DOCUMENT_PATHS] &&
                     [value isKindOfClass:NSArray.class]) {
                xpc_object_t paths = xpc_array_create(NULL, 0);
                for (id path in value) {
                    if ([path isKindOfClass:NSString.class])
                        xpc_array_set_string(paths, XPC_ARRAY_APPEND,
                                             [path UTF8String]);
                }
                xpc_dictionary_set_value(request, key.UTF8String, paths);
            }
            else if ([value isKindOfClass:NSNumber.class]) {
                // Most historical numeric arguments are protocol booleans.
                // A process identity must retain its complete value so hostd
                // can prove that the exact closing application exited before
                // it repairs Dock's stale running-application cache.
                if ([key isEqualToString:@MACWS_CONTROL_KEY_TARGET_PID])
                    xpc_dictionary_set_int64(request, key.UTF8String,
                                             [value longLongValue]);
                else
                    xpc_dictionary_set_bool(request, key.UTF8String,
                                            [value boolValue]);
            }
        }];
        xpc_connection_send_message_with_reply(self.connection, request,
            self.connectionQueue, ^(xpc_object_t reply) {
                NSDictionary *dictionary = MacWSDictionaryFromReply(reply);
                if ([dictionary[@"connection_error"] boolValue]) self.connection = nil;
                dispatch_async(dispatch_get_main_queue(), ^{ completion(dictionary); });
            });
    });
}

- (void)fetchStatus:(MacWSControlCompletion)completion {
    [self performOperation:@MACWS_CONTROL_OP_STATUS arguments:nil completion:completion];
}

- (void)fetchLogs:(MacWSControlCompletion)completion {
    [self performOperation:@MACWS_CONTROL_OP_LOGS arguments:nil completion:completion];
}

- (void)startWithExperimentalMode:(BOOL)experimental
                       completion:(MacWSControlCompletion)completion {
    [self performOperation:@MACWS_CONTROL_OP_START
                 arguments:@{@MACWS_CONTROL_KEY_EXPERIMENTAL: @(experimental)}
                completion:completion];
}

@end

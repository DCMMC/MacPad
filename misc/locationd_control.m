#import <Foundation/Foundation.h>
#include <stdio.h>
#include <string.h>

// RE-confirmed from Ventura locationd's Objective-C protocol metadata:
// CLLocationInternalServiceProtocol uses these selectors and extended
// encodings (the reply status is an int, not an Objective-C BOOL):
//   getLocationServicesEnabledWithReplyBlock:
//       v24@0:8@?<v@?@"NSError"i>16
//   setLocationServicesEnabled:replyBlock:
//       v28@0:8B16@?<v@?@"NSError">20
@protocol CLLocationInternalServiceProtocol
- (void)getLocationServicesEnabledWithReplyBlock:
    (void (^)(NSError *error, int enabledStatus))reply;
- (void)setLocationServicesEnabled:(BOOL)enabled
                         replyBlock:(void (^)(NSError *error))reply;
- (void)getAuthorizationStatusForBundleID:(NSString *)bundleID
                              orBundlePath:(NSString *)bundlePath
                                replyBlock:(void (^)(NSError *error,
                                                     int status))reply;
- (void)setAuthorizationStatus:(BOOL)authorized
    withCorrectiveCompensation:(int)correctiveCompensation
                    forBundleID:(NSString *)bundleID
                   orBundlePath:(NSString *)bundlePath
                     replyBlock:(void (^)(NSError *error))reply;
@end

static id<CLLocationInternalServiceProtocol> location_proxy(
    NSXPCConnection **connection_out) {
    NSXPCConnection *connection = [[NSXPCConnection alloc]
        initWithMachServiceName:@"com.apple.locationd.desktop.synchronous"
                         options:0];
    connection.remoteObjectInterface = [NSXPCInterface
        interfaceWithProtocol:@protocol(CLLocationInternalServiceProtocol)];
    [connection resume];
    id proxy = [connection remoteObjectProxyWithErrorHandler:^(NSError *error) {
        fprintf(stderr, "locationd XPC error: %s\n",
                error.localizedDescription.UTF8String ?: "unknown");
    }];
    *connection_out = connection;
    return proxy;
}

static int get_enabled(id<CLLocationInternalServiceProtocol> proxy) {
    dispatch_semaphore_t done = dispatch_semaphore_create(0);
    __block BOOL received = NO;
    __block int enabled_status = 0;
    __block NSError *reply_error = nil;
    [proxy getLocationServicesEnabledWithReplyBlock:^(NSError *error,
                                                       int value) {
        received = YES;
        reply_error = error;
        enabled_status = value;
        dispatch_semaphore_signal(done);
    }];
    if (dispatch_semaphore_wait(
            done, dispatch_time(DISPATCH_TIME_NOW, 10 * NSEC_PER_SEC)) != 0) {
        fprintf(stderr, "get timed out\n");
        return 70;
    }
    if (reply_error) {
        fprintf(stderr, "get failed: %s\n",
                reply_error.localizedDescription.UTF8String ?: "unknown");
        return 77;
    }
    printf("location-services-status=%d enabled=%s\n", enabled_status,
           enabled_status ? "yes" : "no");
    return received ? (enabled_status ? 0 : 2) : 70;
}

static int get_authorization(id<CLLocationInternalServiceProtocol> proxy,
                             NSString *bundle_id) {
    dispatch_semaphore_t done = dispatch_semaphore_create(0);
    __block BOOL received = NO;
    __block int authorization_status = 0;
    __block NSError *reply_error = nil;
    [proxy getAuthorizationStatusForBundleID:bundle_id
                                orBundlePath:nil
                                  replyBlock:^(NSError *error, int status) {
        received = YES;
        reply_error = error;
        authorization_status = status;
        dispatch_semaphore_signal(done);
    }];
    if (dispatch_semaphore_wait(
            done, dispatch_time(DISPATCH_TIME_NOW, 10 * NSEC_PER_SEC)) != 0) {
        fprintf(stderr, "authorization query timed out\n");
        return 70;
    }
    if (reply_error) {
        fprintf(stderr, "authorization query failed: %s\n",
                reply_error.localizedDescription.UTF8String ?: "unknown");
        return 77;
    }
    printf("bundle=%s authorization-status=%d\n", bundle_id.UTF8String,
           authorization_status);
    return received ? 0 : 70;
}

static int authorize_bundle(id<CLLocationInternalServiceProtocol> proxy,
                            NSString *bundle_id) {
    dispatch_semaphore_t done = dispatch_semaphore_create(0);
    __block BOOL received = NO;
    __block NSError *reply_error = nil;
    [proxy setAuthorizationStatus:YES
        withCorrectiveCompensation:0
                        forBundleID:bundle_id
                       orBundlePath:nil
                         replyBlock:^(NSError *error) {
        received = YES;
        reply_error = error;
        dispatch_semaphore_signal(done);
    }];
    if (dispatch_semaphore_wait(
            done, dispatch_time(DISPATCH_TIME_NOW, 10 * NSEC_PER_SEC)) != 0) {
        fprintf(stderr, "authorization update timed out\n");
        return 70;
    }
    if (reply_error) {
        fprintf(stderr, "authorization update failed: %s\n",
                reply_error.localizedDescription.UTF8String ?: "unknown");
        return 77;
    }
    printf("bundle=%s authorization-update=ok\n", bundle_id.UTF8String);
    return received ? 0 : 70;
}

int main(int argc, char **argv) {
    @autoreleasepool {
        if ((argc != 2 && argc != 3) ||
            (strcmp(argv[1], "get") != 0 &&
             strcmp(argv[1], "enable") != 0 &&
             strcmp(argv[1], "maps-authorization") != 0 &&
             strcmp(argv[1], "authorize-maps") != 0)) {
            fprintf(stderr,
                    "usage: %s get|enable|maps-authorization|authorize-maps "
                    "[bundle-id]\n",
                    argv[0]);
            return 64;
        }
        NSString *bundleID = argc == 3
            ? [NSString stringWithUTF8String:argv[2]] : @"com.apple.Maps";
        if (!bundleID.length) return 64;
        NSXPCConnection *connection = nil;
        id<CLLocationInternalServiceProtocol> proxy = location_proxy(&connection);
        int result = 0;
        if (strcmp(argv[1], "maps-authorization") == 0) {
            result = get_authorization(proxy, bundleID);
        } else if (strcmp(argv[1], "authorize-maps") == 0) {
            result = authorize_bundle(proxy, bundleID);
            if (result == 0) {
                result = get_authorization(proxy, bundleID);
            }
        } else if (strcmp(argv[1], "enable") == 0) {
            dispatch_semaphore_t done = dispatch_semaphore_create(0);
            __block BOOL received = NO;
            __block NSError *reply_error = nil;
            [proxy setLocationServicesEnabled:YES replyBlock:^(NSError *error) {
                received = YES;
                reply_error = error;
                dispatch_semaphore_signal(done);
            }];
            if (dispatch_semaphore_wait(
                    done,
                    dispatch_time(DISPATCH_TIME_NOW, 10 * NSEC_PER_SEC)) != 0) {
                fprintf(stderr, "enable timed out\n");
                result = 70;
            } else {
                if (reply_error) {
                    fprintf(stderr, "enable failed: %s\n",
                            reply_error.localizedDescription.UTF8String ?:
                            "unknown");
                    result = 77;
                } else {
                    printf("enable-reply=ok\n");
                    result = received ? 0 : 70;
                }
            }
        }
        if (result == 0 && (strcmp(argv[1], "get") == 0 ||
                            strcmp(argv[1], "enable") == 0)) {
            result = get_enabled(proxy);
        }
        [connection invalidate];
        return result;
    }
}

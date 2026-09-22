// Native iPadOS CoreLocation -> Ventura locationd provider bridge.
//
// Ventura's locationd cannot attach its macOS Wi-Fi client to iPadOS's
// Apple80211 user client.  Feed live native CLLocation objects into Ventura's
// stock CLSimulationController instead.  The native and Ventura CLLocation
// private keyed archives are not wire-compatible, so this process sends only
// validated scalars to the chroot's macwsinteropd.  That Ventura process
// constructs the CLLocation with Ventura CoreLocation before submitting it.
// This preserves the real Ventura authorization, client, filtering and
// delivery graph; only the hardware provider lives on the native side.

#import <CoreLocation/CoreLocation.h>
#import <Foundation/Foundation.h>

#include <dlfcn.h>
#include <objc/message.h>
#include <notify.h>
#include <stdio.h>
#include <string.h>
#include <xpc/xpc.h>

#include "macws_interop_protocol.h"

// Procursus' reduced iPhoneOS16.5 xpc.h omits this public libxpc entry point
// even though the runtime exports it.  Keep the signed scalar wire type used
// by the Ventura receiver; substituting set_uint64 would change the XPC type
// and make xpc_dictionary_get_int64 return its missing/wrong-type value.
extern void xpc_dictionary_set_int64(xpc_object_t dictionary,
                                     const char *key,
                                     int64_t value);

static NSString *const MacWSEffectiveLocationClient =
    @"com.macwsguide.host";

// RE-confirmed in the iPadOS 16.3 CoreLocation ObjC metadata: entitled
// daemons use this designated initializer to act for a named client.  The
// helper carries com.apple.locationd.effective_bundle, and the effective
// identity matches the real Maps client on both sides of the bridge.
@interface CLLocationManager (MacWSEffectiveBundle)
- (instancetype)initWithEffectiveBundleIdentifier:(NSString *)identifier;
+ (CLAuthorizationStatus)authorizationStatusForBundleIdentifier:
    (NSString *)identifier;
+ (void)setAuthorizationStatusByType:(CLAuthorizationStatus)status
                 forBundleIdentifier:(NSString *)identifier;
@end

@interface MacWSLocationBridge : NSObject <CLLocationManagerDelegate>
@property(nonatomic, strong) CLLocationManager *locationManager;
@property(nonatomic) xpc_connection_t interopConnection;
@property(nonatomic) NSUInteger deliveredCount;
@property(nonatomic) int refreshToken;
@property(nonatomic) NSTimeInterval lastRefreshTime;
@property(nonatomic) BOOL updatingLocation;
@end

static int MacWSNativeLocationType(CLLocation *location) {
    SEL selector = NSSelectorFromString(@"type");
    if (![location respondsToSelector:selector]) return 0;
    return ((int (*)(id, SEL))objc_msgSend)(location, selector);
}

static BOOL MacWSNativeLocationMetadata(CLLocation *location,
                                        int *locationType,
                                        int *referenceFrame,
                                        int *rawReferenceFrame) {
    // RE-confirmed on this iPad's iOS 16.3 CoreLocation and Ventura 13.4
    // CoreLocation. Both -clientLocation implementations return the same
    // 176-byte private value. Their exact disassembly copies internal+0x68 to
    // result+0x60, internal+0x88 to result+0x80, and internal+0x94 to
    // result+0x8c. -referenceFrame independently reads internal+0x8c, which
    // places referenceFrame/rawReferenceFrame at result+0x84/+0x88.
    enum {
        kClientLocationSize = 176,
        kTypeOffset = 0x60,
        kReferenceFrameOffset = 0x84,
        kRawReferenceFrameOffset = 0x88,
    };
    SEL clientSelector = NSSelectorFromString(@"clientLocation");
    SEL referenceSelector = NSSelectorFromString(@"referenceFrame");
    NSMethodSignature *signature =
        [location methodSignatureForSelector:clientSelector];
    if (!signature || signature.methodReturnLength != kClientLocationSize ||
        ![location respondsToSelector:referenceSelector]) return NO;

    uint8_t bytes[kClientLocationSize];
    memset(bytes, 0, sizeof(bytes));
    NSInvocation *getter =
        [NSInvocation invocationWithMethodSignature:signature];
    getter.target = location;
    getter.selector = clientSelector;
    [getter invoke];
    [getter getReturnValue:bytes];

    int type = 0;
    int frame = 0;
    int rawFrame = 0;
    memcpy(&type, bytes + kTypeOffset, sizeof(type));
    memcpy(&frame, bytes + kReferenceFrameOffset, sizeof(frame));
    memcpy(&rawFrame, bytes + kRawReferenceFrameOffset, sizeof(rawFrame));
    int accessorType = MacWSNativeLocationType(location);
    int accessorFrame = ((int (*)(id, SEL))objc_msgSend)(
        location, referenceSelector);
    if (type != accessorType || frame != accessorFrame || type < 1 ||
        type > 9 || frame <= 0 || rawFrame < 0) return NO;
    if (locationType) *locationType = type;
    if (referenceFrame) *referenceFrame = frame;
    if (rawReferenceFrame) *rawReferenceFrame = rawFrame;
    return YES;
}

@implementation MacWSLocationBridge

static void MacWSSetXPCDouble(xpc_object_t dictionary, const char *key,
                              double value) {
    uint64_t bits = 0;
    memcpy(&bits, &value, sizeof(bits));
    xpc_dictionary_set_uint64(dictionary, key, bits);
}

- (void)log:(NSString *)message {
    fprintf(stderr, "[macwslocationd] %s\n", message.UTF8String);
    fflush(stderr);
}

- (void)connectInteropService {
    if (self.interopConnection) {
        xpc_connection_cancel(self.interopConnection);
        self.interopConnection = nil;
    }
    typedef xpc_connection_t (*MacWSCreateMachServiceFn)(
        const char *, dispatch_queue_t, uint64_t);
    static MacWSCreateMachServiceFn createMachService;
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        createMachService = (MacWSCreateMachServiceFn)dlsym(
            RTLD_DEFAULT, "xpc_connection_create_mach_service");
    });
    xpc_connection_t connection = createMachService
        ? createMachService(MACWS_INTEROP_SERVICE,
                            dispatch_get_main_queue(), 0)
        : nil;
    if (!connection) {
        [self log:@"could not create Ventura scalar bridge connection"];
        return;
    }
    __weak typeof(self) weakSelf = self;
    xpc_connection_set_event_handler(connection, ^(xpc_object_t event) {
        (void)event;
        [weakSelf log:@"Ventura scalar bridge connection unavailable"];
        weakSelf.interopConnection = nil;
    });
    xpc_connection_resume(connection);
    self.interopConnection = connection;
    [self log:@"connected to Ventura scalar location bridge"];
}

- (void)start {
    [self connectInteropService];
    __weak typeof(self) weakSelf = self;
    uint32_t result = notify_register_dispatch(MACWS_LOCATION_REFRESH_NOTIFICATION,
        &_refreshToken, dispatch_get_main_queue(), ^(int token) {
            (void)token;
            [weakSelf reconnectLocationClientForReason:@"maps-launch"];
        });
    if (result != NOTIFY_STATUS_OK)
        [self log:[NSString stringWithFormat:@"location refresh subscription failed=%u", result]];
    // Use the installed MacWS Host identity instead of borrowing Maps'
    // When-In-Use grant.  An iOS daemon has no foreground scene, so locationd
    // correctly stops a Maps-identified client as soon as Maps is not
    // foreground.  Runtime-confirmed: an unregistered bridge-only identifier
    // is rejected as an "uninstalled app".  MacWS Host is the installed owner
    // of this feature.  This helper carries locationd.authorizeapplications;
    // the stock private API below is RE-confirmed at CoreLocation 0x1897519ac
    // and reduces the requested enum to the daemon's authorized bit before
    // forwarding the unchanged bundle identifier.
    [CLLocationManager setAuthorizationStatusByType:
        kCLAuthorizationStatusAuthorizedAlways
                             forBundleIdentifier:
        MacWSEffectiveLocationClient];
    CLAuthorizationStatus effectiveAuthorization =
        [CLLocationManager authorizationStatusForBundleIdentifier:
            MacWSEffectiveLocationClient];
    [self log:[NSString stringWithFormat:
        @"native effective client=%@ authorization=%ld",
        MacWSEffectiveLocationClient, (long)effectiveAuthorization]];
    [self reconnectLocationClientForReason:@"startup"];
}

- (void)reconnectLocationClientForReason:(NSString *)reason {
    NSTimeInterval now = NSProcessInfo.processInfo.systemUptime;
    if (self.lastRefreshTime && now - self.lastRefreshTime < 3.0) return;
    self.lastRefreshTime = now;
    // Runtime-confirmed: native locationd retained an AuthorizedAlways grant
    // but marked the Host client TimeMissing with an empty registration.
    // A new real CLLocationManager registration removed TimeMissing and the
    // stock authorization getter returned 3 again. Do not manufacture a fix,
    // write readiness, reset privacy, or repeatedly restart the daemon.
    self.locationManager.delegate = nil;
    [self.locationManager stopUpdatingLocation];
    self.locationManager = nil;
    self.updatingLocation = NO;
    if (!self.interopConnection) [self connectInteropService];
    CLLocationManager *manager = [[CLLocationManager alloc]
        initWithEffectiveBundleIdentifier:MacWSEffectiveLocationClient];
    manager.delegate = self;
    manager.desiredAccuracy = kCLLocationAccuracyBest;
    manager.distanceFilter = 5.0;
    self.locationManager = manager;
    [self log:[NSString stringWithFormat:@"native client refreshed reason=%@ authorization=%ld services=%d",
        reason, (long)manager.authorizationStatus, CLLocationManager.locationServicesEnabled]];
    [self startAuthorizedLocationClient:manager attempt:0];
}

- (void)startAuthorizedLocationClient:(CLLocationManager *)manager
                              attempt:(NSUInteger)attempt {
    if (!manager || manager != self.locationManager || self.updatingLocation) return;
    // Registration and its initial delegate callback are asynchronous. The
    // diagnostic observed an initial cached0, then a real daemon readback3.
    // Poll that stock readback briefly; never convert denied/unknown to ready.
    CLAuthorizationStatus status = [CLLocationManager
        authorizationStatusForBundleIdentifier:MacWSEffectiveLocationClient];
    if (status == kCLAuthorizationStatusAuthorizedAlways ||
        status == kCLAuthorizationStatusAuthorizedWhenInUse) {
        self.updatingLocation = YES;
        [manager startUpdatingLocation];
        [self log:[NSString stringWithFormat:@"native client started verified-authorization=%ld attempt=%lu",
            (long)status, (unsigned long)attempt]];
    } else if (status == kCLAuthorizationStatusNotDetermined && attempt < 8) {
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 500 * NSEC_PER_MSEC),
            dispatch_get_main_queue(), ^{
                [self startAuthorizedLocationClient:manager attempt:attempt + 1];
            });
    } else {
        [self log:[NSString stringWithFormat:@"native client awaiting authorization=%ld attempt=%lu",
            (long)status, (unsigned long)attempt]];
    }
}

- (void)locationManagerDidChangeAuthorization:(CLLocationManager *)manager {
    if (manager != self.locationManager) return;
    [self log:[NSString stringWithFormat:@"native authorization changed=%ld",
                                             (long)manager.authorizationStatus]];
    if (manager.authorizationStatus == kCLAuthorizationStatusAuthorizedAlways ||
        manager.authorizationStatus == kCLAuthorizationStatusAuthorizedWhenInUse) {
        [self startAuthorizedLocationClient:manager attempt:0];
    } else if (manager.authorizationStatus == kCLAuthorizationStatusDenied ||
               manager.authorizationStatus == kCLAuthorizationStatusRestricted) {
        [manager stopUpdatingLocation];
        self.updatingLocation = NO;
    }
}

- (void)locationManagerDidPauseLocationUpdates:(CLLocationManager *)manager {
    if (manager == self.locationManager) [self log:@"native location updates paused"];
}

- (void)locationManagerDidResumeLocationUpdates:(CLLocationManager *)manager {
    if (manager == self.locationManager) [self log:@"native location updates resumed"];
}

- (void)locationManager:(CLLocationManager *)manager
      didUpdateLocations:(NSArray<CLLocation *> *)locations {
    if (manager != self.locationManager) return;
    CLLocation *location = locations.lastObject;
    if (!location || location.horizontalAccuracy < 0) return;

    // The public CLLocation scalar initializers reset CLClientLocation.type
    // to Unknown(0). Ventura's stock CLSimulatedLocationProvider rejects that
    // value even when every coordinate/accuracy field is valid. Preserve the
    // type assigned by iPadOS CoreLocation instead of inventing an enum value.
    int locationType = 0;
    int referenceFrame = 0;
    int rawReferenceFrame = 0;
    if (!MacWSNativeLocationMetadata(location, &locationType,
                                     &referenceFrame,
                                     &rawReferenceFrame)) {
        [self log:@"native fix has unsupported private metadata ABI"];
        return;
    }

    if (!self.interopConnection) [self connectInteropService];
    if (!self.interopConnection) return;

    xpc_object_t message = xpc_dictionary_create(NULL, NULL, 0);
    xpc_dictionary_set_string(message, MACWS_INTEROP_KEY_OP,
                              MACWS_INTEROP_OP_PUBLISH_LOCATION);
    MacWSSetXPCDouble(message, MACWS_INTEROP_KEY_LATITUDE,
                     location.coordinate.latitude);
    MacWSSetXPCDouble(message, MACWS_INTEROP_KEY_LONGITUDE,
                     location.coordinate.longitude);
    MacWSSetXPCDouble(message, MACWS_INTEROP_KEY_ALTITUDE, location.altitude);
    MacWSSetXPCDouble(message, MACWS_INTEROP_KEY_HORIZONTAL_ACCURACY,
                     location.horizontalAccuracy);
    MacWSSetXPCDouble(message, MACWS_INTEROP_KEY_VERTICAL_ACCURACY,
                     location.verticalAccuracy);
    MacWSSetXPCDouble(message, MACWS_INTEROP_KEY_COURSE, location.course);
    MacWSSetXPCDouble(message, MACWS_INTEROP_KEY_SPEED, location.speed);
    MacWSSetXPCDouble(message, MACWS_INTEROP_KEY_TIMESTAMP,
                     location.timestamp.timeIntervalSince1970);
    xpc_dictionary_set_int64(message, MACWS_INTEROP_KEY_LOCATION_TYPE,
                             locationType);
    xpc_dictionary_set_int64(message, MACWS_INTEROP_KEY_REFERENCE_FRAME,
                             referenceFrame);
    xpc_dictionary_set_int64(message,
                             MACWS_INTEROP_KEY_RAW_REFERENCE_FRAME,
                             rawReferenceFrame);
    xpc_connection_send_message(self.interopConnection, message);
    self.deliveredCount++;
    if (self.deliveredCount == 1 || self.deliveredCount % 60 == 0) {
        [self log:[NSString stringWithFormat:
            @"delivered native fix #%lu type=%d accuracy=%.1fm age=%.1fs",
            (unsigned long)self.deliveredCount, locationType,
            location.horizontalAccuracy,
            -location.timestamp.timeIntervalSinceNow]];
    }
}

- (void)locationManager:(CLLocationManager *)manager
       didFailWithError:(NSError *)error {
    if (manager != self.locationManager) return;
    [self log:[NSString stringWithFormat:@"native location failed: %@", error]];
}

@end

int main(void) {
    @autoreleasepool {
        MacWSLocationBridge *bridge = [[MacWSLocationBridge alloc] init];
        [bridge start];
        [[NSRunLoop currentRunLoop] run];
        (void)bridge;
    }
    return 0;
}

#import <AppKit/AppKit.h>
#import <CoreLocation/CoreLocation.h>
#import <Foundation/Foundation.h>

#include <CommonCrypto/CommonDigest.h>
#include <fcntl.h>
#include <math.h>
#include <mach/mach_time.h>
#include <objc/message.h>
#include <stdatomic.h>
#include <string.h>
#include <sys/fsgetpath.h>
#include <sys/mount.h>
#include <unistd.h>
#include <xpc/xpc.h>

#include "macws_interop_protocol.h"

static dispatch_queue_t InteropQueue;
static NSMutableSet *Clients;
static NSInteger LastPasteboardChange = -1;
static NSInteger AppliedPasteboardChange = -1;
static uint64_t DaemonOriginID;
static uint64_t Generation;
static uint64_t LastIncomingOrigin;
static uint64_t LastIncomingGeneration;
static NSXPCConnection *LocationSimulationConnection;
static id LocationSimulationProxy;
static BOOL LocationSimulationStarted;
static uint64_t LocationFixCount;
static CLLocation *LastNativeLocation;
static NSXPCConnection *LocationControlConnection;
static id LocationControlProxy;
static BOOL LocationControlReady;
static BOOL LocationControlInFlight;
static BOOL LocationRetryScheduled;
static CLLocationManager *LocationKeepaliveManager;
static id LocationKeepaliveDelegate;
static id WorkspaceLaunchObserver;

static NSString *const MacWSArchiveVersionKey = @"version";
static NSString *const MacWSArchiveItemsKey = @"items";
static NSString *const MacWSArchiveRepresentationsKey = @"representations";
static NSString *const MacWSArchiveTypeKey = @"type";
static NSString *const MacWSArchiveDataKey = @"data";
static NSString *const MacWSArchiveFilePathKey = @"file_path";
static const NSUInteger MacWSArchiveVersion = 1;
static NSString *const MacWSImportsRoot = @"/Users/Shared/MacWS Imports";
static NSString *const MacWSExportsRoot = @"/Users/Shared/MacWS Exports";
static NSString *const MacWSLocationWitnessBundleID =
    @"com.macwsguide.interopd";
static NSString *const MacWSMapsBundleID = @"com.apple.Maps";

@interface MacWSArchivePasteboardWriter : NSObject <NSPasteboardWriting>
@property(nonatomic, copy) NSArray<NSDictionary *> *representations;
@property(nonatomic, strong) NSURL *fileURL;
@end

@implementation MacWSArchivePasteboardWriter

- (NSArray<NSPasteboardType> *)writableTypesForPasteboard:
    (NSPasteboard *)pasteboard {
    NSMutableOrderedSet<NSPasteboardType> *types = [NSMutableOrderedSet
        orderedSet];
    id<NSPasteboardWriting> fileWriter =
        (id<NSPasteboardWriting>)self.fileURL;
    if (fileWriter) [types addObjectsFromArray:
        [fileWriter writableTypesForPasteboard:pasteboard]];
    for (NSDictionary *representation in self.representations) {
        NSString *type = representation[MacWSArchiveTypeKey];
        if (type.length) [types addObject:type];
    }
    return types.array;
}

- (id)pasteboardPropertyListForType:(NSPasteboardType)type {
    id<NSPasteboardWriting> fileWriter =
        (id<NSPasteboardWriting>)self.fileURL;
    if (fileWriter && [[fileWriter writableTypesForPasteboard:
            NSPasteboard.generalPasteboard]
            containsObject:type]) {
        id value = [fileWriter pasteboardPropertyListForType:type];
        if (value) return value;
    }
    for (NSDictionary *representation in self.representations) {
        if (![representation[MacWSArchiveTypeKey] isEqualToString:type])
            continue;
        NSData *data = representation[MacWSArchiveDataKey];
        if (data) return data;
        NSString *path = representation[MacWSArchiveFilePathKey];
        if (path) return [NSURL fileURLWithPath:path].absoluteString;
    }
    return nil;
}

- (NSPasteboardWritingOptions)writingOptionsForType:(NSPasteboardType)type
                                         pasteboard:(NSPasteboard *)pasteboard {
    id fileWriter = self.fileURL;
    if ([fileWriter respondsToSelector:_cmd] &&
        [[fileWriter writableTypesForPasteboard:pasteboard]
            containsObject:type]) {
        return [fileWriter writingOptionsForType:type pasteboard:pasteboard];
    }
    return 0;
}

@end

// Ventura's private locationd protocol can return the iOS-compatible
// authorized-when-in-use value even though the public macOS SDK marks that
// enum member unavailable.  Keep the wire value explicit instead of asking
// the compiler to reference an unavailable public symbol.
static const int kMacWSAuthorizationStatusAuthorizedWhenInUse = 4;

static void InteropLog(NSString *format, ...) NS_FORMAT_FUNCTION(1, 2);

static void PublishLocationProviderReadiness(void) {
    static _Atomic bool published = false;
    if (atomic_load_explicit(&published, memory_order_acquire)) return;
    // This is an end-to-end readiness witness, not a requested-state flag. It
    // is published only after the unmodified Ventura CLLocationManager
    // receives a location from Ventura locationd.
    const char *path = "/private/tmp/macws_location_provider_ready";
    int descriptor = open(path,
                          O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC,
                          0644);
    if (descriptor < 0) {
        InteropLog(@"could not publish location provider readiness: %s",
                   strerror(errno));
        return;
    }
    char payload[32];
    int length = snprintf(payload, sizeof(payload), "%d\n", getpid());
    ssize_t written = length > 0
        ? write(descriptor, payload, (size_t)length) : -1;
    int savedError = written < 0 ? errno : EIO;
    close(descriptor);
    if (length <= 0 || written != length) {
        unlink(path);
        InteropLog(@"could not complete location provider readiness: %s",
                   strerror(savedError));
        return;
    }
    atomic_store_explicit(&published, true, memory_order_release);
    InteropLog(@"Ventura location provider readiness published");
}

@interface MacWSLocationKeepaliveDelegate : NSObject <CLLocationManagerDelegate>
@end

@implementation MacWSLocationKeepaliveDelegate
- (void)locationManager:(CLLocationManager *)manager
     didUpdateLocations:(NSArray<CLLocation *> *)locations {
    (void)manager;
    static dispatch_once_t once;
    CLLocation *location = locations.lastObject;
    if (!location) return;
    PublishLocationProviderReadiness();
    dispatch_once(&once, ^{
        InteropLog(@"Ventura CLLocationManager output ready");
    });
}

- (void)locationManager:(CLLocationManager *)manager
        didFailWithError:(NSError *)error {
    (void)manager;
    InteropLog(@"Ventura CLLocationManager client error: %@", error);
}
@end

@protocol MacWSSimulationLocationProtocol
- (void)startLocationSimulation;
- (void)stopLocationSimulation;
- (void)setSimulationScenario:(id)scenario;
- (void)appendSimulatedLocations:(NSArray<CLLocation *> *)locations;
- (void)clearSimulatedLocations;
- (void)setLocationDeliveryBehavior:(uint8_t)behavior;
- (void)setLocationRepeatBehavior:(uint8_t)behavior;
- (void)setIntermediateLocationDistance:(double)distance;
- (void)setLocationInterval:(double)interval;
- (void)setLocationTravellingSpeed:(double)speed;
@end

// RE-confirmed from Ventura locationd's Objective-C protocol metadata.  The
// status reply is an int, not BOOL; matching that ABI is load-bearing.
@protocol MacWSLocationInternalServiceProtocol
- (void)setLocationServicesEnabled:(BOOL)enabled
                         replyBlock:(void (^)(NSError *error))reply;
- (void)setAuthorizationStatus:(BOOL)authorized
    withCorrectiveCompensation:(int)correctiveCompensation
                    forBundleID:(NSString *)bundleID
                   orBundlePath:(NSString *)bundlePath
                     replyBlock:(void (^)(NSError *error))reply;
- (void)getAuthorizationStatusForBundleID:(NSString *)bundleID
                             orBundlePath:(NSString *)bundlePath
                               replyBlock:(void (^)(NSError *error,
                                                     int status))reply;
@end

static void SubmitVenturaLocation(CLLocation *location);
static void ScheduleLocationRetry(void);
static void EnsureVenturaLocationClient(void);
static void ScheduleMapsLaunchAuthorizationRefresh(pid_t pid);

static void InteropLog(NSString *format, ...) {
    va_list args;
    va_start(args, format);
    NSString *message = [[NSString alloc] initWithFormat:format arguments:args];
    va_end(args);
    fprintf(stderr, "MACWS-INTEROP %s\n", message.UTF8String);
    fflush(stderr);
}

static void CreateVenturaLocationClientOnMainThread(void) {
    if (LocationKeepaliveManager) return;
    LocationKeepaliveDelegate = [MacWSLocationKeepaliveDelegate new];
    // Do not create NSApplication before CLLocationManager. Runtime logs from
    // the target showed that AppKit changes CoreLocation's default effective
    // bundle to the frontmost app (Maps in the failing run). That selects
    // initWithEffectiveBundleIdentifier:bundlePath:... and CoreLocationAgent
    // receives do_Register(..., forwardVerification=0), whose Ventura
    // implementation only logs "not forwarding" and returns. Construct the
    // ordinary manager on the real main thread while this process still owns
    // its bundle identity. postinst embeds an identifier-only designated
    // requirement so the Agent can validate this live executable without
    // weakening the stock check.
    InteropLog(@"Ventura CoreLocation client identity %@ path=%@",
               NSBundle.mainBundle.bundleIdentifier ?: @"(nil)",
               NSBundle.mainBundle.bundlePath ?: @"(nil)");
    LocationKeepaliveManager = [CLLocationManager new];
    LocationKeepaliveManager.delegate = LocationKeepaliveDelegate;
    LocationKeepaliveManager.desiredAccuracy = kCLLocationAccuracyBest;
    [LocationKeepaliveManager startUpdatingLocation];
    InteropLog(@"Ventura CLLocationManager lifecycle client started");
}

static void EnsureVenturaLocationClient(void) {
    if (LocationKeepaliveManager) return;
    if (NSThread.isMainThread) {
        CreateVenturaLocationClientOnMainThread();
        return;
    }
    CFRunLoopPerformBlock(CFRunLoopGetMain(), kCFRunLoopCommonModes, ^{
        CreateVenturaLocationClientOnMainThread();
    });
    CFRunLoopWakeUp(CFRunLoopGetMain());
}

static id<MacWSSimulationLocationProtocol> LocationSimulation(void) {
    if (LocationSimulationProxy) return LocationSimulationProxy;
    NSXPCInterface *interface = [NSXPCInterface interfaceWithProtocol:
        @protocol(MacWSSimulationLocationProtocol)];
    // Match CLLocationSimulationProtocol's server-side secure-coding
    // whitelist exactly.  Mutable containers decode as NSArray and are not a
    // separate wire type.
    NSSet *classes = [NSSet setWithObjects:NSArray.class,
        CLLocation.class, nil];
    [interface setClasses:classes
              forSelector:@selector(appendSimulatedLocations:)
            argumentIndex:0
                  ofReply:NO];
    NSXPCConnection *connection = [[NSXPCConnection alloc]
        initWithMachServiceName:@"com.apple.macosbooter.locationd.simulation"
                        options:0];
    connection.remoteObjectInterface = interface;
    connection.interruptionHandler = ^{
        dispatch_async(InteropQueue, ^{
            InteropLog(@"Ventura simulation service interrupted");
            LocationSimulationConnection = nil;
            LocationSimulationProxy = nil;
            LocationSimulationStarted = NO;
            ScheduleLocationRetry();
        });
    };
    connection.invalidationHandler = ^{
        dispatch_async(InteropQueue, ^{
            InteropLog(@"Ventura simulation service invalidated");
            LocationSimulationConnection = nil;
            LocationSimulationProxy = nil;
            LocationSimulationStarted = NO;
            ScheduleLocationRetry();
        });
    };
    [connection resume];
    LocationSimulationConnection = connection;
    LocationSimulationProxy = [connection remoteObjectProxyWithErrorHandler:
        ^(NSError *error) {
            dispatch_async(InteropQueue, ^{
                InteropLog(@"Ventura simulation request failed: %@", error);
                LocationSimulationConnection = nil;
                LocationSimulationProxy = nil;
                LocationSimulationStarted = NO;
                ScheduleLocationRetry();
            });
        }];
    return LocationSimulationProxy;
}

static void ResetLocationControl(void) {
    LocationControlConnection = nil;
    LocationControlProxy = nil;
    LocationControlReady = NO;
    LocationControlInFlight = NO;
}

static BOOL LocationControlConnectionIsCurrent(NSXPCConnection *connection) {
    return connection && LocationControlConnection == connection;
}

static BOOL MacWSLocationAuthorizationStatusIsUsable(int status) {
    return status == kCLAuthorizationStatusAuthorizedAlways ||
        status == kMacWSAuthorizationStatusAuthorizedWhenInUse;
}

static void RestartVenturaLocationWitnessClient(void) {
    CFRunLoopPerformBlock(CFRunLoopGetMain(), kCFRunLoopCommonModes, ^{
        if (!LocationKeepaliveManager) return;
        [LocationKeepaliveManager stopUpdatingLocation];
        [LocationKeepaliveManager startUpdatingLocation];
        InteropLog(@"Ventura CLLocationManager witness restarted after "
                   "authorization verification");
    });
    CFRunLoopWakeUp(CFRunLoopGetMain());
}

static void CompleteVenturaLocationAuthorization(
        NSXPCConnection *expectedConnection) {
    id<MacWSLocationInternalServiceProtocol> readbackControl =
        LocationControlProxy;
    [readbackControl
        getAuthorizationStatusForBundleID:MacWSLocationWitnessBundleID
        orBundlePath:nil
        replyBlock:^(NSError *witnessReadbackError, int witnessStatus) {
        dispatch_async(InteropQueue, ^{
            if (!LocationControlConnectionIsCurrent(expectedConnection))
                return;
            if (witnessReadbackError ||
                !MacWSLocationAuthorizationStatusIsUsable(witnessStatus)) {
                InteropLog(@"Ventura location witness authorization "
                           "readback failed status=%d error=%@",
                           witnessStatus,
                           witnessReadbackError ?: @"(nil)");
                ResetLocationControl();
                ScheduleLocationRetry();
                return;
            }
            id<MacWSLocationInternalServiceProtocol> mapsReadback =
                LocationControlProxy;
            [mapsReadback
                getAuthorizationStatusForBundleID:MacWSMapsBundleID
                orBundlePath:nil
                replyBlock:^(NSError *mapsReadbackError, int mapsStatus) {
                dispatch_async(InteropQueue, ^{
                    if (!LocationControlConnectionIsCurrent(
                            expectedConnection)) return;
                    if (mapsReadbackError ||
                        !MacWSLocationAuthorizationStatusIsUsable(
                            mapsStatus)) {
                        InteropLog(@"Ventura Maps authorization readback "
                                   "failed status=%d error=%@",
                                   mapsStatus,
                                   mapsReadbackError ?: @"(nil)");
                        ResetLocationControl();
                        ScheduleLocationRetry();
                        return;
                    }
                    LocationControlReady = YES;
                    LocationControlInFlight = NO;
                    InteropLog(@"Ventura location witness and Maps "
                               "authorization verified witness=%d maps=%d",
                               witnessStatus, mapsStatus);
                    RestartVenturaLocationWitnessClient();
                    if (LastNativeLocation)
                        SubmitVenturaLocation(LastNativeLocation);
                });
            }];
        });
    }];
}

static void AuthorizeVenturaMapsAfterWitness(
        NSXPCConnection *expectedConnection) {
    id<MacWSLocationInternalServiceProtocol> authorizationControl =
        LocationControlProxy;
    [authorizationControl setAuthorizationStatus:YES
        withCorrectiveCompensation:0
                        forBundleID:MacWSMapsBundleID
                       orBundlePath:nil
                         replyBlock:^(NSError *authorizationError) {
        dispatch_async(InteropQueue, ^{
            if (!LocationControlConnectionIsCurrent(expectedConnection))
                return;
            if (authorizationError) {
                InteropLog(@"Ventura Maps authorization failed: %@",
                           authorizationError);
                ResetLocationControl();
                ScheduleLocationRetry();
                return;
            }
            CompleteVenturaLocationAuthorization(expectedConnection);
        });
    }];
}

static void PrepareVenturaLocationControl(void) {
    if (LocationControlReady || LocationControlInFlight) return;
    LocationControlInFlight = YES;
    NSXPCConnection *connection = [[NSXPCConnection alloc]
        initWithMachServiceName:
            @"com.apple.macosbooter.locationd.desktop.synchronous"
                        options:0];
    connection.remoteObjectInterface = [NSXPCInterface
        interfaceWithProtocol:@protocol(MacWSLocationInternalServiceProtocol)];
    __weak NSXPCConnection *weakConnection = connection;
    connection.interruptionHandler = ^{
        dispatch_async(InteropQueue, ^{
            NSXPCConnection *interruptedConnection = weakConnection;
            if (!LocationControlConnectionIsCurrent(interruptedConnection))
                return;
            InteropLog(@"Ventura location control service interrupted");
            ResetLocationControl();
            ScheduleLocationRetry();
        });
    };
    connection.invalidationHandler = ^{
        dispatch_async(InteropQueue, ^{
            NSXPCConnection *invalidatedConnection = weakConnection;
            if (!LocationControlConnectionIsCurrent(invalidatedConnection))
                return;
            InteropLog(@"Ventura location control service invalidated");
            ResetLocationControl();
            ScheduleLocationRetry();
        });
    };
    [connection resume];
    LocationControlConnection = connection;
    LocationControlProxy = [connection remoteObjectProxyWithErrorHandler:
        ^(NSError *error) {
            dispatch_async(InteropQueue, ^{
                NSXPCConnection *failedConnection = weakConnection;
                if (!LocationControlConnectionIsCurrent(failedConnection))
                    return;
                InteropLog(@"Ventura location control request failed: %@",
                           error);
                ResetLocationControl();
                ScheduleLocationRetry();
            });
        }];
    id<MacWSLocationInternalServiceProtocol> control = LocationControlProxy;
    [control setLocationServicesEnabled:YES replyBlock:^(NSError *error) {
        if (error) {
            dispatch_async(InteropQueue, ^{
                NSXPCConnection *failedConnection = weakConnection;
                if (!LocationControlConnectionIsCurrent(failedConnection))
                    return;
                InteropLog(@"Ventura location enable failed: %@", error);
                ResetLocationControl();
                ScheduleLocationRetry();
            });
            return;
        }
        dispatch_async(InteropQueue, ^{
            NSXPCConnection *enabledConnection = weakConnection;
            if (!LocationControlConnectionIsCurrent(enabledConnection))
                return;
            id<MacWSLocationInternalServiceProtocol> authorizationControl =
                LocationControlProxy;
            [authorizationControl setAuthorizationStatus:YES
                withCorrectiveCompensation:0
                                forBundleID:MacWSLocationWitnessBundleID
                               orBundlePath:nil
                                 replyBlock:^(NSError *authorizationError) {
                dispatch_async(InteropQueue, ^{
                    NSXPCConnection *authorizedConnection = weakConnection;
                    if (!LocationControlConnectionIsCurrent(
                            authorizedConnection))
                        return;
                    if (authorizationError) {
                        InteropLog(@"Ventura location witness authorization "
                                   "failed: %@", authorizationError);
                        ResetLocationControl();
                        ScheduleLocationRetry();
                        return;
                    }
                    AuthorizeVenturaMapsAfterWitness(authorizedConnection);
                });
            }];
        });
    }];
}

static void RefreshVenturaMapsAuthorizationForLaunch(pid_t pid) {
    NSRunningApplication *application =
        [NSRunningApplication runningApplicationWithProcessIdentifier:pid];
    if (![application.bundleIdentifier isEqualToString:@"com.apple.Maps"])
        return;

    // Runtime-confirmed on 2026-08-09: locationd's private authorization
    // getter persisted status 3 across a Maps relaunch, but the new Maps PID
    // still logged "Showing Location Services Authorization Prompt with no
    // handler" and locationd did not send fixes to that client. Replaying the
    // stock setter while that exact Maps process existed immediately caused
    // stock locationd to send repeated WGS84 fixes to com.apple.Maps. Bind the
    // control transaction to the application generation instead of treating
    // a pre-launch persisted value as client readiness.
    InteropLog(@"refreshing Maps authorization for application pid=%d", pid);
    NSXPCConnection *oldConnection = LocationControlConnection;
    ResetLocationControl();
    [oldConnection invalidate];
    PrepareVenturaLocationControl();
}

static void ScheduleMapsLaunchAuthorizationRefresh(pid_t pid) {
    if (pid <= 0) return;
    // NSWorkspace publishes after exec, but Maps still has to initialize its
    // AppKit/CoreLocation identity. This one bounded delay avoids racing that
    // setup without polling a process or authorization value indefinitely.
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 2 * NSEC_PER_SEC),
                   InteropQueue, ^{
        RefreshVenturaMapsAuthorizationForLaunch(pid);
    });
}

static void ObserveMapsApplicationGenerations(void) {
    NSNotificationCenter *center = NSWorkspace.sharedWorkspace.notificationCenter;
    WorkspaceLaunchObserver = [center
        addObserverForName:NSWorkspaceDidLaunchApplicationNotification
                    object:nil
                     queue:NSOperationQueue.mainQueue
                usingBlock:^(NSNotification *notification) {
        NSRunningApplication *application =
            notification.userInfo[NSWorkspaceApplicationKey];
        if ([application.bundleIdentifier isEqualToString:@"com.apple.Maps"])
            ScheduleMapsLaunchAuthorizationRefresh(application.processIdentifier);
    }];

    // Also close the daemon-replacement case: package upgrades may restart
    // interop while an existing Maps generation remains alive.
    for (NSRunningApplication *application in
            [NSRunningApplication
                runningApplicationsWithBundleIdentifier:@"com.apple.Maps"]) {
        ScheduleMapsLaunchAuthorizationRefresh(application.processIdentifier);
    }
}

static double MacWSGetXPCDouble(xpc_object_t dictionary, const char *key) {
    uint64_t bits = xpc_dictionary_get_uint64(dictionary, key);
    double value = 0;
    memcpy(&value, &bits, sizeof(value));
    return value;
}

static int MacWSVenturaLocationType(CLLocation *location) {
    SEL selector = NSSelectorFromString(@"type");
    if (![location respondsToSelector:selector]) return 0;
    return ((int (*)(id, SEL))objc_msgSend)(location, selector);
}

static int MacWSVenturaReferenceFrame(CLLocation *location) {
    SEL selector = NSSelectorFromString(@"referenceFrame");
    if (![location respondsToSelector:selector]) return 0;
    return ((int (*)(id, SEL))objc_msgSend)(location, selector);
}

static CLLocation *MacWSLocationByApplyingNativeMetadata(
    CLLocation *location, int locationType, int referenceFrame,
    int rawReferenceFrame) {
    // RE-confirmed against Ventura 13.4 CoreLocation on the target:
    //   -[CLLocation clientLocation] returns a 176-byte CLClientLocation.
    //   -[CLLocation type] loads self->_internal + 0x68.
    //   -clientLocation copies that field to result + 0x60.
    //   -[CLLocation referenceFrame] reads self->_internal + 0x8c.
    //   -clientLocation copies that field to result + 0x84 and the adjacent
    //    rawReferenceFrame to result + 0x88.
    //   -initWithClientLocation: consumes the same 176-byte ABI.
    // Runtime-confirmed before this adaptation: Ventura locationd accepted
    // every bridged fix but logged "location dropped due to referenceFrame"
    // with the value Unknown. Preserve the native provider metadata at this
    // ABI boundary instead of inventing a location result downstream.
    // Fail closed if a different CoreLocation build does not match all of
    // those structural witnesses.
    enum {
        kClientLocationSize = 176,
        kClientLocationTypeOffset = 0x60,
        kClientLocationReferenceFrameOffset = 0x84,
        kClientLocationRawReferenceFrameOffset = 0x88,
    };
    SEL clientSelector = NSSelectorFromString(@"clientLocation");
    SEL initSelector = NSSelectorFromString(@"initWithClientLocation:");
    NSMethodSignature *clientSignature =
        [location methodSignatureForSelector:clientSelector];
    NSMethodSignature *initSignature =
        [CLLocation instanceMethodSignatureForSelector:initSelector];
    if (!clientSignature || !initSignature ||
        clientSignature.methodReturnLength != kClientLocationSize ||
        initSignature.numberOfArguments != 3) {
        InteropLog(@"Ventura CLLocation private ABI unavailable");
        return nil;
    }
    NSUInteger argumentSize = 0;
    NSUInteger argumentAlignment = 0;
    NSGetSizeAndAlignment([initSignature getArgumentTypeAtIndex:2],
                          &argumentSize, &argumentAlignment);
    if (argumentSize != kClientLocationSize || argumentAlignment != 8 ||
        kClientLocationTypeOffset + sizeof(int) > argumentSize ||
        kClientLocationReferenceFrameOffset + sizeof(int) > argumentSize ||
        kClientLocationRawReferenceFrameOffset + sizeof(int) > argumentSize) {
        InteropLog(@"Ventura CLLocation private ABI mismatch size=%lu align=%lu",
                   (unsigned long)argumentSize,
                   (unsigned long)argumentAlignment);
        return nil;
    }

    NSMutableData *clientLocation =
        [NSMutableData dataWithLength:kClientLocationSize];
    NSInvocation *getter =
        [NSInvocation invocationWithMethodSignature:clientSignature];
    getter.target = location;
    getter.selector = clientSelector;
    [getter invoke];
    [getter getReturnValue:clientLocation.mutableBytes];
    memcpy((uint8_t *)clientLocation.mutableBytes + kClientLocationTypeOffset,
           &locationType, sizeof(locationType));
    memcpy((uint8_t *)clientLocation.mutableBytes +
               kClientLocationReferenceFrameOffset,
           &referenceFrame, sizeof(referenceFrame));
    memcpy((uint8_t *)clientLocation.mutableBytes +
               kClientLocationRawReferenceFrameOffset,
           &rawReferenceFrame, sizeof(rawReferenceFrame));

    CLLocation *allocated = [CLLocation alloc];
    NSInvocation *initializer =
        [NSInvocation invocationWithMethodSignature:initSignature];
    initializer.target = allocated;
    initializer.selector = initSelector;
    [initializer setArgument:clientLocation.mutableBytes atIndex:2];
    [initializer invoke];
    __unsafe_unretained CLLocation *unretainedResult = nil;
    [initializer getReturnValue:&unretainedResult];
    CLLocation *rebuilt = unretainedResult;
    if (!rebuilt || MacWSVenturaLocationType(rebuilt) != locationType ||
        MacWSVenturaReferenceFrame(rebuilt) != referenceFrame) {
        InteropLog(@"Ventura CLLocation private metadata reconstruction failed");
        return nil;
    }
    NSMutableData *rebuiltClientLocation =
        [NSMutableData dataWithLength:kClientLocationSize];
    getter.target = rebuilt;
    [getter invoke];
    [getter getReturnValue:rebuiltClientLocation.mutableBytes];
    int rebuiltRawReferenceFrame = 0;
    memcpy(&rebuiltRawReferenceFrame,
           (const uint8_t *)rebuiltClientLocation.bytes +
               kClientLocationRawReferenceFrameOffset,
           sizeof(rebuiltRawReferenceFrame));
    if (rebuiltRawReferenceFrame != rawReferenceFrame) {
        InteropLog(@"Ventura CLLocation raw reference-frame reconstruction failed");
        return nil;
    }
    return rebuilt;
}

static void SubmitVenturaLocation(CLLocation *location) {
    LastNativeLocation = location;
    // The desktop control and simulation listeners are independent Mach
    // services.  Do not serialize simulation startup behind the control
    // service's asynchronous reply: Ventura locationd starts a hard-coded
    // three-second idle timer during startRun.  Runtime evidence on
    // 2026-08-05 showed that it accepted the enable request, but the old early
    // return prevented appendSimulatedLocations: from being sent before that
    // timer expired.
    if (!LocationControlReady && !LocationControlInFlight) {
        PrepareVenturaLocationControl();
    }
    id<MacWSSimulationLocationProtocol> simulation = LocationSimulation();
    if (!simulation) return;
    // Keep one ordinary CoreLocation client registered.  locationd's idle
    // policy intentionally counts real CLLocation clients rather than its
    // administrative simulation/control connections; this also gives us an
    // end-to-end witness that injected fixes leave the provider graph.
    EnsureVenturaLocationClient();
    if (!LocationSimulationStarted) {
        // Use Ventura's stock defaults.  Runtime evidence showed that forcing
        // delivery behavior 0 asks the daemon to synthesize an unavailable
        // CLLocation (rawLat/lon 0, timestamp -1), discarding the valid item
        // that was just appended.
        [simulation clearSimulatedLocations];
        [simulation setLocationInterval:1.0];
        [simulation appendSimulatedLocations:@[ location ]];
        [simulation startLocationSimulation];
        LocationSimulationStarted = YES;
    } else {
        [simulation appendSimulatedLocations:@[ location ]];
    }
    LocationFixCount++;
    InteropLog(@"submitted Ventura-native location #%llu accuracy=%.1fm",
               (unsigned long long)LocationFixCount,
               location.horizontalAccuracy);
}

static void ScheduleLocationRetry(void) {
    if (LocationRetryScheduled || !LastNativeLocation) return;
    LocationRetryScheduled = YES;
    // The launch contract deliberately throttles failed/idle Ventura daemon
    // relaunches to ten seconds.  Retry after that window instead of spinning.
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 11 * NSEC_PER_SEC),
                   InteropQueue, ^{
        LocationRetryScheduled = NO;
        if (LastNativeLocation &&
            (!LocationControlReady || !LocationSimulationStarted)) {
            InteropLog(@"retrying last native location after service restart");
            SubmitVenturaLocation(LastNativeLocation);
        }
    });
}

static void ApplyNativeLocation(xpc_object_t request) {
    double latitude = MacWSGetXPCDouble(
        request, MACWS_INTEROP_KEY_LATITUDE);
    double longitude = MacWSGetXPCDouble(
        request, MACWS_INTEROP_KEY_LONGITUDE);
    double altitude = MacWSGetXPCDouble(
        request, MACWS_INTEROP_KEY_ALTITUDE);
    double horizontalAccuracy = MacWSGetXPCDouble(
        request, MACWS_INTEROP_KEY_HORIZONTAL_ACCURACY);
    double verticalAccuracy = MacWSGetXPCDouble(
        request, MACWS_INTEROP_KEY_VERTICAL_ACCURACY);
    double course = MacWSGetXPCDouble(request, MACWS_INTEROP_KEY_COURSE);
    double speed = MacWSGetXPCDouble(request, MACWS_INTEROP_KEY_SPEED);
    double timestamp = MacWSGetXPCDouble(
        request, MACWS_INTEROP_KEY_TIMESTAMP);
    int64_t locationTypeValue = xpc_dictionary_get_int64(
        request, MACWS_INTEROP_KEY_LOCATION_TYPE);
    int64_t referenceFrameValue = xpc_dictionary_get_int64(
        request, MACWS_INTEROP_KEY_REFERENCE_FRAME);
    int64_t rawReferenceFrameValue = xpc_dictionary_get_int64(
        request, MACWS_INTEROP_KEY_RAW_REFERENCE_FRAME);
    if (!isfinite(latitude) || !isfinite(longitude) ||
        !isfinite(altitude) || !isfinite(horizontalAccuracy) ||
        !isfinite(verticalAccuracy) || !isfinite(course) ||
        !isfinite(speed) || !isfinite(timestamp) ||
        latitude < -90.0 || latitude > 90.0 ||
        longitude < -180.0 || longitude > 180.0 ||
        horizontalAccuracy < 0.0 || timestamp < 978307200.0 ||
        locationTypeValue < 1 || locationTypeValue > 9 ||
        referenceFrameValue <= 0 || referenceFrameValue > INT32_MAX ||
        rawReferenceFrameValue < 0 || rawReferenceFrameValue > INT32_MAX) {
        InteropLog(@"rejected malformed native location scalar message");
        return;
    }
    CLLocation *location = [[CLLocation alloc]
        initWithCoordinate:CLLocationCoordinate2DMake(latitude, longitude)
                  altitude:altitude
        horizontalAccuracy:horizontalAccuracy
          verticalAccuracy:verticalAccuracy
                    course:course
                     speed:speed
                 timestamp:[NSDate dateWithTimeIntervalSince1970:timestamp]];
    CLLocation *typedLocation = MacWSLocationByApplyingNativeMetadata(
        location, (int)locationTypeValue, (int)referenceFrameValue,
        (int)rawReferenceFrameValue);
    if (!typedLocation) return;
    SubmitVenturaLocation(typedLocation);
}

static void Digest(NSData *data, uint8_t output[16]) {
    uint8_t digest[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256(data.bytes, (CC_LONG)data.length, digest);
    memcpy(output, digest, 16);
}

static BOOL MacWSAbsoluteArchivePath(NSString *path) {
    if (![path isKindOfClass:NSString.class] || ![path hasPrefix:@"/"] ||
        [path lengthOfBytesUsingEncoding:NSUTF8StringEncoding] >
            MACWS_INTEROP_MAX_PATH_BYTES ||
        [path rangeOfString:@"\0"].location != NSNotFound) return NO;
    return [path.stringByStandardizingPath hasPrefix:@"/"];
}

static NSArray<NSDictionary *> *ValidatedArchiveItems(NSData *archive,
                                                       NSString **message) {
    if (!archive.length || archive.length > MACWS_INTEROP_MAX_INLINE_BYTES) {
        if (message) *message = @"pasteboard archive size is invalid";
        return nil;
    }
    NSError *error = nil;
    id root = [NSPropertyListSerialization propertyListWithData:archive
        options:NSPropertyListImmutable format:nil error:&error];
    NSArray *items = [root isKindOfClass:NSDictionary.class]
        ? root[MacWSArchiveItemsKey] : nil;
    if (![root isKindOfClass:NSDictionary.class] ||
        ![root[MacWSArchiveVersionKey] isEqual:@(MacWSArchiveVersion)] ||
        ![items isKindOfClass:NSArray.class] || !items.count ||
        items.count > MACWS_INTEROP_MAX_ITEMS) {
        if (message) *message = @"pasteboard archive structure is invalid";
        return nil;
    }
    NSMutableArray *normalizedItems = [NSMutableArray array];
    NSUInteger representationCount = 0;
    NSUInteger inlineBytes = 0;
    for (id itemValue in items) {
        NSArray *representations = [itemValue isKindOfClass:NSDictionary.class]
            ? itemValue[MacWSArchiveRepresentationsKey] : nil;
        if (![representations isKindOfClass:NSArray.class] ||
            !representations.count) {
            if (message) *message = @"pasteboard item has no representations";
            return nil;
        }
        NSMutableArray *normalizedRepresentations = [NSMutableArray array];
        for (id representationValue in representations) {
            if (++representationCount > MACWS_INTEROP_MAX_REPRESENTATIONS ||
                ![representationValue isKindOfClass:NSDictionary.class]) {
                if (message) *message = @"pasteboard representation count is invalid";
                return nil;
            }
            NSString *type = representationValue[MacWSArchiveTypeKey];
            NSData *data = representationValue[MacWSArchiveDataKey];
            NSString *path = representationValue[MacWSArchiveFilePathKey];
            if (![type isKindOfClass:NSString.class] || !type.length ||
                [type lengthOfBytesUsingEncoding:NSUTF8StringEncoding] >
                    MACWS_INTEROP_MAX_TYPE_BYTES ||
                ((data != nil) == (path != nil))) {
                if (message) *message = @"pasteboard representation is malformed";
                return nil;
            }
            if (data) {
                if (![data isKindOfClass:NSData.class] ||
                    data.length > MACWS_INTEROP_MAX_INLINE_BYTES - inlineBytes) {
                    if (message) *message = @"pasteboard representation data is invalid";
                    return nil;
                }
                inlineBytes += data.length;
                [normalizedRepresentations addObject:@{
                    MacWSArchiveTypeKey: type, MacWSArchiveDataKey: data
                }];
            } else {
                if (!MacWSAbsoluteArchivePath(path)) {
                    if (message) *message = @"pasteboard file path is invalid";
                    return nil;
                }
                [normalizedRepresentations addObject:@{
                    MacWSArchiveTypeKey: type,
                    MacWSArchiveFilePathKey: path.stringByStandardizingPath
                }];
            }
        }
        [normalizedItems addObject:@{
            MacWSArchiveRepresentationsKey: normalizedRepresentations
        }];
    }
    return normalizedItems;
}

static NSString *ResolvedFileURLString(NSString *urlString) {
    NSURL *url = urlString.length ? [NSURL URLWithString:urlString] : nil;
    if (!url.isFileURL) return nil;
    NSString *path = url.path;
    // NSURL deliberately leaves -path nil for a file-reference URL when the
    // underlying /.file resolver is unavailable. The serialized URL remains
    // an ordinary percent-encoded file URL, so retain its path component for
    // the fsgetpath fallback below.
    if (!path.length && [urlString hasPrefix:@"file://"])
        path = [[urlString substringFromIndex:@"file://".length]
            stringByRemovingPercentEncoding];
    path = path.stringByStandardizingPath;
    if (path.length && ![path hasPrefix:@"/.file/id="] &&
        [NSFileManager.defaultManager fileExistsAtPath:path]) return path;

    // Runtime-confirmed on the target with Ventura Finder: NSDragPboard first
    // advertises com.apple.finder.node as a file-reference URL such as
    // file:///.file/id=6620456.35919769/. NSURL.filePathURL cannot resolve it
    // because this chroot's synthetic /.file is not the APFS magic directory.
    // The public fsgetpath(2) API resolves the same catalog object ID against
    // the real mounted filesystem (object 35919769 ->
    // /private/var/root/Documents in the captured run).
    NSRange marker = [path rangeOfString:@"/.file/id="];
    if (marker.location != 0) return nil;
    NSString *reference = [path substringFromIndex:marker.length];
    NSArray<NSString *> *parts = [reference componentsSeparatedByString:@"."];
    if (parts.count < 2) return nil;
    const char *objectText = parts[1].UTF8String;
    if (!objectText || !*objectText) return nil;
    char *end = NULL;
    errno = 0;
    uint64_t objectID = strtoull(objectText, &end, 10);
    if (errno || !objectID || end == objectText) return nil;

    struct statfs *mounts = NULL;
    int mountCount = getmntinfo(&mounts, MNT_NOWAIT);
    for (int index = 0; index < mountCount; index++) {
        char resolved[MACWS_INTEROP_MAX_PATH_BYTES + 1] = {0};
        if (fsgetpath(resolved, sizeof(resolved), &mounts[index].f_fsid,
                      objectID) < 0 || resolved[0] != '/') continue;
        NSString *candidate = [[NSString stringWithUTF8String:resolved]
            stringByStandardizingPath];
        if (![NSFileManager.defaultManager fileExistsAtPath:candidate])
            continue;
        InteropLog(@"resolved Finder file-reference object=%llu path=%@",
            (unsigned long long)objectID, candidate);
        return candidate;
    }
    return nil;
}

static void MakeExportReadable(NSString *path) {
    BOOL isDirectory = NO;
    if (![NSFileManager.defaultManager fileExistsAtPath:path
                                            isDirectory:&isDirectory]) return;
    [NSFileManager.defaultManager setAttributes:@{
        NSFilePosixPermissions: @(isDirectory ? 0755 : 0644)
    } ofItemAtPath:path error:nil];
    if (!isDirectory) return;
    NSDirectoryEnumerator *enumerator = [NSFileManager.defaultManager
        enumeratorAtURL:[NSURL fileURLWithPath:path]
        includingPropertiesForKeys:@[NSURLIsDirectoryKey]
        options:0
        errorHandler:^BOOL(NSURL *url, NSError *error) {
            InteropLog(@"export traversal skipped url=%@ error=%@", url, error);
            return YES;
        }];
    for (NSURL *url in enumerator) {
        NSNumber *directory = nil;
        [url getResourceValue:&directory forKey:NSURLIsDirectoryKey error:nil];
        [NSFileManager.defaultManager setAttributes:@{
            NSFilePosixPermissions: @(directory.boolValue ? 0755 : 0644)
        } ofItemAtPath:url.path error:nil];
    }
}

static NSString *StageMacOSExport(NSString *sourcePath,
                                  NSString **batchPath,
                                  NSString **message) {
    NSString *source = sourcePath.stringByStandardizingPath;
    if (!source.length || ![source hasPrefix:@"/"]) return nil;
    // iPadOS-originated files already live in a mobile-readable shared cache.
    if ([source isEqualToString:MacWSImportsRoot] ||
        [source hasPrefix:[MacWSImportsRoot stringByAppendingString:@"/"]] ||
        [source isEqualToString:MacWSExportsRoot] ||
        [source hasPrefix:[MacWSExportsRoot stringByAppendingString:@"/"]])
        return source;
    BOOL isDirectory = NO;
    if (![NSFileManager.defaultManager fileExistsAtPath:source
                                            isDirectory:&isDirectory]) return nil;
    NSError *error = nil;
    if (!batchPath || !*batchPath) {
        NSString *batch = [MacWSExportsRoot stringByAppendingPathComponent:
            NSUUID.UUID.UUIDString];
        if (![NSFileManager.defaultManager createDirectoryAtPath:batch
                                      withIntermediateDirectories:YES
                                                       attributes:@{
                    NSFilePosixPermissions: @0755
                } error:&error]) {
            if (message) *message = error.localizedDescription;
            return nil;
        }
        if (batchPath) *batchPath = batch;
    }
    NSString *name = source.lastPathComponent.length
        ? source.lastPathComponent : @"Exported Item";
    NSString *destination = [*batchPath stringByAppendingPathComponent:name];
    NSString *stem = name.stringByDeletingPathExtension;
    NSString *extension = name.pathExtension;
    NSUInteger suffix = 2;
    while ([NSFileManager.defaultManager fileExistsAtPath:destination]) {
        NSString *numbered = [NSString stringWithFormat:@"%@-%lu",
            stem.length ? stem : @"Exported Item", (unsigned long)suffix++];
        destination = [*batchPath stringByAppendingPathComponent:
            extension.length ? [numbered stringByAppendingPathExtension:extension]
                             : numbered];
    }
    if (![NSFileManager.defaultManager copyItemAtPath:source
                                               toPath:destination error:&error]) {
        if (message) *message = error.localizedDescription;
        return nil;
    }
    MakeExportReadable(destination);
    InteropLog(@"staged macOS export source=%@ destination=%@ directory=%@",
        source, destination, isDirectory ? @"YES" : @"NO");
    return destination;
}

static NSData *ArchiveDataForPasteboard(NSPasteboard *pasteboard,
                                        NSString **message) {
    NSMutableArray *items = [NSMutableArray array];
    NSUInteger representationCount = 0;
    NSUInteger inlineBytes = 0;
    NSString *exportBatch = nil;
    for (NSPasteboardItem *pasteboardItem in pasteboard.pasteboardItems) {
        if (items.count >= MACWS_INTEROP_MAX_ITEMS) break;
        NSMutableArray *representations = [NSMutableArray array];
        NSString *fileURLString = [pasteboardItem
            stringForType:NSPasteboardTypeFileURL];
        if (!fileURLString.length) {
            NSData *finderNode = [pasteboardItem
                dataForType:@"com.apple.finder.node"];
            fileURLString = [[NSString alloc] initWithData:finderNode
                                                   encoding:NSUTF8StringEncoding];
        }
        NSString *resolvedFilePath = ResolvedFileURLString(fileURLString);
        NSString *stagedFilePath = resolvedFilePath
            ? StageMacOSExport(resolvedFilePath, &exportBatch, message) : nil;
        if (stagedFilePath &&
            representationCount < MACWS_INTEROP_MAX_REPRESENTATIONS) {
            [representations addObject:@{
                MacWSArchiveTypeKey: NSPasteboardTypeFileURL,
                MacWSArchiveFilePathKey: stagedFilePath
            }];
            representationCount++;
        }
        for (NSPasteboardType type in pasteboardItem.types) {
            if (representationCount >= MACWS_INTEROP_MAX_REPRESENTATIONS)
                break;
            if (![type isKindOfClass:NSString.class] || !type.length ||
                [type lengthOfBytesUsingEncoding:NSUTF8StringEncoding] >
                    MACWS_INTEROP_MAX_TYPE_BYTES) continue;
            NSDictionary *representation = nil;
            if ([type isEqualToString:NSPasteboardTypeFileURL]) {
                if (!stagedFilePath) {
                    NSString *path = [NSURL URLWithString:fileURLString].path;
                    if (MacWSAbsoluteArchivePath(path)) {
                        representation = @{
                            MacWSArchiveTypeKey: type,
                            MacWSArchiveFilePathKey: path.stringByStandardizingPath
                        };
                    }
                }
            } else {
                NSData *data = [pasteboardItem dataForType:type];
                if (data && data.length <=
                    MACWS_INTEROP_MAX_INLINE_BYTES - inlineBytes) {
                    inlineBytes += data.length;
                    representation = @{
                        MacWSArchiveTypeKey: type, MacWSArchiveDataKey: data
                    };
                }
            }
            if (representation) {
                [representations addObject:representation];
                representationCount++;
            }
        }
        if (representations.count) [items addObject:@{
            MacWSArchiveRepresentationsKey: representations
        }];
    }
    if (!items.count) {
        if (message) *message = @"pasteboard has no bounded representations";
        return nil;
    }
    NSError *error = nil;
    NSData *archive = [NSPropertyListSerialization dataWithPropertyList:@{
        MacWSArchiveVersionKey: @(MacWSArchiveVersion),
        MacWSArchiveItemsKey: items
    } format:NSPropertyListBinaryFormat_v1_0 options:0 error:&error];
    if (!archive.length || archive.length > MACWS_INTEROP_MAX_INLINE_BYTES) {
        if (message) *message = error.localizedDescription ?:
            @"pasteboard archive exceeds 64 MiB";
        return nil;
    }
    return archive;
}

static void AttachArchive(xpc_object_t dictionary, NSData *archive,
                          const char *eventName) {
    MacWSInteropItemDescriptor descriptor = {
        .magic = MACWS_INTEROP_MAGIC,
        .version = MACWS_INTEROP_VERSION,
        .size = sizeof(MacWSInteropItemDescriptor),
        .kind = MacWSInteropKindPasteboardArchive,
        .flags = MacWSInteropInlinePayload | MacWSInteropFromMacOS,
        .generation = ++Generation,
        .originID = DaemonOriginID,
        .payloadLength = archive.length,
    };
    Digest(archive, descriptor.digest);
    if (eventName)
        xpc_dictionary_set_string(dictionary, MACWS_INTEROP_KEY_EVENT,
                                  eventName);
    xpc_dictionary_set_data(dictionary, MACWS_INTEROP_KEY_DESCRIPTOR,
                            &descriptor, sizeof(descriptor));
    xpc_dictionary_set_data(dictionary, MACWS_INTEROP_KEY_PAYLOAD,
                            archive.bytes, archive.length);
}

static void Broadcast(xpc_object_t event) {
    for (id object in [Clients copy]) {
        xpc_connection_t connection = (xpc_connection_t)object;
        xpc_connection_send_message(connection, event);
    }
}

static void PublishPasteboardIfChanged(void) {
    NSPasteboard *pasteboard = NSPasteboard.generalPasteboard;
    NSInteger change = pasteboard.changeCount;
    if (change == LastPasteboardChange) return;
    LastPasteboardChange = change;
    if (change == AppliedPasteboardChange) return;

    NSString *message = nil;
    NSData *archive = ArchiveDataForPasteboard(pasteboard, &message);
    if (!archive) return;
    xpc_object_t event = xpc_dictionary_create(NULL, NULL, 0);
    AttachArchive(event, archive, MACWS_INTEROP_EVENT_PASTEBOARD);
    Broadcast(event);
}

static BOOL SafeImportedPath(NSString *path) {
    NSString *root = @"/Users/Shared/MacWS Imports";
    NSString *standard = path.stringByStandardizingPath;
    return [standard isEqualToString:root] ||
        [standard hasPrefix:[root stringByAppendingString:@"/"]];
}

static void ApplyInlineClipboard(xpc_object_t request) {
    size_t descriptorSize = 0;
    const void *descriptorBytes = xpc_dictionary_get_data(
        request, MACWS_INTEROP_KEY_DESCRIPTOR, &descriptorSize);
    if (!descriptorBytes || descriptorSize != sizeof(MacWSInteropItemDescriptor))
        return;
    MacWSInteropItemDescriptor descriptor;
    memcpy(&descriptor, descriptorBytes, sizeof(descriptor));
    if (!MacWSInteropItemDescriptorIsValid(&descriptor, descriptorSize) ||
        descriptor.originID == DaemonOriginID ||
        ((descriptor.flags & MacWSInteropFromIOS) == 0)) return;
    size_t payloadSize = 0;
    const void *payloadBytes = xpc_dictionary_get_data(
        request, MACWS_INTEROP_KEY_PAYLOAD, &payloadSize);
    if (!payloadBytes || payloadSize != descriptor.payloadLength ||
        payloadSize > MACWS_INTEROP_MAX_INLINE_BYTES) return;
    NSData *payload = [NSData dataWithBytes:payloadBytes length:payloadSize];
    uint8_t digest[16];
    Digest(payload, digest);
    if (memcmp(digest, descriptor.digest, sizeof(digest)) != 0) return;
    if (descriptor.originID == LastIncomingOrigin &&
        descriptor.generation <= LastIncomingGeneration) return;

    NSPasteboard *pasteboard = NSPasteboard.generalPasteboard;
    [pasteboard clearContents];
    BOOL applied = NO;
    if (descriptor.kind == MacWSInteropKindUTF8Text) {
        NSString *text = [[NSString alloc] initWithData:payload
                                               encoding:NSUTF8StringEncoding];
        if (text) applied = [pasteboard setString:text
                                          forType:NSPasteboardTypeString];
    } else if (descriptor.kind == MacWSInteropKindPNG) {
        applied = [pasteboard setData:payload forType:NSPasteboardTypePNG];
    } else if (descriptor.kind == MacWSInteropKindJPEG) {
        applied = [pasteboard setData:payload
                              forType:@"public.jpeg"];
    }
    if (applied) {
        LastIncomingOrigin = descriptor.originID;
        LastIncomingGeneration = descriptor.generation;
        AppliedPasteboardChange = pasteboard.changeCount;
        LastPasteboardChange = AppliedPasteboardChange;
    }
}

static void ApplyImportedFiles(xpc_object_t request) {
    xpc_object_t items = xpc_dictionary_get_value(request,
                                                   MACWS_INTEROP_KEY_ITEMS);
    if (!items || xpc_get_type(items) != XPC_TYPE_ARRAY) return;
    NSMutableArray<NSURL *> *urls = [NSMutableArray array];
    xpc_array_apply(items, ^bool(size_t index, xpc_object_t value) {
        (void)index;
        if (urls.count >= MACWS_INTEROP_MAX_ITEMS ||
            xpc_get_type(value) != XPC_TYPE_STRING) return true;
        const char *pathBytes = xpc_string_get_string_ptr(value);
        NSString *path = pathBytes ? [NSString stringWithUTF8String:pathBytes]
                                   : nil;
        BOOL isDirectory = NO;
        if (path && SafeImportedPath(path) &&
            [NSFileManager.defaultManager fileExistsAtPath:path
                                                isDirectory:&isDirectory]) {
            [urls addObject:[NSURL fileURLWithPath:path isDirectory:isDirectory]];
        }
        return true;
    });
    if (!urls.count) return;
    NSPasteboard *pasteboard = NSPasteboard.generalPasteboard;
    [pasteboard clearContents];
    if ([pasteboard writeObjects:urls]) {
        AppliedPasteboardChange = pasteboard.changeCount;
        LastPasteboardChange = AppliedPasteboardChange;
    }
}

static BOOL ApplyPasteboardArchive(xpc_object_t request, NSString **message) {
    size_t descriptorSize = 0;
    const void *descriptorBytes = xpc_dictionary_get_data(
        request, MACWS_INTEROP_KEY_DESCRIPTOR, &descriptorSize);
    if (!descriptorBytes || descriptorSize != sizeof(MacWSInteropItemDescriptor)) {
        if (message) *message = @"missing pasteboard descriptor";
        return NO;
    }
    MacWSInteropItemDescriptor descriptor;
    memcpy(&descriptor, descriptorBytes, sizeof(descriptor));
    if (!MacWSInteropItemDescriptorIsValid(&descriptor, descriptorSize) ||
        descriptor.kind != MacWSInteropKindPasteboardArchive ||
        descriptor.originID == DaemonOriginID ||
        (descriptor.flags & MacWSInteropFromIOS) == 0) {
        if (message) *message = @"invalid pasteboard descriptor";
        return NO;
    }
    size_t payloadSize = 0;
    const void *payloadBytes = xpc_dictionary_get_data(
        request, MACWS_INTEROP_KEY_PAYLOAD, &payloadSize);
    if (!payloadBytes || payloadSize != descriptor.payloadLength ||
        payloadSize > MACWS_INTEROP_MAX_INLINE_BYTES) {
        if (message) *message = @"invalid pasteboard payload length";
        return NO;
    }
    NSData *archive = [NSData dataWithBytes:payloadBytes length:payloadSize];
    uint8_t digest[16];
    Digest(archive, digest);
    if (memcmp(digest, descriptor.digest, sizeof(digest)) != 0) {
        if (message) *message = @"pasteboard digest mismatch";
        return NO;
    }
    if (descriptor.originID == LastIncomingOrigin &&
        descriptor.generation <= LastIncomingGeneration) {
        if (message) *message = @"duplicate pasteboard generation";
        return NO;
    }
    NSArray *items = ValidatedArchiveItems(archive, message);
    if (!items) return NO;

    NSPasteboard *pasteboard = NSPasteboard.generalPasteboard;
    NSMutableArray<id<NSPasteboardWriting>> *pasteboardItems =
        [NSMutableArray array];
    for (NSDictionary *item in items) {
        NSMutableArray<NSDictionary *> *safeRepresentations =
            [NSMutableArray array];
        NSURL *fileURL = nil;
        for (NSDictionary *representation in
                item[MacWSArchiveRepresentationsKey]) {
            NSString *type = representation[MacWSArchiveTypeKey];
            NSString *path = representation[MacWSArchiveFilePathKey];
            if (path) {
                BOOL isDirectory = NO;
                if (SafeImportedPath(path) &&
                    [NSFileManager.defaultManager fileExistsAtPath:path
                                                        isDirectory:&isDirectory]) {
                    NSURL *url = [NSURL fileURLWithPath:path
                                           isDirectory:isDirectory];
                    if ([type isEqualToString:NSPasteboardTypeFileURL])
                        fileURL = url;
                    [safeRepresentations addObject:representation];
                }
            } else {
                [safeRepresentations addObject:representation];
            }
        }
        if (safeRepresentations.count) {
            MacWSArchivePasteboardWriter *writer =
                [MacWSArchivePasteboardWriter new];
            writer.representations = safeRepresentations;
            writer.fileURL = fileURL;
            [pasteboardItems addObject:writer];
        }
    }
    if (!pasteboardItems.count) {
        if (message) *message = @"no safe pasteboard representations to apply";
        return NO;
    }
    [pasteboard clearContents];
    if (![pasteboard writeObjects:pasteboardItems]) {
        if (message) *message = @"NSPasteboard rejected the archive";
        return NO;
    }
    LastIncomingOrigin = descriptor.originID;
    LastIncomingGeneration = descriptor.generation;
    AppliedPasteboardChange = pasteboard.changeCount;
    LastPasteboardChange = AppliedPasteboardChange;
    InteropLog(@"applied iPadOS pasteboard generation=%llu items=%lu change=%ld",
        (unsigned long long)descriptor.generation,
        (unsigned long)pasteboardItems.count, (long)AppliedPasteboardChange);
    return YES;
}

static void ReplyWithDragPasteboard(xpc_connection_t peer,
                                    xpc_object_t request) {
    NSPasteboard *pasteboard = [NSPasteboard pasteboardWithName:NSPasteboardNameDrag];
    xpc_object_t reply = xpc_dictionary_create_reply(request);
    if (!reply) return;
    xpc_object_t afterValue = xpc_dictionary_get_value(
        request, MACWS_INTEROP_KEY_AFTER_CHANGE_COUNT);
    uint64_t after = afterValue ? xpc_dictionary_get_uint64(
        request, MACWS_INTEROP_KEY_AFTER_CHANGE_COUNT) : 0;
    uint64_t waitMilliseconds = MIN(xpc_dictionary_get_uint64(
        request, MACWS_INTEROP_KEY_WAIT_MILLISECONDS), 600);
    uint64_t deadline = mach_absolute_time();
    mach_timebase_info_data_t timebase;
    mach_timebase_info(&timebase);
    deadline += waitMilliseconds * 1000000ull * timebase.denom / timebase.numer;
    uint64_t stableTicks = 60ull * 1000000ull * timebase.denom / timebase.numer;
    uint64_t observedChange = (uint64_t)MAX(pasteboard.changeCount, 0);
    uint64_t stableSince = mach_absolute_time();
    while (afterValue && mach_absolute_time() < deadline) {
        uint64_t current = (uint64_t)MAX(pasteboard.changeCount, 0);
        if (current != observedChange) {
            observedChange = current;
            stableSince = mach_absolute_time();
        } else if (current > after &&
                   mach_absolute_time() - stableSince >= stableTicks) {
            // Runtime-confirmed with Ventura Finder on the target: the first
            // drag-board generation exposes only com.apple.finder.node; its
            // native release/finalization adds public.file-url in a later
            // generation. Do not snapshot the private intermediate form.
            BOOL finderNodePending = [pasteboard availableTypeFromArray:@[
                @"com.apple.finder.node"]] != nil &&
                [pasteboard availableTypeFromArray:@[
                    NSPasteboardTypeFileURL]] == nil;
            if (!finderNodePending) break;
        }
        usleep(10000);
    }
    uint64_t change = (uint64_t)MAX(pasteboard.changeCount, 0);
    xpc_dictionary_set_uint64(reply, MACWS_INTEROP_KEY_CHANGE_COUNT, change);
    if (afterValue && change > after) {
        NSString *message = nil;
        NSData *archive = ArchiveDataForPasteboard(pasteboard, &message);
        if (archive) {
            AttachArchive(reply, archive, NULL);
            xpc_dictionary_set_bool(reply, MACWS_INTEROP_KEY_OK, true);
        } else if (message.length) {
            xpc_dictionary_set_string(reply, MACWS_INTEROP_KEY_MESSAGE,
                                      message.UTF8String);
        }
    }
    xpc_connection_send_message(peer, reply);
}

static void HandleMessage(xpc_connection_t peer, xpc_object_t message) {
    if (message == XPC_ERROR_CONNECTION_INVALID ||
        message == XPC_ERROR_CONNECTION_INTERRUPTED) {
        [Clients removeObject:(id)peer];
        return;
    }
    if (!message || xpc_get_type(message) != XPC_TYPE_DICTIONARY) return;
    const char *operation = xpc_dictionary_get_string(message,
                                                       MACWS_INTEROP_KEY_OP);
    if (!operation) return;
    if (strcmp(operation, MACWS_INTEROP_OP_HELLO) == 0) {
        xpc_object_t ready = xpc_dictionary_create(NULL, NULL, 0);
        xpc_dictionary_set_string(ready, MACWS_INTEROP_KEY_EVENT,
                                  MACWS_INTEROP_EVENT_READY);
        uint64_t version = xpc_dictionary_get_uint64(
            message, MACWS_INTEROP_KEY_PROTOCOL_VERSION);
        if (version != MACWS_INTEROP_VERSION) {
            xpc_dictionary_set_string(ready, MACWS_INTEROP_KEY_EVENT,
                                      MACWS_INTEROP_EVENT_ERROR);
            xpc_dictionary_set_string(ready, MACWS_INTEROP_KEY_MESSAGE,
                                      "protocol version mismatch");
        }
        xpc_dictionary_set_uint64(ready, MACWS_INTEROP_KEY_PROTOCOL_VERSION,
                                  MACWS_INTEROP_VERSION);
        xpc_connection_send_message(peer, ready);
    } else if (strcmp(operation, MACWS_INTEROP_OP_SUBSCRIBE) == 0) {
        LastPasteboardChange = -1;
        PublishPasteboardIfChanged();
    } else if (strcmp(operation, MACWS_INTEROP_OP_PUBLISH_CLIPBOARD) == 0) {
        ApplyInlineClipboard(message);
    } else if (strcmp(operation, MACWS_INTEROP_OP_IMPORT_FILES) == 0) {
        ApplyImportedFiles(message);
    } else if (strcmp(operation, MACWS_INTEROP_OP_PUBLISH_PASTEBOARD) == 0) {
        NSString *replyMessage = nil;
        BOOL applied = ApplyPasteboardArchive(message, &replyMessage);
        xpc_object_t reply = xpc_dictionary_create_reply(message);
        if (reply) {
            xpc_dictionary_set_bool(reply, MACWS_INTEROP_KEY_OK, applied);
            if (replyMessage.length)
                xpc_dictionary_set_string(reply, MACWS_INTEROP_KEY_MESSAGE,
                                          replyMessage.UTF8String);
            xpc_connection_send_message(peer, reply);
        }
    } else if (strcmp(operation,
                      MACWS_INTEROP_OP_SNAPSHOT_DRAG_PASTEBOARD) == 0) {
        ReplyWithDragPasteboard(peer, message);
    } else if (strcmp(operation, MACWS_INTEROP_OP_PUBLISH_LOCATION) == 0) {
        ApplyNativeLocation(message);
    }
}

static void AcceptConnection(xpc_connection_t peer) {
    [Clients addObject:(id)peer];
    xpc_connection_set_target_queue(peer, InteropQueue);
    xpc_connection_set_event_handler(peer, ^(xpc_object_t message) {
        HandleMessage(peer, message);
    });
    xpc_connection_resume(peer);
}

int main(void) {
    @autoreleasepool {
        InteropQueue = dispatch_queue_create("com.macwsguide.interop.queue",
                                             DISPATCH_QUEUE_SERIAL);
        Clients = [NSMutableSet set];
        arc4random_buf(&DaemonOriginID, sizeof(DaemonOriginID));
        if (!DaemonOriginID) DaemonOriginID = 1;
        xpc_connection_t listener = xpc_connection_create_mach_service(
            MACWS_INTEROP_SERVICE, InteropQueue,
            XPC_CONNECTION_MACH_SERVICE_LISTENER);
        if (!listener) return 1;
        xpc_connection_set_event_handler(listener, ^(xpc_object_t event) {
            if (xpc_get_type(event) == XPC_TYPE_CONNECTION)
                AcceptConnection((xpc_connection_t)event);
        });
        xpc_connection_resume(listener);
        dispatch_source_t timer = dispatch_source_create(
            DISPATCH_SOURCE_TYPE_TIMER, 0, 0, InteropQueue);
        dispatch_source_set_timer(timer, dispatch_time(DISPATCH_TIME_NOW, 0),
                                  400 * NSEC_PER_MSEC, 50 * NSEC_PER_MSEC);
        dispatch_source_set_event_handler(timer, ^{ PublishPasteboardIfChanged(); });
        dispatch_resume(timer);
        // dispatch_main() terminates the process's original main thread. That
        // is valid for a pure GCD daemon, but CoreLocation installs timers and
        // delegate delivery on the actual main run loop. Runtime evidence on
        // the target was: "Attempting to add timer to main runloop, but the
        // main thread has exited", followed by the client aborting. Keep the
        // real main thread and its CFRunLoop alive instead.
        EnsureVenturaLocationClient();
        ObserveMapsApplicationGenerations();
        InteropLog(@"READY service=%s protocol=%u origin=%llu",
            MACWS_INTEROP_SERVICE, MACWS_INTEROP_VERSION,
            (unsigned long long)DaemonOriginID);
        CFRunLoopRun();
    }
}

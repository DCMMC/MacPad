// iOS CoreLocation diagnostic. Default mode is read-only. --observe-client
// creates a short-lived effective client to observe real registration. Neither
// mode changes authorization, logs coordinates, or starts a location feed.
#import <Foundation/Foundation.h>
#import <CoreLocation/CoreLocation.h>
#import <objc/message.h>
#include <signal.h>
#include <unistd.h>

@interface ProbeClient : NSObject <CLLocationManagerDelegate>
@end
@implementation ProbeClient
- (void)locationManagerDidChangeAuthorization:(CLLocationManager *)manager {
    fprintf(stderr, "native-location-status client-authorization=%d\n", manager.authorizationStatus);
}
@end

static void Deadline(int signum) { (void)signum; _exit(124); }

int main(int argc, const char **argv) {
    BOOL observe = argc == 2 && !strcmp(argv[1], "--observe-client");
    if (argc > 1 && !observe) return 64;
    signal(SIGALRM, Deadline);
    alarm(12);
    @autoreleasepool {
        fprintf(stderr, "native-location-status begin read-only\n");
        SEL query = NSSelectorFromString(@"authorizationStatusForBundleIdentifier:");
        BOOL available = [CLLocationManager respondsToSelector:query];
        NSInteger status = available
            ? ((NSInteger (*)(id, SEL, id))objc_msgSend)(CLLocationManager.class,
                query, @"com.macwsguide.host") : -1;
        printf("native-location-status services-enabled=%d host-authorization=%ld query-available=%d\n",
            CLLocationManager.locationServicesEnabled, (long)status, available);
        if (observe) {
            SEL init = NSSelectorFromString(@"initWithEffectiveBundleIdentifier:");
            if (![CLLocationManager instancesRespondToSelector:init]) return 2;
            CLLocationManager *manager = ((id (*)(id, SEL, id))objc_msgSend)(
                [CLLocationManager alloc], init, @"com.macwsguide.host");
            ProbeClient *client = [ProbeClient new];
            manager.delegate = client;
            [[NSRunLoop currentRunLoop] runUntilDate:[NSDate dateWithTimeIntervalSinceNow:5]];
            fprintf(stderr, "native-location-status observed-client-authorization=%d\n", manager.authorizationStatus);
            manager.delegate = nil;
        }
    }
    return 0;
}

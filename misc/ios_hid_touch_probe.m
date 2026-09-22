// Standalone iOS-native touch diagnostic. No UI hooks, kernel changes, app
// activation, or app-local event enqueue. --observe and --describe-drag do not
// dispatch. --send-drag is deliberately explicit and MUST NOT be run while a
// physical finger is down. Local build only until the observer/coordinate space
// have been verified on the target device.
//
// Source/API evidence:
// https://github.com/WebKit/WebKit/blob/main/Tools/WebKitTestRunner/ios/HIDEventGenerator.mm
// https://github.com/epic0001/zxtouchrootless/blob/main/pccontrol/Touch.xm
// Actual iOS 16.3.1 IOKit disassembly: tmp/ios-hid-api-disassembly.txt.
// CreateDigitizerEvent 0x18ee67b10 takes d0..d4, and reads two 32-bit stack
// slots for touch/options. Use uint32_t states to initialize those entire slots;
// do not copy the old vendor header's uint8 Boolean prototype here.
#import <Foundation/Foundation.h>
#include <dlfcn.h>
#include <errno.h>
#include <mach/mach_time.h>
#include <math.h>
#include <signal.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>

typedef CFTypeRef HIDRef;
typedef void (*EventCallback)(void *, void *, HIDRef, HIDRef);
static struct {
    HIDRef (*createClient)(CFAllocatorRef, uint32_t, CFDictionaryRef);
    HIDRef (*createSimple)(CFAllocatorRef);
    void (*matching)(HIDRef, CFDictionaryRef);
    void (*schedule)(HIDRef, CFRunLoopRef, CFStringRef);
    void (*unschedule)(HIDRef, CFRunLoopRef, CFStringRef);
    void (*registerCallback)(HIDRef, EventCallback, void *, void *);
    void (*unregisterCallback)(HIDRef);
    uint32_t (*type)(HIDRef);
    uint64_t (*sender)(HIDRef);
    CFTypeRef (*registry)(HIDRef);
    CFArrayRef (*services)(HIDRef);
    CFTypeRef (*property)(HIDRef, CFStringRef);
    HIDRef (*copyEvent)(HIDRef, uint32_t, HIDRef, uint32_t);
    CFArrayRef (*children)(HIDRef);
    double (*getFloat)(HIDRef, uint32_t);
    CFIndex (*getInteger)(HIDRef, uint32_t);
    HIDRef (*hand)(CFAllocatorRef, uint64_t, uint32_t, uint32_t, uint32_t,
        uint32_t, uint32_t, double, double, double, double, double,
        uint32_t, uint32_t, uint32_t);
    HIDRef (*finger)(CFAllocatorRef, uint64_t, uint32_t, uint32_t, uint32_t,
        double, double, double, double, double, uint32_t, uint32_t, uint32_t);
    void (*setInteger)(HIDRef, uint32_t, CFIndex);
    void (*setFloat)(HIDRef, uint32_t, double);
    void (*setSender)(HIDRef, uint64_t);
    void (*setTime)(HIDRef, uint64_t);
    void (*append)(HIDRef, HIDRef, uint32_t);
    void (*dispatch)(HIDRef, HIDRef);
} api;
static volatile sig_atomic_t interrupted;
static void Interrupted(int signalNumber) { (void)signalNumber; interrupted = 1; }
static void ObservationExpired(int signalNumber) {
    (void)signalNumber;
    static const char message[] = "observe hard deadline reached; injected=0\n";
    write(STDERR_FILENO, message, sizeof(message) - 1);
    _exit(124);
}
static double Now(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec / 1e9;
}
static void WaitUntil(double deadline) {
    while (!interrupted) {
        double remaining = deadline - Now();
        if (remaining <= 0) return;
        struct timespec pause = {0, (long)(MIN(remaining, .01) * 1e9)};
        nanosleep(&pause, NULL);
    }
}
static BOOL LoadAPI(BOOL observing) {
    void *iokit = dlopen("/System/Library/Frameworks/IOKit.framework/IOKit", RTLD_NOW | RTLD_LOCAL);
    if (!iokit) { fprintf(stderr, "IOKit: %s\n", dlerror()); return NO; }
#define LOAD(member, symbol) do { \
    api.member = (__typeof__(api.member))dlsym(iokit, #symbol); \
    if (!api.member) { fprintf(stderr, "missing %s\n", #symbol); return NO; } \
} while (0)
    LOAD(children, IOHIDEventGetChildren);
    if (observing) {
        LOAD(createClient, IOHIDEventSystemClientCreateWithType);
        LOAD(matching, IOHIDEventSystemClientSetMatching);
        LOAD(schedule, IOHIDEventSystemClientScheduleWithRunLoop);
        LOAD(unschedule, IOHIDEventSystemClientUnscheduleWithRunLoop);
        LOAD(registerCallback, IOHIDEventSystemClientRegisterEventCallback);
        LOAD(unregisterCallback, IOHIDEventSystemClientUnregisterEventCallback);
        LOAD(type, IOHIDEventGetType);
        LOAD(sender, IOHIDEventGetSenderID);
        LOAD(registry, IOHIDServiceClientGetRegistryID);
        LOAD(services, IOHIDEventSystemClientCopyServices);
        LOAD(property, IOHIDServiceClientCopyProperty);
        LOAD(copyEvent, IOHIDServiceClientCopyEvent);
        LOAD(getFloat, IOHIDEventGetFloatValue);
        LOAD(getInteger, IOHIDEventGetIntegerValue);
    } else {
        LOAD(createSimple, IOHIDEventSystemClientCreateSimpleClient);
        LOAD(hand, IOHIDEventCreateDigitizerEvent);
        LOAD(finger, IOHIDEventCreateDigitizerFingerEvent);
        LOAD(setInteger, IOHIDEventSetIntegerValue);
        LOAD(setFloat, IOHIDEventSetFloatValue);
        LOAD(setSender, IOHIDEventSetSenderID);
        LOAD(setTime, IOHIDEventSetTimeStamp);
        LOAD(append, IOHIDEventAppendEvent);
        LOAD(dispatch, IOHIDEventSystemClientDispatchEvent);
    }
#undef LOAD
    return YES;
}

static NSUInteger observed;
static uint64_t RegistryID(HIDRef service) {
    CFTypeRef value = service ? api.registry(service) : NULL;
    int64_t number = 0;
    if (value && CFGetTypeID(value) == CFNumberGetTypeID())
        CFNumberGetValue(value, kCFNumberSInt64Type, &number);
    return (uint64_t)number;
}
static void ObserveEvent(void *target, void *refcon, HIDRef service, HIDRef event) {
    (void)target; (void)refcon;
    if (api.type(event) != 11 || observed >= 16) return;
    observed++;
    HIDRef finger = event;
    CFArrayRef children = api.children(event);
    if (children && CFArrayGetCount(children)) finger = CFArrayGetValueAtIndex(children, 0);
    printf("observe monotonic=%.6f sender=0x%llx service=0x%llx "
        "x=%.7f y=%.7f range=%ld touch=%ld mask=0x%lx integrated=%ld\n",
        Now(), api.sender(event), RegistryID(service),
        api.getFloat(finger, 0xb0000), api.getFloat(finger, 0xb0001),
        api.getInteger(finger, 0xb0008), api.getInteger(finger, 0xb0009),
        api.getInteger(finger, 0xb0007), api.getInteger(event, 0xb0019));
    fflush(stdout);
}
static NSDictionary *TouchMatching(void) {
    return @{@"PrimaryUsagePage": @13, @"PrimaryUsage": @4,
        @"Transport": @"SPI", @"Built-In": @YES};
}
static int Metadata(void) {
    HIDRef client = api.createClient(kCFAllocatorDefault, 1, NULL);
    if (!client) return 69;
    api.matching(client, (__bridge CFDictionaryRef)TouchMatching());
    CFArrayRef services = api.services(client);
    CFIndex count = services ? CFArrayGetCount(services) : 0;
    printf("metadata monitor-created=YES matching-services=%ld injected=0\n", count);
    for (CFIndex i = 0; i < MIN(count, 4); i++) {
        HIDRef service = CFArrayGetValueAtIndex(services, i);
        NSMutableDictionary *record = [NSMutableDictionary dictionary];
        record[@"registry_id"] = [NSString stringWithFormat:@"0x%llx", RegistryID(service)];
        for (NSString *key in @[@"PrimaryUsagePage", @"PrimaryUsage", @"Transport", @"Built-In",
            @"DisplayIntegrated", @"GraphicsOrientation", @"ProtectedAccess", @"IOClass"]) {
            CFTypeRef value = api.property(service, (__bridge CFStringRef)key);
            if (value) record[key] = CFBridgingRelease(value);
        }
        NSData *json = [NSJSONSerialization dataWithJSONObject:record options:0 error:NULL];
        if (json) printf("service=%.*s\n", (int)json.length, (const char *)json.bytes);
        fflush(stdout);
        // A query only; this does not manufacture or dispatch a contact. A
        // missing cached event is expected and is not a fabricated sender.
        HIDRef event = api.copyEvent(service, 11, NULL, 0);
        if (event) {
            printf("copied-event type=%u sender=0x%llx\n", api.type(event), api.sender(event));
            ObserveEvent(NULL, NULL, service, event);
            CFRelease(event);
        } else printf("copied-event unavailable\n");
    }
    if (services) CFRelease(services);
    CFRelease(client);
    return count == 1 ? 0 : 75;
}
static int Observe(double deadline) {
    HIDRef client = api.createClient(kCFAllocatorDefault, 1, NULL); // Monitor; never a filter.
    if (!client) { fprintf(stderr, "monitor client unavailable\n"); return 69; }
    api.matching(client, (__bridge CFDictionaryRef)TouchMatching());
    api.registerCallback(client, ObserveEvent, NULL, NULL);
    api.schedule(client, CFRunLoopGetCurrent(), kCFRunLoopDefaultMode);
    while (!interrupted) {
        double remaining = deadline - Now();
        if (remaining <= 0) break;
        CFRunLoopRunInMode(kCFRunLoopDefaultMode, MIN(.02, remaining), false);
    }
    api.unschedule(client, CFRunLoopGetCurrent(), kCFRunLoopDefaultMode);
    api.unregisterCallback(client);
    CFRelease(client);
    fprintf(stderr, "observe complete events=%lu injected=0\n", (unsigned long)observed);
    return observed ? 0 : 75;
}

static HIDRef TouchEvent(uint64_t sender, double x, double y, int phase) {
    uint32_t contact = phase != 2;
    uint32_t mask = phase == 1 ? 4 : (1 | 2 | 32);
    uint64_t timestamp = mach_absolute_time();
    HIDRef hand = api.hand(kCFAllocatorDefault, timestamp, 3, 0, 0, mask, 0,
        0, 0, 0, 0, 0, contact, contact, 0);
    if (!hand) return NULL;
    HIDRef finger = api.finger(kCFAllocatorDefault, timestamp, 5, 5, mask,
        x, y, 0, 0, 0, contact, contact, 0);
    if (!finger) { CFRelease(hand); return NULL; }
    api.setInteger(hand, 0xb0019, 1); // Display-integrated collection.
    api.setInteger(hand, 4, 1);       // Built-in device event.
    api.setFloat(finger, 0xb0014, .04);
    api.setFloat(finger, 0xb0015, .04);
    api.setSender(hand, sender);
    api.append(hand, finger, 0);
    CFRelease(finger);
    return hand;
}
static int SendDrag(uint64_t sender, double x0, double y0, double x1, double y1,
                    double duration, double midpointPause, double initialHold,
                    double endingHold) {
    HIDRef client = api.createSimple(kCFAllocatorDefault);
    if (!client) return 69;
    // Prepare release before touch-down, so any later allocation failure can
    // still terminate the gesture. No persistent contact or background loop.
    HIDRef release = TouchEvent(sender, x0, y0, 2);
    HIDRef down = TouchEvent(sender, x0, y0, 0);
    if (!release || !down) {
        if (release) CFRelease(release);
        if (down) CFRelease(down);
        CFRelease(client);
        return 71;
    }
    double started = Now();
    api.dispatch(client, down);
    CFRelease(down);
    WaitUntil(started + .05);
    NSUInteger steps = (NSUInteger)ceil(duration * 60);
    NSUInteger emittedMoves = 0;
    double lastX = x0, lastY = y0;
    int status = 0;
    for (NSUInteger i = 1; i <= steps && !interrupted; i++) {
        double progress = (double)i / steps;
        // One uninterrupted contact, including the pause. This exercises a
        // native resize that stops moving without a finger-up; a sequence of
        // independent drags cannot detect premature reverse settlement.
        double pauseOffset = i > steps / 2 ? midpointPause : 0;
        WaitUntil(started + .05 + initialHold + duration * progress + pauseOffset);
        if (interrupted) break;
        double nextX = x0 + (x1 - x0) * progress;
        double nextY = y0 + (y1 - y0) * progress;
        HIDRef move = TouchEvent(sender, nextX, nextY, 1);
        if (!move) { status = 71; break; }
        api.dispatch(client, move);
        lastX = nextX; lastY = nextY;
        emittedMoves++;
        CFRelease(move);
    }
    if (!interrupted && status == 0 && endingHold > 0.0)
        WaitUntil(started + .05 + initialHold + duration + endingHold);
    // On interruption, release exactly where the last event landed; do not
    // jump to the originally requested endpoint. Refresh the child timestamp
    // too: the fallback release was allocated before the gesture began.
    uint64_t releaseTime = mach_absolute_time();
    api.setTime(release, releaseTime);
    CFArrayRef releaseChildren = api.children(release);
    if (releaseChildren && CFArrayGetCount(releaseChildren)) {
        HIDRef releaseFinger = CFArrayGetValueAtIndex(releaseChildren, 0);
        api.setFloat(releaseFinger, 0xb0000, lastX);
        api.setFloat(releaseFinger, 0xb0001, lastY);
        api.setTime(releaseFinger, releaseTime);
    }
    api.dispatch(client, release);
    CFRelease(release);
    CFRelease(client);
    fprintf(stderr, "dispatch-finished elapsed=%.3f moves=%lu planned-moves=%lu interrupted=%d "
        "visual-acceptance=UNVERIFIED\n", Now() - started,
        (unsigned long)emittedMoves, (unsigned long)steps, interrupted != 0);
    return interrupted ? 130 : status;
}

// Two stationary contacts in one hardware report. This exercises UIKit's
// two-finger recognizers, not a synthetic AppInput secondary-click shortcut.
// Allocate the terminal report first and always release both contacts.
static HIDRef TwoFingerEvent(uint64_t sender, double x0, double y0,
                            double x1, double y1, BOOL released) {
    HIDRef hand = TouchEvent(sender, x0, y0, released ? 2 : 0);
    if (!hand) return NULL;
    HIDRef second = api.finger(kCFAllocatorDefault, mach_absolute_time(),
        6, 6, 1 | 2 | 32, x1, y1, 0, 0, 0, !released, !released, 0);
    if (!second) { CFRelease(hand); return NULL; }
    api.setFloat(second, 0xb0014, .04);
    api.setFloat(second, 0xb0015, .04);
    api.append(hand, second, 0);
    CFRelease(second);
    return hand;
}

static int SendTwoFingerHold(uint64_t sender, double x0, double y0,
                             double x1, double y1, double duration) {
    HIDRef client = api.createSimple(kCFAllocatorDefault);
    HIDRef release = TwoFingerEvent(sender, x0, y0, x1, y1, YES);
    HIDRef down = TwoFingerEvent(sender, x0, y0, x1, y1, NO);
    if (!client || !release || !down) {
        if (client) CFRelease(client);
        if (release) CFRelease(release);
        if (down) CFRelease(down);
        return 71;
    }
    double started = Now();
    api.dispatch(client, down);
    WaitUntil(started + duration);
    uint64_t ended = mach_absolute_time();
    api.setTime(release, ended);
    CFArrayRef children = api.children(release);
    for (CFIndex i = 0; children && i < CFArrayGetCount(children); i++)
        api.setTime(CFArrayGetValueAtIndex(children, i), ended);
    api.dispatch(client, release);
    CFRelease(down);
    CFRelease(release);
    CFRelease(client);
    fprintf(stderr, "two-finger-hold elapsed=%.3f contacts=2 released=2 interrupted=%d "
        "visual-acceptance=UNVERIFIED\n", Now() - started, interrupted != 0);
    return interrupted ? 130 : 0;
}

// Native three-contact swipe for iPadOS Mission Control/App Exposé admission.
// Keep identities 5/6/7 stable for the entire transaction and allocate the
// release report before any down so an allocation failure cannot strand touches.
static HIDRef ThreeFingerEvent(uint64_t sender, double x, double y, int phase) {
    HIDRef hand = TouchEvent(sender, x, y, phase);
    if (!hand) return NULL;
    uint32_t contact = phase != 2;
    uint32_t mask = phase == 1 ? 4 : (1 | 2 | 32);
    for (uint32_t identity = 6; identity <= 7; identity++) {
        double offset = identity == 6 ? -.035 : .035;
        HIDRef finger = api.finger(kCFAllocatorDefault, mach_absolute_time(),
            identity, identity, mask, x + offset, y, 0, 0, 0,
            contact, contact, 0);
        if (!finger) { CFRelease(hand); return NULL; }
        api.setFloat(finger, 0xb0014, .04);
        api.setFloat(finger, 0xb0015, .04);
        api.append(hand, finger, 0);
        CFRelease(finger);
    }
    return hand;
}

static int SendThreeFingerSwipe(uint64_t sender, double x0, double y0,
                                double x1, double y1, double duration) {
    HIDRef client = api.createSimple(kCFAllocatorDefault);
    HIDRef release = ThreeFingerEvent(sender, x0, y0, 2);
    HIDRef down = ThreeFingerEvent(sender, x0, y0, 0);
    if (!client || !release || !down) {
        if (client) CFRelease(client);
        if (release) CFRelease(release);
        if (down) CFRelease(down);
        return 71;
    }
    double started = Now();
    api.dispatch(client, down);
    CFRelease(down);
    NSUInteger steps = (NSUInteger)ceil(duration * 60);
    NSUInteger emitted = 0;
    double lastX = x0, lastY = y0;
    int status = 0;
    for (NSUInteger i = 1; i <= steps && !interrupted; i++) {
        WaitUntil(started + duration * (double)i / steps);
        if (interrupted) break;
        double progress = (double)i / steps;
        double x = x0 + (x1 - x0) * progress;
        double y = y0 + (y1 - y0) * progress;
        HIDRef move = ThreeFingerEvent(sender, x, y, 1);
        if (!move) { status = 71; break; }
        api.dispatch(client, move);
        CFRelease(move);
        lastX = x; lastY = y;
        emitted++;
    }
    uint64_t ended = mach_absolute_time();
    api.setTime(release, ended);
    CFArrayRef children = api.children(release);
    for (CFIndex i = 0; children && i < CFArrayGetCount(children); i++) {
        HIDRef finger = CFArrayGetValueAtIndex(children, i);
        double offset = i == 1 ? -.035 : (i == 2 ? .035 : 0);
        api.setFloat(finger, 0xb0000, lastX + offset);
        api.setFloat(finger, 0xb0001, lastY);
        api.setTime(finger, ended);
    }
    api.dispatch(client, release);
    CFRelease(release);
    CFRelease(client);
    fprintf(stderr, "three-finger-swipe elapsed=%.3f moves=%lu planned=%lu "
        "released=3 interrupted=%d visual-acceptance=UNVERIFIED\n",
        Now() - started, (unsigned long)emitted, (unsigned long)steps,
        interrupted != 0);
    return interrupted ? 130 : status;
}

int main(int argc, char **argv) {
    @autoreleasepool {
        signal(SIGINT, Interrupted);
        signal(SIGTERM, Interrupted);
        if (argc == 2 && strcmp(argv[1], "--metadata") == 0) {
            signal(SIGALRM, ObservationExpired);
            struct itimerval cap = {{0, 0}, {2, 0}};
            if (setitimer(ITIMER_REAL, &cap, NULL)) return 71;
            return LoadAPI(YES) ? Metadata() : 69;
        }
        if (argc == 3 && strcmp(argv[1], "--observe") == 0) {
            char *end = NULL;
            double seconds = strtod(argv[2], &end);
            if (!*argv[2] || *end || !isfinite(seconds) || seconds < .05 || seconds > 2) return 64;
            // The monitor is read-only, so a hard process deadline cannot
            // strand an injected contact. Include loading/setup in the cap.
            signal(SIGALRM, ObservationExpired);
            struct itimerval cap = {{0, 0}, {2, 0}};
            if (setitimer(ITIMER_REAL, &cap, NULL)) return 71;
            double deadline = Now() + MIN(seconds, 1.9);
            return LoadAPI(YES) ? Observe(deadline) : 69;
        }
        BOOL paused = argc == 9 && strcmp(argv[1], "--send-paused-drag") == 0;
        BOOL held = argc == 9 && strcmp(argv[1], "--send-hold-drag") == 0;
        BOOL dropDwell = argc == 9 && strcmp(argv[1], "--send-drop-dwell") == 0;
        BOOL holdDropDwell = argc == 10 &&
            strcmp(argv[1], "--send-hold-drop-dwell") == 0;
        BOOL twoFinger = argc == 8 && strcmp(argv[1], "--send-two-finger-hold") == 0;
        BOOL threeFinger = argc == 8 && strcmp(argv[1], "--send-three-finger-swipe") == 0;
        BOOL execute = twoFinger || threeFinger || paused || held ||
            dropDwell || holdDropDwell ||
            (argc == 8 && strcmp(argv[1], "--send-drag") == 0);
        BOOL describe = argc == 8 && strcmp(argv[1], "--describe-drag") == 0;
        if (!execute && !describe) {
            fprintf(stderr, "usage: ios_hid_touch_probe --metadata\n"
                "   or: --observe SECONDS (0.05..2)\n"
                "   or: --describe-drag SENDER X0 Y0 X1 Y1 DURATION\n"
                "   or: --send-drag SENDER X0 Y0 X1 Y1 DURATION\n"
                "   or: --send-paused-drag SENDER X0 Y0 X1 Y1 DURATION PAUSE\n"
                "   or: --send-hold-drag SENDER X0 Y0 X1 Y1 DURATION HOLD\n"
                "   or: --send-drop-dwell SENDER X0 Y0 X1 Y1 DURATION DWELL\n"
                "   or: --send-hold-drop-dwell SENDER X0 Y0 X1 Y1 DURATION HOLD DWELL\n"
                "   or: --send-two-finger-hold SENDER X0 Y0 X1 Y1 DURATION\n"
                "   or: --send-three-finger-swipe SENDER X0 Y0 X1 Y1 DURATION\n"
                "Coordinates are normalized raw sensor space, NOT screenshot pixels.\n"
                "Use a sender and coordinate transform verified by --observe.\n"
                "Duration 0.1..1.5 seconds; do not overlap physical touches.\n");
            return 64;
        }
        char *end = NULL;
        errno = 0;
        uint64_t sender = strtoull(argv[2], &end, 0);
        if (argv[2][0] == '-' || !*argv[2] || *end || !sender || errno == ERANGE) return 64;
        double values[5];
        for (int i = 0; i < 5; i++) {
            values[i] = strtod(argv[i + 3], &end);
            if (!*argv[i + 3] || *end || !isfinite(values[i])) return 64;
            if (i < 4 && (values[i] < 0 || values[i] > 1)) return 64;
        }
        if (values[4] < .1 || values[4] > 1.5) return 64;
        if (threeFinger &&
            (values[0] < .035 || values[0] > .965 ||
             values[2] < .035 || values[2] > .965)) return 64;
        double midpointPause = 0;
        if (paused || held || dropDwell || holdDropDwell) {
            midpointPause = strtod(argv[8], &end);
            if (!*argv[8] || *end || !isfinite(midpointPause) ||
                midpointPause < .1 || midpointPause > .6) return 64;
        }
        double finalPause = 0;
        if (holdDropDwell) {
            finalPause = strtod(argv[9], &end);
            if (!*argv[9] || *end || !isfinite(finalPause) ||
                finalPause < .1 || finalPause > .6) return 64;
        }
        printf("drag sender=0x%llx raw-from=%.6f,%.6f raw-to=%.6f,%.6f "
            "duration=%.3f execute=%s\n", sender, values[0], values[1],
            values[2], values[3], values[4], execute ? "YES" : "NO");
        fflush(stdout);
        if (paused) fprintf(stderr, "midpoint-pause=%.3f contact-held=YES\n", midpointPause);
        if (held) fprintf(stderr, "initial-hold=%.3f contact-held=YES\n", midpointPause);
        if (dropDwell) fprintf(stderr, "drop-dwell=%.3f contact-held=YES\n", midpointPause);
        if (holdDropDwell) fprintf(stderr, "initial-hold=%.3f drop-dwell=%.3f contact-held=YES\n", midpointPause, finalPause);
        if (describe) return 0;
        if (twoFinger) return LoadAPI(NO) ? SendTwoFingerHold(sender,
            values[0], values[1], values[2], values[3], values[4]) : 69;
        if (threeFinger) return LoadAPI(NO) ? SendThreeFingerSwipe(sender,
            values[0], values[1], values[2], values[3], values[4]) : 69;
        return LoadAPI(NO) ? SendDrag(sender, values[0], values[1], values[2], values[3], values[4], paused ? midpointPause : 0, (held || holdDropDwell) ? midpointPause : 0, dropDwell ? midpointPause : finalPause) : 69;
    }
}

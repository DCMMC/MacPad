#import <ApplicationServices/ApplicationServices.h>
#import <AppKit/AppKit.h>
#import <Foundation/Foundation.h>
#import <mach/mach_time.h>
#include <sched.h>

static CGRect MacWSLargestWindowForPID(pid_t pid) {
    CGRect best = CGRectNull;
    CGFloat bestArea = 0.0;
    CFArrayRef windows = CGWindowListCopyWindowInfo(
        kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements,
        kCGNullWindowID);
    for (NSDictionary *entry in (__bridge NSArray *)windows) {
        // A native macOS fullscreen Space is not required to remain on
        // layer 0.  Stray's 1440x900 visible game surface is layer 25 while
        // its layer-0 helper windows are offscreen 1440x30 strips.  The
        // CGWindow query is already restricted to on-screen, non-desktop
        // windows, so constrain by owner and select the largest surface
        // instead of assuming a layer number.
        if ([entry[(id)kCGWindowOwnerPID] intValue] != pid) continue;
        CGRect bounds = CGRectZero;
        if (!CGRectMakeWithDictionaryRepresentation(
                (__bridge CFDictionaryRef)entry[(id)kCGWindowBounds],
                &bounds)) continue;
        CGFloat area = bounds.size.width * bounds.size.height;
        if (area > bestArea) {
            best = bounds;
            bestArea = area;
        }
    }
    if (windows) CFRelease(windows);
    return best;
}

static void MacWSPostMouse(pid_t pid, CGEventType type, CGPoint point,
                           CGMouseButton button) {
    (void)pid;
    CGEventRef event = CGEventCreateMouseEvent(NULL, type, point, button);
    if (!event) return;
    CGEventSetIntegerValueField(event, kCGMouseEventClickState, 1);
    // Post through WindowServer's ordinary HID event tap.  CGEventPostToPid
    // reports success but does not participate in AppKit's mouse target and
    // pressed-button state machine on current macOS; that is a different and
    // unfair path from the hardware trackpad baseline this tool measures.
    // The destination is still constrained by the discovered InputLab window
    // geometry, and the caller must have Accessibility event-post access.
    CGEventPost(kCGHIDEventTap, event);
    CFRelease(event);
}

static void MacWSWaitUntil(uint64_t deadline, mach_timebase_info_data_t timebase) {
    for (;;) {
        uint64_t now = mach_absolute_time();
        if (now >= deadline) return;
        uint64_t remaining = deadline - now;
        uint64_t nanos = remaining * timebase.numer / timebase.denom;
        if (nanos > 2000000) {
            struct timespec sleep = {
                .tv_sec = 0,
                .tv_nsec = (long)(nanos - 1000000),
            };
            nanosleep(&sleep, NULL);
        } else {
            sched_yield();
        }
    }
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc == 2 && strcmp(argv[1], "--preflight") == 0) {
            printf("{\"accessibility_preflight\":%s}\n",
                   CGPreflightPostEventAccess() ? "true" : "false");
            return CGPreflightPostEventAccess() ? 0 : 3;
        }
        if (argc == 4 && strcmp(argv[1], "--click") == 0) {
            CGPoint point = CGPointMake(strtod(argv[2], NULL),
                                        strtod(argv[3], NULL));
            MacWSPostMouse(getpid(), kCGEventLeftMouseDown, point,
                           kCGMouseButtonLeft);
            usleep(30000);
            MacWSPostMouse(getpid(), kCGEventLeftMouseUp, point,
                           kCGMouseButtonLeft);
            return 0;
        }
        if (argc == 5 && strcmp(argv[1], "--key-hold") == 0) {
            pid_t pid = (pid_t)strtol(argv[2], NULL, 10);
            CGKeyCode key = (CGKeyCode)strtoul(argv[3], NULL, 10);
            double duration = strtod(argv[4], NULL);
            if (pid <= 1 || key > 127 || duration <= 0 || duration > 300)
                return 64;
            NSRunningApplication *application =
                [NSRunningApplication runningApplicationWithProcessIdentifier:pid];
            [application activateWithOptions:0];
            [NSThread sleepForTimeInterval:0.15];
            CGEventRef down = CGEventCreateKeyboardEvent(NULL, key, true);
            CGEventRef up = CGEventCreateKeyboardEvent(NULL, key, false);
            if (down == NULL || up == NULL) return 2;
            CGEventPost(kCGHIDEventTap, down);
            [NSThread sleepForTimeInterval:duration];
            CGEventPost(kCGHIDEventTap, up);
            CFRelease(down);
            CFRelease(up);
            printf("{\"result\":\"SENT\",\"pid\":%d,"
                   "\"key_code\":%u,\"duration_s\":%.3f,"
                   "\"accessibility_preflight\":%s}\n",
                   pid, key, duration,
                   CGPreflightPostEventAccess() ? "true" : "false");
            return 0;
        }
        BOOL mouseMove = argc == 5 &&
            strcmp(argv[1], "--mouse-move") == 0;
        int argumentOffset = mouseMove ? 1 : 0;
        if ((!mouseMove && argc != 4) || (mouseMove && argc != 5)) {
            fprintf(stderr,
                    "usage: %s PID DURATION_SECONDS HZ | "
                    "--mouse-move PID DURATION_SECONDS HZ | "
                    "--key-hold PID KEYCODE DURATION_SECONDS | "
                    "--click X Y | --preflight\n",
                    argv[0]);
            return 64;
        }
        pid_t pid = (pid_t)strtol(argv[1 + argumentOffset], NULL, 10);
        double duration = strtod(argv[2 + argumentOffset], NULL);
        double hz = strtod(argv[3 + argumentOffset], NULL);
        if (pid <= 1 || duration <= 0 || hz <= 0 || hz > 1000) return 64;

        CGRect bounds = MacWSLargestWindowForPID(pid);
        if (CGRectIsNull(bounds) || CGRectIsEmpty(bounds)) {
            fprintf(stderr, "no visible layer-0 window for pid %d\n", pid);
            return 2;
        }
        NSRunningApplication *application =
            [NSRunningApplication runningApplicationWithProcessIdentifier:pid];
        [application activateWithOptions:0];
        [NSThread sleepForTimeInterval:0.15];
        // InputLab's canvas fills the upper portion of the content view.  A
        // horizontal sweep through the visual center remains safely outside
        // the title bar and native controls on both the MacBook and iPad.
        CGFloat left = CGRectGetMinX(bounds) + bounds.size.width * 0.30;
        CGFloat right = CGRectGetMinX(bounds) + bounds.size.width * 0.75;
        CGFloat y = CGRectGetMinY(bounds) + bounds.size.height * 0.46;
        CGPoint start = CGPointMake(left, y);

        mach_timebase_info_data_t timebase;
        mach_timebase_info(&timebase);
        NSUInteger samples = MAX(2, (NSUInteger)llround(duration * hz));
        uint64_t interval = (uint64_t)llround(
            (1e9 / hz) * timebase.denom / timebase.numer);
        uint64_t began = mach_absolute_time();
        if (!mouseMove)
            MacWSPostMouse(pid, kCGEventLeftMouseDown, start,
                           kCGMouseButtonLeft);
        uint64_t deadline = mach_absolute_time();
        for (NSUInteger index = 0; index < samples; index++) {
            double phase = (double)index / MAX(1.0, (double)samples - 1.0) * 3.0;
            double fraction = fmod(phase, 1.0);
            if (((NSUInteger)phase) & 1) fraction = 1.0 - fraction;
            CGPoint point = CGPointMake(
                left + fraction * (right - left),
                y + 35.0 * sin((double)index * 2.0 * M_PI / 60.0));
            MacWSPostMouse(pid,
                           mouseMove ? kCGEventMouseMoved :
                               kCGEventLeftMouseDragged,
                           point,
                           kCGMouseButtonLeft);
            deadline += interval;
            MacWSWaitUntil(deadline, timebase);
        }
        if (!mouseMove)
            MacWSPostMouse(pid, kCGEventLeftMouseUp, start,
                           kCGMouseButtonLeft);
        uint64_t ended = mach_absolute_time();
        double elapsed = (double)(ended - began) * timebase.numer /
            timebase.denom / 1e9;
        NSDictionary *result = @{
            @"result": @"SENT",
            @"transport": @"macOS WindowServer HID event tap",
            @"input_mode": mouseMove ? @"mouse-move" : @"left-drag",
            @"pid": @(pid),
            @"requested_hz": @(hz),
            @"duration_s": @(elapsed),
            @"move_records_sent": @(samples),
            @"window_bounds": @{
                @"x": @(bounds.origin.x), @"y": @(bounds.origin.y),
                @"width": @(bounds.size.width),
                @"height": @(bounds.size.height),
            },
            @"accessibility_preflight": @(CGPreflightPostEventAccess()),
        };
        NSData *json = [NSJSONSerialization dataWithJSONObject:result
            options:NSJSONWritingPrettyPrinted error:nil];
        fwrite(json.bytes, 1, json.length, stdout);
        fputc('\n', stdout);
    }
    return 0;
}

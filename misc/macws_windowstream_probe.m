#import <AppKit/AppKit.h>
#import <CoreGraphics/CoreGraphics.h>
#import <IOSurface/IOSurfaceRef.h>

#include <dlfcn.h>
#include <stdbool.h>
#include <stdio.h>

typedef CGDisplayStreamRef (*MacWSWindowStreamCreate)(
    uint32_t windowID, bool useFrameShape, CFDictionaryRef properties,
    dispatch_queue_t queue, CGDisplayStreamFrameAvailableHandler handler);

static bool ProbeFlag(MacWSWindowStreamCreate createStream, uint32_t windowID,
                      bool useFrameShape) {
    dispatch_queue_t queue = dispatch_get_main_queue();
    __block size_t width = 0;
    __block size_t height = 0;
    NSDictionary *properties = @{
        (__bridge id)kCGDisplayStreamQueueDepth: @1,
        (__bridge id)kCGDisplayStreamShowCursor: @NO,
        (__bridge id)kCGDisplayStreamMinimumFrameTime: @(1.0 / 30.0),
    };
    CGDisplayStreamRef stream = createStream(windowID, useFrameShape,
        (__bridge CFDictionaryRef)properties, queue,
        ^(CGDisplayStreamFrameStatus status, uint64_t displayTime,
          IOSurfaceRef surface, CGDisplayStreamUpdateRef update) {
            (void)displayTime;
            (void)update;
            if (status == kCGDisplayStreamFrameStatusFrameComplete && surface) {
                width = IOSurfaceGetWidth(surface);
                height = IOSurfaceGetHeight(surface);
            }
        });
    if (!stream) {
        fprintf(stderr, "probe flag=%d create=NULL\n", useFrameShape);
        return false;
    }
    CGError start = CGDisplayStreamStart(stream);
    NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:3.0];
    while (start == kCGErrorSuccess && width == 0 &&
           deadline.timeIntervalSinceNow > 0) {
        [NSRunLoop.currentRunLoop runMode:NSDefaultRunLoopMode
                              beforeDate:[NSDate dateWithTimeIntervalSinceNow:0.05]];
    }
    long wait = width && height ? 0 : 1;
    CGDisplayStreamStop(stream);
    CFRelease(stream);
    fprintf(stderr, "probe flag=%d start=%d frame=%zux%zu wait=%ld\n",
            useFrameShape, start, width, height, wait);
    return start == kCGErrorSuccess && wait == 0 && width && height;
}

int main(void) {
    @autoreleasepool {
        NSApplication *application = NSApplication.sharedApplication;
        [application setActivationPolicy:NSApplicationActivationPolicyRegular];
        [application finishLaunching];
        NSWindow *window = [[NSWindow alloc]
            initWithContentRect:NSMakeRect(0, 0, 480, 300)
                      styleMask:NSWindowStyleMaskTitled |
                                NSWindowStyleMaskClosable |
                                NSWindowStyleMaskResizable
                        backing:NSBackingStoreBuffered
                          defer:NO];
        window.title = @"MacWS DisplayStream Probe";
        [window center];
        [window orderFrontRegardless];
        [application activateIgnoringOtherApps:YES];
        [window displayIfNeeded];
        [NSRunLoop.currentRunLoop runUntilDate:
            [NSDate dateWithTimeIntervalSinceNow:0.2]];

        void *skyLight = dlopen(
            "/System/Library/PrivateFrameworks/SkyLight.framework/SkyLight",
            RTLD_LAZY | RTLD_LOCAL);
        MacWSWindowStreamCreate createStream = (MacWSWindowStreamCreate)dlsym(
            skyLight ?: RTLD_DEFAULT,
            "SLSHWCaptureStreamCreateWithWindow");
        if (!createStream || window.windowNumber <= 0) {
            fprintf(stderr, "probe unavailable symbol=%p window=%ld\n",
                    createStream, (long)window.windowNumber);
            return 2;
        }

        CGRect frame = window.frame;
        fprintf(stderr,
                "probe window=%ld appkit-frame=(%.0f,%.0f %.0fx%.0f) "
                "backing-scale=%.2f\n",
                (long)window.windowNumber, frame.origin.x, frame.origin.y,
                frame.size.width, frame.size.height,
                window.backingScaleFactor);
        bool contentOK = ProbeFlag(createStream,
                                   (uint32_t)window.windowNumber, false);
        bool frameOK = ProbeFlag(createStream,
                                 (uint32_t)window.windowNumber, true);
        if (skyLight) dlclose(skyLight);
        return contentOK && frameOK ? 0 : 1;
    }
}

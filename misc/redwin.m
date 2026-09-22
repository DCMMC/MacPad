// redwin — the most minimal possible window-compositing test. A borderless window whose
// own backing is solid red (no content view, no drawRect, no Metal, no CALayer of ours).
// If THIS is black on VNC, the window backing surface is not being composited to screen at
// all -> the bug is the app-window-IOSurface -> WindowServer handoff (WSCompositeDestination).
// If RED, basic compositing works and only richer content paths break.
@import Cocoa;
#import <stdio.h>

@interface D : NSObject <NSApplicationDelegate> { NSWindow *_w; } @end
@implementation D
- (void)applicationDidFinishLaunching:(NSNotification *)n {
    // Fixed startup delay so a debugger can attach + arm breakpoints BEFORE the window/
    // surface is created (env REDWIN_DELAY overrides; default 18s — env doesn't survive
    // launchdchrootexec so a baked default is needed).
    const char *dly = getenv("REDWIN_DELAY");
    int s = dly ? atoi(dly) : 18;
    fprintf(stderr, "REDWIN: sleeping %ds for debugger attach before window...\n", s);
    fflush(stderr);
    sleep(s);
    fprintf(stderr, "REDWIN: now creating window\n"); fflush(stderr);
    NSRect f = NSMakeRect(150, 150, 500, 350);
    _w = [[NSWindow alloc] initWithContentRect:f
            styleMask:NSWindowStyleMaskBorderless
            backing:NSBackingStoreBuffered defer:NO];
    [_w setBackgroundColor:[NSColor redColor]];
    [_w setOpaque:YES];
    [_w setLevel:NSFloatingWindowLevel];        // float above everything so it's unmistakable
    [_w setContentView:[[NSView alloc] initWithFrame:f]];  // plain view, no drawing
    [_w makeKeyAndOrderFront:nil];
    [_w center];
    fprintf(stderr, "REDWIN: borderless solid-red window shown, center, floating. RED or BLACK on VNC?\n");
    [NSApp activateIgnoringOtherApps:YES];
    // Recreate the window backing every 3s so a debugger attaching at any time still catches
    // the surface-creation calls (env REDWIN_LOOP=1).
    if (getenv("REDWIN_LOOP")) {
        [NSTimer scheduledTimerWithTimeInterval:3.0 repeats:YES block:^(NSTimer *t){
            fprintf(stderr, "REDWIN: forcing window redraw (recreate backing)\n");
            [_w setBackgroundColor:[NSColor redColor]];
            [[_w contentView] setNeedsDisplay:YES];
            [_w displayIfNeeded];
            // force a fresh backing store
            [_w setContentSize:NSMakeSize(500 + (arc4random()%3), 350)];
        }];
    }
}
@end
int main(int argc, char **argv) {
    @autoreleasepool {
        NSApplication *app = [NSApplication sharedApplication];
        [app setActivationPolicy:NSApplicationActivationPolicyRegular];
        [app setDelegate:[[D alloc] init]];
        [app run];
    }
    return 0;
}

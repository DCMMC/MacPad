// drawtest — a window whose content view DRAWS REAL CONTENT (text + shapes) into a
// layer-backed view. Unlike a solid backgroundColor (which needs no IOSurface), a layer
// with drawn content REQUIRES a backing IOSurface — so this exercises the real surface
// path that Terminal uses. If this view's content is BLACK on VNC, AND lldb shows
// CAIOSurfaceCreate IS hit, then the app creates the surface fine and the bug is
// WindowServer compositing it. If CAIOSurfaceCreate is NOT hit, the app-side surface
// allocation is the bug.
@import Cocoa;
#import <stdio.h>
#import <stdlib.h>

@interface DrawView : NSView @end
@implementation DrawView
- (BOOL)wantsUpdateLayer { return NO; }   // force drawRect path (needs backing surface)
- (void)drawRect:(NSRect)r {
    // real drawn content -> requires an IOSurface-backed layer
    [[NSColor redColor] setFill];
    NSRectFill(self.bounds);
    [[NSColor yellowColor] setFill];
    NSRectFill(NSMakeRect(40, 40, 200, 120));
    NSDictionary *attrs = @{ NSForegroundColorAttributeName: [NSColor blackColor],
                             NSFontAttributeName: [NSFont systemFontOfSize:28] };
    [@"HELLO MACOS" drawAtPoint:NSMakePoint(50, 150) withAttributes:attrs];
    fprintf(stderr, "DRAWTEST drawRect fired (real content drawn)\n"); fflush(stderr);
}
@end

@interface D : NSObject <NSApplicationDelegate> { NSWindow *_w; DrawView *_v; } @end
@implementation D
- (void)applicationDidFinishLaunching:(NSNotification *)n {
    { const char *d = getenv("DRAW_DELAY"); int s = d ? atoi(d) : 18;
      fprintf(stderr, "DRAWTEST: sleeping %ds for debugger attach...\n", s); fflush(stderr); sleep(s);
      fprintf(stderr, "DRAWTEST: creating window now\n"); fflush(stderr); }
    NSRect f = NSMakeRect(200, 200, 400, 300);
    _w = [[NSWindow alloc] initWithContentRect:f
            styleMask:(NSWindowStyleMaskTitled|NSWindowStyleMaskClosable)
            backing:NSBackingStoreBuffered defer:NO];
    [_w setTitle:@"DRAWTEST"];
    _v = [[DrawView alloc] initWithFrame:f];
    _v.wantsLayer = YES;   // layer-backed; drawRect content -> CA must allocate a backing IOSurface
    [_w setContentView:_v];
    [_w makeKeyAndOrderFront:nil];
    [_w center];
    fprintf(stderr, "DRAWTEST window shown — VNC: is the content RED with YELLOW box + text, or BLACK?\n");
    [NSApp activateIgnoringOtherApps:YES];
    // keep redrawing so a debugger catches the surface path repeatedly
    [NSTimer scheduledTimerWithTimeInterval:0.3 repeats:YES block:^(NSTimer *t){
        [_v setNeedsDisplay:YES];
    }];
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

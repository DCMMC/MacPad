// toygui — minimal Cocoa app to isolate WHY window CONTENT is black while chrome renders.
// Each mode produces window content a different way; whichever shows on VNC tells us which
// layer of the app->WindowServer compositing handoff works.
//   argv[1] = mode:
//     1 = NSView wantsLayer + layer.backgroundColor RED   (pure CoreAnimation, no drawRect, no Metal)
//     2 = NSView drawRect: CoreGraphics RED fill           (CG software draw -> backing store)
//     3 = CAMetalLayer rendering RED                        (Metal -> IOSurface drawable -> layer)
// Window is placed at a fixed visible spot. Runs the app loop so you can VNC and look.
@import Cocoa;
@import QuartzCore;
@import Metal;
#import <stdio.h>
#import <stdlib.h>

static int g_mode = 1;

@interface CGView : NSView @end
@implementation CGView
- (void)drawRect:(NSRect)r {
    [[NSColor redColor] setFill];
    NSRectFill(self.bounds);
    fprintf(stderr, "TOYGUI drawRect fired, bounds=%.0fx%.0f\n", self.bounds.size.width, self.bounds.size.height);
}
@end

@interface MetalView : NSView { CAMetalLayer *_ml; id<MTLDevice> _dev; id<MTLCommandQueue> _q; NSTimer *_t; }
@end
@implementation MetalView
- (CALayer *)makeBackingLayer {
    _ml = [CAMetalLayer layer];
    _dev = MTLCreateSystemDefaultDevice();
    _ml.device = _dev;
    _ml.pixelFormat = MTLPixelFormatBGRA8Unorm;
    _ml.framebufferOnly = YES;
    _q = [_dev newCommandQueue];
    fprintf(stderr, "TOYGUI CAMetalLayer device=%p name=%s queue=%p\n", (void*)_dev, _dev?[[_dev name] UTF8String]:"NIL", (void*)_q);
    return _ml;
}
- (void)viewDidMoveToWindow {
    [super viewDidMoveToWindow];
    self.wantsLayer = YES;
    _ml.drawableSize = CGSizeMake(self.bounds.size.width, self.bounds.size.height);
    _t = [NSTimer scheduledTimerWithTimeInterval:0.2 target:self selector:@selector(render) userInfo:nil repeats:YES];
}
- (void)render {
    @autoreleasepool {
        id<CAMetalDrawable> d = [_ml nextDrawable];
        if (!d) { fprintf(stderr, "TOYGUI nextDrawable=NIL (no drawable -> black)\n"); return; }
        MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];
        rp.colorAttachments[0].texture = d.texture;
        rp.colorAttachments[0].loadAction = MTLLoadActionClear;
        rp.colorAttachments[0].storeAction = MTLStoreActionStore;
        rp.colorAttachments[0].clearColor = MTLClearColorMake(1, 0, 0, 1);
        id<MTLCommandBuffer> cb = [_q commandBuffer];
        id<MTLRenderCommandEncoder> e = [cb renderCommandEncoderWithDescriptor:rp];
        [e endEncoding];
        [cb presentDrawable:d];
        [cb commit];
        static int once=0; if(!once){once=1; fprintf(stderr, "TOYGUI Metal present committed (drawable=%p tex=%p)\n",(void*)d,(void*)d.texture);}
    }
}
@end

@interface AppDelegate : NSObject <NSApplicationDelegate> { NSWindow *_w; }
@end
@implementation AppDelegate
- (void)applicationDidFinishLaunching:(NSNotification *)n {
    { const char *d = getenv("TOY_DELAY"); int s = d ? atoi(d) : 18;
      fprintf(stderr, "TOYGUI: sleeping %ds for debugger attach...\n", s); fflush(stderr); sleep(s);
      fprintf(stderr, "TOYGUI: creating window now\n"); fflush(stderr); }
    NSRect frame = NSMakeRect(200, 200, 400, 300);
    _w = [[NSWindow alloc] initWithContentRect:frame
            styleMask:(NSWindowStyleMaskTitled|NSWindowStyleMaskClosable|NSWindowStyleMaskResizable)
            backing:NSBackingStoreBuffered defer:NO];
    [_w setTitle:[NSString stringWithFormat:@"TOYGUI mode %d", g_mode]];
    NSView *content = nil;
    if (g_mode == 1) {
        content = [[NSView alloc] initWithFrame:frame];
        content.wantsLayer = YES;
        content.layer.backgroundColor = [[NSColor redColor] CGColor];
        fprintf(stderr, "TOYGUI mode1: NSView layer.backgroundColor=red, layer=%p\n", (void*)content.layer);
    } else if (g_mode == 2) {
        content = [[CGView alloc] initWithFrame:frame];
        fprintf(stderr, "TOYGUI mode2: CGView drawRect red\n");
    } else {
        content = [[MetalView alloc] initWithFrame:frame];
        content.wantsLayer = YES;
        fprintf(stderr, "TOYGUI mode3: CAMetalLayer red\n");
    }
    [_w setContentView:content];
    [_w makeKeyAndOrderFront:nil];
    [_w center];
    fprintf(stderr, "TOYGUI window shown (mode %d) at center — LOOK AT VNC: is the content area RED or BLACK?\n", g_mode);
    [NSApp activateIgnoringOtherApps:YES];
    // Aggressively force CoreAnimation to commit every 100ms so a debugger can catch the
    // surface-creation path (toggle layer color + setNeedsDisplay + explicit CATransaction flush).
    __block int tick = 0;
    [NSTimer scheduledTimerWithTimeInterval:0.1 repeats:YES block:^(NSTimer *t){
        tick++;
        content.layer.backgroundColor = [[NSColor colorWithRed:1 green:(tick%2)*0.3 blue:0 alpha:1] CGColor];
        [content.layer setNeedsDisplay];
        [content setNeedsDisplay:YES];
        [CATransaction flush];
        if (tick == 1) { fprintf(stderr, "TOYGUI: forced CATransaction flush (tick1)\n"); fflush(stderr); }
    }];
}
- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication *)a { return YES; }
@end

int main(int argc, char **argv) {
    g_mode = (argc > 1) ? atoi(argv[1]) : 1;
    @autoreleasepool {
        NSApplication *app = [NSApplication sharedApplication];
        [app setActivationPolicy:NSApplicationActivationPolicyRegular];
        AppDelegate *d = [[AppDelegate alloc] init];
        [app setDelegate:d];
        [app run];
    }
    return 0;
}

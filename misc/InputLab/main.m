#import <Cocoa/Cocoa.h>
#import <QuartzCore/QuartzCore.h>
#import <objc/runtime.h>
#include <sys/stat.h>

static NSString *const MacWSEventLogPath = @"/tmp/macws_inputlab_events.jsonl";
static NSString *const MacWSStatePath = @"/tmp/macws_inputlab_state.json";

static BOOL MacWSInputLabDiagnosticsEnabled(void) {
    static dispatch_once_t onceToken;
    static BOOL enabled;
    dispatch_once(&onceToken, ^{
        enabled = getenv("MACWS_INPUTLAB_DIAGNOSTICS") != NULL;
    });
    return enabled;
}

@interface MacWSInputRecorder : NSObject
@property(nonatomic, strong) NSMutableDictionary<NSString *, NSNumber *> *counts;
@property(nonatomic, copy) NSString *lastEvent;
@property(nonatomic) uint64_t sequence;
@property(nonatomic, copy) void (^stateChanged)(NSDictionary *state);
@property(nonatomic) dispatch_queue_t ioQueue;
@property(nonatomic, strong) NSFileHandle *eventHandle;
@property(nonatomic, strong) NSDictionary *latestStateForIO;
@property(nonatomic) BOOL stateFlushScheduled;
- (void)record:(NSString *)name event:(NSEvent *)event details:(NSDictionary *)details;
@end

@implementation MacWSInputRecorder
- (instancetype)init {
    self = [super init];
    if (!self) return nil;
    _counts = [NSMutableDictionary dictionary];
    _lastEvent = @"ready";
    [[NSFileManager defaultManager] removeItemAtPath:MacWSEventLogPath error:nil];
    [[NSData data] writeToFile:MacWSEventLogPath atomically:YES];
    chmod(MacWSEventLogPath.fileSystemRepresentation, 0666);
    _eventHandle = [NSFileHandle fileHandleForWritingAtPath:MacWSEventLogPath];
    _ioQueue = dispatch_queue_create("com.macwsguide.inputlab.io",
                                     DISPATCH_QUEUE_SERIAL);
    return self;
}

- (void)record:(NSString *)name event:(NSEvent *)event details:(NSDictionary *)details {
    self.sequence++;
    self.counts[name] = @([self.counts[name] unsignedLongLongValue] + 1);
    self.lastEvent = name;
    NSPoint location = event ? event.locationInWindow : NSZeroPoint;
    NSTimeInterval now = NSProcessInfo.processInfo.systemUptime;
    NSEventType type = event ? event.type : 0;
    BOOL mouseButtonEvent = type >= NSEventTypeLeftMouseDown &&
        type <= NSEventTypeRightMouseDragged;
    NSTimeInterval eventTimestamp = event ? event.timestamp : 0;
    NSTimeInterval deliveryLatency = eventTimestamp > 0
        ? (now - eventTimestamp) * 1000.0 : 0;
    // NSEvent asserts when clickCount/buttonNumber are queried for a keyboard,
    // scroll or gesture event.  That assertion made the old recorder itself
    // hide every non-mouse event and looked exactly like a broken input route.
    // Also reject timestamps from a different boot clock instead of clamping
    // them to a misleading zero-latency result.
    BOOL latencyValid = eventTimestamp > 0 && deliveryLatency >= 0.0 &&
        deliveryLatency <= 10000.0;
    NSMutableDictionary *entry = [@{
        @"sequence": @(self.sequence),
        @"event": name,
        @"received_uptime": @(now),
        @"event_timestamp": @(eventTimestamp),
        @"latency_valid": @(latencyValid),
        @"latency_ms": @(latencyValid ? deliveryLatency : 0),
        @"window": @(event ? event.windowNumber : 0),
        @"x": @(location.x),
        @"y": @(location.y),
        @"modifiers": @(event ? event.modifierFlags : 0),
        @"click_count": @(mouseButtonEvent ? event.clickCount : 0),
        @"button": @(mouseButtonEvent ? event.buttonNumber : -1),
        @"pressed_buttons": @(NSEvent.pressedMouseButtons),
    } mutableCopy];
    if (details) [entry addEntriesFromDictionary:details];

    NSDictionary *state = @{
        @"sequence": @(self.sequence),
        @"last_event": self.lastEvent,
        @"counts": [self.counts copy],
        @"last": entry,
    };
    NSDictionary *entrySnapshot = [entry copy];
    NSDictionary *stateSnapshot = [state copy];
    dispatch_async(self.ioQueue, ^{
        NSData *json = [NSJSONSerialization dataWithJSONObject:entrySnapshot
                                                       options:0 error:nil];
        if (json && self.eventHandle) {
            // The regression runner intentionally truncates this public file
            // between scenarios. Seek on every background append so an open
            // descriptor follows that boundary without creating a sparse file.
            [self.eventHandle seekToEndOfFile];
            [self.eventHandle writeData:json];
            [self.eventHandle writeData:
                [@"\n" dataUsingEncoding:NSUTF8StringEncoding]];
        }
        self.latestStateForIO = stateSnapshot;
        if (!self.stateFlushScheduled) {
            self.stateFlushScheduled = YES;
            dispatch_after(dispatch_time(DISPATCH_TIME_NOW,
                                         16 * NSEC_PER_MSEC),
                           self.ioQueue, ^{
                NSDictionary *latest = self.latestStateForIO;
                self.latestStateForIO = nil;
                self.stateFlushScheduled = NO;
                NSData *stateJSON = [NSJSONSerialization
                    dataWithJSONObject:latest
                               options:NSJSONWritingPrettyPrinted error:nil];
                [stateJSON writeToFile:MacWSStatePath atomically:YES];
            });
        }
    });
    if (self.stateChanged) self.stateChanged(state);
}
@end

@interface MacWSInputCanvas : NSView
@property(nonatomic, strong) MacWSInputRecorder *recorder;
@property(nonatomic) NSPoint markerPoint;
@property(nonatomic) CGFloat scrollOffset;
@property(nonatomic) CGFloat magnification;
@end

@interface NSWindow (MacWSInputLabPrivateScrollWitness)
- (void)_latchViewForScrollEvent:(NSEvent *)event;
- (BOOL)_isViewScrolling;
- (void)_willBeginViewScrolling;
- (void)_didEndViewScrolling;
@end

// Boundary witness for synthetic scroll reconstruction. AppInputBridge enters
// NSWindow's ordinary dispatcher after resolving the exact captured window;
// recording here distinguishes a malformed NSEvent from a responder-routing
// failure without changing the event or invoking a view action directly.
@interface MacWSInputWindow : NSWindow
@property(nonatomic, weak) MacWSInputRecorder *inputRecorder;
@end

@implementation MacWSInputWindow
- (void)_latchViewForScrollEvent:(NSEvent *)event {
    BOOL diagnostics = MacWSInputLabDiagnosticsEnabled();
    if (diagnostics) {
        fprintf(stderr,
                "INPUTLAB NSWINDOW-LATCH phase=%lu momentum=%lu window=%ld "
                "scrolling-before=%s\n",
                (unsigned long)event.phase,
                (unsigned long)event.momentumPhase,
                (long)event.windowNumber,
                self._isViewScrolling ? "YES" : "NO");
    }
    [super _latchViewForScrollEvent:event];
    if (!diagnostics) return;
    fprintf(stderr, "INPUTLAB NSWINDOW-LATCH-RETURN scrolling-after=%s\n",
            self._isViewScrolling ? "YES" : "NO");
    unsigned int ivarCount = 0;
    Ivar *ivars = class_copyIvarList(NSWindow.class, &ivarCount);
    for (unsigned int index = 0; index < ivarCount; index++) {
        const char *name = ivar_getName(ivars[index]);
        const char *type = ivar_getTypeEncoding(ivars[index]);
        if (name && strcasestr(name, "scroll")) {
            id value = type && type[0] == '@'
                ? object_getIvar(self, ivars[index]) : nil;
            fprintf(stderr,
                    "INPUTLAB NSWINDOW-SCROLL-IVAR %s type=%s offset=%td "
                    "object=%s\n", name, type ?: "", ivar_getOffset(ivars[index]),
                    value ? object_getClassName(value) : "nil/nonobject");
        }
    }
    free(ivars);
}
- (void)_willBeginViewScrolling {
    if (MacWSInputLabDiagnosticsEnabled())
        fprintf(stderr, "INPUTLAB NSWINDOW-WILL-BEGIN-SCROLL\n");
    [super _willBeginViewScrolling];
}
- (void)_didEndViewScrolling {
    if (MacWSInputLabDiagnosticsEnabled())
        fprintf(stderr, "INPUTLAB NSWINDOW-DID-END-SCROLL\n");
    [super _didEndViewScrolling];
}
- (void)sendEvent:(NSEvent *)event {
    if (event.type == NSEventTypeScrollWheel && self.inputRecorder) {
        [self.inputRecorder record:@"scroll_window_boundary" event:event
                           details:@{
            @"delta_x": @(event.scrollingDeltaX),
            @"delta_y": @(event.scrollingDeltaY),
            @"precise": @(event.hasPreciseScrollingDeltas),
            @"phase": @(event.phase),
            @"momentum_phase": @(event.momentumPhase),
        }];
    }
    [super sendEvent:event];
}
@end

@implementation MacWSInputCanvas
- (instancetype)initWithFrame:(NSRect)frameRect {
    self = [super initWithFrame:frameRect];
    if (self) {
        _markerPoint = NSMakePoint(NSMidX(self.bounds), NSMidY(self.bounds));
        _magnification = 1.0;
    }
    return self;
}
- (BOOL)acceptsFirstResponder { return YES; }
- (BOOL)acceptsFirstMouse:(NSEvent *)event { (void)event; return YES; }
- (BOOL)isFlipped { return YES; }
- (void)updateTrackingAreas {
    [super updateTrackingAreas];
    for (NSTrackingArea *area in self.trackingAreas) [self removeTrackingArea:area];
    NSTrackingAreaOptions options = NSTrackingMouseEnteredAndExited |
        NSTrackingMouseMoved | NSTrackingActiveAlways | NSTrackingInVisibleRect;
    [self addTrackingArea:[[NSTrackingArea alloc] initWithRect:self.bounds
        options:options owner:self userInfo:nil]];
}
- (void)drawRect:(NSRect)dirtyRect {
    [super drawRect:dirtyRect];
    [[NSColor colorWithCalibratedRed:0.055 green:0.075 blue:0.11 alpha:1] setFill];
    NSRectFill(dirtyRect);
    NSDictionary *attributes = @{
        NSFontAttributeName: [NSFont monospacedSystemFontOfSize:17
            weight:NSFontWeightSemibold],
        NSForegroundColorAttributeName: NSColor.systemCyanColor,
    };
    [@"Input canvas — click, drag, scroll, right-click and type here"
        drawAtPoint:NSMakePoint(24, 22) withAttributes:attributes];
    [[NSColor colorWithCalibratedWhite:1.0 alpha:0.10] setStroke];
    NSBezierPath *grid = [NSBezierPath bezierPath];
    CGFloat spacing = 48.0 * fmax(0.5, fmin(self.magnification, 2.0));
    CGFloat offset = fmod(self.scrollOffset, spacing);
    for (CGFloat y = 64.0 + offset; y < NSHeight(self.bounds); y += spacing) {
        [grid moveToPoint:NSMakePoint(0, y)];
        [grid lineToPoint:NSMakePoint(NSWidth(self.bounds), y)];
    }
    [grid stroke];
    [[NSColor systemCyanColor] setFill];
    NSRect marker = NSMakeRect(self.markerPoint.x - 11.0,
                               self.markerPoint.y - 11.0, 22.0, 22.0);
    [[NSBezierPath bezierPathWithOvalInRect:marker] fill];
}
- (void)mouseDown:(NSEvent *)event {
    [self.window makeFirstResponder:self];
    self.markerPoint = [self convertPoint:event.locationInWindow fromView:nil];
    [self setNeedsDisplay:YES];
    [self.recorder record:@"left_down" event:event details:nil];
}
- (void)mouseUp:(NSEvent *)event {
    [self.recorder record:@"left_up" event:event details:nil];
}
- (void)mouseDragged:(NSEvent *)event {
    self.markerPoint = [self convertPoint:event.locationInWindow fromView:nil];
    [self setNeedsDisplay:YES];
    [self.recorder record:@"left_drag" event:event details:@{
        @"delta_x": @(event.deltaX), @"delta_y": @(event.deltaY)}];
}
- (void)rightMouseDown:(NSEvent *)event {
    [self.recorder record:@"right_down" event:event details:nil];
}
- (void)rightMouseUp:(NSEvent *)event {
    [self.recorder record:@"right_up" event:event details:nil];
}
- (void)otherMouseDown:(NSEvent *)event {
    [self.recorder record:@"other_down" event:event details:nil];
}
- (void)otherMouseUp:(NSEvent *)event {
    [self.recorder record:@"other_up" event:event details:nil];
}
- (void)mouseMoved:(NSEvent *)event {
    self.markerPoint = [self convertPoint:event.locationInWindow fromView:nil];
    [self setNeedsDisplay:YES];
    [self.recorder record:@"move" event:event details:nil];
}
- (void)mouseEntered:(NSEvent *)event {
    [self.recorder record:@"entered" event:event details:nil];
}
- (void)mouseExited:(NSEvent *)event {
    [self.recorder record:@"exited" event:event details:nil];
}
- (void)scrollWheel:(NSEvent *)event {
    self.scrollOffset += event.scrollingDeltaY;
    [self setNeedsDisplay:YES];
    [self.recorder record:@"scroll" event:event details:@{
        @"delta_x": @(event.scrollingDeltaX),
        @"delta_y": @(event.scrollingDeltaY),
        @"precise": @(event.hasPreciseScrollingDeltas),
        @"phase": @(event.phase),
        @"momentum_phase": @(event.momentumPhase),
    }];
}
- (void)magnifyWithEvent:(NSEvent *)event {
    self.magnification = fmax(0.5, fmin(2.0,
        self.magnification + event.magnification));
    [self setNeedsDisplay:YES];
    [self.recorder record:@"magnify" event:event details:@{
        @"magnification": @(event.magnification),
        @"phase": @(event.phase),
    }];
}
- (void)beginGestureWithEvent:(NSEvent *)event {
    [self.recorder record:@"gesture_begin" event:event details:@{
        @"phase": @(event.phase)}];
}
- (void)endGestureWithEvent:(NSEvent *)event {
    [self.recorder record:@"gesture_end" event:event details:@{
        @"phase": @(event.phase)}];
}
- (void)rotateWithEvent:(NSEvent *)event {
    [self.recorder record:@"rotate" event:event details:@{@"rotation": @(event.rotation)}];
}
- (void)swipeWithEvent:(NSEvent *)event {
    [self.recorder record:@"swipe" event:event details:@{
        @"delta_x": @(event.deltaX), @"delta_y": @(event.deltaY)}];
}
- (void)pressureChangeWithEvent:(NSEvent *)event {
    [self.recorder record:@"pressure" event:event details:@{
        @"pressure": @(event.pressure), @"stage": @(event.stage)}];
}
- (void)keyDown:(NSEvent *)event {
    [self.recorder record:@"key_down" event:event details:@{
        @"key_code": @(event.keyCode),
        @"characters": event.characters ?: @"",
        @"characters_ignoring_modifiers": event.charactersIgnoringModifiers ?: @"",
        @"repeat": @(event.isARepeat),
    }];
}
- (void)keyUp:(NSEvent *)event {
    [self.recorder record:@"key_up" event:event details:@{
        @"key_code": @(event.keyCode),
        @"characters": event.characters ?: @"",
        @"characters_ignoring_modifiers": event.charactersIgnoringModifiers ?: @"",
    }];
}
- (void)flagsChanged:(NSEvent *)event {
    [self.recorder record:@"flags_changed" event:event details:@{
        @"key_code": @(event.keyCode)}];
}
@end

@interface MacWSInputLabDelegate : NSObject <NSApplicationDelegate>
@property(nonatomic, strong) NSWindow *window;
@property(nonatomic, strong) NSTextField *status;
@property(nonatomic, strong) MacWSInputRecorder *recorder;
@property(nonatomic, strong) NSDictionary *pendingStatusState;
@property(nonatomic) BOOL statusUpdateScheduled;
@end

@implementation MacWSInputLabDelegate
- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    (void)notification;
    if (MacWSInputLabDiagnosticsEnabled()) {
        unsigned int factoryCount = 0;
        Method *factories = class_copyMethodList(object_getClass(NSEvent.class),
                                                  &factoryCount);
        for (unsigned int index = 0; index < factoryCount; index++) {
            const char *name = sel_getName(method_getName(factories[index]));
            if (name && (strcasestr(name, "scroll") ||
                         strcasestr(name, "momentum"))) {
                fprintf(stderr, "INPUTLAB NSEVENT-FACTORY %s types=%s\n", name,
                        method_getTypeEncoding(factories[index]));
            }
        }
        free(factories);
        unsigned int windowMethodCount = 0;
        Method *windowMethods = class_copyMethodList(NSWindow.class,
                                                      &windowMethodCount);
        for (unsigned int index = 0; index < windowMethodCount; index++) {
            const char *name = sel_getName(method_getName(windowMethods[index]));
            if (name && (strcasestr(name, "scroll") ||
                         strcasestr(name, "momentum"))) {
                fprintf(stderr, "INPUTLAB NSWINDOW-METHOD %s types=%s\n", name,
                        method_getTypeEncoding(windowMethods[index]));
            }
        }
        free(windowMethods);
    }
    self.recorder = [MacWSInputRecorder new];
    NSRect frame = NSMakeRect(160, 120, 860, 620);
    self.window = [[MacWSInputWindow alloc] initWithContentRect:frame
        styleMask:NSWindowStyleMaskTitled | NSWindowStyleMaskClosable |
                  NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable
        backing:NSBackingStoreBuffered defer:NO];
    self.window.title = @"MacWS Input Lab";
    ((MacWSInputWindow *)self.window).inputRecorder = self.recorder;
    self.window.minSize = NSMakeSize(640, 460);

    NSView *root = self.window.contentView;
    MacWSInputCanvas *canvas = [[MacWSInputCanvas alloc]
        initWithFrame:NSMakeRect(20, 180, 820, 420)];
    canvas.autoresizingMask = NSViewWidthSizable | NSViewHeightSizable;
    canvas.recorder = self.recorder;
    [root addSubview:canvas];

    NSButton *button = [NSButton buttonWithTitle:@"Native Button"
        target:self action:@selector(controlActivated:)];
    button.identifier = @"button";
    button.frame = NSMakeRect(20, 128, 140, 34);
    [root addSubview:button];

    NSButton *checkbox = [NSButton checkboxWithTitle:@"Checkbox"
        target:self action:@selector(controlActivated:)];
    checkbox.identifier = @"checkbox";
    checkbox.frame = NSMakeRect(180, 128, 120, 34);
    [root addSubview:checkbox];

    NSSlider *slider = [NSSlider sliderWithValue:0.35 minValue:0 maxValue:1
        target:self action:@selector(controlActivated:)];
    slider.identifier = @"slider";
    slider.continuous = YES;
    slider.frame = NSMakeRect(320, 128, 200, 34);
    [root addSubview:slider];

    NSTextField *text = [[NSTextField alloc] initWithFrame:NSMakeRect(540, 128, 300, 34)];
    text.placeholderString = @"Hardware/software keyboard target";
    text.identifier = @"text_field";
    text.target = self;
    text.action = @selector(controlActivated:);
    [root addSubview:text];

    self.status = [NSTextField labelWithString:@"ready"];
    self.status.frame = NSMakeRect(20, 18, 820, 94);
    self.status.font = [NSFont monospacedSystemFontOfSize:13 weight:NSFontWeightRegular];
    self.status.textColor = NSColor.labelColor;
    self.status.maximumNumberOfLines = 5;
    self.status.lineBreakMode = NSLineBreakByWordWrapping;
    self.status.autoresizingMask = NSViewWidthSizable | NSViewMaxYMargin;
    [root addSubview:self.status];

    __weak typeof(self) weakSelf = self;
    self.recorder.stateChanged = ^(NSDictionary *state) {
        MacWSInputLabDelegate *strongSelf = weakSelf;
        if (!strongSelf) return;
        strongSelf.pendingStatusState = state;
        if (strongSelf.statusUpdateScheduled) return;
        strongSelf.statusUpdateScheduled = YES;
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW,
                                     16 * NSEC_PER_MSEC),
                       dispatch_get_main_queue(), ^{
            MacWSInputLabDelegate *innerSelf = weakSelf;
            if (!innerSelf) return;
            NSDictionary *latest = innerSelf.pendingStatusState;
            innerSelf.pendingStatusState = nil;
            innerSelf.statusUpdateScheduled = NO;
            innerSelf.status.stringValue = [NSString stringWithFormat:
                @"sequence=%@  last=%@\n%@\nlog=%@",
                latest[@"sequence"], latest[@"last_event"],
                latest[@"counts"], MacWSEventLogPath];
        });
    };

    NSMenu *mainMenu = [NSMenu new];
    NSMenuItem *appRoot = [NSMenuItem new];
    [mainMenu addItem:appRoot];
    NSMenu *appMenu = [[NSMenu alloc] initWithTitle:@"Input Lab"];
    [appMenu addItemWithTitle:@"Record Menu Action" action:@selector(menuAction:)
        keyEquivalent:@"m"];
    [appMenu addItem:[NSMenuItem separatorItem]];
    [appMenu addItemWithTitle:@"Quit Input Lab" action:@selector(terminate:)
        keyEquivalent:@"q"];
    appRoot.submenu = appMenu;
    NSApp.mainMenu = mainMenu;

    [self.window makeKeyAndOrderFront:nil];
    [NSApp activateIgnoringOtherApps:YES];
    [self.window makeFirstResponder:canvas];
    [self.recorder record:@"ready" event:nil details:@{
        @"pid": @(NSProcessInfo.processInfo.processIdentifier)}];
}

- (void)controlActivated:(id)sender {
    NSString *identifier = [sender identifier] ?: NSStringFromClass([sender class]);
    id value = [sender respondsToSelector:@selector(objectValue)]
        ? [sender objectValue] ?: [NSNull null] : [NSNull null];
    [self.recorder record:[@"control_" stringByAppendingString:identifier]
        event:NSApp.currentEvent details:@{@"value": value}];
}

- (void)menuAction:(id)sender {
    (void)sender;
    [self.recorder record:@"menu_action" event:NSApp.currentEvent details:nil];
}

- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication *)sender {
    (void)sender;
    return YES;
}
@end

int main(void) {
    @autoreleasepool {
        NSApplication *application = NSApplication.sharedApplication;
        application.activationPolicy = NSApplicationActivationPolicyRegular;
        MacWSInputLabDelegate *delegate = [MacWSInputLabDelegate new];
        application.delegate = delegate;
        [application run];
    }
    return 0;
}

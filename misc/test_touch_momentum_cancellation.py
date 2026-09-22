"""Execute Host's real contact-entry/stop/tick code with an observing clock sink."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class TouchMomentumCancellationTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "darwin" and shutil.which("clang"),
                         "Objective-C Foundation compiler required")
    def test_real_entry_closes_old_momentum_before_any_new_contact(self):
        source = (ROOT / "MacWSHost/Rendering/MacWSMetalView.m").read_text()
        start = source.index("- (void)touchesBegan:(NSSet<UITouch *> *)touches",
                             source.index("@implementation MacWSMetalView"))
        body = source[start:source.index("- (void)touchesMoved:", start)]
        focus = '[self restoreHardwareKeyboardFocusWithReason:@"pointer-down"];'
        # Execute the unchanged statements at the actual common entry, not a
        # separately modeled cancellation helper. Device-specific UIKit paths
        # remain below this boundary and are deliberately not simulated here.
        prefix = body[:body.index(focus) + len(focus)]
        self.assertIn("if (touches.count != 0)", prefix)
        self.assertIn("[self stopScrollMomentumWithTerminalPhase:YES];", prefix)
        self.assertLess(prefix.index("stopScrollMomentumWithTerminalPhase"),
                        prefix.index("restoreHardwareKeyboardFocusWithReason"))
        self.assertNotIn("touch.type", prefix)
        self.assertNotIn("self.inputMode", prefix)
        stop_start = source.index("- (void)stopScrollMomentumWithTerminalPhase:")
        stop = source[stop_start:source.index(
            "- (void)startScrollMomentumWithVelocity:", stop_start)]
        tick_start = source.index("- (void)scrollMomentumTick:")
        tick = source[tick_start:source.index("- (void)twoFingerPanned:", tick_start)]
        fixture = r'''
#import <Foundation/Foundation.h>
#import <CoreGraphics/CoreGraphics.h>
#include <assert.h>
#include <math.h>
#include "macws_touch_policy.h"
typedef double CFTimeInterval;
static CFTimeInterval CACurrentMediaTime(void) { return 123.5; }
typedef struct {char kind; CGPoint point; uint16_t flags; MacWSInputSource source; CGFloat direction;} Event;
static Event events[16];
static unsigned count;
static void note(char kind) { assert(count<16); events[count++]=(Event){.kind=kind}; }
@interface UITouch : NSObject
@property unsigned type;
@end
@implementation UITouch
@end
@interface UIEvent : NSObject
@end
@implementation UIEvent
@end
@interface CADisplayLink : NSObject
@property double timestamp;
@property double duration;
@property BOOL invalidated;
- (void)invalidate;
@end
@implementation CADisplayLink
- (void)invalidate { self.invalidated=YES; note('i'); }
@end
@interface Probe : NSObject {
@public
    CADisplayLink *_scrollMomentumDisplayLink;
    CGPoint _scrollMomentumFramePoint, _scrollMomentumVelocity;
    MacWSInputSource _scrollMomentumSource;
    CGFloat _scrollMomentumDirectionMultiplier;
    CFTimeInterval _scrollMomentumLastTimestamp;
    BOOL _scrollMomentumBegan;
}
- (void)stopScrollMomentumWithTerminalPhase:(BOOL)terminal;
- (void)scrollMomentumTick:(CADisplayLink *)link;
- (void)touchesBegan:(NSSet<UITouch *> *)touches withEvent:(UIEvent *)event;
- (void)restoreHardwareKeyboardFocusWithReason:(NSString *)reason;
- (void)emitScrollAtFramePoint:(CGPoint)point translation:(CGPoint)translation
    flags:(uint16_t)flags timestamp:(NSTimeInterval)timestamp
    source:(MacWSInputSource)source directionMultiplier:(CGFloat)direction;
@end
@implementation Probe
- (void)restoreHardwareKeyboardFocusWithReason:(NSString *)reason {
    assert([reason isEqualToString:@"pointer-down"]); note('f');
}
- (void)emitScrollAtFramePoint:(CGPoint)point translation:(CGPoint)translation
    flags:(uint16_t)flags timestamp:(NSTimeInterval)timestamp
    source:(MacWSInputSource)source directionMultiplier:(CGFloat)direction {
    (void)timestamp;
    assert(translation.x==0 && translation.y==0 && count<16);
    events[count++]=(Event){'e',point,flags,source,direction};
}
'''
        fixture += stop + tick + prefix
        fixture += r'''
    (void)event;
    // This marks the beginning of the existing device-specific dispatch.
    // It does not model UIKit recognition or fabricate a pointer event.
    note('c');
}
@end
int main(void) { @autoreleasepool {
    // Direct finger, physical pointer, and Pencil all enter this same prefix.
    // A two-contact arrival must cancel once, never once per touch.
    for(unsigned type=0;type<3;type++) for(unsigned contacts=1;contacts<=2;contacts++)
    for(unsigned oldSource=0;oldSource<2;oldSource++) for(unsigned began=0;began<2;began++) {
        Probe *p=[Probe new]; CADisplayLink *old=[CADisplayLink new];
        old.timestamp=124;old.duration=1.0/120;
        p->_scrollMomentumDisplayLink=old;
        p->_scrollMomentumFramePoint=CGPointMake(620.814,791.825);
        p->_scrollMomentumVelocity=CGPointMake(128,32);
        p->_scrollMomentumSource=oldSource?MacWSInputSourceIndirectPointer:MacWSInputSourceFinger;
        p->_scrollMomentumDirectionMultiplier=oldSource?-1:1;
        p->_scrollMomentumLastTimestamp=123;
        p->_scrollMomentumBegan=began;
        NSMutableSet *touches=[NSMutableSet set];
        for(unsigned n=0;n<contacts;n++){UITouch *t=[UITouch new];t.type=type;[touches addObject:t];}
        count=0;
        [p touchesBegan:touches withEvent:[UIEvent new]];
        assert(count==4 && events[0].kind=='e' && events[1].kind=='i' && events[2].kind=='f' && events[3].kind=='c');
        assert(events[0].point.x==620.814 && events[0].point.y==791.825);
        assert(events[0].flags==(MacWSInputFlagScrollEnded|MacWSInputFlagScrollMomentum));
        assert(events[0].source==(oldSource?MacWSInputSourceIndirectPointer:MacWSInputSourceFinger));
        assert(events[0].direction==(oldSource?-1:1));
        assert(old.invalidated && p->_scrollMomentumDisplayLink==nil);
        assert(p->_scrollMomentumVelocity.x==0 && p->_scrollMomentumVelocity.y==0);
        assert(p->_scrollMomentumSource==MacWSInputSourceUnknown);
        assert(p->_scrollMomentumLastTimestamp==0 && !p->_scrollMomentumBegan);
        // Even an already-dispatched old callback cannot emit another tail
        // after cancellation zeroed its velocity and removed its displaylink.
        [p scrollMomentumTick:old]; assert(count==4);
        // The next contact, or a second finger in a drag, adds no scroll event
        // when there is no deceleration left to retire.
        [p touchesBegan:touches withEvent:[UIEvent new]];
        assert(count==6 && events[4].kind=='f' && events[5].kind=='c');
    }
    Probe *p=[Probe new]; CADisplayLink *old=[CADisplayLink new];
    p->_scrollMomentumDisplayLink=old;p->_scrollMomentumVelocity=CGPointMake(128,0);
    count=0;[p touchesBegan:[NSSet set] withEvent:nil];
    assert(count==2 && !old.invalidated && p->_scrollMomentumDisplayLink==old);
    assert(p->_scrollMomentumVelocity.x==128);
    puts("touch-momentum PASS: old terminal before focus/contact, all contact kinds, no stale tail, no spurious end");
} return 0; }
'''
        with tempfile.TemporaryDirectory(prefix="macws-touch-momentum-") as directory:
            binary = Path(directory) / "probe"
            subprocess.run(["clang", "-x", "objective-c", "-fobjc-arc",
                            "-Wall", "-Wextra", "-Werror", "-fsanitize=undefined",
                            "-I", str(ROOT / "include"), "-framework", "Foundation",
                            "-framework", "CoreGraphics", "-", "-o", str(binary)],
                           input=fixture, text=True, check=True, capture_output=True)
            result = subprocess.run([str(binary)], text=True, capture_output=True,
                                    check=True, timeout=5)
            self.assertIn("touch-momentum PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()

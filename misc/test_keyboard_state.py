"""Execute the real framework-free modifier state machine with a failing poster."""
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class KeyboardStateTests(unittest.TestCase):
    def test_executable_state_contract(self):
        compiler = shutil.which("clang") or shutil.which("cc")
        if not compiler:
            self.skipTest("C compiler unavailable")
        program = r'''
#include "macws_keyboard_state.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
typedef struct {uint16_t code; bool down; uint32_t flags;} Event;
typedef struct {Event events[128]; unsigned count, calls, fail, delivered; uint8_t native;} Sink;
static bool post(void *p,uint16_t c,bool d,uint32_t f) {
    Sink *s=p;s->calls++;
    if(s->fail==s->calls)return false;
    assert(s->count<128);s->events[s->count++]=(Event){c,d,f};return true;
}
static void reset(MacWSKeyboardState *s,Sink *o){memset(s,0,sizeof(*s));memset(o,0,sizeof(*o));}
// Acceptance and native visibility are separate, ordered boundaries. Reading
// native before drain intentionally returns a stale state without reordering
// any accepted event, exactly the contract reconciliation must tolerate.
static void drain(Sink *o) {
    while(o->delivered<o->count) {
        Event e=o->events[o->delivered++];
        uint8_t bit=MacWSKeyboardSideForKeyCode(e.code);
        if(e.down)o->native|=bit;else o->native&=(uint8_t)~bit;
    }
}
int main(void) {
    MacWSKeyboardState s;Sink o;reset(&s,&o);
    for(unsigned i=0;i<8;i++)assert(MacWSKeyboardSideForKeyCode(MacWSKeyboardKeyCodeForSide(i))==(1u<<i));
    assert(MacWSKeyboardSidesForFlags(MacWSKeyboardControl,0)==4);
    assert(MacWSKeyboardSidesForFlags(MacWSKeyboardControl,8)==8);
    assert(MacWSKeyboardSidesForFlags(0,255)==0);

    // Runtime witness: local explicit/synthetic zero but native left Ctrl held.
    assert(MacWSKeyboardApplySnapshot(&s,0,0,4,true,post,&o));
    assert(o.count==1&&o.events[0].code==59&&!o.events[0].down&&o.events[0].flags==0);
    assert(s.postedSides==0&&s.syntheticSides==0);

    // CGEventPost acceptance precedes the native state read. A lagging zero
    // cannot erase the tracked down, otherwise the matching up disappears.
    reset(&s,&o);
    assert(MacWSKeyboardApplySnapshot(&s,4,MacWSKeyboardControl,0,true,post,&o));
    assert(s.postedSides==4&&o.count==1&&o.events[0].down);
    assert(MacWSKeyboardApplySnapshot(&s,0,0,0,true,post,&o));
    assert(o.count==2&&o.events[1].code==59&&!o.events[1].down);
    assert(s.postedSides==0);
    // Conversely, an already-released side can remain observed down until
    // that queued release lands; a repeated release must not create a down.
    assert(MacWSKeyboardApplySnapshot(&s,0,0,4,true,post,&o));
    assert(o.count==3&&!o.events[2].down&&s.postedSides==0);

    // Old owner releases, new owner acquires while physically still held.
    // A stale native down cannot replace the required down AFTER queued up.
    reset(&s,&o);
    assert(MacWSKeyboardApplySnapshot(&s,4,MacWSKeyboardControl,0,true,post,&o));
    drain(&o);assert(o.native==4);
    assert(MacWSKeyboardApplySnapshot(&s,0,0,o.native,true,post,&o));
    assert(s.postedSides==0&&s.acceptedUpSides==4&&o.native==4);
    assert(MacWSKeyboardApplySnapshot(&s,4,MacWSKeyboardControl,o.native,true,post,&o));
    assert(o.count==3&&o.events[2].down&&o.events[2].code==59);
    assert(s.acceptedUpSides==0);
    drain(&o);assert(o.native==4&&s.postedSides==4);
    assert(MacWSKeyboardApplySnapshot(&s,0,0,o.native,true,post,&o));
    drain(&o);assert(!o.native&&!s.postedSides);

    // A failed re-press retains the accepted-up history and retries exactly
    // that missing down, even if the old queued release has since landed.
    reset(&s,&o);
    assert(MacWSKeyboardApplySnapshot(&s,4,MacWSKeyboardControl,0,true,post,&o));
    drain(&o);
    assert(MacWSKeyboardApplySnapshot(&s,0,0,o.native,true,post,&o));
    o.fail=o.calls+1;
    assert(!MacWSKeyboardApplySnapshot(&s,4,MacWSKeyboardControl,o.native,true,post,&o));
    assert(s.postedSides==0&&s.acceptedUpSides==4&&o.count==2);
    drain(&o);assert(!o.native);
    o.fail=0;
    assert(MacWSKeyboardApplySnapshot(&s,4,MacWSKeyboardControl,o.native,true,post,&o));
    drain(&o);assert(o.native==4&&s.acceptedUpSides==0);

    // Native zero is not an ACK: it can precede both accepted down and up.
    // It must not erase history before a subsequent stale down observation.
    reset(&s,&o);
    assert(MacWSKeyboardApplySnapshot(&s,4,MacWSKeyboardControl,0,true,post,&o));
    assert(MacWSKeyboardApplySnapshot(&s,0,0,0,true,post,&o));
    assert(MacWSKeyboardApplySnapshot(&s,0,0,0,true,post,&o));
    assert(s.acceptedUpSides==4);
    o.native=4;o.delivered=1; // only the original down has landed
    assert(MacWSKeyboardApplySnapshot(&s,4,MacWSKeyboardControl,o.native,true,post,&o));
    drain(&o);assert(o.native==4&&s.postedSides==4);

    // A failed redundant release may import stale native-down knowledge into
    // postedSides. It is still not a new accepted down, including when the
    // next reconciliation is an ordinary hardware record (no native read).
    reset(&s,&o);
    assert(MacWSKeyboardApplySnapshot(&s,4,MacWSKeyboardControl,0,true,post,&o));
    drain(&o);
    assert(MacWSKeyboardApplySnapshot(&s,0,0,o.native,true,post,&o));
    o.fail=o.calls+1;
    assert(!MacWSKeyboardApplySnapshot(&s,0,0,o.native,true,post,&o));
    assert(s.postedSides==4&&s.acceptedUpSides==4);
    o.fail=0;
    assert(MacWSKeyboardPostHardwareKey(&s,59,true,4,MacWSKeyboardControl,post,&o));
    drain(&o);assert(o.native==4&&s.postedSides==4&&s.acceptedUpSides==0);

    // The same imported observation cannot masquerade as an accepted
    // software re-press, nor leave syntheticSides committed on a post failure.
    reset(&s,&o);
    assert(MacWSKeyboardApplySnapshot(&s,0,0,4,true,post,&o));
    o.fail=o.calls+1;
    assert(!MacWSKeyboardApplySnapshot(&s,0,0,4,true,post,&o));
    assert(s.postedSides==4&&s.syntheticSides==4&&s.acceptedUpSides==4);
    o.fail=o.calls+1;
    assert(!MacWSKeyboardPostSoftwareKey(&s,0,true,MacWSKeyboardControl,post,&o));
    assert(!s.postedSides&&!s.syntheticSides&&s.acceptedUpSides==4);
    o.fail=0;
    assert(MacWSKeyboardPostSoftwareKey(&s,0,true,MacWSKeyboardControl,post,&o));
    assert(s.postedSides==4&&s.syntheticSides==4&&s.acceptedUpSides==0);
    assert(MacWSKeyboardPostSoftwareKey(&s,0,false,MacWSKeyboardControl,post,&o));
    drain(&o);assert(!o.native&&!s.postedSides&&!s.syntheticSides);

    // Re-pressing left Control must not release independently held right.
    reset(&s,&o);
    assert(MacWSKeyboardApplySnapshot(&s,12,MacWSKeyboardControl,0,true,post,&o));
    drain(&o);
    assert(MacWSKeyboardApplySnapshot(&s,8,MacWSKeyboardControl,o.native,true,post,&o));
    assert(MacWSKeyboardApplySnapshot(&s,12,MacWSKeyboardControl,o.native,true,post,&o));
    assert(o.count==4&&o.events[2].code==59&&!o.events[2].down);
    assert(o.events[3].code==59&&o.events[3].down);
    assert(o.events[2].flags==MacWSKeyboardControl&&o.events[3].flags==MacWSKeyboardControl);
    drain(&o);assert(o.native==12&&s.postedSides==12);

    // Independently held sides: releasing one does not release the other.
    reset(&s,&o);
    assert(MacWSKeyboardPostHardwareKey(&s,59,true,4,MacWSKeyboardControl,post,&o));
    assert(MacWSKeyboardPostHardwareKey(&s,62,true,12,MacWSKeyboardControl,post,&o));
    assert(MacWSKeyboardPostHardwareKey(&s,59,false,8,MacWSKeyboardControl,post,&o));
    assert(o.count==3&&!o.events[2].down&&o.events[2].code==59);
    assert(o.events[2].flags==MacWSKeyboardControl&&s.postedSides==8);
    assert(MacWSKeyboardPostHardwareKey(&s,62,false,0,0,post,&o));
    assert(s.postedSides==0&&o.events[3].flags==0);

    // Focus cancellation is the explicit empty physical snapshot, not a timer.
    reset(&s,&o);
    assert(MacWSKeyboardApplySnapshot(&s,0x44,MacWSKeyboardControl|MacWSKeyboardCommand,0,false,post,&o));
    assert(MacWSKeyboardApplySnapshot(&s,0,0,0,false,post,&o));
    assert(o.count==4&&s.postedSides==0&&s.physicalSides==0);

    // No successful-post fiction; retry must send the same missing edge.
    reset(&s,&o);o.fail=1;
    assert(!MacWSKeyboardApplySnapshot(&s,4,MacWSKeyboardControl,0,false,post,&o));
    assert(s.physicalSides==4&&s.postedSides==0&&o.count==0);
    o.fail=0;assert(MacWSKeyboardApplySnapshot(&s,4,MacWSKeyboardControl,0,false,post,&o));
    o.fail=o.calls+1;assert(!MacWSKeyboardApplySnapshot(&s,0,0,0,false,post,&o));
    assert(s.physicalSides==0&&s.postedSides==4&&o.count==1);
    o.fail=0;assert(MacWSKeyboardApplySnapshot(&s,0,0,0,false,post,&o));
    assert(o.count==2&&s.postedSides==0);

    // A failure midway commits only already delivered edges, then resumes.
    reset(&s,&o);s.postedSides=0x44;o.fail=2;
    assert(!MacWSKeyboardApplySnapshot(&s,2,MacWSKeyboardShift,0,false,post,&o));
    assert(s.postedSides==64&&o.count==1&&o.events[0].code==59);
    o.fail=0;assert(MacWSKeyboardApplySnapshot(&s,2,MacWSKeyboardShift,0,false,post,&o));
    assert(s.postedSides==2&&o.count==3);
    assert(o.events[1].code==55&&!o.events[1].down);
    assert(o.events[2].code==60&&o.events[2].down);

    // Software Cmd+A while physical right Cmd is held must not release it.
    reset(&s,&o);
    assert(MacWSKeyboardApplySnapshot(&s,128,MacWSKeyboardCommand,0,false,post,&o));
    assert(MacWSKeyboardPostSoftwareKey(&s,0,true,MacWSKeyboardCommand,post,&o));
    assert(MacWSKeyboardPostSoftwareKey(&s,0,false,MacWSKeyboardCommand,post,&o));
    assert(o.count==3&&s.postedSides==128&&s.syntheticSides==0);
    assert(o.events[1].code==0&&o.events[1].down&&o.events[2].code==0&&!o.events[2].down);

    // Additional software Ctrl bracket releases only Ctrl, preserving Cmd.
    assert(MacWSKeyboardPostSoftwareKey(&s,48,true,MacWSKeyboardControl,post,&o));
    assert(MacWSKeyboardPostSoftwareKey(&s,48,false,MacWSKeyboardControl,post,&o));
    assert(o.count==7&&s.postedSides==128&&s.syntheticSides==0);
    assert(o.events[3].code==59&&o.events[3].down);
    assert(o.events[6].code==59&&!o.events[6].down&&o.events[6].flags==MacWSKeyboardCommand);

    // Failed software key-up does not prematurely release its chord.
    reset(&s,&o);
    assert(MacWSKeyboardPostSoftwareKey(&s,0,true,MacWSKeyboardCommand,post,&o));
    o.fail=o.calls+1;assert(!MacWSKeyboardPostSoftwareKey(&s,0,false,MacWSKeyboardCommand,post,&o));
    assert(s.postedSides==64&&s.syntheticSides==64&&o.count==2);
    o.fail=0;assert(MacWSKeyboardPostSoftwareKey(&s,0,false,MacWSKeyboardCommand,post,&o));
    assert(s.postedSides==0&&o.count==4);

    // Hardware snapshot reconciles stale software state before its own key.
    reset(&s,&o);
    assert(MacWSKeyboardPostSoftwareKey(&s,0,true,MacWSKeyboardCommand,post,&o));
    assert(MacWSKeyboardPostHardwareKey(&s,48,true,0,0,post,&o));
    assert(o.events[2].code==55&&!o.events[2].down);
    assert(o.events[3].code==48&&o.events[3].down&&o.events[3].flags==0);

    // A zero snapshot on an already neutral session posts no events.
    reset(&s,&o);assert(MacWSKeyboardApplySnapshot(&s,0,0,0,true,post,&o));
    assert(o.count==0);
    assert(!MacWSKeyboardPostHardwareKey(&s,59,true,0,0,post,&o));
    assert(!MacWSKeyboardPostHardwareKey(&s,256,true,0,0,post,&o));
    assert(!MacWSKeyboardPostSoftwareKey(&s,55,true,0,post,&o));
    assert(!MacWSKeyboardApplySnapshot(&s,4,0,0,false,post,&o));
    assert(!MacWSKeyboardApplySnapshot(&s,0,MacWSKeyboardControl,0,false,post,&o));
    assert(o.count==0);

    // Exhaust all left/right transitions, including partial-post failure.
    for(unsigned a=0;a<256;a++)for(unsigned b=0;b<256;b++){
        reset(&s,&o);s.postedSides=(uint8_t)a;
        assert(MacWSKeyboardApplySnapshot(&s,(uint8_t)b,MacWSKeyboardFlagsForSides((uint8_t)b),0,false,post,&o));
        assert(s.postedSides==b&&s.syntheticSides==0);
        unsigned count=0;for(unsigned bit=0;bit<8;bit++)count+=((a^b)>>bit)&1u;
        assert(o.count==count);
        unsigned simulated=a;bool seenDown=false;
        for(unsigned e=0;e<o.count;e++){
            Event x=o.events[e];unsigned bit=MacWSKeyboardSideForKeyCode(x.code);
            if(x.down){seenDown=true;simulated|=bit;}else{assert(!seenDown);simulated&=~bit;}
            assert(x.flags==MacWSKeyboardFlagsForSides((uint8_t)simulated));
        }
        assert(simulated==b);
    }
    // Exhaust every old/new owner pair with ALL old releases still pending
    // and native reads returning the old held sides. No sleep/native ACK is
    // assumed. Final accepted queue must realize exactly the new snapshot.
    for(unsigned a=0;a<256;a++)for(unsigned b=0;b<256;b++){
        reset(&s,&o);
        assert(MacWSKeyboardApplySnapshot(&s,(uint8_t)a,MacWSKeyboardFlagsForSides((uint8_t)a),0,true,post,&o));
        drain(&o);assert(o.native==a);
        assert(MacWSKeyboardApplySnapshot(&s,0,0,o.native,true,post,&o));
        assert(MacWSKeyboardApplySnapshot(&s,(uint8_t)b,MacWSKeyboardFlagsForSides((uint8_t)b),o.native,true,post,&o));
        drain(&o);
        assert(o.native==b&&s.postedSides==b&&s.physicalSides==b&&s.syntheticSides==0);
        assert(!(s.acceptedUpSides&b));
        assert(MacWSKeyboardApplySnapshot(&s,0,0,o.native,true,post,&o));
        drain(&o);assert(!o.native&&!s.postedSides&&!s.physicalSides);
    }
    puts("keyboard-state PASS: native divergence, side ownership, failure retry, software restore, 65536 transitions, 65536 async owner handoffs");
    return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix="macws-keyboard-state-") as tmp:
            source = pathlib.Path(tmp) / "test.c"
            binary = pathlib.Path(tmp) / "test"
            source.write_text(program)
            subprocess.run([compiler, "-std=c11", "-O2", "-Wall", "-Wextra",
                            "-Werror", "-fsanitize=undefined", "-I", str(ROOT / "include"), str(source),
                            "-o", str(binary)], check=True, capture_output=True)
            result = subprocess.run([str(binary)], check=True, text=True,
                                    capture_output=True, timeout=10)
            self.assertIn("65536 transitions", result.stdout)
            self.assertIn("65536 async owner handoffs", result.stdout)


if __name__ == "__main__":
    unittest.main()

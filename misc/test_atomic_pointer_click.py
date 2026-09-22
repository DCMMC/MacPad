"""Execute the production atomic-click transaction with an observing poster."""
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class AtomicPointerClickTests(unittest.TestCase):
    def test_real_transaction_sequence_and_results(self):
        compiler = shutil.which("clang") or shutil.which("cc")
        if not compiler:
            self.skipTest("C compiler unavailable")
        source = r'''
#include "macws_atomic_pointer_click.h"
#include <assert.h>
#include <stdio.h>
typedef struct {char kind; bool left,right; double x,y; uint32_t us;} Event;
typedef struct {Event events[8]; unsigned count,posts; double x,y; int32_t results[3];} Sink;
static int32_t post(void *opaque,bool left,bool right) {
    Sink *s=opaque; assert(s->count<8 && s->posts<3);
    s->events[s->count++]=(Event){'p',left,right,s->x,s->y,0};
    return s->results[s->posts++];
}
static void pause_click(void *opaque,uint32_t us) {
    Sink *s=opaque; assert(s->count<8);
    s->events[s->count++]=(Event){'w',false,false,s->x,s->y,us};
}
int main(void) {
    for(unsigned secondary=0;secondary<2;secondary++) {
        for(unsigned held=0;held<2;held++) {
            Sink s={.x=859.125,.y=375.75,.results={-17,23,-41}};
            MacWSAtomicPointerClickResult r=MacWSPostAtomicPointerClick(
                secondary,held,post,pause_click,&s);
            unsigned down=held?0:1;
            unsigned downPost=held?0:1;
            assert(s.count==(held?3:4) && s.posts==(held?2:3));
            if(!held) {
                assert(s.events[0].kind=='p' && !s.events[0].left && !s.events[0].right);
            }
            assert(s.events[down].kind=='p');
            assert(s.events[down].left==!secondary && s.events[down].right==!!secondary);
            assert(s.events[down+1].kind=='w' && s.events[down+1].us==2000);
            assert(s.events[down+2].kind=='p' && !s.events[down+2].left && !s.events[down+2].right);
            // Even a failing pre-motion is not invented as successful: the
            // existing down/up results and click sequence remain transparent.
            // A held-left stream keeps its previous click sequence without
            // the prep callback.
            assert(r.downResult==s.results[downPost] && r.upResult==s.results[downPost+1]);
            unsigned pressed=0;
            for(unsigned i=0;i<s.count;i++) {
                assert(s.events[i].x==s.x && s.events[i].y==s.y);
                pressed+=s.events[i].kind=='p' && (s.events[i].left||s.events[i].right);
            }
            assert(pressed==1); // Pre-motion is not a second click.
        }
    }
    puts("atomic-pointer-click PASS: move/down/2000us/up, held-left no prep, result transparency");
    return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix="macws-atomic-click-") as tmp:
            c = pathlib.Path(tmp) / "test.c"
            binary = pathlib.Path(tmp) / "test"
            c.write_text(source)
            subprocess.run([compiler, "-std=c11", "-Wall", "-Wextra", "-Werror",
                            "-I", str(ROOT / "include"), str(c), "-o", str(binary)],
                           check=True, capture_output=True)
            result = subprocess.run([str(binary)], check=True, capture_output=True,
                                    text=True, timeout=10)
            self.assertIn("atomic-pointer-click PASS", result.stdout)

    def test_proxy_uses_same_transaction_and_real_position(self):
        source = (ROOT / "libmachook/mac_hooks.m").read_text()
        adapter = source.split("static int32_t macws_vnc_atomic_pointer_post", 1)[1].split(
            "static void macws_vnc_atomic_pointer_pause", 1)[0]
        self.assertIn("context->postMouse(context->point, true, 3, left, right, false)", adapter)
        self.assertNotIn("macws_vnc_atomic_pointer_activate", source)
        listener = source.split("static void *macws_vnc_pointer_proxy_listener", 1)[1]
        tap = listener.split("case MacWSInputKindTap:", 1)[1].split("default:", 1)[0]
        self.assertIn("MacWSVNCAtomicPointerContext context = {postMouse, point}", tap)
        self.assertIn("record.kind == MacWSInputKindSecondaryTap, leftDown", tap)
        self.assertIn("MacWSPostAtomicPointerClick(", tap)
        self.assertNotIn("coordinate_activation", tap)
        self.assertNotIn("leftDown =", tap)
        self.assertNotIn("activeContact =", tap)


if __name__ == "__main__":
    unittest.main()

"""Executable coordinate-domain contract for fullscreen touch and RFB input."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PointerActivationContract(unittest.TestCase):
    def test_both_producers_use_the_same_frame_local_contract(self):
        source = (ROOT / 'libmachook/mac_hooks.m').read_text()
        self.assertIn('macws_vnc_coordinate_activation(&record)', source)
        self.assertIn('macws_vnc_coordinate_activation(&pointer)', source)
        self.assertNotIn('macws_vnc_coordinate_activation(point)', source)
        start = source.index('static BOOL macws_vnc_coordinate_activation(',
                             source.index('static int macws_vnc_activation_reply_socket'))
        body = source[start:source.index('static BOOL macws_vnc_forward_input', start)]
        self.assertIn('MacWSGlobalActivationRecord(pointer,', body)
        self.assertNotIn('macws_rfbScreen', body)

    @unittest.skipUnless(shutil.which('clang'), 'C compiler required')
    def test_compiled_activation_hits_the_native_mouse_point(self):
        fixture = r'''
#include "macws_pointer_activation.h"
#include <assert.h>
#include <string.h>

int main(void) {
    const unsigned extents[][2] = {{1194,834},{2388,1668},{2985,2085},{1,1}};
    const double origins[][2] = {{0,0},{-1200,37},{480,-216}};
    for (unsigned s=0; s<4; ++s) {
        for (unsigned o=0; o<3; ++o) {
            for (unsigned n=0; n<5; ++n) {
                MacWSInputRecord pointer = {
                    .x = (extents[s][0]-1) * n / 4.0f,
                    .y = (extents[s][1]-1) * (4-n) / 4.0f,
                    .frameWidth=extents[s][0], .frameHeight=extents[s][1],
                    .targetPID=12345, .sceneID=UINT64_MAX,
                    .flags=UINT16_MAX, .contactID=99,
                };
                MacWSInputRecord activation;
                assert(MacWSGlobalActivationRecord(&pointer, 10.5, 17, &activation));
                double nativeX=origins[o][0]+pointer.x/pointer.frameWidth*1194.0;
                double nativeY=origins[o][1]+pointer.y/pointer.frameHeight*834.0;
                double hitX=origins[o][0]+activation.x/activation.frameWidth*1194.0;
                double hitY=origins[o][1]+activation.y/activation.frameHeight*834.0;
                assert(fabs(nativeX-hitX)<0.0001 && fabs(nativeY-hitY)<0.0001);
                assert(activation.targetPID==0 && activation.flags==0);
                assert(MacWSInputWindowIDForScene(activation.sceneID)==0);
                assert(activation.source==MacWSInputSourceVNC);
                assert(activation.contactID==99 && activation.sampleSequence==17);
                assert(activation.kind==MacWSInputKindActivateTarget);
                assert(activation.version==MACWS_INPUT_LEGACY_VERSION);
            }
        }
    }
    MacWSInputRecord p={.x=-3,.y=999,.frameWidth=100,.frameHeight=200}, a;
    assert(MacWSGlobalActivationRecord(&p,1,1,&a) && a.x==0 && a.y==199);
    assert(!MacWSGlobalActivationRecord(NULL,1,1,&a));
    assert(!MacWSGlobalActivationRecord(&p,1,1,NULL));
    assert(!MacWSGlobalActivationRecord(&p,1,0,&a));
    assert(!MacWSGlobalActivationRecord(&p,NAN,1,&a));
    p.x=NAN; assert(!MacWSGlobalActivationRecord(&p,1,1,&a));
    p.x=0; p.y=INFINITY; assert(!MacWSGlobalActivationRecord(&p,1,1,&a));
    p.y=0; p.frameWidth=0; assert(!MacWSGlobalActivationRecord(&p,1,1,&a));
    p.frameWidth=8193; assert(!MacWSGlobalActivationRecord(&p,1,1,&a));
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'activation-contract'
            subprocess.run(['clang', '-x', 'c', '-', '-O2', '-Wall', '-Wextra',
                            '-Werror', '-I', str(ROOT / 'include'), '-o', str(binary)],
                           input=fixture, text=True, capture_output=True, check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    unittest.main()

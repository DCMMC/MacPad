"""App Expose owns scroll while its native Dock modal handler is active."""

from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
DOCK = (ROOT / 'libmachook/AppInputBridge.m').read_text()
HOST = (ROOT / 'MacWSHost/Rendering/MacWSMetalView.m').read_text()


class DockExposeScrollOwnership(unittest.TestCase):
    def test_state_encodes_writer_and_modal_ownership(self):
        source = '''
#include "macws_dock_expose_notify.h"
#include <assert.h>
int main(void) {
    uint64_t active = MacWSDockExposeState(32793, true);
    assert(MacWSDockExposeWriter(active) == 32793);
    assert(MacWSDockExposeIsActive(active));
    uint64_t idle = MacWSDockExposeState(32793, false);
    assert(MacWSDockExposeWriter(idle) == 32793);
    assert(!MacWSDockExposeIsActive(idle));
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'dock_expose_contract'
            subprocess.run(
                ['clang', '-std=c11', '-I', str(ROOT / 'include'), '-x',
                 'c', '-', '-o', str(binary)], input=source, text=True,
                check=True, capture_output=True)
            subprocess.run([str(binary)], check=True, capture_output=True)

    def test_dock_publishes_only_native_expose_handler(self):
        router = DOCK.split('static void MacWSDockModalEventRouterWitness(', 1)[1]
        router = router.split('static BOOL MacWSInstallDockModalEventWitness', 1)[0]
        self.assertIn('_TtC4Dock21ExposeEventController', router)
        self.assertIn('MacWSPublishDockExposeState(exposeActive);', router)
        self.assertIn('if (windowCount > 0 && windows && topLayers', router)
        self.assertIn('topLayers[0] && exposeActive', router)
        self.assertIn('MacWSPublishDockExposeState(NO);', DOCK)
        self.assertIn('notify_set_state(MacWSDockExposeStateToken,', DOCK)
        self.assertIn('MacWSDockModalAddHandlerWitness', DOCK)
        self.assertIn('MacWSDockModalRemoveHandlerWitness', DOCK)
        self.assertIn('MacWSPublishDockCurrentModalHandler(self);', DOCK)
        self.assertIn('"v32@0:8@16B24B28"', DOCK)
        self.assertIn('"v24@0:8@16"', DOCK)

    def test_host_suppresses_whole_scroll_transaction_not_pointer_drag(self):
        reader = HOST.split('- (BOOL)dockExposeOwnsScroll {', 1)[1]
        reader = reader.split('- (void)emitScrollAtFramePoint:', 1)[0]
        self.assertIn('MacWSStreamModeFullscreen', reader)
        self.assertIn('notify_get_state(_dockExposeStateToken, &state)', reader)
        self.assertIn('writerPID == [self dockSystemGestureTargetPID]', reader)
        self.assertIn('MacWSAppInputEndpointReady(writerPID)', reader)

        emitter = HOST.split('directionMultiplier:(CGFloat)direction {', 1)[1]
        emitter = emitter.split('- (void)consumeIndirectScrollTranslation:', 1)[0]
        self.assertIn('BOOL exposeOwnsScroll = [self dockExposeOwnsScroll];',
                      emitter)
        self.assertIn('_scrollSuppressedByDockExpose = exposeOwnsScroll;', emitter)
        self.assertIn('else if (exposeOwnsScroll && !_scrollSuppressedByDockExpose)',
                      emitter)
        self.assertIn('[self stopScrollMomentumWithTerminalPhase:NO];', emitter)
        self.assertIn('if (_scrollSuppressedByDockExpose) {', emitter)
        self.assertLess(emitter.index('if (_scrollSuppressedByDockExpose) {'),
                        emitter.index('[self.statusDelegate metalView:self emittedInput:record];'))
        momentum = HOST.split('directionMultiplier:(CGFloat)directionMultiplier {',
                              1)[1].split('- (void)scrollMomentumTick:', 1)[0]
        self.assertIn('if (_scrollSuppressedByDockExpose) return;', momentum)
        pointer = HOST.split('- (BOOL)routeFullscreenInputRecord:', 1)[1]
        pointer = pointer.split('- (void)updatePointerVisibility', 1)[0]
        self.assertNotIn('_scrollSuppressedByDockExpose',
                         pointer)


if __name__ == '__main__':
    unittest.main()

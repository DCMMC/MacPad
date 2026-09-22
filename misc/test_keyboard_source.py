"""Execute the UIKit producer's distinction between edges and inferred sides."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class KeyboardSourceTests(unittest.TestCase):
    def test_real_producer_state_machine(self):
        compiler = shutil.which("clang") or shutil.which("cc")
        if not compiler:
            self.skipTest("C compiler unavailable")
        program = r'''
#include "macws_keyboard_source.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
static uint8_t apply(MacWSKeyboardSourceState *s, uint32_t flags,
                     uint8_t down, uint8_t up) {
    uint8_t output = 0xa5;
    assert(MacWSKeyboardSourceApply(s, flags, down, up, &output));
    assert(!(s->observedSides & s->inferredSides));
    assert(!(s->inferredSides & 0xaa));
    assert(output == (uint8_t)(s->observedSides | s->inferredSides));
    return output;
}
int main(void) {
    MacWSKeyboardSourceState s = {0};
    // Acquire with right Control already held: its missing down allows only
    // aggregate knowledge, not a fictitious observed left-Control down.
    assert(apply(&s, MacWSKeyboardControl, 0, 0) == 4);
    assert(s.observedSides == 0 && s.inferredSides == 4);
    // The real right release retires that inference, even with pre-edge flags.
    assert(apply(&s, MacWSKeyboardControl, 0, 8) == 0);
    assert(s.observedSides == 0 && s.inferredSides == 0);

    // Genuine left+right holds retain the other real side on either release.
    assert(apply(&s, 0, 4, 0) == 4);
    assert(apply(&s, MacWSKeyboardControl, 8, 0) == 12);
    assert(apply(&s, MacWSKeyboardControl, 0, 8) == 4);
    assert(s.observedSides == 4 && s.inferredSides == 0);
    assert(apply(&s, MacWSKeyboardControl, 0, 4) == 0);
    assert(apply(&s, 0, 0, 0) == 0);

    // Edge promotion replaces inferred-left by the actual right side.
    assert(apply(&s, MacWSKeyboardControl, 0, 0) == 4);
    assert(apply(&s, MacWSKeyboardControl, 8, 0) == 8);
    assert(s.observedSides == 8 && s.inferredSides == 0);
    assert(apply(&s, MacWSKeyboardControl, 0, 0) == 8);
    // A later aggregate-free event repairs a missing key-up.
    assert(apply(&s, 0, 0, 0) == 0);

    // Two opposite side edges in the same event use their post-edge state.
    assert(apply(&s, 0, 4, 0) == 4);
    assert(apply(&s, MacWSKeyboardControl, 8, 4) == 8);
    assert(apply(&s, MacWSKeyboardControl, 4, 8) == 4);
    // A release in one pair must not retire another pair's held evidence.
    assert(apply(&s, MacWSKeyboardControl | MacWSKeyboardShift, 2, 0) == 6);
    assert(apply(&s, MacWSKeyboardControl | MacWSKeyboardShift, 0, 4) == 2);
    assert(apply(&s, MacWSKeyboardShift, 0, 2) == 0);

    // Every left/right inferred-release and genuine-down combination.
    for (unsigned side = 0; side < 8; side++) {
        s = (MacWSKeyboardSourceState){0};
        uint8_t bit = (uint8_t)(1u << side);
        uint32_t flag = MacWSKeyboardFlagsForSides(bit);
        assert(apply(&s, flag, 0, 0) == (uint8_t)(1u << ((side/2)*2)));
        assert(apply(&s, flag, 0, bit) == 0);
        assert(apply(&s, 0, bit, 0) == bit); // pre-edge down flags
        assert(apply(&s, flag, 0, bit) == 0); // pre-edge up flags
    }
    for (unsigned held = 0; held < 256; held++) {
        for (unsigned side = 0; side < 8; side++) {
            uint8_t bit = (uint8_t)(1u << side);
            s = (MacWSKeyboardSourceState){.observedSides = (uint8_t)held};
            uint32_t flags = MacWSKeyboardFlagsForSides((uint8_t)held);
            assert(apply(&s, flags, 0, 0) == held);
            // Unrelated genuine sides remain held; pre-edge aggregate flags
            // cannot resurrect this released side or its inferred sibling.
            assert(apply(&s, flags, 0, bit) == (uint8_t)(held & ~bit));
        }
    }
    // Non-modifier flags are not mistaken for physical modifier sides.
    s = (MacWSKeyboardSourceState){0};
    assert(apply(&s, 0x10000u | 0x800000u, 0, 0) == 0);

    // Drawable/input suspension permits owned releases, never new ownership
    // or new modifier downs. Both actual and aggregate-only release work.
    uint8_t ownedOutput = 0xa5;
    s = (MacWSKeyboardSourceState){.observedSides = 8};
    assert(MacWSKeyboardSourceApplyOwned(&s, false, true,
        MacWSKeyboardControl, 0, 8, &ownedOutput));
    assert(ownedOutput == 0);
    assert(MacWSKeyboardSourceApplyOwned(&s, false, true,
        MacWSKeyboardCommand, 64, 0, &ownedOutput));
    assert(ownedOutput == 0);
    s.observedSides = 4;
    assert(MacWSKeyboardSourceApplyOwned(&s, false, true,
        0, 0, 0, &ownedOutput));
    assert(ownedOutput == 0);
    ownedOutput = 0xa5;
    assert(!MacWSKeyboardSourceApplyOwned(&s, false, false,
        MacWSKeyboardControl, 4, 0, &ownedOutput));
    assert(ownedOutput == 0xa5 && s.observedSides == 0);
    assert(MacWSKeyboardSourceApplyOwned(&s, true, false,
        0, 8, 0, &ownedOutput));
    assert(ownedOutput == 8);

    uint8_t output = 0xa5;
    s = (MacWSKeyboardSourceState){.observedSides = 8, .inferredSides = 1};
    MacWSKeyboardSourceState before = s;
    assert(!MacWSKeyboardSourceApply(&s, 0, 4, 4, &output));
    assert(!memcmp(&s, &before, sizeof(s)) && output == 0xa5);
    assert(!MacWSKeyboardSourceApply(NULL, 0, 0, 0, &output));
    assert(!MacWSKeyboardSourceApply(&s, 0, 0, 0, NULL));
    assert(!memcmp(&s, &before, sizeof(s)) && output == 0xa5);
    puts("keyboard-source PASS: inferred-vs-observed, pre-edge flags, 2048 real-side releases");
    return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix="macws-keyboard-source-") as tmp:
            path = Path(tmp)
            (path / "probe.c").write_text(program)
            subprocess.run([compiler, "-std=c11", "-O2", "-Wall", "-Wextra",
                            "-Werror", "-fsanitize=undefined", "-I",
                            str(ROOT / "include"), str(path / "probe.c"),
                            "-o", str(path / "probe")], check=True,
                           capture_output=True)
            result = subprocess.run([str(path / "probe")], check=True,
                                    capture_output=True, text=True, timeout=5)
            self.assertIn("2048 real-side releases", result.stdout)


if __name__ == "__main__":
    unittest.main()

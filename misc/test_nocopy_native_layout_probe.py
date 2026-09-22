"""Observer contract only: owned CPU fixture, no GPU or remote process access."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "misc/macws_nocopy_native_layout_probe.m"


class NativeNoCopyLayoutProbeTests(unittest.TestCase):
    def test_probe_is_explicit_bounded_and_not_production(self):
        source = SOURCE.read_text()
        self.assertNotIn(SOURCE.stem, (ROOT / "libmachook/Makefile").read_text())
        self.assertIn('alarm(10)', source)
        self.assertIn('strcmp(argv[1], "--observe")', source)
        self.assertIn('exact_type(method, 5, "I")', source)
        self.assertIn('unsigned char snapshot[104]', source)
        self.assertIn('sample <= 8', source)
        self.assertIn('@finally', source)
        self.assertIn('method_setImplementation(method, (IMP)original_init)', source)
        for forbidden in ('newCommandQueue', 'commandBuffer', 'MSHookFunction',
                          'task_for_pid', 'kill(', 'MACWS_AGX_KEEP_PINNED_ALLOC'):
            self.assertNotIn(forbidden, source)

    @unittest.skipUnless(sys.platform == 'darwin', 'requires Objective-C Foundation')
    def test_actual_wrapper_preserves_arguments_result_errno_and_budget(self):
        fixture = r'''
#define main unused_native_layout_probe_main
#include "macws_nocopy_native_layout_probe.m"
#undef main
#include <assert.h>
static unsigned calls;
static void *expected_arguments;
static id fixture_original(id self, SEL selector, id device, NSUInteger options,
                          const void *arguments, uint32_t size) {
    assert(self == device);
    assert(selector == sel_registerName("fixture"));
    assert(arguments == expected_arguments && size == 96 && options == 0x123);
    assert(errno == EAGAIN);
    calls++;
    ((unsigned char *)arguments)[0x14] = 0x70;
    errno = EINTR;
    return self;
}
int main(void) {
    @autoreleasepool {
        id object = [[NSObject alloc] init];
        unsigned char arguments[96];
        original_init = fixture_original;
        expected_arguments = arguments;
        for (unsigned i = 0; i < 10; i++) {
            memset(arguments, 0xa5, sizeof(arguments));
            errno = EAGAIN;
            id result = observed_init(object, sel_registerName("fixture"), object,
                                      0x123, arguments, sizeof(arguments));
            assert(result == object && errno == EINTR);
            for (unsigned j = 0; j < sizeof(arguments); j++)
                assert(arguments[j] == (j == 0x14 ? 0x70 : 0xa5));
        }
        assert(calls == 10 && observations == 10);
        [object release];
    }
    puts("OBSERVER-CONTRACT PASS");
    return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix='macws-nocopy-observer-') as directory:
            path = Path(directory)
            (path / 'fixture.m').write_text(fixture)
            command = ['xcrun', 'clang', '-O2', '-Wall', '-Wextra', '-Werror',
                       '-fno-objc-arc', '-fblocks', '-I', str(SOURCE.parent),
                       '-framework', 'Foundation', '-framework', 'Metal',
                       str(path / 'fixture.m'), '-o', str(path / 'fixture')]
            subprocess.run(command, check=True, capture_output=True, text=True)
            result = subprocess.run([str(path / 'fixture')], check=True,
                                    capture_output=True, text=True, timeout=10)
        self.assertIn('OBSERVER-CONTRACT PASS', result.stdout)
        self.assertEqual(result.stdout.count('NATIVE-RESOURCE sample='), 8)
        self.assertEqual(result.stdout.count('NATIVE-RESOURCE-AFTER sample='), 8)
        self.assertIn('+10:a5a5a5a5a5a5a5a5', result.stdout)
        self.assertIn('+10:a5a5a570a5a5a5a5', result.stdout)


if __name__ == '__main__':
    unittest.main()

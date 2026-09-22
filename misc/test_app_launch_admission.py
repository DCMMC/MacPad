"""Compile the actual launch admission loop against deterministic witnesses."""
from pathlib import Path
import shutil
import plistlib
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'macwshostd/main.m').read_text()
START = SOURCE.rindex('static BOOL WaitForInitialApplicationWindow(')
FUNCTION = SOURCE[START:SOURCE.index('\n}', START) + 2]


class AppLaunchAdmission(unittest.TestCase):
    def test_native_metal_profile_covers_real_app_bundles(self):
        start = SOURCE.index('static BOOL RootApplicationRequiresNativeMetal(')
        function = SOURCE[start:SOURCE.index('\n}', start) + 2]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name, identifier in [('Bench', 'com.primatelabs.Geekbench6'),
                                     ('Other', 'org.example.other')]:
                contents = root / f'{name}.app/Contents'
                (contents / 'MacOS').mkdir(parents=True)
                (contents / 'Info.plist').write_bytes(plistlib.dumps(
                    {'CFBundleIdentifier': identifier}))
            program = '''
#import <Foundation/Foundation.h>
#include <assert.h>
static const char *kRootFS;
#define HostLog(...) ((void)0)
''' + function + '''
int main(int argc, char **argv) { @autoreleasepool {
    kRootFS = argv[1];
    assert(RootApplicationRequiresNativeMetal(@"/Bench.app/Contents/MacOS/Bench"));
    assert(RootApplicationRequiresNativeMetal(@"/Other.app/Contents/MacOS/Other"));
    assert(!RootApplicationRequiresNativeMetal(@"/Missing.app/Contents/MacOS/App"));
    assert(!RootApplicationRequiresNativeMetal(@"/Bench.app/Contents/Resources/worker"));
    return 0;
} }
'''
            binary = root / 'profile'
            subprocess.run(['clang', '-x', 'objective-c', '-', '-fobjc-arc',
                            '-framework', 'Foundation', '-o', str(binary)],
                           input=program, text=True, capture_output=True, check=True)
            subprocess.run([str(binary), folder], check=True)

    @unittest.skipUnless(shutil.which('cc'), 'C compiler required')
    def test_actual_loop_event_race_exit_and_deadline(self):
        prefix = r'''
#include <assert.h>
#include <stdio.h>
#include <stdbool.h>
#include <sys/types.h>
#include <limits.h>
typedef double CFAbsoluteTime;
typedef double NSTimeInterval;
typedef bool BOOL;
#define YES true
#define NO false
#define MIN(a,b) ((a)<(b)?(a):(b))
#define HostLog(...) ((void)0)
static double now, window_at, endpoint_at, exit_at, sent_at;
static int requests;
static CFAbsoluteTime CFAbsoluteTimeGetCurrent(void) { return now; }
static BOOL WaitForWindowMetrics(pid_t pid, NSTimeInterval duration, int *status) {
    assert(duration >= 0 && pid == 42);
    if (now >= window_at) return true;
    if (now >= exit_at) { *status = 0; return false; }
    now += duration;
    return false;
}
static BOOL IsSocket(const char *path) { return now >= endpoint_at; }
static BOOL RequestApplicationReopen(pid_t pid, NSTimeInterval remaining) {
    assert(pid == 42 && remaining > 0);
    requests++; sent_at = now;
    return true;
}
static void reset(void) {
    now = 0; window_at = endpoint_at = exit_at = 100;
    sent_at = -1; requests = 0;
}
'''
        suffix = r'''
int main(void) {
    int status = -1;
    reset(); endpoint_at = 0.4;
    assert(WaitForInitialApplicationWindow(42, 30, &status));
    assert(requests == 1 && sent_at < 0.51); /* not the old three-second grace */
    reset(); window_at = 0; endpoint_at = 0;
    assert(WaitForInitialApplicationWindow(42, 30, &status));
    assert(requests == 0); /* existing real window wins, no duplicate reopen */
    reset(); exit_at = 0.1;
    assert(!WaitForInitialApplicationWindow(42, 30, &status));
    assert(requests == 0 && now < 0.2);
    reset(); status = -1;
    assert(!WaitForInitialApplicationWindow(42, 0.25, &status));
    assert(requests == 0 && now < 0.31);
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as folder:
            binary = Path(folder) / 'admission'
            subprocess.run(['cc', '-x', 'c', '-', '-O2', '-o', str(binary)],
                           input=prefix + FUNCTION + suffix, text=True,
                           capture_output=True, check=True)
            subprocess.run([str(binary)], capture_output=True, check=True)

    def test_document_delivery_branch_remains_separate(self):
        method = SOURCE.split('static BOOL LaunchRootExecutable(', 1)[1]
        self.assertIn('if (documentOpenPending)', method)
        self.assertNotIn('NSTimeInterval initialWindowTimeout = MIN(timeout, 3.0)', method)
        self.assertIn('WaitForInitialApplicationWindow(pid, timeout,', method)

    def test_dynamic_plugins_are_admitted_before_spawn_not_reuse(self):
        method = SOURCE.split('static BOOL LaunchRootExecutable(', 1)[1]
        self.assertLess(method.index('FindRunningRootExecutable(rootPath)'),
                        method.index('PrepareSystemApplicationCode(rootPath, message)'))
        self.assertLess(method.index('PrepareSystemApplicationCode(rootPath, message)'),
                        method.index('SpawnMacOSApplication('))
        preparation = SOURCE.split('static BOOL PrepareSystemApplicationCode(', 1)[1].split(
            'static BOOL LaunchRootExecutable(', 1)[0]
        self.assertIn('macws_system_app_prepare.py', preparation)
        self.assertIn('result != 0', preparation)
        self.assertNotIn('ldid', preparation)

    def test_runningboard_live_capability_requires_installed_hook(self):
        tweak = (ROOT / 'MacWSCatalystLaunch/Tweak.x').read_text()
        constructor = tweak.split('%ctor {', 1)[1]
        self.assertLess(constructor.index('if (!method) return;'),
                        constructor.index('%init;'))
        self.assertLess(constructor.index('%init;'), constructor.index(
            'if (method_getImplementation(method) == original) return;'))
        self.assertLess(constructor.index(
            'if (method_getImplementation(method) == original) return;'),
            constructor.index('MacWSPublishRunningBoardBridgeReadiness();'))
        self.assertTrue('MACWS_SETTINGS_BRIDGE_REFRESH_NAME' in constructor,
                        'live producer must answer a capability refresh')
        start = SOURCE.index('static pid_t RunningBoardSettingsBridgePublisherPID(')
        consumer = SOURCE[start:SOURCE.index('\n}', start) + 2]
        self.assertTrue('MacWSSettingsBridgeLiveCapabilities(&state)' in consumer,
                        'hostd must verify the live publisher')
        self.assertFalse('macws-runningboard-settings-bridge.ready' in SOURCE + tweak,
                         'Settings readiness must not depend on a file')

    @unittest.skipUnless(shutil.which('cc'), 'C compiler required')
    def test_runningboard_capability_protocol_rejects_other_daemon_and_old_abi(self):
        program = r'''
#include "macws_settings_bridge_protocol.h"
#include "macws_windowing_protocol.h"
#include <assert.h>
int main(void) {
    uint64_t state = MacWSSettingsBridgeState(500);
    assert(MacWSSettingsBridgeStateSupports(state));
    assert(MacWSSettingsBridgePublisher(state) == 500);
    assert(!MacWSSettingsBridgeStateSupports(0));
    assert(!MacWSSettingsBridgeStateSupports(MacWSSettingsBridgeState(1)));
    assert(!MacWSSettingsBridgeStateSupports(state ^ (UINT64_C(1) << 40)));
    assert(!MacWSSettingsBridgeStateSupports(state & ~(UINT64_C(1) << 32)));
    assert(!MacWSSettingsBridgeStateSupports(
        MacWSWindowingState(500, MacWSWindowingRequired)));
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'settings-bridge-protocol'
            subprocess.run(['cc', '-x', 'c', '-', '-I', str(ROOT / 'include'),
                            '-O2', '-o', str(binary)], input=program, text=True,
                           capture_output=True, check=True)
            subprocess.run([str(binary)], capture_output=True, check=True)


if __name__ == '__main__':
    unittest.main()

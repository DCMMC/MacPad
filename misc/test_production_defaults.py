"""Production guardrails; not a substitute for on-device performance tests."""
from pathlib import Path
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import audit_runtime_switches as audit

ROOT = Path(__file__).resolve().parents[1]


class ProductionDefaults(unittest.TestCase):
    def test_all_application_subprojects_load_defaults_before_theos(self):
        for path in [ROOT / 'Makefile', *ROOT.glob('*/Makefile')]:
            source = path.read_text()
            if 'include $(THEOS)/makefiles/common.mk' not in source:
                continue
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertLess(source.index('config/production.mk'),
                                source.index('include $(THEOS)/makefiles/common.mk'))

    @unittest.skipUnless(shutil.which('gmake') or shutil.which('make'), 'make required')
    def test_build_defaults_and_explicit_overrides(self):
        make = shutil.which('gmake') or shutil.which('make')
        fixture = (f'include {ROOT}/config/production.mk\n'
                   'all:\n\t@echo $(FINALPACKAGE) $(OPTFLAG) $(DEBUG)\n')
        environment = os.environ.copy()
        for name in ('FINALPACKAGE', 'OPTFLAG', 'DEBUG', 'MAKEFLAGS', 'MFLAGS'):
            environment.pop(name, None)
        for flags, expected in (([], '1 -O2'),
                                (['STRIP=0'], '1 -O2'),
                                (['DEBUG=1', 'OPTFLAG=-O0'], '1 -O0 1'),
                                (['FINALPACKAGE=0'], '0 -O2')):
            with self.subTest(flags=flags):
                result = subprocess.run([make, '--no-print-directory', '-f', '-', *flags],
                                        input=fixture, text=True, capture_output=True,
                                        env=environment, check=True)
                self.assertEqual(result.stdout.strip(), expected)

    def test_shipped_plists_have_no_off_switches(self):
        self.assertFalse(audit.production_environment_errors(
            audit.load_manifest(), audit.production_plists()))
        self.assertFalse(audit.production_argument_errors(audit.production_plists()))
        self.assertIn(ROOT / 'misc/com.macwsguide.vscode.plist', audit.production_plists())
        self.assertIn(ROOT / 'misc/com.macwsguide.chrome150.plist', audit.production_plists())

    def test_remote_debugging_arguments_are_rejected_in_shipped_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'job.plist'
            for arguments in (
                ['Electron', '--remote-debugging-port=9222'],
                ['Electron', '--remote-debugging-port=0'],
                ['Chrome', '--remote-debugging-port', '9223'],
                ['Chrome', '--remote-debugging-pipe'],
                ['Chrome', '--remote-allow-origins=*'],
                ['Chrome', '--remote-allow-origins', 'http://localhost:9223'],
            ):
                with self.subTest(arguments=arguments):
                    path.write_bytes(plistlib.dumps({'ProgramArguments': arguments}))
                    errors = audit.production_argument_errors([path])
                    self.assertEqual(len(errors), 1)
                    self.assertIn('production=off argument', errors[0])
            path.write_bytes(plistlib.dumps({'ProgramArguments': [
                'Electron', '--use-angle=metal', '--disable-gpu-sandbox',
                '--remote-debugging-portability',
                'https://example.invalid/?--remote-debugging-port=9222',
            ]}))
            self.assertEqual(audit.production_argument_errors([path]), [])

    def test_argument_audit_rejects_malformed_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'job.plist'
            for arguments in ('not-an-array', [1], {'--remote-debugging-port': '0'}):
                path.write_bytes(plistlib.dumps({'ProgramArguments': arguments}))
                self.assertEqual(len(audit.production_argument_errors([path])), 1)

    def test_web_url_route_uses_private_extension_socket_not_cdp(self):
        host = (ROOT / 'macwshostd/main.m').read_text()
        route = host.split('static BOOL SendWebURLToVSCodeExtension(', 1)[1]
        route = route.split('static void ActivateVSCodeAfterWebOpen(', 1)[0]
        self.assertIn('socket(AF_UNIX, SOCK_STREAM, 0)', route)
        self.assertIn('kVSCodeURLSocket', route)
        protocol = (ROOT / 'include/macws_control_protocol.h').read_text()
        self.assertIn('"/private/tmp/macws_vscode_url.sock"', protocol)
        extension = (ROOT / 'misc/vscode-aquarium-runner/extension.js').read_text()
        self.assertIn('const urlSocketPath = "/private/tmp/macws_vscode_url.sock"', extension)
        self.assertIn('server.listen(urlSocketPath', extension)
        self.assertIn('vscode.commands.executeCommand("simpleBrowser.show", url)', extension)
        for source in (route, extension):
            self.assertNotIn('9222', source)
            self.assertNotIn('/json/list', source)

    def test_vscode_preflight_does_not_require_retired_namespace_optin(self):
        source = (ROOT / 'layout/usr/macOS/bin/macos_gui.sh').read_text()
        preflight = source.split('production_preflight() {', 1)[1]
        block = re.search(r'        for key in MACWS_JIT_MPROTECT_COMPAT.*?\n        done',
                          preflight, re.S).group(0)
        fixture = '''bad=0
VSCODE_PLIST=fixture
log() { printf '%s\\n' "$*"; }
plutil() {
    printf '%s\\n' 'MACWS_JIT_MPROTECT_COMPAT = 1;' \\
        'MACWS_JIT_FAULT_WRITE_COMPAT = 1;' \\
        'MACWS_AMFI_IMMOVABLE_TASK_PORT_COMPAT = 1;' \\
        'MACWS_MACOS_SYSTEM_POLICY_COMPAT = 1;'
}
'''
        result = subprocess.run(['bash'], input=fixture + block + '\nexit "$bad"\n',
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_boot_local_switches_do_not_use_persistent_preferences(self):
        sources = {
            'MacWSWindowing/Tweak.x': (
                '/tmp/com.macwsguide.dense-grid.disabled',
            ),
            'MacWSHost/main.m': (
                '/tmp/iosclear_run',
            ),
            'MTLCompilerBypassOSCheck/Tweak.x': (
                '/tmp/macws_mtlcompiler_diagnostics',
                '/tmp/macws_mtlcompiler_hold',
            ),
        }
        persistent_prefixes = (
            '/var/mobile/Library/Preferences/com.macwsguide.dense-grid.',
            '/var/jb/var/mobile/macws_mtlcompiler_',
            '/var/mobile/macws_mtlcompiler_',
            '/var/mobile/iosclear_',
        )
        for relative, required in sources.items():
            with self.subTest(source=relative):
                text = (ROOT / relative).read_text()
                for path in required:
                    self.assertIn(path, text)
                for prefix in persistent_prefixes:
                    self.assertNotIn(prefix, text)

    def test_no_persistent_autosignd_or_layout_test_flags(self):
        for path in ('layout/usr/macOS/bin/restart_autosignd.sh',
                     'misc/run_type82_layout_test.sh'):
            source = (ROOT / path).read_text()
            with self.subTest(path=path):
                self.assertNotIn('/var/jb/var/mobile/.macws-autosignd-restart.lock',
                                 source)
                self.assertNotIn('/var/jb/var/mobile/macws_type82_test_start',
                                 source)

    def test_off_switch_is_rejected_even_when_set_to_zero(self):
        manifest = {('env', 'MACWS_HOST_DIAGNOSTICS'): ('off', 'test', 'test')}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'job.plist'
            for value in ('0', '1', ''):
                path.write_bytes(plistlib.dumps({'EnvironmentVariables': {
                    'MACWS_HOST_DIAGNOSTICS': value}}))
                self.assertEqual(len(audit.production_environment_errors(manifest, [path])), 1)

    def test_invalid_shipped_plist_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'job.plist'
            path.write_text('not a plist')
            self.assertEqual(len(audit.production_environment_errors({}, [path])), 1)

    def test_functional_sdl_disable_requires_exact_zero(self):
        manifest = {('env', 'SDL_JOYSTICK_HIDAPI'): ('off', 'test', 'test')}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'job.plist'
            for value, count in (('0', 0), ('1', 1), ('', 1)):
                path.write_bytes(plistlib.dumps({'EnvironmentVariables': {
                    'SDL_JOYSTICK_HIDAPI': value}}))
                self.assertEqual(len(audit.production_environment_errors(manifest, [path])), count)

    def test_source_scan_excludes_temporary_and_generated_trees(self):
        self.assertTrue({'tmp', '.theos', '.git', 'evidence'} <= audit.EXCLUDED_PARTS)

    def test_logging_is_bounded_and_file_io_is_off_caller(self):
        source = (ROOT / 'MacWSHost/Support/MacWSHostDiagnostics.m').read_text()
        logger = source.split('void MacWSLog(', 1)[1]
        caller, worker = logger.split('dispatch_async(queue, ^{', 1)
        for call in ('open(', 'stat(', 'dprintf(', 'rename(', 'NSLog('):
            self.assertNotIn(call, caller)
        self.assertIn('>= 128', caller)
        self.assertIn('message.length > 4096', caller)
        self.assertIn('4 * 1024 * 1024', worker)
        self.assertIn('log-overflow dropped=', worker)
        self.assertNotIn('NSLog(', logger)

    def test_inactive_frame_instrumentation_returns_before_lock(self):
        source = (ROOT / 'MacWSHost/MacWSPerformanceMonitor.m').read_text()
        method = source.split('- (void)recordBaseTransportFinalComposite:', 1)[1]
        self.assertLess(method.index('if (!atomic_load(&_instrumentationActive)) return;'),
                        method.index('os_unfair_lock_lock'))

    @unittest.skipUnless(sys.platform == 'darwin' and shutil.which('xcrun'),
                         'Apple Foundation/clang required')
    def test_compiled_switch_parser_and_lazy_log_arguments(self):
        # Execute the real C parser and real public macro, not a Python model.
        fixture = r'''
#import "MacWSHostDiagnostics.h"
#include "macws_diagnostics_policy.h"
#include <assert.h>
static BOOL enabled;
static int calls, arguments;
BOOL MacWSHostDiagnosticsEnabled(void) { return enabled; }
void MacWSLog(NSString *format, ...) { calls++; }
static NSString *expensive(void) { arguments++; return @"trace"; }
int main(void) {
    const char *off[] = {NULL, "", "0", "false", "FALSE", "no", "off", "garbage", "10"};
    const char *on[] = {"1", "true", "TRUE", "yes", "YES", "on", "ON"};
    for (unsigned i = 0; i < sizeof(off)/sizeof(*off); i++)
        assert(!MacWSDiagnosticSwitchEnabled(off[i]));
    for (unsigned i = 0; i < sizeof(on)/sizeof(*on); i++)
        assert(MacWSDiagnosticSwitchEnabled(on[i]));
    @autoreleasepool {
        MacWSDiagnosticLog(@"%@", expensive());
        assert(calls == 0 && arguments == 0);
        enabled = YES;
        MacWSDiagnosticLog(@"%@", expensive());
        assert(calls == 1 && arguments == 1);
    }
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'diagnostic-policy-test'
            subprocess.run(['xcrun', 'clang', '-x', 'objective-c', '-', '-O2',
                            '-fobjc-arc', '-framework', 'Foundation',
                            '-I', str(ROOT / 'include'),
                            '-I', str(ROOT / 'MacWSHost/Support'), '-o', str(binary)],
                           input=fixture, text=True, capture_output=True, check=True)
            subprocess.run([str(binary)], capture_output=True, check=True)


if __name__ == '__main__':
    unittest.main()

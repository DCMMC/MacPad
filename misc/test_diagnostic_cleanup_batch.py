"""Execute the real cleanup in bash with fake rm/find; never touch flag paths."""
from pathlib import Path
import shlex
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'layout/usr/macOS/bin/macos_gui.sh').read_text()
START = SOURCE.index('clear_diagnostic_state() {')
FUNCTION = SOURCE[START:SOURCE.index('\n}\n', START) + 2]
# Frozen pre-batch loop. The remaining cleanup commands are the same actual
# production body, so this comparison covers their ordering/status too.
OLD_PREFIX = '''clear_diagnostic_state() {
    local path
    diagnostic_flag_paths | while IFS= read -r path; do
        rm -f "$ROOTFS$path" "$path"
    done
'''
BASELINE = OLD_PREFIX + FUNCTION[FUNCTION.index('    rm -f "$MTLCOMPILER_DIAGNOSTICS"'):]


def execute(function, paths, *, failed_path='', find_status=0, list_status=0):
    generator = ("printf '%s\\n' " + ' '.join(map(shlex.quote, paths)) + '\n'
                 if paths else ':\n')
    script = r'''
set -u
ROOTFS='/fixture root'
LOGDIR='/fixture log'
MTLCOMPILER_DIAGNOSTICS='/fixture/compiler diagnostics'
MTLCOMPILER_HOLD='/fixture/compiler hold'
STEAM_ANGLE_ASSET_BUILD='/fixture/angle asset'
CATALYST_LAUNCH_TRACE='/fixture/catalyst trace'
record() { local kind="$1"; shift; printf '%s\0' "$kind" "$#" "$@"; }
rm() {
    record rm "$@"
    local operand
    for operand in "$@"; do
        if [ -n "$FAILED_PATH" ] && [ "$operand" = "$FAILED_PATH" ]; then
            return 7
        fi
    done
    return 0
}
find() { record find "$@"; return "$FIND_STATUS"; }
'''
    script += f'FAILED_PATH={shlex.quote(failed_path)}\nFIND_STATUS={find_status}\n'
    script += 'diagnostic_flag_paths() {\n' + generator + f'return {list_status}\n}}\n'
    script += function + r'''
set -- 'caller argument' 'untouched'
clear_diagnostic_state
result=$?
record caller "$@"
exit "$result"
'''
    result = subprocess.run(['bash'], input=script.encode(), capture_output=True,
                            timeout=10)
    fields = result.stdout.split(b'\0')
    events = []
    position = 0
    while position < len(fields) - 1:
        name = fields[position].decode()
        count = int(fields[position + 1])
        args = [value.decode() for value in fields[position + 2:position + 2 + count]]
        events.append((name, args))
        position += count + 2
    if position != len(fields) - 1 or fields[-1] != b'':
        raise AssertionError('malformed fake-poster output')
    return result.returncode, events, result.stderr


def operands(events):
    """Compare ordered targets and options, allowing only rm batching."""
    expanded = []
    for name, args in events:
        if name == 'rm':
            for path in args[1:]:
                expanded.append(('rm', args[0], path))
        else:
            expanded.append((name, tuple(args)))
    return expanded


class DiagnosticCleanupBatch(unittest.TestCase):
    def assert_equivalent(self, paths, **options):
        old = execute(BASELINE, paths, **options)
        new = execute(FUNCTION, paths, **options)
        self.assertEqual(new[0], old[0], (new, old))
        self.assertEqual(new[2], old[2])
        self.assertEqual(operands(new[1]), operands(old[1]))
        self.assertEqual(new[1][-1], ('caller', ['caller argument', 'untouched']))
        return old, new

    def test_actual_manifest_same_operands_fewer_process_invocations(self):
        manifest = ROOT / 'layout/usr/macOS/bin/macws_diagnostic_flags.sh'
        result = subprocess.run(['bash', '-c', 'source "$1"; macws_diagnostic_flag_paths',
                                 'test', str(manifest)], capture_output=True,
                                text=True, check=True, timeout=10)
        paths = result.stdout.splitlines()
        self.assertGreater(len(paths), 1)
        old, new = self.assert_equivalent(paths)
        old_calls = sum(name == 'rm' for name, _ in old[1])
        new_calls = sum(name == 'rm' for name, _ in new[1])
        self.assertEqual(old_calls - new_calls, len(paths) - 1)
        self.assertEqual(new[1][0], ('rm', ['-f', *[
            item for path in paths for item in ('/fixture root' + path, path)]]))

    def test_whitespace_glob_backslash_and_duplicate_operands_preserved(self):
        self.assert_equivalent(['/tmp/with spaces', '/tmp/a*[b]?', '/tmp/back\\slash',
                                '/private/tmp/alias', '/tmp/with spaces', '/tmp/line\ttab'])

    def test_empty_or_failed_generator_does_not_add_rm_or_abort_later_cleanup(self):
        for paths in ([], ['/tmp/first', '/tmp/second']):
            for status in (0, 7):
                with self.subTest(paths=paths, status=status):
                    old, new = self.assert_equivalent(paths, list_status=status)
                    self.assertEqual(new[0], 0)
                    if not paths:
                        self.assertEqual(new[1], old[1])

    def test_operand_failures_keep_later_cleanup_and_final_status(self):
        paths = ['/tmp/first', '/tmp/middle', '/tmp/last']
        for failed in paths:
            for spelling in (failed, '/fixture root' + failed):
                for final_status in (0, 9):
                    with self.subTest(failed=spelling, final_status=final_status):
                        _, new = self.assert_equivalent(
                            paths, failed_path=spelling, find_status=final_status)
                        self.assertEqual(new[0], final_status)
                        self.assertEqual(sum(name == 'find' for name, _ in new[1]), 2)

    def test_both_existing_startup_call_sites_remain(self):
        self.assertEqual(SOURCE.count('\n    clear_diagnostic_state\n'), 2)
        for name in ('cleanup_macos', 'production_preflight'):
            start = SOURCE.index(name + '() {')
            body = SOURCE[start:SOURCE.index('\n}\n', start) + 2]
            self.assertEqual(body.count('\n    clear_diagnostic_state\n'), 1)


if __name__ == '__main__':
    unittest.main()

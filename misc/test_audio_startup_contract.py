"""Execute the production audio-start block against a strict launchctl stub."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'layout/usr/macOS/bin/macos_gui.sh').read_text()
BLOCK = SOURCE.split(
    '    log "Publishing the private macOS audio catalog and native output bridge..."',
    1,
)[1].split('    log "TIMING start-macos stage=status-preferences', 1)[0]


class AudioStartup(unittest.TestCase):
    def run_start(self, fail_kickstart=False):
        with tempfile.TemporaryDirectory(prefix='macws-audio-contract-') as logs:
            preamble = r'''
LOGDIR="$1"
FAIL_KICKSTART="$2"
AUDIO_COMPONENT_REGISTRAR_PLIST=registrar.plist
COREAUDIOD_PLIST=coreaudiod.plist
AUDIO_OUTPUT_PLIST=output.plist
AUDIO_COMPONENT_REGISTRAR_LABEL=com.apple.macosbooter.audio.AudioComponentRegistrar
COREAUDIOD_LABEL=com.apple.audio.coreaudiod
AUDIO_OUTPUT_LABEL=com.macwsguide.audio-output
log() { printf '%s\n' "$*"; }
launchctl() {
    printf 'CALL %s %s\n' "$1" "$2"
    case "$1" in
        load|list) return 0 ;;
        kickstart)
            [ "$2" = "user/foreground/$AUDIO_COMPONENT_REGISTRAR_LABEL" ] || {
                printf 'Unrecognized target specifier\n' >&2
                return 64
            }
            if [ "$FAIL_KICKSTART" = yes ]; then
                printf 'fixture: audio bootstrap failed\n' >&2
                return 5
            fi
            return 0 ;;
        *) return 127 ;;
    esac
}
start_audio() {
'''
            result = subprocess.run(
                ['bash', '-c', preamble + BLOCK + '\n}\nstart_audio\n',
                 'audio-startup-test', logs, 'yes' if fail_kickstart else 'no'],
                capture_output=True, text=True, timeout=5,
            )
            registrar_log = Path(logs, 'macws-audio-component-registrar.log')
            return result, registrar_log.read_text() if registrar_log.exists() else ''

    def test_correct_launch_domain_reaches_success(self):
        result, log = self.run_start()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(
            'CALL kickstart user/foreground/com.apple.macosbooter.audio.AudioComponentRegistrar',
            log,
        )
        self.assertEqual(result.stdout.count('CALL load '), 3)

    def test_bootstrap_failure_is_visible_and_propagates(self):
        result, log = self.run_start(fail_kickstart=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('ERROR: private macOS audio catalog could not be started.',
                      result.stdout)
        self.assertIn('fixture: audio bootstrap failed', result.stdout)
        self.assertIn('fixture: audio bootstrap failed', log)


if __name__ == '__main__':
    unittest.main()

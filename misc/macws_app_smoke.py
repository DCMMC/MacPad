"""Explicit on-device sequential app smoke run; screenshots still need review.

Never reboots/restarts system services or force-quits apps. Only applications
created by this invocation are asked to quit, through their native menu action.
Stop the batch if a new app cannot quit, rather than accumulating GUI processes.
"""
import argparse
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time

from macws_window_metrics_dump import read_metrics
from host_input_matrix import record

PROBE = '/var/mobile/Media/macws-launch-admission-probe-20260913'
PYTHON = '/var/jb/usr/bin/python3'
CAPTURE = '/var/mobile/Media/macws_ipados_capture-v9'
MENU = '/var/mobile/Media/macws_menu_probe.py'
HOST_LOG = Path('/var/mobile/Library/Logs/MacWSHostd.log')
APP_LOG = Path('/var/mobile/Library/Logs/CustomApp.host.log')


def bounded_tail(path, offset=None):
    try:
        with path.open('rb') as stream:
            size = os.fstat(stream.fileno()).st_size
            stream.seek(max(0, size - 65536, min(offset or 0, size)))
            return stream.read(65536).replace(b'\0', b'').decode(errors='replace')
    except OSError as error:
        return str(error)


def spawned_pid(log, bundle):
    matches = re.findall(r'launch-app id=\S+ pid=(\d+) executable=(.*)', log)
    matching = [int(pid) for pid, executable in matches
                if executable.startswith(bundle + '/Contents/MacOS/')]
    return matching[-1] if matching else 0


def run(arguments, timeout=15, env=None):
    try:
        result = subprocess.run(arguments, capture_output=True, text=True,
                                timeout=timeout, env=env)
        return {'returncode': result.returncode,
                'output': (result.stdout + result.stderr)[-24000:]}
    except subprocess.TimeoutExpired as error:
        output = error.stdout or b''
        return {'returncode': 124, 'output': output.decode(errors='replace')}


def pids():
    return {int(line) for line in subprocess.check_output(
        ['/bin/ps', '-ax', '-o', 'pid='], text=True).split()}


def smoke(path, folder, close):
    before = pids()
    began = time.time()
    log_offset = HOST_LOG.stat().st_size
    health = run(['/var/jb/usr/macOS/bin/macwsthermal'])
    if 'thermal-state=nominal ' not in health['output']:
        raise RuntimeError('non-nominal thermal state: ' + health['output'])
    launch = run([PROBE, path], timeout=40)
    result = {'path': path, 'began': began, 'launch': launch,
              'launch_seconds': round(time.time() - began, 3),
              'status': 'not-visually-reviewed', 'thermal': health}
    match = re.search(r'LAUNCH-PROBE .* ready=(yes|no) pid=(\d+)', launch['output'])
    pid = int(match[2]) if match else 0
    result['host_log'] = bounded_tail(HOST_LOG, log_offset)
    result['app_log'] = bounded_tail(APP_LOG)
    # A failed readiness result legitimately has active PID 0. Track the
    # exact newborn target from this launch's broker log for cleanup/evidence;
    # never relabel it ready or infer ownership from a process-name match.
    if not pid:
        pid = spawned_pid(result['host_log'], path)
    result.update(pid=pid, owned=pid > 1 and pid not in before)
    time.sleep(1)
    if pid > 1:
        try:
            result['metrics'] = read_metrics(
                f'/var/mnt/rootfs/private/tmp/macws_window_metrics.{pid}.bin')
        except (OSError, ValueError) as error:
            result['metrics_error'] = str(error)
    windows = result.get('metrics', {}).get('windows', [])
    visible = [window for window in windows if 'visible' in window['flags']
               and 'menu_bar' not in window['flags']]
    if visible:
        # Ordinary Host activation, not fabricated metrics or a menu guard
        # bypass. Observe the resulting native key window before any action.
        window_id = visible[0]['window_id']
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as client:
            client.sendto(record(8, 1, pid, window_id, 2778, 1940, 0, 0),
                          '/var/mnt/rootfs/private/tmp/macws_host_input.sock')
        time.sleep(0.6)
        result['metrics'] = read_metrics(
            f'/var/mnt/rootfs/private/tmp/macws_window_metrics.{pid}.bin')
        windows = result['metrics']['windows']
    env = dict(os.environ, _MSSafeMode='1')
    result['capture'] = run([CAPTURE, str(folder / 'screen.png')], env=env)
    focused = [window['window_id'] for window in windows if 'focused' in window['flags']]
    if len(focused) == 1:
        result['menu'] = run([PYTHON, MENU, str(pid), str(focused[0]), '--list'])
        if close and result['owned']:
            result['quit'] = run([PYTHON, MENU, str(pid), str(focused[0]),
                                  '--shortcut', '⌘Q', '--perform'])
            for _ in range(20):
                if pid not in pids():
                    break
                time.sleep(0.1)
    elif close and result['owned'] and pid in pids():
        # Some failing/documentless apps have no NSWindow but still process
        # their standard Quit command. Deliver it only to our exact PID.
        result['quit'] = run([PYTHON, '/var/mobile/Media/host_key_probe.py',
                             '--pid', str(pid), '--key-window', '--width', '2778',
                             '--height', '1940', '--key', 'q', '--command'])
        for _ in range(20):
            if pid not in pids():
                break
            time.sleep(0.1)
    result['alive_after'] = pid in pids()
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--close-owned', action='store_true')
    parser.add_argument('--continue-after-clean-exit', action='store_true',
                        help='continue a failed launch only when its exact owned PID has exited')
    parser.add_argument('apps', nargs='+')
    args = parser.parse_args()
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    for index, path in enumerate(args.apps):
        folder = args.output / f'{index:02d}'
        folder.mkdir()
        print('START ' + path, flush=True)
        result = smoke(path, folder, args.close_owned)
        (folder / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps({key: result[key] for key in
                         ('path', 'pid', 'owned', 'launch_seconds', 'alive_after')},
                         ensure_ascii=False), flush=True)
        failed_but_clean = (args.continue_after_clean_exit and result['owned'] and
                            not result['alive_after'])
        if (result['launch']['returncode'] != 0 and not failed_but_clean) or (
                args.close_owned and result['owned'] and result['alive_after']):
            print('STOP: inspect failed launch or unclosed diagnostic app before continuing', flush=True)
            break

#!/bin/bash
# Capture the real iPadOS composite through the already deployed native helper.
# Keep every observation and receipt; this script does not send UI input.
set -euo pipefail
capture_sequence=0
if [ "${1:-}" = "--sequence" ]; then
    capture_sequence=1
    shift
fi
if [ "$capture_sequence" -eq 1 ]; then
    if [ "$#" -lt 5 ] || [ "$#" -gt 6 ]; then
        echo "usage: bash misc/capture_ipados.sh --sequence IP PORT NEW_OUTPUT_DIR COUNT INTERVAL [REMOTE_HELPER]" >&2
        exit 64
    fi
    capture_host="$1"
    capture_port="$2"
    capture_output="$3"
    capture_count="$4"
    capture_interval="$5"
    capture_helper="${6:-/var/mobile/Media/macws_ipados_capture-v9}"
    [[ "$capture_host" =~ ^[[:alnum:]][[:alnum:].:-]*$ ]] || exit 64
    [[ "$capture_port" =~ ^[0-9]+$ ]] || exit 64
    [[ "$capture_helper" =~ ^/[[:alnum:]_./-]+$ ]] || exit 64
    [[ "$capture_count" =~ ^[0-9]+$ ]] || exit 64
    [[ "$capture_interval" =~ ^[0-9]+([.][0-9]+)?$ ]] || exit 64
    if [ -e "$capture_output" ]; then
        echo "Capture directory must not already exist: $capture_output" >&2
        exit 73
    fi
    mkdir -p "$(dirname "$capture_output")"
    capture_remote="/var/mobile/Media/macws-ipados-sequence-$(date +%Y%m%dT%H%M%S)-$$"
    set +e
    ssh -o BatchMode=yes -o ConnectTimeout=5 -p "$capture_port" "root@$capture_host" \
        "_MSSafeMode=1 '$capture_helper' --sequence '$capture_remote' '$capture_count' '$capture_interval'" \
        2>&1 | tee "$capture_output.capture.log"
    capture_status="${PIPESTATUS[0]}"
    set -e
    # Transfer partial artifacts even on failure, while preserving nonzero exit.
    if ! scp -q -r -P "$capture_port" "root@$capture_host:$capture_remote" "$capture_output"; then
        [ "$capture_status" -ne 0 ] || capture_status=74
    fi
    if [ "$capture_status" -ne 0 ]; then
        echo "Sequence incomplete; inspect $capture_output.capture.log and sequence.json" >&2
        exit "$capture_status"
    fi
    printf 'Full iPadOS sequence: %s (visual acceptance still required)\n' "$capture_output"
    exit 0
fi
if [ "$#" -lt 3 ] || [ "$#" -gt 4 ]; then
    echo "usage: bash misc/capture_ipados.sh IP PORT OUTPUT.png [REMOTE_HELPER]" >&2
    exit 64
fi
capture_host="$1"
capture_port="$2"
capture_output="$3"
capture_helper="${4:-/var/mobile/Media/macws_ipados_capture-v9}"
[[ "$capture_host" =~ ^[[:alnum:]][[:alnum:].:-]*$ ]] || exit 64
[[ "$capture_port" =~ ^[0-9]+$ ]] || exit 64
[[ "$capture_helper" =~ ^/[[:alnum:]_./-]+$ ]] || exit 64
capture_remote="/var/mobile/Media/macws-ipados-capture-$(date +%Y%m%dT%H%M%S)-$$.png"
mkdir -p "$(dirname "$capture_output")"
ssh -o BatchMode=yes -o ConnectTimeout=5 -p "$capture_port" "root@$capture_host" \
    "_MSSafeMode=1 '$capture_helper' '$capture_remote'" \
    2>&1 | tee "$capture_output.capture.log"
scp -q -P "$capture_port" "root@$capture_host:$capture_remote" "$capture_output"
scp -q -P "$capture_port" "root@$capture_host:$capture_remote.json" "$capture_output.json"
printf 'Full iPadOS capture: %s (visual acceptance still required)\n' "$capture_output"

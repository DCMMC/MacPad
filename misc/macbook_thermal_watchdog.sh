#!/bin/bash
# Run a MacBook-side benchmark command under a mandatory whole-system thermal
# guard. Sensor reads occur once before launch and then every 300 seconds.

set -u

MACWS_SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
MACWS_PROBE_SOURCE="$MACWS_SCRIPT_DIR/macbook_thermal_probe.m"
MACWS_THERMAL_INTERVAL=300
MACWS_THERMAL_LOG="${TMPDIR:-/tmp}/macws_macbook_thermal_watchdog.log"
MACWS_THERMAL_CACHE_DIR="${TMPDIR:-/tmp}/macws-macbook-thermal-${UID}"
MACWS_PROBE_BIN="$MACWS_THERMAL_CACHE_DIR/macbook_thermal_probe"
MACWS_CHILD_PID=""

usage() {
    cat <<'USAGE'
Usage:
  bash misc/macbook_thermal_watchdog.sh --check
  bash misc/macbook_thermal_watchdog.sh [--log=PATH] -- COMMAND [ARG ...]

The probe runs immediately before COMMAND and every 300 seconds thereafter.
Only the state "critical" intervenes. Nominal/fair/serious states, numeric
temperatures, and unreadable samples are logged without stopping COMMAND.
USAGE
}

log() {
    printf '%s [macbook-thermal] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" \
        | tee -a "$MACWS_THERMAL_LOG"
}

build_probe() {
    mkdir -p "$MACWS_THERMAL_CACHE_DIR" || return 1
    if [ ! -x "$MACWS_PROBE_BIN" ] ||
       [ "$MACWS_PROBE_SOURCE" -nt "$MACWS_PROBE_BIN" ]; then
        local clang_path="" sdk_path=""
        clang_path=$(xcrun --find clang 2>/dev/null) || {
            log "ERROR: xcrun could not locate clang"
            return 1
        }
        sdk_path=$(xcrun --sdk macosx --show-sdk-path 2>/dev/null) || {
            log "ERROR: xcrun could not locate the macOS SDK"
            return 1
        }
        "$clang_path" -isysroot "$sdk_path" -fobjc-arc -O2 -Wall -Wextra \
            -framework Foundation -framework IOKit \
            "$MACWS_PROBE_SOURCE" -o "$MACWS_PROBE_BIN" || return 1
    fi
    return 0
}

field() {
    local line="$1" wanted="$2"
    printf '%s\n' "$line" | awk -v wanted="$wanted" '
        {
            for (i = 1; i <= NF; i++) {
                split($i, pair, "=")
                if (pair[1] == wanted) { print pair[2]; exit }
            }
        }'
}

snapshot() {
    MACWS_THERMAL_LINE=""
    MACWS_THERMAL_STATE=""
    MACWS_THERMAL_TEMP_CENTIC=""
    MACWS_THERMAL_TEMP_VALID=0
    MACWS_PROBE_RC=127
    MACWS_THERMAL_LINE=$("$MACWS_PROBE_BIN" 2>&1)
    MACWS_PROBE_RC=$?
    MACWS_THERMAL_STATE=$(field "$MACWS_THERMAL_LINE" thermal-state)
    MACWS_THERMAL_TEMP_CENTIC=$(field "$MACWS_THERMAL_LINE" effective-temp-centic)
    case "$MACWS_THERMAL_STATE" in
        nominal|fair|serious|critical) ;;
        *) return 1 ;;
    esac
    case "$MACWS_THERMAL_TEMP_CENTIC" in
        ''|*[!0-9]*) ;;
        *) [ "$MACWS_THERMAL_TEMP_CENTIC" -gt 0 ] && MACWS_THERMAL_TEMP_VALID=1 ;;
    esac
    return 0
}

temp_c() {
    awk -v centic="$1" 'BEGIN { printf "%.2f", centic / 100.0 }'
}

preflight() {
    if ! snapshot; then
        log "WARNING: thermal telemetry unavailable rc=$MACWS_PROBE_RC output='${MACWS_THERMAL_LINE:-}'; continuing because only an observed critical state may intervene"
        return 0
    fi
    log "preflight $MACWS_THERMAL_LINE"
    if [ "$MACWS_THERMAL_STATE" = critical ]; then
        log "ERROR: refusing benchmark while thermal state is critical"
        return 1
    fi
    if [ "$MACWS_THERMAL_TEMP_VALID" = 1 ]; then
        log "state=$MACWS_THERMAL_STATE and temperature=$(temp_c "$MACWS_THERMAL_TEMP_CENTIC") C are log-only"
    else
        log "state=$MACWS_THERMAL_STATE; numeric temperature unavailable and log-only"
    fi
    return 0
}

stop_child() {
    local waited=0
    [ -n "$MACWS_CHILD_PID" ] || return 0
    kill -TERM "$MACWS_CHILD_PID" 2>/dev/null || return 0
    while kill -0 "$MACWS_CHILD_PID" 2>/dev/null && [ "$waited" -lt 10 ]; do
        sleep 1
        waited=$((waited + 1))
    done
    kill -KILL "$MACWS_CHILD_PID" 2>/dev/null || true
}

trap_handler() {
    stop_child
    exit 130
}

MACWS_CHECK_ONLY=0
while [ $# -gt 0 ]; do
    case "$1" in
        --check) MACWS_CHECK_ONLY=1; shift ;;
        --log=*) MACWS_THERMAL_LOG=${1#--log=}; shift ;;
        --) shift; break ;;
        -h|--help) usage; exit 0 ;;
        *) break ;;
    esac
done

mkdir -p "$(dirname -- "$MACWS_THERMAL_LOG")" || exit 1
build_probe || exit 1
preflight || exit 75
if [ "$MACWS_CHECK_ONLY" = 1 ]; then
    exit 0
fi
if [ $# -eq 0 ]; then
    usage >&2
    exit 64
fi

trap trap_handler INT TERM HUP
"$@" &
MACWS_CHILD_PID=$!
log "armed pid=$MACWS_CHILD_PID interval=${MACWS_THERMAL_INTERVAL}s command=$*"
MACWS_NEXT_THERMAL=$(( $(date +%s) + MACWS_THERMAL_INTERVAL ))

while kill -0 "$MACWS_CHILD_PID" 2>/dev/null; do
    sleep 2
    MACWS_NOW=$(date +%s)
    [ "$MACWS_NOW" -ge "$MACWS_NEXT_THERMAL" ] || continue
    MACWS_NEXT_THERMAL=$((MACWS_NOW + MACWS_THERMAL_INTERVAL))
    if ! snapshot; then
        log "WARNING: thermal telemetry unavailable rc=$MACWS_PROBE_RC output='${MACWS_THERMAL_LINE:-}'; no intervention without an observed critical state"
        continue
    fi
    log "sample $MACWS_THERMAL_LINE"
    if [ "$MACWS_THERMAL_STATE" = critical ]; then
        log "SAFETY TRIP: thermal state=critical"
        stop_child
        exit 75
    fi
    if [ "$MACWS_THERMAL_TEMP_VALID" = 1 ]; then
        log "state=$MACWS_THERMAL_STATE and temperature=$(temp_c "$MACWS_THERMAL_TEMP_CENTIC") C are log-only"
    else
        log "state=$MACWS_THERMAL_STATE; numeric temperature unavailable and log-only"
    fi
done

wait "$MACWS_CHILD_PID"
MACWS_CHILD_RC=$?
MACWS_CHILD_PID=""
log "command exited rc=$MACWS_CHILD_RC"
exit "$MACWS_CHILD_RC"

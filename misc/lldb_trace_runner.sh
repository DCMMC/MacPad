# misc/lldb_trace_runner.sh — non-interactive variant of lldb_remote.sh.
#
# Same iOS debugserver + SSH tunnel setup as lldb_remote.sh, but instead of
# leaving you in an interactive (lldb) prompt, runs `--batch` with a chain
# of -o commands sourced from a trace script. Captures output to a file so
# Claude can read it back.
#
# Usage:
#   bash misc/lldb_trace_runner.sh <host> [port] [process-name] <commands-file>
#
# commands-file format: one lldb command per non-blank, non-comment line.
# For multi-line commands (python callbacks), join with literal "\n" — the
# runner converts to real newlines before sending.
#
# Output ends up in /tmp/lldb_trace_run.log; the tail is printed to stdout.

set -u

HOST=${1:-}
PORT=${2:-2222}
PROC_NAME=${3:-WindowServer}
CMDS_FILE=${4:-}
SSH_USER=${SSH_USER:-root}
DBG_PORT=${DBG_PORT:-5555}
RUN_LOG=${RUN_LOG:-/tmp/lldb_trace_run.log}
LLDB_MEMORY_MODULE_LOAD_LEVEL=${LLDB_MEMORY_MODULE_LOAD_LEVEL:-}
LLDB_TARGET_BINARY=${LLDB_TARGET_BINARY:-}
IOS_SYMBOL_ROOT=${IOS_SYMBOL_ROOT:-}
SUDO_PASSWORD=${SUDO_PASSWORD:-}
TRACE_CLEANUP_DELAY=${TRACE_CLEANUP_DELAY:-1}
TRACE_ATTACH_DELAY=${TRACE_ATTACH_DELAY:-3}
TRACE_TUNNEL_DELAY=${TRACE_TUNNEL_DELAY:-1}

if [ -z "$HOST" ] || [ -z "$CMDS_FILE" ]; then
    echo "usage: bash $0 <host> [ssh-port] [process-name] <commands-file>" >&2
    exit 1
fi

if [ "$SSH_USER" != root ] && [ -z "$SUDO_PASSWORD" ]; then
    echo "error: set SUDO_PASSWORD when SSH_USER is not root" >&2
    exit 2
fi

ssh_privileged() {
    local remote_command="$1" quoted
    if [ "$SSH_USER" = root ]; then
        ssh -p "$PORT" "$SSH_USER@$HOST" "$remote_command"
        return
    fi
    printf -v quoted '%q' "$remote_command"
    printf '%s\n' "$SUDO_PASSWORD" | ssh -p "$PORT" "$SSH_USER@$HOST" \
        "sudo -S bash -c $quoted"
}
if [ ! -f "$CMDS_FILE" ]; then
    echo "error: commands file $CMDS_FILE not found" >&2
    exit 1
fi

PID=$(ssh -p "$PORT" "$SSH_USER@$HOST" \
    "ps aux | grep -E '$PROC_NAME' | grep -v grep | head -1 | awk '{print \$2}'")
if [ -z "$PID" ]; then
    echo "error: no process matching '$PROC_NAME'" >&2
    exit 1
fi
echo "[trace] $PROC_NAME PID=$PID" >&2

cleanup() {
    pkill -f "ssh.*-L $DBG_PORT:127.0.0.1" 2>/dev/null || true
    ssh_privileged \
        'for p in $(ps aux | grep debugserver | grep -v grep | awk "{print $2}"); do kill -9 $p 2>/dev/null; done' \
        >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

cleanup
sleep "$TRACE_CLEANUP_DELAY"

ssh_privileged \
    "/var/jb/usr/bin/debugserver 127.0.0.1:$DBG_PORT --attach=$PID" \
    >/tmp/debugserver_remote.log 2>&1 &
sleep "$TRACE_ATTACH_DELAY"

STATE=$(ssh -p "$PORT" "$SSH_USER@$HOST" \
    "ps -o state= -p $PID 2>/dev/null | tr -d ' ' || echo MISS")
if [ "${STATE:0:1}" != "T" ]; then
    echo "[trace] WARNING: target state '$STATE' (expected T) — debugserver attach failed?" >&2
fi

ssh -fN -p "$PORT" -L "$DBG_PORT:127.0.0.1:$DBG_PORT" "$SSH_USER@$HOST"
sleep "$TRACE_TUNNEL_DELAY"

# lldb runs the commands file via --source — that respects multi-line
# `script ... DONE` blocks, lldb python heredocs, etc. (which `-o` would
# split across separate one-line invocations).
LLDB_ARGS=(--batch)
if [ -n "$LLDB_TARGET_BINARY" ]; then
    LLDB_ARGS+=(--file "$LLDB_TARGET_BINARY")
fi
if [ -n "$LLDB_MEMORY_MODULE_LOAD_LEVEL" ]; then
    LLDB_ARGS+=(
        -O "settings set target.memory-module-load-level $LLDB_MEMORY_MODULE_LOAD_LEVEL"
        -O "settings set target.preload-symbols false"
    )
fi
if [ -n "$IOS_SYMBOL_ROOT" ]; then
    LLDB_ARGS+=(
        -O "settings set target.exec-search-paths $IOS_SYMBOL_ROOT"
        -O "settings set target.debug-file-search-paths $IOS_SYMBOL_ROOT"
    )
fi
LLDB_ARGS+=(
    -O "process connect --plugin gdb-remote connect://127.0.0.1:$DBG_PORT"
    -O "settings set interpreter.stop-command-source-on-error false"
    --source "$CMDS_FILE"
    -o "process detach"
    -o "quit"
)

echo "[trace] running lldb in batch (output -> $RUN_LOG)" >&2
/usr/bin/lldb "${LLDB_ARGS[@]}" >"$RUN_LOG" 2>&1 || true

echo
echo "===== LLDB SESSION OUTPUT ====="
tail -500 "$RUN_LOG"

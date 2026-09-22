# misc/lldb_remote.sh — set up iOS debugserver + SSH tunnel and run a local
# Mac lldb against a chroot process (default: WindowServer).
#
# Architecture:
#   iOS device:  debugserver 127.0.0.1:$PORT --attach=$PID
#                ^ MUST bind 127.0.0.1 (NOT "localhost") — debugserver resolves
#                  localhost to ::1 only, but SSH tunnel forwards IPv4. With
#                  127.0.0.1 bind, IPv4 tunnel reaches it.
#   Host (Mac):  ssh -L $PORT:127.0.0.1:$PORT root@iOS  (tunnel)
#                /usr/bin/lldb  ← full Apple lldb with python scripting
#                                  connects via gdb-remote to 127.0.0.1:$PORT
#
# This gives us full-featured macOS lldb (python, breakpoints, scripting)
# driving an arm64e iOS process. Avoids the Procursus iOS lldb's lack of
# embedded script interpreter.
#
# Usage:
#   bash misc/lldb_remote.sh <host> [port] [process-name] [lldb-script-file]
#
# Examples:
#   bash misc/lldb_remote.sh 172.23.154.141
#     # attaches to WindowServer interactively
#   bash misc/lldb_remote.sh 172.23.154.141 2222 WindowServer misc/lldb_initimpl_trace.lldb
#     # runs the canned trace script
#   bash misc/lldb_remote.sh 172.23.154.141 2222 GlassDemo
#     # attaches to GlassDemo
#
# Cleanup:
#   The script kills debugserver and SSH tunnel on exit (trap), so a Ctrl-C
#   leaves no zombies.

set -u

HOST=${1:-}
PORT=${2:-2222}
PROC_NAME=${3:-WindowServer}
LLDB_SCRIPT=${4:-}
SSH_USER=${SSH_USER:-root}
SUDO_PASSWORD=${SUDO_PASSWORD:-}
DBG_PORT=${DBG_PORT:-5555}
LLDB_MODULE_LOAD_LEVEL=${LLDB_MODULE_LOAD_LEVEL:-minimal}

case "$LLDB_MODULE_LOAD_LEVEL" in
    minimal|partial|complete) ;;
    *)
        echo "error: LLDB_MODULE_LOAD_LEVEL must be minimal, partial, or complete" >&2
        exit 2
        ;;
esac

if [ -z "$HOST" ]; then
    echo "usage: bash $0 <host> [ssh-port] [process-name] [lldb-script-file]" >&2
    exit 1
fi

if [ "$SSH_USER" != root ] && [ -z "$SUDO_PASSWORD" ]; then
    echo "error: set SUDO_PASSWORD when SSH_USER is not root" >&2
    exit 2
fi

ssh_privileged() {
    local remote_command="$1" quoted
    if [ "$SSH_USER" = root ]; then
        ssh -n -p "$PORT" "$SSH_USER@$HOST" "$remote_command"
        return
    fi
    printf -v quoted '%q' "$remote_command"
    printf '%s\n' "$SUDO_PASSWORD" | ssh -p "$PORT" "$SSH_USER@$HOST" \
        "sudo -S bash -c $quoted"
}

# Resolve target PID on device. Accepting an exact numeric PID avoids matching
# wrapper/tmux command lines which contain the child executable's name.
if [[ "$PROC_NAME" =~ ^[0-9]+$ ]]; then
    PID=$PROC_NAME
else
    PID=$(ssh -n -p "$PORT" "$SSH_USER@$HOST" \
        "ps aux | grep -E '$PROC_NAME' | grep -v grep | head -1 | awk '{print \$2}'")
fi
if [ -z "$PID" ]; then
    echo "error: no process matching '$PROC_NAME' on $HOST" >&2
    exit 1
fi
echo "[lldb_remote] target $PROC_NAME PID=$PID" >&2

cleanup() {
    echo "[lldb_remote] cleanup" >&2
    pkill -f "ssh.*-L $DBG_PORT:127.0.0.1" 2>/dev/null || true
    ssh_privileged \
        'for p in $(ps aux | grep debugserver | grep -v grep | awk "{print $2}"); do kill -9 $p 2>/dev/null; done' \
        >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

# Kill any stale debugserver / tunnel before we start.
cleanup
sleep 1

# Start debugserver on device (in background via SSH).
ssh_privileged \
    "/var/jb/usr/bin/debugserver 127.0.0.1:$DBG_PORT --attach=$PID" \
    >/tmp/debugserver_remote.log 2>&1 &
DBG_SSH_PID=$!
echo "[lldb_remote] debugserver SSH PID=$DBG_SSH_PID (logging to /tmp/debugserver_remote.log)" >&2

# Give debugserver time to attach + bind.
sleep 3

# Verify the device-side process is in state T (debugger-stopped).
STATE=$(ssh -n -p "$PORT" "$SSH_USER@$HOST" \
    "ps -o state= -p $PID 2>/dev/null | tr -d ' ' || echo MISS")
if [ "${STATE:0:1}" != "T" ]; then
    echo "[lldb_remote] WARNING: target state is '$STATE' (expected T*) — debugserver attach may have failed" >&2
fi

# Set up SSH tunnel local:DBG_PORT → device:127.0.0.1:DBG_PORT.
ssh -fN -p "$PORT" -L "$DBG_PORT:127.0.0.1:$DBG_PORT" "$SSH_USER@$HOST"
sleep 1

echo "[lldb_remote] tunnel up: localhost:$DBG_PORT -> $HOST:127.0.0.1:$DBG_PORT" >&2
echo "[lldb_remote] launching local lldb" >&2

# Build lldb args. If a script file is given, source it AFTER connecting.
LLDB_ARGS=(
    -O "settings set target.memory-module-load-level $LLDB_MODULE_LOAD_LEVEL"
    -O "process connect --plugin gdb-remote connect://127.0.0.1:$DBG_PORT"
)
if [ -n "$LLDB_SCRIPT" ]; then
    if [ ! -f "$LLDB_SCRIPT" ]; then
        echo "error: $LLDB_SCRIPT not found" >&2
        exit 1
    fi
    LLDB_ARGS+=(--source "$LLDB_SCRIPT")
fi

# Run lldb interactively (no --batch) so user can control the session.
# When user types `quit` or Ctrl-D, trap cleans up debugserver.
/usr/bin/lldb "${LLDB_ARGS[@]}"

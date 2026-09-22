# misc/lldb_mcp_setup.sh — bring up the lldb MCP pipeline.
#
# Pipeline (left = Claude side, right = iOS chroot WS):
#
#   Claude Code  --stdio-->  mcp_stdio_tcp_bridge.py  --TCP-->  lldb (Mac, MCP server)
#                                                                  |
#                                                                  | gdb-remote over
#                                                                  | SSH tunnel
#                                                                  v
#                                                              debugserver (iOS) --attach--> WS
#
# Usage:
#   bash misc/lldb_mcp_setup.sh start <ios-host> [ssh-port] [proc-name]
#       Starts: debugserver on iOS (attached to WS), SSH tunnel, lldb on Mac
#       with `process connect` already done and `protocol-server start MCP
#       listen://127.0.0.1:$MCP_PORT` running. The lldb process stays alive
#       in the background as the MCP server.
#       After this returns, ensure Claude Code's MCP config has:
#           claude mcp add lldb-mcp -- python3 \
#               <repo>/misc/mcp_stdio_tcp_bridge.py 127.0.0.1 $MCP_PORT
#       and restart Claude.
#
#   bash misc/lldb_mcp_setup.sh stop
#       Tears down everything (lldb, tunnel, debugserver).
#
#   bash misc/lldb_mcp_setup.sh status
#       Shows what's running.
#
# Env overrides:
#   DBG_PORT=5555   debugserver port (iOS local + Mac tunnel)
#   MCP_PORT=9999   lldb MCP TCP listener (Mac local)
#   PASSWORD=alpine sudo password on iOS device

set -u

DBG_PORT=${DBG_PORT:-5555}
MCP_PORT=${MCP_PORT:-9999}
PASSWORD=${PASSWORD:-alpine}
PIDFILE_LLDB=/tmp/lldb_mcp_lldb.pid
PIDFILE_TUNNEL=/tmp/lldb_mcp_tunnel.pid
LLDB_LOG=/tmp/lldb_mcp_lldb.log

ACTION=${1:-status}

repo_dir() {
    cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd
}

start() {
    HOST=${2:-}
    PORT=${3:-2222}
    PROC=${4:-WindowServer}
    if [ -z "$HOST" ]; then
        echo "usage: bash $0 start <ios-host> [ssh-port] [proc-name]" >&2
        exit 1
    fi

    stop  # idempotent cleanup

    # 1. Find WS PID on device.
    PID=$(ssh -p "$PORT" root@"$HOST" \
        "ps aux | grep -E '$PROC' | grep -v grep | head -1 | awk '{print \$2}'")
    if [ -z "$PID" ]; then
        echo "error: no process matching '$PROC' on $HOST" >&2
        exit 1
    fi
    echo "[mcp] target=$PROC PID=$PID on $HOST" >&2

    # 2. Start debugserver on device. CRITICAL: bind 127.0.0.1 (NOT localhost
    #    — that's ::1 only and SSH tunnel uses IPv4).
    ssh -p "$PORT" root@"$HOST" \
        "echo $PASSWORD | sudo -S /var/jb/usr/bin/debugserver 127.0.0.1:$DBG_PORT --attach=$PID" \
        >>/tmp/lldb_mcp_debugserver.log 2>&1 &
    echo "[mcp] debugserver (ssh) backgrounded" >&2
    sleep 3

    STATE=$(ssh -p "$PORT" root@"$HOST" \
        "ps -o state= -p $PID 2>/dev/null | tr -d ' ' || echo MISS")
    if [ "${STATE:0:1}" != "T" ]; then
        echo "[mcp] WARNING: target state=$STATE (expected T*) — debugserver attach may have failed" >&2
    fi

    # 3. SSH tunnel local:DBG_PORT → device:127.0.0.1:DBG_PORT.
    ssh -fN -p "$PORT" -L "$DBG_PORT:127.0.0.1:$DBG_PORT" root@"$HOST"
    sleep 1
    echo "[mcp] ssh tunnel up: localhost:$DBG_PORT -> $HOST:127.0.0.1:$DBG_PORT" >&2

    # 4. Start lldb. Order matters:
    #    a) -O protocol-server start FIRST so MCP listener is up immediately
    #       (it runs on a separate thread, accepts connections while lldb's
    #       main thread is otherwise busy).
    #    b) -O process connect AFTER. This is a SLOW operation (downloads
    #       WS binary symbols over gdb-remote — ~1-3 minutes for SkyLight)
    #       but doesn't block the MCP listener.
    #    c) keepalive stdin = `tail -f /dev/null` so lldb sits at the (lldb)
    #       prompt forever, ready to process MCP-dispatched commands.
    #
    #    The pipeline child is the `tail -f` keeper, but the LLDB process is
    #    spawned as its child. We track via "ps aux | grep /usr/bin/lldb"
    #    instead of $! because the latter captures the pipeline subshell pid.
    : >"$LLDB_LOG"
    nohup bash -c "tail -f /dev/null | /usr/bin/lldb \
        -O 'protocol-server start MCP listen://127.0.0.1:$MCP_PORT' \
        -O 'process connect --plugin gdb-remote connect://127.0.0.1:$DBG_PORT' \
        -O 'log enable -f /tmp/lldb_mcp_lldb_packets.log gdb-remote packets'" \
        >>"$LLDB_LOG" 2>&1 &
    BASH_PID=$!
    echo $BASH_PID >"$PIDFILE_LLDB"
    sleep 4

    # 5. Sanity-check MCP port.
    if nc -z 127.0.0.1 "$MCP_PORT" 2>/dev/null; then
        echo "[mcp] MCP port $MCP_PORT listening" >&2
    else
        echo "[mcp] WARNING: MCP port $MCP_PORT not up after 4s. Check $LLDB_LOG" >&2
    fi
    LLDB_REAL_PID=$(ps aux | grep '/usr/bin/lldb' | grep -v grep | awk '{print $2}' | head -1)
    echo "[mcp] lldb (real) PID=$LLDB_REAL_PID, pipeline PID=$BASH_PID" >&2

    REPO=$(repo_dir)
    cat <<EOF

==========================================================
✓ LLDB MCP server is running on 127.0.0.1:$MCP_PORT
✓ Connected to chroot $PROC (PID $PID) via debugserver

NEXT STEPS — run from a terminal (NOT inside Claude):

  1. Register the MCP server with Claude Code:

       claude mcp add lldb-mcp -- python3 \\
         $REPO/misc/mcp_stdio_tcp_bridge.py 127.0.0.1 $MCP_PORT

  2. Restart Claude Code. The 'lldb_command' tool then appears
     in MCP tools, callable to send any lldb command and get
     structured output.

Note: 'process connect' is still running in the background — the
WS binary symbol download takes 1-3 minutes for first use. MCP
commands work immediately, but until connect finishes, target
inspection commands ('process status', 'image list', etc.) will
return partial results.

To tear down:  bash $0 stop
To check:      bash $0 status
==========================================================
EOF
}

stop() {
    # Kill local lldb.
    if [ -f "$PIDFILE_LLDB" ] && kill -0 "$(cat $PIDFILE_LLDB)" 2>/dev/null; then
        kill "$(cat $PIDFILE_LLDB)" 2>/dev/null || true
        sleep 1
        kill -9 "$(cat $PIDFILE_LLDB)" 2>/dev/null || true
    fi
    rm -f "$PIDFILE_LLDB"

    # Kill SSH tunnel.
    pkill -f "ssh.*-L $DBG_PORT:127.0.0.1:$DBG_PORT" 2>/dev/null || true

    # Kill device-side debugserver (if any).
    # Caller must pass HOST/PORT for stop to reach the device; if not
    # provided, skip remote cleanup (debugserver will exit when its TCP
    # client — our lldb — went away).
}

status() {
    echo "--- pidfiles ---"
    for f in "$PIDFILE_LLDB" "$PIDFILE_TUNNEL"; do
        if [ -f "$f" ]; then
            p=$(cat "$f")
            if kill -0 "$p" 2>/dev/null; then echo "$f: $p ALIVE"
            else echo "$f: $p stale"; fi
        else
            echo "$f: missing"
        fi
    done
    echo "--- ports ---"
    nc -z 127.0.0.1 "$DBG_PORT" 2>/dev/null && echo "dbg port $DBG_PORT: open" || echo "dbg port $DBG_PORT: closed"
    nc -z 127.0.0.1 "$MCP_PORT" 2>/dev/null && echo "mcp port $MCP_PORT: open" || echo "mcp port $MCP_PORT: closed"
    echo "--- ssh tunnels ---"
    pgrep -fl "ssh.*-L $DBG_PORT" || echo "  no tunnel running"
}

case "$ACTION" in
    start)  start "$@" ;;
    stop)   stop ;;
    status) status ;;
    *) echo "usage: bash $0 {start <ios-host> [ssh-port] [proc-name] | stop | status}" >&2; exit 1 ;;
esac

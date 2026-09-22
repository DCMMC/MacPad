# misc/ios_lldb_tmux.sh — drive iOS Procursus lldb interactively over tmux.
#
# Strategy: lldb runs ON iOS attached to the chroot process. iOS-local symbol
# lookups (dyld shared cache, chroot binaries) are fast — no network in the
# debug loop. We never restart lldb; the session persists across commands.
#
# Stable interface design:
#   - One named tmux session ("ioslldb") on iOS holds the interactive lldb
#   - tmux send-keys delivers commands; tmux capture-pane reads output
#   - After each send, we wait for the next "(lldb) " prompt to appear
#   - All comms go through SSH-over-USB (port 22222 by default)
#
# This script is designed to be invoked many times in a row from Claude:
#
#   bash misc/ios_lldb_tmux.sh attach <iOS-host> [ssh-port] [proc-name]
#       Start (or re-attach to) the persistent lldb tmux session targeting
#       the named iOS process (default: WindowServer).
#
#   bash misc/ios_lldb_tmux.sh cmd '<lldb-command>'
#       Send one lldb command. Prints the response (everything between the
#       previous prompt and the next one).
#
#   bash misc/ios_lldb_tmux.sh cmd-multi <<'EOF'
#       breakpoint command add 1
#       register read x0 x21
#       continue
#       DONE
#       EOF
#       Send a multi-line command. Useful for breakpoint command bodies
#       and `script ... DONE` blocks.
#
#   bash misc/ios_lldb_tmux.sh capture [N-lines]
#       Dump the most-recent N lines (default 200) of the pane.
#
#   bash misc/ios_lldb_tmux.sh stop
#       Detach lldb (process detach), quit, kill the tmux session.
#
#   bash misc/ios_lldb_tmux.sh status
#       Show whether session + lldb are alive.

set -u

SESSION=${SESSION:-ioslldb}
SSH_USER=${SSH_USER:-root}
PASSWORD=${PASSWORD:-alpine}

# Cached connection params (set by attach, read by other actions).
STATE_FILE=/tmp/ios_lldb_tmux.state

load_state() {
    if [ -f "$STATE_FILE" ]; then
        # shellcheck disable=SC1090
        . "$STATE_FILE"
    fi
}

save_state() {
    # Shell-quote values because process names such as "Google Chrome" contain
    # spaces.  The state file is sourced by later invocations of this helper.
    printf 'HOST=%q\nPORT=%q\nPROC=%q\nPID=%q\n' \
        "$HOST" "$PORT" "$PROC" "$PID" >"$STATE_FILE"
}

ssh_run() {
    ssh -p "$PORT" "$SSH_USER@$HOST" "$@"
}

ssh_sudo() {
    # Run a command as root via sudo, password from $PASSWORD.
    # - LC_CTYPE=UTF-8: iOS only ships a bare "UTF-8" locale; Procursus tmux
    #   demands a UTF-8 locale or refuses to start.
    # - TMUX_TMPDIR=/var/tmp: default Procursus tmux tries to put its socket
    #   in /private/preboot/<huge UUID>/.../procursus/tmp/ which exceeds the
    #   Unix-domain-socket name length limit (~104 chars). /var/tmp is short
    #   enough and writable by root.
    ssh -p "$PORT" "$SSH_USER@$HOST" \
        "echo $PASSWORD | sudo -S env LC_CTYPE=UTF-8 TMUX_TMPDIR=/var/tmp $1"
}

ssh_root_sh() {
    # Run a multi-line shell snippet via stdin under sudo bash.
    ssh -p "$PORT" "$SSH_USER@$HOST" "echo $PASSWORD | sudo -S bash" <<EOF
$1
EOF
}

attach() {
    HOST=${1:-}
    PORT=${2:-22222}
    PROC=${3:-WindowServer}
    if [ -z "$HOST" ]; then
        echo "usage: bash $0 attach <ios-host> [ssh-port] [proc-name]" >&2
        exit 1
    fi

    PID=$(ssh_run "ps aux | grep -E '$PROC' | grep -v grep | head -1 | awk '{print \$2}'")
    if [ -z "$PID" ]; then
        echo "error: no process matching '$PROC' on $HOST" >&2
        exit 1
    fi
    save_state
    echo "[ios_lldb_tmux] target $PROC PID=$PID on $HOST:$PORT" >&2

    # Kill any prior session.
    ssh_sudo "/var/jb/usr/bin/tmux kill-session -t $SESSION 2>/dev/null" || true
    sleep 0.5

    # Start a fresh tmux session running iOS lldb attached to the process.
    # Capture-pane needs scrollback history-limit reasonably high.
    ssh_sudo "/var/jb/usr/bin/tmux new-session -d -s $SESSION -x 200 -y 200 '/var/jb/usr/bin/lldb -p $PID'" >/dev/null

    # Wait for (lldb) prompt — first attach takes ~3s on iOS (no network).
    wait_for_prompt 30
    echo "[ios_lldb_tmux] session $SESSION attached to PID $PID, prompt ready" >&2
    capture 30
}

# Strip ANSI escape sequences (tmux can emit them).
strip_ansi() {
    sed -E 's/\x1B\[[0-9;]*[a-zA-Z]//g; s/\x1B\][^\x07]*\x07//g; s/\x1B[()][AB012]//g'
}

# Read the entire pane scrollback (best-effort) and emit to stdout.
capture() {
    local lines=${1:-200}
    ssh_sudo "/var/jb/usr/bin/tmux capture-pane -t $SESSION -p -S -$lines" | strip_ansi
}

# Block until the most-recent line contains '(lldb) ' (the prompt).
# Returns 0 on found, 1 on timeout.
wait_for_prompt() {
    local timeout=${1:-15}
    local deadline=$(( $(date +%s) + timeout ))
    while [ "$(date +%s)" -lt "$deadline" ]; do
        # Capture only last 5 lines; faster than full scrollback each poll.
        local tail
        tail=$(ssh_sudo "/var/jb/usr/bin/tmux capture-pane -t $SESSION -p -S -5" | strip_ansi)
        if echo "$tail" | tail -n 1 | grep -qE '\(lldb\) *$'; then
            return 0
        fi
        sleep 0.5
    done
    return 1
}

# Send a single lldb command. Capture its output.
cmd() {
    local cmd=$1
    # Mark position so we can extract just this command's output.
    local marker="MARK_$(date +%s%N)"
    # Send a no-op echo marker via lldb's `script` would need python;
    # instead, snapshot prompt count BEFORE and AFTER, capture diff.
    local before_lines
    before_lines=$(ssh_sudo "/var/jb/usr/bin/tmux capture-pane -t $SESSION -p -S -10000" \
                   | strip_ansi | wc -l | tr -d ' ')

    # Send the command + Enter. tmux send-keys with literal newlines via "Enter".
    # Escape inner double quotes for the SSH/sudo/tmux chain.
    local escaped_cmd
    # The command is parsed once more by the remote shell. Preserve LLDB
    # register references such as $x1 instead of letting that shell expand
    # them, and preserve quotes used by breakpoint conditions.
    escaped_cmd=$(printf '%s' "$cmd" | sed 's/[$"]/\\&/g')
    ssh_sudo "/var/jb/usr/bin/tmux send-keys -t $SESSION \"$escaped_cmd\" Enter" >/dev/null

    if ! wait_for_prompt 30; then
        echo "[ios_lldb_tmux] WARNING: timeout waiting for (lldb) prompt" >&2
    fi

    # Capture the new lines (everything past before_lines).
    ssh_sudo "/var/jb/usr/bin/tmux capture-pane -t $SESSION -p -S -10000" \
        | strip_ansi | tail -n +"$((before_lines + 1))"
}

# Send a multi-line command body via stdin. Used for `breakpoint command add 1` /
# `DONE` blocks where each interior line is part of the body.
cmd_multi() {
    local before_lines
    before_lines=$(ssh_sudo "/var/jb/usr/bin/tmux capture-pane -t $SESSION -p -S -10000" \
                   | strip_ansi | wc -l | tr -d ' ')

    while IFS= read -r line; do
        local escaped
        escaped=$(printf '%s' "$line" | sed 's/[$"]/\\&/g')
        ssh_sudo "/var/jb/usr/bin/tmux send-keys -t $SESSION \"$escaped\" Enter" >/dev/null
        sleep 0.2  # small spacing so lldb doesn't merge keystrokes
    done

    if ! wait_for_prompt 30; then
        echo "[ios_lldb_tmux] WARNING: timeout waiting for (lldb) prompt" >&2
    fi

    ssh_sudo "/var/jb/usr/bin/tmux capture-pane -t $SESSION -p -S -10000" \
        | strip_ansi | tail -n +"$((before_lines + 1))"
}

status() {
    if ssh_sudo "/var/jb/usr/bin/tmux has-session -t $SESSION 2>/dev/null"; then
        echo "session $SESSION: ALIVE"
        echo "--- last 20 lines ---"
        capture 20
    else
        echo "session $SESSION: not running"
    fi
}

stop() {
    # Best-effort detach then quit then kill session.
    ssh_sudo "/var/jb/usr/bin/tmux send-keys -t $SESSION 'process detach' Enter" >/dev/null || true
    sleep 1
    ssh_sudo "/var/jb/usr/bin/tmux send-keys -t $SESSION 'quit' Enter" >/dev/null || true
    sleep 1
    ssh_sudo "/var/jb/usr/bin/tmux kill-session -t $SESSION 2>/dev/null" || true
    rm -f "$STATE_FILE"
    echo "[ios_lldb_tmux] stopped"
}

ACTION=${1:-status}
shift || true
load_state

case "$ACTION" in
    attach)    attach "$@" ;;
    cmd)       cmd "$1" ;;
    cmd-multi) cmd_multi ;;  # reads multi-line body from stdin
    capture)   capture "${1:-200}" ;;
    stop)      stop ;;
    status)    status ;;
    *)         echo "usage: bash $0 {attach <host> [port] [proc] | cmd '<lldb-cmd>' | cmd-multi <<EOF...EOF | capture [N] | stop | status}" >&2; exit 1 ;;
esac

# Invoke through zsh/bash. Deliberately no shebang: iOS AMFI rejects script
# execve in this jailbreak environment.

set -eu

sudo -v
echo SUDO_READY
next_sudo_refresh=30

while :; do
    if [ "$SECONDS" -ge "$next_sudo_refresh" ]; then
        sudo -n -v
        next_sudo_refresh=$((SECONDS + 30))
    fi
    game_pid=
    overlay_pid=
    while IFS=' ' read -r pid command; do
        pid=${pid##* }
        case "$command" in
            *'/Stray-Mac-Shipping') game_pid=$pid ;;
            *'/gameoverlayui'*) overlay_pid=$pid ;;
        esac
    done < <(ps -Ao pid=,command=)

    if [ -n "$game_pid" ] && [ -n "$overlay_pid" ]; then
        echo "GAME_PID=$game_pid OVERLAY_PID=$overlay_pid"
        exec sudo -n /var/jb/usr/bin/lldb -p "$game_pid"
    fi
    sleep 0.1
done

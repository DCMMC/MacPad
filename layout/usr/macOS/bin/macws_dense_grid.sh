set -eu

FLAG=/tmp/com.macwsguide.dense-grid.disabled

case "${1:-status}" in
    enable)
        rm -f "$FLAG"
        echo "MacWS dense Stage Manager grid: enabled for the next SpringBoard start"
        ;;
    disable)
        : > "$FLAG"
        chmod 0644 "$FLAG"
        echo "MacWS dense Stage Manager grid: disabled for the next SpringBoard start"
        ;;
    status)
        if [ -e "$FLAG" ]; then
            echo "MacWS dense Stage Manager grid: disabled"
        else
            echo "MacWS dense Stage Manager grid: enabled"
        fi
        if /var/jb/usr/macOS/bin/macws_control_probe windowing-status; then
            for witness in /tmp/com.macwsguide.dense-grid.width \
                           /tmp/com.macwsguide.dense-grid.height \
                           /tmp/com.macwsguide.dense-grid.width-getter \
                           /tmp/com.macwsguide.dense-grid.height-getter; do
                [ ! -f "$witness" ] || head -n 1 "$witness"
            done
        else
            echo "SpringBoard hook: not loaded in the current SpringBoard"
        fi
        ;;
    *)
        echo "usage: $0 {enable|disable|status}" >&2
        exit 64
        ;;
esac

echo "No respring was performed. The policy changes only on a later user-initiated SpringBoard restart."

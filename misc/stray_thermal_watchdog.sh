#!/var/jb/usr/bin/bash
# Compatibility front end retained only to disarm older installations.
# Thermal state is observation-only. This script never creates a watchdog,
# suspends Steam, or terminates Stray.

set -u

LABEL=com.macwsguide.stray-safety
PLIST=/var/jb/Library/LaunchDaemons/com.macwsguide.stray-safety.plist
HEARTBEAT=/var/mobile/Library/Logs/macws-stray-safety.heartbeat

disarm() {
    launchctl unload "$PLIST" >/dev/null 2>&1 || true
    rm -f "$PLIST" "$HEARTBEAT"
}

arm() {
    disarm
    echo "disabled label=$LABEL thermal-policy=observe-only process-control=none"
}

case "${1:-}" in
    arm) arm "${2:-90}" ;;
    disarm) disarm ;;
    *)
        echo "usage: bash $0 {arm [heartbeat-timeout]|disarm}" >&2
        exit 2
        ;;
esac

# No shebang: AMFI rejects execve of shebang scripts on this device. Invoke
# this file through bash from package/runtime post-installation paths.

AUTOSIGND=/var/jb/usr/macOS/bin/autosignd
AUTOSIGND_SOCKET=/var/mnt/rootfs/tmp/autosignd.sock
AUTOSIGND_LOG=/var/mnt/rootfs/tmp/autosignd.log
RESTART_LOCK=/tmp/.macws-autosignd-restart.lock
KILLALL=/var/jb/usr/bin/killall
LDID=/var/jb/usr/bin/ldid
JBCTL=/var/jb/usr/bin/jbctl
FORCE=0

case "${1:-}" in
    '') ;;
    --force) FORCE=1 ;;
    *) echo "usage: bash $0 [--force]" >&2; exit 64 ;;
esac

[ -x "$AUTOSIGND" ] || exit 0

# The dynamic trustcache is reboot-volatile and autosignd is itself the first
# image in this signing path, so it must be admitted before it can help any
# chroot child.
for arch in arm64 arm64e; do
    hash=$("$LDID" -arch "$arch" -h "$AUTOSIGND" 2>/dev/null |
        grep CDHash= | cut -c8-)
    [ -n "$hash" ] && "$JBCTL" trustcache add "$hash" 2>/dev/null || true
done

# A package can be configured before the macOS rootfs is mounted. In that
# state there is no socket namespace shared with the future chroot, so defer
# daemon publication instead of creating a misleading iOS-root socket.
# Filtered rootfs backups intentionally omit private/tmp while preserving the
# stock /tmp -> private/tmp symlink. Once SystemVersion.plist proves that the
# real rootfs is present, recreate that volatile directory before publishing
# the socket; otherwise a freshly restored system cannot execute even bash.
ROOTFS=/var/mnt/rootfs
if [ ! -d "$ROOTFS/tmp" ]; then
    if [ ! -f "$ROOTFS/System/Library/CoreServices/SystemVersion.plist" ]; then
        echo '[INFO] autosignd start deferred until the macOS rootfs is mounted'
        exit 0
    fi
    mkdir -p "$ROOTFS/private/tmp" || exit 1
    chmod 1777 "$ROOTFS/private/tmp" || exit 1
    if [ ! -e "$ROOTFS/tmp" ] && [ ! -L "$ROOTFS/tmp" ]; then
        ln -s private/tmp "$ROOTFS/tmp" || exit 1
    fi
fi
[ -d "$ROOTFS/tmp" ] || {
    echo '[ERROR] macOS rootfs /tmp is not a usable directory' >&2
    exit 1
}

# Package configuration, GUI startup, and desktop recovery can request this
# lifecycle at the same time. Serialize the complete kill/publish/probe
# transaction. A daemon-level flock provides the final singleton invariant,
# while this lock prevents a second helper from killing the listener between
# the first helper's successful probe and its caller launching a chroot child.
restart_lock_wait=0
while ! mkdir "$RESTART_LOCK" 2>/dev/null; do
    restart_owner=$(sed -n '1p' "$RESTART_LOCK/owner" 2>/dev/null || true)
    case "$restart_owner" in
        ''|*[!0-9]*) restart_owner=0 ;;
    esac
    if [ "$restart_owner" -le 1 ] ||
       ! kill -0 "$restart_owner" 2>/dev/null; then
        rm -f "$RESTART_LOCK/owner"
        rmdir "$RESTART_LOCK" 2>/dev/null || true
        continue
    fi
    if [ "$restart_lock_wait" -ge 100 ]; then
        echo '[ERROR] timed out waiting for the autosignd lifecycle lock' >&2
        exit 1
    fi
    sleep 0.1
    restart_lock_wait=$((restart_lock_wait + 1))
done
printf '%s\n' "$$" > "$RESTART_LOCK/owner"
release_restart_lock() {
    rm -f "$RESTART_LOCK/owner"
    rmdir "$RESTART_LOCK" 2>/dev/null || true
}
trap release_restart_lock EXIT HUP INT TERM

# Normal GUI starts only need a live endpoint. Package post-install passes
# --force because the on-disk executable may have changed underneath the
# mapped process and must replace it before returning.
if [ "$FORCE" -eq 0 ] && "$AUTOSIGND" --probe >/dev/null 2>&1; then
    echo '[INFO] reused the live autosignd endpoint after an RPC probe'
    exit 0
fi

# Procursus on the target has no pkill. The former unqualified call leaked one
# daemon per installation, each blocked on an unlinked socket inode. Retire
# exactly autosignd, allow a bounded normal exit, then force only survivors.
if [ -x "$KILLALL" ]; then
    "$KILLALL" autosignd 2>/dev/null || true
    autosignd_wait=0
    while "$KILLALL" -0 autosignd 2>/dev/null &&
          [ "$autosignd_wait" -lt 20 ]; do
        sleep 0.1
        autosignd_wait=$((autosignd_wait + 1))
    done
    if "$KILLALL" -0 autosignd 2>/dev/null; then
        "$KILLALL" -9 autosignd 2>/dev/null || true
    fi
fi

rm -f "$AUTOSIGND_SOCKET"
if [ -x /var/jb/usr/bin/nohup ]; then
    /var/jb/usr/bin/nohup "$AUTOSIGND" >"$AUTOSIGND_LOG" 2>&1 &
else
    nohup "$AUTOSIGND" >"$AUTOSIGND_LOG" 2>&1 &
fi

autosignd_wait=0
while [ "$autosignd_wait" -lt 50 ]; do
    [ -S "$AUTOSIGND_SOCKET" ] &&
        "$AUTOSIGND" --probe >/dev/null 2>&1 && break
    sleep 0.1
    autosignd_wait=$((autosignd_wait + 1))
done
if [ ! -S "$AUTOSIGND_SOCKET" ] ||
   ! "$AUTOSIGND" --probe >/dev/null 2>&1; then
    echo '[ERROR] autosignd did not publish a responsive socket' >&2
    exit 1
fi

echo '[INFO] started one autosignd instance and verified its RPC endpoint'

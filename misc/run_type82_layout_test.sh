# One-shot native-AGX validation for the macOS->iOS type=0x82 resource ABI.
# Run as root on the iPad.  The explicitly gated VNC probe performs a BGRA8
# control draw followed by one pf=550 read, then this harness restores iOS.
# No shebang: this jailbreak's AMFI rejects execve of script files.  Invoke as
# `bash misc/run_type82_layout_test.sh`.

set -u

PROJECT=/var/jb/var/mobile/MacWSBootingGuide
ROOTFS=/var/mnt/rootfs
WS_LOG=/var/jb/var/mobile/WindowServer.err

bash "$PROJECT/misc/cleanup_all.sh" >/tmp/macws-pretest-cleanup.log 2>&1 || true
rm -f "$WS_LOG" /var/jb/var/mobile/WindowServer.out
rm -f "$ROOTFS"/private/tmp/macws_submit_*
rm -f "$ROOTFS/private/tmp/macws_vnc_surfid"
rm -f "$ROOTFS/private/tmp/macws_stop_after_clear"
touch "$ROOTFS/private/tmp/macws_res_diag"
touch "$ROOTFS/private/tmp/macws_submit_diag"
touch "$ROOTFS/private/tmp/macws_kcmd_fix"
touch "$ROOTFS/private/tmp/macws_capture_final"
touch "$ROOTFS/private/tmp/macws_vnc_share"
if [ "${STOP_AFTER_CLEAR:-0}" = 1 ]; then
    touch "$ROOTFS/private/tmp/macws_stop_after_clear"
fi
date +%Y-%m-%dT%H:%M:%S%z > /tmp/macws_type82_test_start

bash /var/jb/usr/macOS/bin/macos_gui.sh \
    start coexist --no-terminal --no-vnc
launchctl start 'UIKitApplication:com.macwsguide.windowserver'

found=0
i=0
while [ "$i" -lt 45 ]; do
    if grep -Eq 'VNC-FINAL (captured|pf550 read rejected|DIAGNOSTIC-STOP)|VNC-FINAL pass MACWS VNC pf550 scanout read error' \
        "$WS_LOG" 2>/dev/null; then
        found=1
        break
    fi
    sleep 1
    i=$((i + 1))
done

echo "MACWS_TEST_WAIT_SECONDS=$i FOUND=$found"
grep -E 'AGXIOC type=0x82 patch #1|AGX_RES_DIAG #14 (RAW|SENT|RETURN)|VNC-FINAL (clear-control|control clear|captured|pf550 read rejected)|VNC-FINAL pass MACWS VNC pf550' \
    "$WS_LOG" 2>/dev/null | head -40

bash "$PROJECT/misc/cleanup_all.sh" >/tmp/macws-posttest-cleanup.log 2>&1 || true

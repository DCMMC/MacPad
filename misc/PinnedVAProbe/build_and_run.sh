#!/bin/bash
# Build PinnedVAProbe on-device and run it.
#
# Usage:
#   bash misc/PinnedVAProbe/build_and_run.sh                 # default requestedVA = 0x1158048000
#   bash misc/PinnedVAProbe/build_and_run.sh 0x2000000000    # custom VA
#
# Runs from iOS shell (NOT in chroot — we want to probe the native iOS path).
# Assumes Theos is at /var/jb/var/mobile/theos on-device, repo at
# /var/jb/var/mobile/MacWSBootingGuide.

set -e

DEVICE_IP="${DEVICE_IP:-192.168.5.8}"
DEVICE_PORT="${DEVICE_PORT:-2222}"
REQUESTED_VA="${1:-0x1158048000}"

echo "=== build_and_run: building PinnedVAProbe ==="
ssh -p "$DEVICE_PORT" "root@$DEVICE_IP" bash -s <<'EOF'
set -e
export THEOS=/var/jb/var/mobile/theos
cd /var/jb/var/mobile/MacWSBootingGuide/misc/PinnedVAProbe
# Always clean first — Theos's incremental build sometimes misses ObjC source edits
# pushed via scp (mtime granularity confuses make). One full rebuild per push is cheap.
rm -rf .theos
make 2>&1 | tail -10
# Without FINALPACKAGE=1 the binary lands in obj/debug/. Look in both spots.
BIN=$(ls .theos/obj/PinnedVAProbe .theos/obj/debug/PinnedVAProbe 2>/dev/null | head -1)
[ -z "$BIN" ] && { echo "BUILD FAILED: no output binary"; exit 1; }
ls -l "$BIN"
ldid -S/var/jb/var/mobile/MacWSBootingGuide/entitlements.plist -M "$BIN"
sudo install -m 0755 "$BIN" /var/jb/usr/macOS/bin/PinnedVAProbe
H=$(ldid -arch arm64 -h /var/jb/usr/macOS/bin/PinnedVAProbe 2>/dev/null | grep CDHash= | cut -c8-)
echo "CDHash: $H"
[ -n "$H" ] && sudo /var/jb/usr/bin/jbctl trustcache add "$H"
EOF

echo
echo "=== running PinnedVAProbe with VA=$REQUESTED_VA ==="
ssh -p "$DEVICE_PORT" "root@$DEVICE_IP" \
    "sudo /var/jb/usr/macOS/bin/PinnedVAProbe $REQUESTED_VA"
RC=$?
echo
echo "=== exit code: $RC ==="
echo "Interpretation:"
echo "  0  -> requestedVA == reportedVA  : Path C2 VIABLE (selector honored end-to-end)"
echo "  7  -> requestedVA != reportedVA  : pinned-VA is bookkeeping only, Path C2 DEAD"
echo "  3  -> selector not on class      : framework/class mismatch — investigate"
echo "  5  -> init returned nil          : args struct shape wrong, need IOGPU reverse-engineering"
echo "  other -> see stderr"

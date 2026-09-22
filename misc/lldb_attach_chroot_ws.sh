#!/bin/bash
# Attach iOS lldb to the chroot WindowServer process.
# Useful for runtime debugging when static analysis isn't enough.
#
# Usage:
#   ssh -p 2222 root@<ip> "bash /var/jb/usr/macOS/bin/lldb_attach_chroot_ws.sh"
#
# Then inside lldb prompt:
#   (lldb) thread backtrace all
#   (lldb) memory read --force <addr>
#   (lldb) p (char*)dlerror()
#   (lldb) image list AGXMetal13_3
#   (lldb) breakpoint set --address 0x204435e58   # setupDeferred imp at runtime
#   (lldb) process continue
#   (lldb) process detach
set -e
PID=$(pgrep -nf "SkyLight.framework/Resources/WindowServer" || pgrep -n WindowServer)
if [ -z "$PID" ]; then
    echo "WindowServer not running"
    exit 1
fi
echo "Attaching iOS lldb to WindowServer pid=$PID"
exec /var/jb/usr/bin/lldb -p "$PID"

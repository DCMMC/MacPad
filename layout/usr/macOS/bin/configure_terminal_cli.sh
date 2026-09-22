# Configure only MacWS's managed tail block in the chroot user's interactive
# shell file. Keep this in one helper because both the Debian maintainer script
# and the manually-invoked deep postinst path must establish the same CLI
# environment.
set -e

ROOTFS=${1:-/var/mnt/rootfs}
case "$ROOTFS" in
	/*) ;;
	*) echo "ERROR: macOS rootfs must be an absolute path: $ROOTFS" >&2; exit 1 ;;
esac

TERMINAL_USER_BASHRC="$ROOTFS/Users/root/.bashrc"
TERMINAL_CLI_ENV_MARKER='# MacWS: managed CLI environment v2'
mkdir -p "${TERMINAL_USER_BASHRC%/*}"
if ! grep -Fq "$TERMINAL_CLI_ENV_MARKER" "$TERMINAL_USER_BASHRC" 2>/dev/null; then
	{
		printf '\n%s\n' "$TERMINAL_CLI_ENV_MARKER"
		printf 'export PATH=/opt/local/bin:/opt/local/sbin:/usr/local/bin:/opt/homebrew/bin:/opt/homebrew/sbin:/usr/bin:/bin:/usr/sbin:/sbin\n'
		# The upstream 341 KiB Bash implementation starts many short-lived
		# processes. Native macws-neofetch gathers the same default core fields
		# in one process; the original remains available by absolute path.
		printf "alias neofetch='command /usr/local/bin/macws-neofetch'\n"
	} >> "$TERMINAL_USER_BASHRC"
fi
echo '[INFO] Terminal CLI priority and fast neofetch profile are installed'

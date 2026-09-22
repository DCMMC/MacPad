# iOS-side launch preflight for the optional Steam GUI job.  launchd invokes
# this file through Procursus bash, so it deliberately has no shebang (AMFI on
# the target rejects execve of scripts with a shebang).

ROOTFS=/var/mnt/rootfs
STEAM_TMP="$ROOTFS/private/tmp"
TRUST_PREFLIGHT=/var/jb/usr/macOS/bin/ensure_steam_trust.sh
CHROOT_EXEC=/var/jb/usr/macOS/bin/launchdchrootexec

export PATH=/var/jb/usr/bin:/var/jb/usr/sbin:/usr/bin:/bin:/usr/sbin:/sbin

launch_args=()
if [ -f "$STEAM_TMP/macws_steam_applaunch_once" ]; then
    IFS= read -r appid < "$STEAM_TMP/macws_steam_applaunch_once"
    /var/jb/usr/bin/rm -f "$STEAM_TMP/macws_steam_applaunch_once"
    case "$appid" in
        *[!0-9]*|'') exit 64 ;;
    esac
    launch_args=(-applaunch "$appid")
fi

retire_breakpad_backlog() {
    # Runtime-confirmed 2026-08-23: /private/tmp/dumps1 retained 348
    # Config-* files and 414 minidumps.  crashhandler.dylib logged
    # "Uploaded 348 pending dumps" and its RE-confirmed loop at arm64
    # +0x6060..+0x6150 calls system(3) once for every Config-* file.  On this
    # chroot those children remained zombies of steam_osx, exhausting the
    # process table.  These are Breakpad's temporary upload queue, not Steam
    # game or account data.  Remove every validated numbered sibling before a
    # new owner is launched; never touch the queue of a live Steam process.
    local dump_dir
    for dump_dir in "$STEAM_TMP"/dumps "$STEAM_TMP"/dumps[0-9]*; do
        [ -d "$dump_dir" ] || continue
        case "$dump_dir" in
            "$STEAM_TMP"/dumps|"$STEAM_TMP"/dumps[0-9]*) ;;
            *)
                printf 'Steam runtime preflight: refusing unexpected dump path %s\n' \
                    "$dump_dir" >&2
                return 1
                ;;
        esac
        /var/jb/usr/bin/rm -rf "$dump_dir" || return 1
    done
}

if ! /var/jb/usr/bin/killall -0 steam_osx 2>/dev/null; then
    # Jetsam can kill steam_osx while leaving its CEF singleton namespaces and
    # Breakpad queue behind.  Retire only exact temporary namespaces while no
    # Steam owner exists.  Game data, htmlcache, login state and steamapps are
    # outside this directory and remain untouched.
    /var/jb/usr/bin/find "$STEAM_TMP" -maxdepth 1 -type d \
        \( -name '.com.valvesoftware.Steam.*' -o \
           -name 'steam??????' -o -name steam \) \
        -exec /var/jb/usr/bin/rm -rf {} +
    retire_breakpad_backlog || exit 1
fi

/var/jb/usr/bin/rm -f "$STEAM_TMP/steam.pipe"
/var/jb/usr/bin/find "$STEAM_TMP" -maxdepth 1 \
    \( -type f -o -type p -o -type s \) \
    \( -name '.macws-steam-sem-*' -o -name '.macws-sysvsem-*' -o \
       -name 'steam_chrome_shmem_uid501_spid*' -o -name 'steam??????' \) \
    -delete

/var/jb/usr/bin/bash "$TRUST_PREFLIGHT" || exit $?
exec "$CHROOT_EXEC" 501 501 "$ROOTFS" /bin/bash \
    /usr/local/bin/macws-run-steam.sh "${launch_args[@]}"

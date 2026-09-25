# iOS-side launch preflight for the optional Steam GUI job.  launchd invokes
# this file through Procursus bash, so it deliberately has no shebang (AMFI on
# the target rejects execve of scripts with a shebang).

ROOTFS=/var/mnt/rootfs
STEAM_TMP="$ROOTFS/private/tmp"
TRUST_PREFLIGHT=/var/jb/usr/macOS/bin/ensure_steam_trust.sh
CHROOT_EXEC=/var/jb/usr/macOS/bin/launchdchrootexec
DEFAULTS_BIN=/usr/bin/defaults
SEVEN_DTD_PREFERENCES=com.The-Fun-Pimps.7-Days-To-Die

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

prepare_7dtd_arm_runtime() {
    # Steam app 251570 currently ships x86_64 launcher/player entry points.
    # A user-provisioned arm64 Unity 2022.3.62f2 bundle is intentionally kept
    # separate from those depot-owned files.  Publish one bounded runtime copy
    # below macws-runtime so MacWSSteamProcess can preserve Steam as launch
    # owner while selecting executable code that iPadOS can actually run.
    local common_root="$ROOTFS/Users/root/Library/Application Support/Steam/steamapps/common/7 Days To Die"
    local runtime_root="$ROOTFS/Users/root/Library/Application Support/Steam/steamapps/macws-runtime/7 Days To Die"
    local source="$common_root/7DaysToDie-ARM.app"
    local destination="$runtime_root/7DaysToDie-ARM.app"
    local depot_link="$runtime_root/7DaysToDie.app"
    local marker="$runtime_root/.macws-shadow-ready"
    local temporary="$runtime_root/.7DaysToDie-ARM.app.new.$$"
    local source_executable="$source/Contents/MacOS/7 Days To Die"
    local destination_executable="$destination/Contents/MacOS/7 Days To Die"

    [ -f "$source/Contents/Info.plist" ] &&
        [ -f "$source_executable" ] || return 0
    /var/jb/usr/bin/lipo "$source_executable" -verify_arch arm64 \
        >/dev/null 2>&1 || {
        printf 'Steam runtime preflight: refusing non-arm64 7DTD runtime %s\n' \
            "$source_executable" >&2
        return 1
    }
    mkdir -p "$runtime_root" || return 1
    if [ ! -d "$destination" ]; then
        rm -rf "$temporary"
        cp -a "$source" "$temporary" || {
            rm -rf "$temporary"
            return 1
        }
        mv "$temporary" "$destination" || {
            rm -rf "$temporary"
            return 1
        }
    elif ! cmp -s "$source_executable" "$destination_executable" ||
         ! cmp -s "$source/Contents/Info.plist" \
                   "$destination/Contents/Info.plist"; then
        printf 'Steam runtime preflight: existing 7DTD arm64 runtime does not match its provisioned source\n' >&2
        return 1
    fi

    # The compact ARM bundle deliberately uses relative links into its sibling
    # 7DaysToDie.app for the 20-GiB Data/Resources tree.  Recreate that sibling
    # name as a link to Steam's untouched depot rather than duplicating it.
    if [ ! -e "$depot_link" ]; then
        ln -s '../../common/7 Days To Die/7DaysToDie.app' "$depot_link" ||
            return 1
    fi
    [ -e "$destination/Data" ] &&
        [ -e "$destination/Contents/Resources" ] || {
        printf 'Steam runtime preflight: 7DTD runtime resource links are unresolved\n' >&2
        return 1
    }
    printf '%s\n' '7dtd-arm64-unity-2022.3.62f2-v1' > "$marker" || return 1
    chmod 0644 "$marker" || return 1
    printf 'Steam runtime preflight: 7DTD arm64 runtime ready\n'
}

prepare_7dtd_user_data() {
    # Steam launches the prepared player as uid/gid 501.  Earlier direct root
    # diagnostics created both of Unity's real persistent-data directories as
    # root, which makes the production process fail to create its content
    # catalog and rewrite Saves/serveradmin.xml.  Repair only these two exact
    # game-owned trees; never widen ~/Library/Application Support itself.
    local support="$ROOTFS/Users/root/Library/Application Support"
    local legacy="$support/7DaysToDie"
    local unity="$support/com.The-Fun-Pimps.7-Days-To-Die"
    local logs="$ROOTFS/Users/root/Library/Logs"
    local unity_logs="$logs/Unity"
    local root_preferences="$ROOTFS/private/var/root/Library/Preferences/com.The-Fun-Pimps.7-Days-To-Die.plist"
    local mobile_preferences="$ROOTFS/Users/mobile/Library/Preferences"
    local mobile_game_preferences="$mobile_preferences/com.The-Fun-Pimps.7-Days-To-Die.plist"
    local directory

    for directory in "$legacy" "$unity"; do
        case "$directory" in
            "$support/7DaysToDie"|\
            "$support/com.The-Fun-Pimps.7-Days-To-Die") ;;
            *)
                printf 'Steam runtime preflight: refusing unexpected 7DTD data path %s\n' \
                    "$directory" >&2
                return 1
                ;;
        esac
        if [ -L "$directory" ]; then
            printf 'Steam runtime preflight: refusing symlinked 7DTD data path %s\n' \
                "$directory" >&2
            return 1
        fi
        mkdir -p "$directory" || return 1
        # GNU chown's default recursive traversal does not follow symlinks it
        # encounters.  -h also changes a link itself rather than its target.
        chown -R -h 501:501 "$directory" || return 1
    done
    if [ -L "$logs" ] || [ -L "$unity_logs" ]; then
        printf 'Steam runtime preflight: refusing symlinked Unity log path\n' >&2
        return 1
    fi
    mkdir -p "$unity_logs" || return 1
    chown root:wheel "$logs" || return 1
    chmod 0755 "$logs" || return 1
    chown -R -h 501:501 "$unity_logs" || return 1

    # The uid-501 cfprefsd login agent resolves its real home to /Users/mobile.
    # Preserve an existing mobile preference domain.  On first migration only,
    # carry forward the player's root-era Unity preferences (including their
    # chosen graphics/audio settings and EULA decision) without editing any
    # key or value.
    if [ -L "$mobile_preferences" ]; then
        printf 'Steam runtime preflight: refusing symlinked mobile preferences path\n' >&2
        return 1
    fi
    mkdir -p "$mobile_preferences" || return 1
    chown 501:501 "$ROOTFS/Users/mobile" \
        "$ROOTFS/Users/mobile/Library" "$mobile_preferences" || return 1
    chmod 0755 "$ROOTFS/Users/mobile" "$ROOTFS/Users/mobile/Library" || return 1
    chmod 0700 "$mobile_preferences" || return 1
    if [ -f "$root_preferences" ] && [ ! -e "$mobile_game_preferences" ]; then
        cp -p "$root_preferences" "$mobile_game_preferences" || return 1
        chown 501:501 "$mobile_game_preferences" || return 1
        chmod 0600 "$mobile_game_preferences" || return 1
    fi
    printf 'Steam runtime preflight: 7DTD uid-501 data roots ready\n'
}

run_7dtd_mobile_defaults() {
    HOME=/Users/mobile USER=mobile LOGNAME=mobile \
        MACWS_CFPREFERENCES_CLIENT=1 MACWS_SYNTHETIC_MOBILE_USER=1 \
        /var/jb/usr/bin/timeout -k 2 15 \
        "$CHROOT_EXEC" 501 501 "$ROOTFS" "$DEFAULTS_BIN" "$@"
}

prepare_7dtd_m2_graphics_profile() {
    # Runtime-confirmed on the iPad14,5 test device with the exact arm64
    # Unity 2022.3.62f2 player: the game's stock 1.0 dynamic-resolution
    # preference allocates a 2732x2048 intermediate and settles at
    # 21.35-24.35 FPS.  The game's own `gfx dr 0.35` path allocates 956x716,
    # keeps the completed frame visually correct, and reached 60.56 FPS.
    # Persist that real GamePrefs value once, through the uid-501 cfprefsd
    # agent.  Exact machine gating keeps the M1/iOS 16.3 path untouched.
    local machine="" marker="" current_scale="" applied_scale=""
    # This preflight runs on rootless iOS, where Procursus publishes sysctl at
    # /var/jb/usr/sbin and /usr/sbin/sysctl does not exist.  Resolve it through
    # the explicit PATH above so the exact-device gate is actually evaluated.
    # Runtime-confirmed on the iPad14,5 test host: the old absolute path logged
    # `no such file or directory` and no graphics-profile line was emitted.
    machine=$(sysctl -n hw.machine 2>/dev/null) || machine=""
    [ "$machine" = iPad14,5 ] || return 0

    marker=$(run_7dtd_mobile_defaults read "$SEVEN_DTD_PREFERENCES" \
        MacWSM2GraphicsProfileVersion 2>/dev/null) || marker=""
    [ "$marker" = 1 ] && return 0

    current_scale=$(run_7dtd_mobile_defaults read \
        "$SEVEN_DTD_PREFERENCES" OptionsGfxDynamicScale 2>/dev/null) || \
        current_scale=""
    case "$current_scale" in
        ''|1|1.0|1.00|1.000|1.0000|1.00000|1.000000)
            run_7dtd_mobile_defaults write "$SEVEN_DTD_PREFERENCES" \
                OptionsGfxUpscalerMode -int 4 || return 1
            run_7dtd_mobile_defaults write "$SEVEN_DTD_PREFERENCES" \
                OptionsGfxDynamicScale -float 0.35 || return 1
            applied_scale=$(run_7dtd_mobile_defaults read \
                "$SEVEN_DTD_PREFERENCES" OptionsGfxDynamicScale) || return 1
            case "$applied_scale" in
                0.35|0.350000) ;;
                *)
                    printf 'Steam runtime preflight: 7DTD M2 scale verification failed (%s)\n' \
                        "$applied_scale" >&2
                    return 1
                    ;;
            esac
            printf 'Steam runtime preflight: applied iPad14,5 7DTD graphics profile scale=0.35 upscaler=4\n'
            ;;
        *)
            # A non-default value is an existing user decision.  Record that
            # this migration was considered, but never overwrite it now or on
            # subsequent Steam launches.
            printf 'Steam runtime preflight: preserved user 7DTD dynamic scale=%s on iPad14,5\n' \
                "$current_scale"
            ;;
    esac
    run_7dtd_mobile_defaults write "$SEVEN_DTD_PREFERENCES" \
        MacWSM2GraphicsProfileVersion -int 1 || return 1
    marker=$(run_7dtd_mobile_defaults read "$SEVEN_DTD_PREFERENCES" \
        MacWSM2GraphicsProfileVersion) || return 1
    [ "$marker" = 1 ] || {
        printf 'Steam runtime preflight: 7DTD M2 profile marker verification failed (%s)\n' \
            "$marker" >&2
        return 1
    }
}

prepare_steam_user_cache() {
    # Steam itself also runs as uid 501 while retaining its existing
    # /Users/root data root.  Runtime log evidence on iPad14,5 showed CEF's
    # Simple Cache Backend repeatedly failing to create the exact
    # Library/Caches/Steam/.../Code Cache children after an earlier root-run
    # generation created that tree.  Repair only Steam's own cache subtree;
    # login state, userdata and steamapps remain untouched.
    local cache_root="$ROOTFS/Users/root/Library/Caches"
    local steam_cache="$cache_root/Steam"
    if [ -L "$cache_root" ] || [ -L "$steam_cache" ]; then
        printf 'Steam runtime preflight: refusing symlinked Steam cache path\n' >&2
        return 1
    fi
    mkdir -p "$steam_cache" || return 1
    chown root:wheel "$cache_root" || return 1
    chmod 0755 "$cache_root" || return 1
    chown -R -h 501:501 "$steam_cache" || return 1
    printf 'Steam runtime preflight: uid-501 Steam cache ready\n'
}

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

prepare_7dtd_arm_runtime || exit $?
prepare_7dtd_user_data || exit $?
prepare_7dtd_m2_graphics_profile || exit $?
prepare_steam_user_cache || exit $?
/var/jb/usr/bin/bash "$TRUST_PREFLIGHT" || exit $?
exec "$CHROOT_EXEC" 501 501 "$ROOTFS" /bin/bash \
    /usr/local/bin/macws-run-steam.sh "${launch_args[@]}"

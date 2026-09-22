# No shebang: iPadOS AMFI rejects execve of shebang scripts in this setup.
# Invoke with: bash /var/jb/usr/macOS/bin/ensure_settings_extensions_runtime.sh

set -e

ROOTFS=/var/mnt/rootfs
EXTENSIONS_ROOT="$ROOTFS/System/Library/ExtensionKit/Extensions"
SETTINGS_PLUGINS_ROOT="$ROOTFS/System/Applications/System Settings.app/Contents/PlugIns"
LIBMACHOOK=/var/jb/usr/macOS/lib/libmachook.dylib
SUBSTRATE=/var/jb/usr/lib/libellekit.dylib
TRAMPOLINES="$ROOTFS/usr/lib/libobjc-trampolines.dylib"
MACHO_PATCHER=/var/jb/usr/macOS/bin/set_macos_version.py
LOAD_PATCHER=/var/jb/usr/macOS/bin/add_macho_load_dylib.py
SETTINGS_ENT=/var/jb/usr/macOS/bin/settings-extension-entitlements.plist
LDID=/var/jb/usr/bin/ldid
JBCTL=/var/jb/usr/bin/jbctl
OTOOL=/var/jb/usr/bin/otool
PLUTIL=/var/jb/usr/bin/plutil
UICACHE=/var/jb/usr/bin/uicache
BASE_CARRIER_APP=/var/jb/Applications/SettingsExtensionProxy.app
BASE_CARRIER_EXECUTABLE="$BASE_CARRIER_APP/SettingsExtensionProxy"
CARRIER_ENTITLEMENTS="/tmp/macws-settings-carrier-entitlements.$$"
TRUST_MANIFEST=/var/jb/var/mobile/macws-settings-runtime.trust-hashes
UICACHE_LIST=""
TRUSTCACHE_INFO=""
RUNTIME_SCHEMA="macws-settings-extension-runtime-v2"
RUNTIME_BASE_FINGERPRINT=""
RUNTIME_HOOK_HASH=""
RUNTIME_SUBSTRATE_HASH=""
RUNTIME_TRAMPOLINES_HASH=""

if [ ! -d "$EXTENSIONS_ROOT" ]; then
    echo '[INFO] Settings extension runtime deferred: macOS rootfs is not mounted'
    exit 0
fi
for required in "$LIBMACHOOK" "$SUBSTRATE" "$TRAMPOLINES" \
                "$MACHO_PATCHER" "$LOAD_PATCHER" "$SETTINGS_ENT"; do
    if [ ! -f "$required" ]; then
        echo "[ERROR] Settings extension runtime prerequisite missing: $required" >&2
        exit 1
    fi
done
if [ ! -f "$BASE_CARRIER_APP/Info.plist" ] ||
   [ ! -x "$BASE_CARRIER_EXECUTABLE" ]; then
    echo "[ERROR] Settings extension carrier is missing: $BASE_CARRIER_APP" >&2
    exit 1
fi

# Single-process complete verifier: real signatures, per-pane identity/setuid,
# iOS registration and current trustcache membership. A mismatch returns to
# macwshostd's existing repair path; never accept a stale boot marker instead.
if [ "$#" -eq 1 ] && [ "$1" = "--verify" ] &&
   [ -f /var/jb/usr/macOS/bin/macws_settings_verify.py ]; then
    exec /var/jb/usr/bin/python3 /var/jb/usr/macOS/bin/macws_settings_verify.py
fi

selected_cdhash() {
    local path="$1" hash
    hash=$($LDID -arch arm64e -h "$path" 2>/dev/null |
        grep CDHash= | cut -c8-)
    if [ -z "$hash" ]; then
        hash=$($LDID -arch arm64 -h "$path" 2>/dev/null |
            grep CDHash= | cut -c8-)
    fi
    printf '%s' "$hash"
}

RUNTIME_HOOK_HASH=$(selected_cdhash "$LIBMACHOOK")
RUNTIME_SUBSTRATE_HASH=$(selected_cdhash "$SUBSTRATE")
RUNTIME_TRAMPOLINES_HASH=$(selected_cdhash "$TRAMPOLINES")
[ -n "$RUNTIME_HOOK_HASH" ] && [ -n "$RUNTIME_SUBSTRATE_HASH" ] &&
    [ -n "$RUNTIME_TRAMPOLINES_HASH" ] || {
    echo '[ERROR] Settings runtime dependency hash is missing' >&2
    exit 1
}
RUNTIME_BASE_FINGERPRINT="$RUNTIME_SCHEMA|$RUNTIME_HOOK_HASH|$RUNTIME_SUBSTRATE_HASH|$RUNTIME_TRAMPOLINES_HASH"
# Verification reads the real pane/runtime identities and current trustcache.
# No boot-ready flag can substitute for those prerequisites. The production
# Python verifier above batches the same checks into one process.

$LDID -e "$BASE_CARRIER_EXECUTABLE" > "$CARRIER_ENTITLEMENTS" 2>/dev/null
[ ! -x "$UICACHE" ] || UICACHE_LIST=$($UICACHE -l 2>/dev/null || true)
# Normalize the live dump once. The dependency repair visits 49 panes and
# checks five hashes each; spawning grep for every membership query made a
# no-op reconciliation expensive even when all images were already trusted.
TRUSTCACHE_INFO=$($JBCTL trustcache info 2>/dev/null |
    tr '[:lower:]' '[:upper:]' || true)
trap 'rm -f "$CARRIER_ENTITLEMENTS"' EXIT

ensure_trust_hash() {
    local hash="$1" normalized
    [ -n "$hash" ] || return 0
    [[ "$hash" =~ ^[0-9A-Fa-f]{40}$ ]] || return 1
    normalized=${hash^^}
    if [[ "$TRUSTCACHE_INFO" == *"$normalized"* ]]; then
        return 0
    fi
    $JBCTL trustcache add "$hash" >/dev/null 2>&1 || return 1
    TRUSTCACHE_INFO="${TRUSTCACHE_INFO}
$normalized"
}

restore_runtime_trust_manifest() {
    local manifest_fingerprint hash normalized restored=0
    [ -f "$TRUST_MANIFEST" ] || return 0
    manifest_fingerprint=$(sed -n '1p' "$TRUST_MANIFEST" 2>/dev/null)
    [ "$manifest_fingerprint" = "$RUNTIME_BASE_FINGERPRINT" ] || return 0
    while IFS= read -r hash; do
        [ -n "$hash" ] || continue
        [[ "$hash" =~ ^[0-9A-Fa-f]{40}$ ]] || return 1
        normalized=${hash^^}
        if [[ "$TRUSTCACHE_INFO" != *"$normalized"* ]]; then
            $JBCTL trustcache add "$hash" >/dev/null 2>&1 || return 1
            TRUSTCACHE_INFO="${TRUSTCACHE_INFO}
$normalized"
            restored=$((restored + 1))
        fi
    done < <(sed -n '2,$p' "$TRUST_MANIFEST" 2>/dev/null)
    echo "[INFO] Settings runtime trust manifest restored: $restored"
}

write_runtime_trust_manifest() {
    local temporary="${TRUST_MANIFEST}.new-$$" marker=""
    {
        printf '%s\n' "$RUNTIME_BASE_FINGERPRINT"
        for marker in "$EXTENSIONS_ROOT"/*.appex/Contents/Frameworks/.macws-settings-runtime \
            "$SETTINGS_PLUGINS_ROOT"/*.appex/Contents/Frameworks/.macws-settings-runtime; do
            [ -f "$marker" ] || continue
            awk -F'|' '{ for (field = 5; field <= 9; field++) if ($field != "") print $field }' \
                "$marker"
        done | sort -u
    } > "$temporary" || {
        rm -f "$temporary"
        return 1
    }
    chmod 0644 "$temporary" || return 1
    mv -f "$temporary" "$TRUST_MANIFEST"
}

# The dynamic trustcache is recreated after a device boot.  Re-add only the
# exact CDHashes recorded by the previous full 48-pane verification, then run
# the unchanged deep verifier below.  This turns cold-boot recovery into a
# bounded trust restore instead of re-signing/copying every pane and carrier.
if [ "$#" -eq 1 ] && [ "$1" = "--verify" ]; then
    restore_runtime_trust_manifest || {
        echo '[ERROR] Settings runtime trust manifest restore failed' >&2
        exit 1
    }
fi

trust_macho() {
    local path="$1" arch hash
    for arch in arm64 arm64e x86_64; do
        hash=$($LDID -arch "$arch" -h "$path" 2>/dev/null |
            grep CDHash= | cut -c8-)
        [ -z "$hash" ] || ensure_trust_hash "$hash" || true
    done
}

fresh_copy_if_changed() {
    local source="$1" destination="$2" temporary
    if [ -f "$destination" ] && cmp -s "$source" "$destination"; then
        return 0
    fi
    temporary="${destination}.new-$$"
    rm -f "$temporary"
    cp "$source" "$temporary" || return 1
    # Verify the replacement before its atomic rename. The caller no longer
    # needs a second full-file cmp for every already-identical pane copy.
    cmp -s "$source" "$temporary" || {
        rm -f "$temporary"
        return 1
    }
    chmod 755 "$temporary" || return 1
    chown root:wheel "$temporary" 2>/dev/null || true
    mv -f "$temporary" "$destination"
}

prepare_carrier() {
    local identifier="$1" carrier_identifier carrier_app carrier_executable
    local source_hash marker_hash temporary arch hash
    printf '%s\n' "$identifier" |
        grep -Eq '^[A-Za-z0-9.-]+$' || {
            echo "[ERROR] Unsafe Settings extension identifier: $identifier" >&2
            return 1
        }
    carrier_identifier="com.macwsguide.settings-extension-carrier.$identifier"
    carrier_app="/var/jb/Applications/MacWSSettingsExtension-$identifier.app"
    carrier_executable="$carrier_app/SettingsExtensionProxy"
    source_hash=$($LDID -arch arm64e -h "$BASE_CARRIER_EXECUTABLE" 2>/dev/null |
        grep CDHash= | cut -c8-)
    if [ -z "$source_hash" ]; then
        source_hash=$($LDID -arch arm64 -h "$BASE_CARRIER_EXECUTABLE" 2>/dev/null |
            grep CDHash= | cut -c8-)
    fi
    [ -n "$source_hash" ] || {
        echo "[ERROR] Cannot identify Settings carrier source image" >&2
        return 1
    }

    mkdir -p "$carrier_app"
    chmod 755 "$carrier_app"
    if [ ! -f "$carrier_app/Info.plist" ]; then
        cp "$BASE_CARRIER_APP/Info.plist" "$carrier_app/Info.plist"
    fi
    $PLUTIL -key CFBundleIdentifier -value "$carrier_identifier" \
        "$carrier_app/Info.plist" >/dev/null
    chmod 644 "$carrier_app/Info.plist"

    marker_hash=""
    [ -f "$carrier_app/.macws-source-cdhash" ] &&
        marker_hash=$(sed -n '1p' "$carrier_app/.macws-source-cdhash")
    if [ "$marker_hash" != "$source_hash" ] ||
       [ ! -x "$carrier_executable" ] ||
       ! $LDID -h "$carrier_executable" 2>/dev/null |
           grep -Fqx "Identifier=$carrier_identifier"; then
        temporary="${carrier_executable}.new-$$"
        rm -f "$temporary"
        cp "$BASE_CARRIER_EXECUTABLE" "$temporary"
        chmod 4755 "$temporary"
        chown root:wheel "$temporary" 2>/dev/null || true
        $LDID -I"$carrier_identifier" -S"$CARRIER_ENTITLEMENTS" -M \
            "$temporary"
        $LDID -I"$carrier_identifier" -S"$CARRIER_ENTITLEMENTS" -M \
            "$temporary"
        mv -f "$temporary" "$carrier_executable"
        printf '%s\n' "$source_hash" > "$carrier_app/.macws-source-cdhash"
    fi
    chown root:wheel "$carrier_executable" 2>/dev/null || true
    chmod 4755 "$carrier_executable"
    for arch in arm64 arm64e; do
        hash=$($LDID -arch "$arch" -h "$carrier_executable" 2>/dev/null |
            grep CDHash= | cut -c8-)
        [ -z "$hash" ] || ensure_trust_hash "$hash" || true
    done
    if [ -x "$UICACHE" ] &&
       ! printf '%s\n' "$UICACHE_LIST" |
           grep -Fq "$carrier_identifier : "; then
        $UICACHE -p "$carrier_app" >/dev/null 2>&1 || {
            echo "[ERROR] Failed to register Settings carrier: $carrier_app" >&2
            return 1
        }
        UICACHE_LIST="${UICACHE_LIST}
$carrier_identifier : $carrier_app"
    fi
}

prepare_extension() {
    local bundle="$1" contents info executable_name identifier executable
    local frameworks substrate_local changed dependency output entitlements
    local candidate candidate_count candidate_path
    local carrier_app carrier_executable runtime_marker runtime_entry
    local executable_hash carrier_hash hook_hash substrate_hash trampolines_hash
    contents="$bundle/Contents"
    info="$contents/Info.plist"
    [ -f "$info" ] || return 0
    # Procursus plutil writes its structured display to stderr.  Select only
    # the exact Ventura Settings extension point; unrelated ExtensionKit
    # bundles in this directory remain untouched.
    $PLUTIL -show "$info" 2>&1 |
        grep -Fq 'EXExtensionPointIdentifier = "com.apple.Settings.extension.ui";' ||
        return 0
    executable_name=$($PLUTIL -key CFBundleExecutable "$info" 2>/dev/null)
    identifier=$($PLUTIL -key CFBundleIdentifier "$info" 2>/dev/null)
    # Ventura's stock WalletSettingsExtension is a valid Settings UI appex but
    # omits CFBundleExecutable from Info.plist.  Do not invent a name from the
    # display title: accept the on-disk executable only when Contents/MacOS
    # contains exactly one regular file.  Ambiguous bundles remain a hard
    # error so this cannot silently sign or launch the wrong image.
    if [ -z "$executable_name" ]; then
        candidate=""
        candidate_count=0
        for candidate_path in "$contents"/MacOS/*; do
            [ -f "$candidate_path" ] || continue
            case "$candidate_path" in
                *.macws-preload-backup|*.new-*) continue ;;
            esac
            candidate="$candidate_path"
            candidate_count=$((candidate_count + 1))
        done
        if [ "$candidate_count" -eq 1 ]; then
            executable_name=${candidate##*/}
            echo "[INFO] Settings extension has no CFBundleExecutable; using sole Contents/MacOS image: $identifier/$executable_name"
        fi
    fi
    if [ -z "$executable_name" ] || [ -z "$identifier" ]; then
        echo "[ERROR] Invalid Settings extension metadata: $bundle" >&2
        return 1
    fi
    prepare_carrier "$identifier"
    executable="$contents/MacOS/$executable_name"
    frameworks="$contents/Frameworks"
    [ -f "$executable" ] || {
        echo "[ERROR] Settings extension executable missing: $executable" >&2
        return 1
    }

    mkdir -p "$frameworks/.jbroot/Library/Frameworks/CydiaSubstrate.framework"
    chmod 755 "$frameworks" "$frameworks/.jbroot" \
        "$frameworks/.jbroot/Library" \
        "$frameworks/.jbroot/Library/Frameworks" \
        "$frameworks/.jbroot/Library/Frameworks/CydiaSubstrate.framework"

    fresh_copy_if_changed "$LIBMACHOOK" "$frameworks/libmachook.dylib"
    trust_macho "$frameworks/libmachook.dylib"

    substrate_local="$frameworks/.jbroot/Library/Frameworks/CydiaSubstrate.framework/CydiaSubstrate"
    if [ ! -f "$substrate_local" ] ||
       ! $OTOOL -l "$substrate_local" 2>/dev/null |
           grep -A4 LC_BUILD_VERSION | grep -q 'platform 1'; then
        fresh_copy_if_changed "$SUBSTRATE" "$substrate_local"
        /var/jb/usr/bin/python3 "$MACHO_PATCHER" "$substrate_local"
        $LDID -S -M "$substrate_local"
        $LDID -S -M "$substrate_local"
    fi
    trust_macho "$substrate_local"

    fresh_copy_if_changed "$TRAMPOLINES" "$frameworks/libobjc-trampolines.dylib"
    trust_macho "$frameworks/libobjc-trampolines.dylib"

    if [ ! -f "$executable.macws-preload-backup" ]; then
        cp -p "$executable" "$executable.macws-preload-backup"
    fi

    changed=0
    for dependency in \
        '@executable_path/../Frameworks/libmachook.dylib' \
        '@executable_path/../Frameworks/libobjc-trampolines.dylib'; do
        output=$(/var/jb/usr/bin/python3 "$LOAD_PATCHER" \
            "$executable" "$dependency")
        case "$output" in *'modified=1'*) changed=1 ;; esac
    done

    entitlements=$($LDID -e "$executable" 2>/dev/null || true)
    printf '%s\n' "$entitlements" |
        grep -q 'com.apple.macosbooter.lsd.modifydb' || changed=1
    $LDID -h "$executable" 2>/dev/null |
        grep -Fqx "Identifier=$identifier" || changed=1
    if [ "$changed" -eq 1 ]; then
        # -M merges MacWS admission and service exceptions into the pane's
        # existing native entitlements.  Never replace Wi-Fi/Bluetooth/etc.
        # with Appearance's private capability set.
        $LDID -I"$identifier" -S"$SETTINGS_ENT" -M "$executable"
        $LDID -I"$identifier" -S"$SETTINGS_ENT" -M "$executable"
    fi
    trust_macho "$executable"
    carrier_app="/var/jb/Applications/MacWSSettingsExtension-$identifier.app"
    carrier_executable="$carrier_app/SettingsExtensionProxy"
    runtime_marker="$frameworks/.macws-settings-runtime"
    executable_hash=$(selected_cdhash "$executable")
    carrier_hash=$(selected_cdhash "$carrier_executable")
    hook_hash=$(selected_cdhash "$frameworks/libmachook.dylib")
    substrate_hash=$(selected_cdhash "$substrate_local")
    trampolines_hash=$(selected_cdhash "$frameworks/libobjc-trampolines.dylib")
    if [ -z "$executable_hash" ] || [ -z "$carrier_hash" ] ||
       [ -z "$hook_hash" ] || [ -z "$substrate_hash" ] ||
       [ -z "$trampolines_hash" ]; then
        echo "[ERROR] Settings extension runtime hash missing: $identifier" >&2
        return 1
    fi
    runtime_entry="$RUNTIME_BASE_FINGERPRINT|$executable_hash|$carrier_hash|$hook_hash|$substrate_hash|$trampolines_hash"
    printf '%s\n' "$runtime_entry" > "$runtime_marker"
    chmod 644 "$runtime_marker"
    prepared_count=$((prepared_count + 1))
    echo "[INFO] Settings extension ready: $identifier"
}

verify_current_runtime() {
    local bundle contents info executable_name identifier executable frameworks
    local carrier_app carrier_executable runtime_marker runtime_entry
    local executable_hash carrier_hash hook_hash substrate_hash trampolines_hash hash
    local candidate candidate_count candidate_path verified_count
    local marker_schema marker_base_hook marker_base_substrate marker_base_tramp
    local marker_executable marker_carrier marker_hook marker_substrate marker_tramp marker_extra
    verified_count=0
    for bundle in "$EXTENSIONS_ROOT"/*.appex "$SETTINGS_PLUGINS_ROOT"/*.appex; do
        [ -d "$bundle" ] || continue
        contents="$bundle/Contents"
        info="$contents/Info.plist"
        [ -f "$info" ] || continue
        $PLUTIL -show "$info" 2>&1 |
            grep -Fq 'EXExtensionPointIdentifier = "com.apple.Settings.extension.ui";' ||
            continue
        executable_name=$($PLUTIL -key CFBundleExecutable "$info" 2>/dev/null)
        identifier=$($PLUTIL -key CFBundleIdentifier "$info" 2>/dev/null)
        if [ -z "$executable_name" ]; then
            candidate=""
            candidate_count=0
            for candidate_path in "$contents"/MacOS/*; do
                [ -f "$candidate_path" ] || continue
                case "$candidate_path" in
                    *.macws-preload-backup|*.new-*) continue ;;
                esac
                candidate="$candidate_path"
                candidate_count=$((candidate_count + 1))
            done
            [ "$candidate_count" -eq 1 ] && executable_name=${candidate##*/}
        fi
        [ -n "$identifier" ] && [ -n "$executable_name" ] || return 1
        executable="$contents/MacOS/$executable_name"
        frameworks="$contents/Frameworks"
        carrier_app="/var/jb/Applications/MacWSSettingsExtension-$identifier.app"
        carrier_executable="$carrier_app/SettingsExtensionProxy"
        runtime_marker="$frameworks/.macws-settings-runtime"
        [ -x "$executable" ] && [ -x "$carrier_executable" ] &&
            [ -u "$carrier_executable" ] && [ -f "$runtime_marker" ] &&
            [ -f "$frameworks/libmachook.dylib" ] &&
            [ -f "$frameworks/.jbroot/Library/Frameworks/CydiaSubstrate.framework/CydiaSubstrate" ] &&
            [ -f "$frameworks/libobjc-trampolines.dylib" ] ||
            return 1
        [ "$($PLUTIL -key CFBundleIdentifier "$carrier_app/Info.plist" 2>/dev/null)" = \
            "com.macwsguide.settings-extension-carrier.$identifier" ] || return 1
        printf '%s\n' "$UICACHE_LIST" | grep -Fq \
            "com.macwsguide.settings-extension-carrier.$identifier : " || return 1
        IFS='|' read -r marker_schema marker_base_hook \
            marker_base_substrate marker_base_tramp marker_executable \
            marker_carrier marker_hook marker_substrate marker_tramp \
            marker_extra < "$runtime_marker"
        [ -z "$marker_extra" ] &&
            [ "$marker_schema|$marker_base_hook|$marker_base_substrate|$marker_base_tramp" = \
              "$RUNTIME_BASE_FINGERPRINT" ] || return 1
        executable_hash="$marker_executable"
        carrier_hash="$marker_carrier"
        hook_hash="$marker_hook"
        substrate_hash="$marker_substrate"
        trampolines_hash="$marker_tramp"
        for hash in "$executable_hash" "$carrier_hash" "$hook_hash" \
                    "$substrate_hash" "$trampolines_hash"; do
            [ -n "$hash" ] && printf '%s\n' "$TRUSTCACHE_INFO" |
                grep -Fiq "$hash" || return 1
        done
        verified_count=$((verified_count + 1))
    done
    [ "$verified_count" -gt 0 ] || return 1
    echo "[INFO] Settings ExtensionKit runtime verification passed: $verified_count"
}

# A libmachook FAST deployment changes the dependency fingerprint embedded in
# every pane marker, but it does not change those 48 stock executables or their
# already-registered per-identity carrier apps. The former fallback repeated
# the complete carrier/uicache/entitlement pipeline and held macwshostd's
# launch-path transaction for 201.5 seconds on 2026-09-02. Reconcile exactly
# the dependency copies and trust entries first. Any missing/ambiguous bundle,
# malformed marker, changed substrate platform image, or verifier failure still
# falls through to the unchanged full preparation path in macwshostd.
repair_dependency_runtime() {
    local bundle contents info executable_name identifier executable frameworks
    local carrier_app carrier_executable runtime_marker candidate candidate_count
    local candidate_path marker_schema marker_base_hook marker_base_substrate
    local marker_base_tramp marker_executable marker_carrier marker_hook
    local marker_substrate marker_tramp marker_extra substrate_local
    local output local_substrate_hash="" repaired_count=0 hash=""

    ensure_trust_hash "$RUNTIME_HOOK_HASH" || return 1
    ensure_trust_hash "$RUNTIME_TRAMPOLINES_HASH" || return 1
    for bundle in "$EXTENSIONS_ROOT"/*.appex "$SETTINGS_PLUGINS_ROOT"/*.appex; do
        [ -d "$bundle" ] || continue
        contents="$bundle/Contents"
        info="$contents/Info.plist"
        [ -f "$info" ] || continue
        $PLUTIL -show "$info" 2>&1 |
            grep -Fq 'EXExtensionPointIdentifier = "com.apple.Settings.extension.ui";' ||
            continue
        executable_name=$($PLUTIL -key CFBundleExecutable "$info" 2>/dev/null)
        identifier=$($PLUTIL -key CFBundleIdentifier "$info" 2>/dev/null)
        if [ -z "$executable_name" ]; then
            candidate=""
            candidate_count=0
            for candidate_path in "$contents"/MacOS/*; do
                [ -f "$candidate_path" ] || continue
                case "$candidate_path" in
                    *.macws-preload-backup|*.new-*) continue ;;
                esac
                candidate="$candidate_path"
                candidate_count=$((candidate_count + 1))
            done
            [ "$candidate_count" -eq 1 ] && executable_name=${candidate##*/}
        fi
        [ -n "$identifier" ] && [ -n "$executable_name" ] || return 1
        executable="$contents/MacOS/$executable_name"
        frameworks="$contents/Frameworks"
        carrier_app="/var/jb/Applications/MacWSSettingsExtension-$identifier.app"
        carrier_executable="$carrier_app/SettingsExtensionProxy"
        runtime_marker="$frameworks/.macws-settings-runtime"
        substrate_local="$frameworks/.jbroot/Library/Frameworks/CydiaSubstrate.framework/CydiaSubstrate"
        [ -x "$executable" ] && [ -x "$carrier_executable" ] &&
            [ -u "$carrier_executable" ] && [ -f "$runtime_marker" ] &&
            [ -f "$substrate_local" ] || return 1
        [ "$($PLUTIL -key CFBundleIdentifier \
                "$carrier_app/Info.plist" 2>/dev/null)" = \
            "com.macwsguide.settings-extension-carrier.$identifier" ] ||
            return 1
        [[ "$UICACHE_LIST" == *"com.macwsguide.settings-extension-carrier.$identifier : "* ]] ||
            return 1
        IFS='|' read -r marker_schema marker_base_hook \
            marker_base_substrate marker_base_tramp marker_executable \
            marker_carrier marker_hook marker_substrate marker_tramp \
            marker_extra < "$runtime_marker"
        [ "$marker_schema" = "$RUNTIME_SCHEMA" ] && [ -z "$marker_extra" ] &&
            [ -n "$marker_executable" ] && [ -n "$marker_carrier" ] &&
            [ -n "$marker_substrate" ] && [ -n "$marker_tramp" ] || return 1

        mkdir -p "$frameworks"
        fresh_copy_if_changed \
            "$LIBMACHOOK" "$frameworks/libmachook.dylib" || return 1
        fresh_copy_if_changed \
            "$TRAMPOLINES" "$frameworks/libobjc-trampolines.dylib" || return 1

        if [ "$marker_base_substrate" != "$RUNTIME_SUBSTRATE_HASH" ] ||
           ! $OTOOL -l "$substrate_local" 2>/dev/null |
               grep -A4 LC_BUILD_VERSION | grep -q 'platform 1'; then
            fresh_copy_if_changed "$SUBSTRATE" "$substrate_local" ||
                return 1
            /var/jb/usr/bin/python3 "$MACHO_PATCHER" "$substrate_local" ||
                return 1
            $LDID -S -M "$substrate_local" || return 1
            $LDID -S -M "$substrate_local" || return 1
            local_substrate_hash=$(selected_cdhash "$substrate_local")
            [ -n "$local_substrate_hash" ] || return 1
        else
            local_substrate_hash="$marker_substrate"
        fi

        for hash in "$marker_executable" "$marker_carrier" \
                    "$RUNTIME_HOOK_HASH" "$local_substrate_hash" \
                    "$RUNTIME_TRAMPOLINES_HASH"; do
            ensure_trust_hash "$hash" || return 1
        done
        printf '%s|%s|%s|%s|%s|%s\n' \
            "$RUNTIME_BASE_FINGERPRINT" "$marker_executable" \
            "$marker_carrier" "$RUNTIME_HOOK_HASH" \
            "$local_substrate_hash" "$RUNTIME_TRAMPOLINES_HASH" > \
            "$runtime_marker" || return 1
        chmod 0644 "$runtime_marker" || return 1
        repaired_count=$((repaired_count + 1))
    done
    [ "$repaired_count" -gt 0 ] || return 1
    write_runtime_trust_manifest || return 1
    # The caller's mandatory post-repair --verify checks actual signatures
    # and live trustcache membership; do not publish a ready-file shortcut.
    echo "[INFO] Settings dependency runtimes reconciled and verified incrementally: $repaired_count"
}

prepared_count=0
if [ "$#" -eq 1 ] && [ "$1" = "--verify" ]; then
    verify_current_runtime || {
        echo '[ERROR] Settings ExtensionKit runtime verification failed' >&2
        exit 1
    }
    write_runtime_trust_manifest || {
        echo '[ERROR] Settings runtime trust manifest update failed' >&2
        exit 1
    }
    exit 0
elif [ "$#" -eq 1 ] && [ "$1" = "--repair-dependencies" ]; then
    repair_dependency_runtime || {
        echo '[ERROR] Settings dependency-only reconciliation failed' >&2
        exit 1
    }
    exit 0
elif [ "$#" -gt 0 ]; then
    for bundle in "$@"; do
        case "$bundle" in
            "$EXTENSIONS_ROOT"/*.appex|"$SETTINGS_PLUGINS_ROOT"/*.appex) ;;
            *)
                echo "[ERROR] Settings extension path is outside the stock directory: $bundle" >&2
                exit 64
                ;;
        esac
        [ -d "$bundle" ] || {
            echo "[ERROR] Settings extension bundle is missing: $bundle" >&2
            exit 66
        }
        prepare_extension "$bundle"
    done
else
    for bundle in "$EXTENSIONS_ROOT"/*.appex "$SETTINGS_PLUGINS_ROOT"/*.appex; do
        [ -d "$bundle" ] || continue
        prepare_extension "$bundle"
    done
fi
if [ "$prepared_count" -eq 0 ]; then
    echo '[ERROR] No Ventura Settings extensions were prepared' >&2
    exit 1
fi
echo "[INFO] Settings ExtensionKit runtimes ready: $prepared_count"

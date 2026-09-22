# Steam owns an updater-managed application below Application Support.  Its
# package may replace executables after macos_gui.sh has already restored the
# current boot's application trust closure.  Verify the exact updater state at
# every Steam launch, but walk/re-sign the large bundle only when either the
# iPad boot, package inventory, or primary executable bytes changed.

ROOTFS=/var/mnt/rootfs
BUNDLE="$ROOTFS/Users/root/Library/Application Support/Steam/Steam.AppBundle/Steam"
SIGNER=/var/jb/usr/macOS/bin/sign_installed.sh
LDID=/var/jb/usr/bin/ldid
JBCTL=/var/jb/usr/bin/jbctl
PYTHON=/var/jb/usr/bin/python3
LIPO=/var/jb/usr/bin/lipo
STAMP=/var/jb/var/mobile/macws-steam-trust.stamp
CEF_PATCHER=/var/jb/usr/macOS/bin/patch_steam_cef126_pa_ios_va.py
CEF_REFRESHER=/var/jb/usr/macOS/bin/refresh_steam_inventory.py
CEF_RELATIVE='Frameworks/Chromium Embedded Framework.framework/Versions/A/Chromium Embedded Framework'
CEF="$BUNDLE/Contents/MacOS/$CEF_RELATIVE"
CEF_WORK=""
STEAM_MACOS="$BUNDLE/Contents/MacOS"
STEAM_THIN_DIR="$ROOTFS/usr/local/lib"

# launchd's rootless job environment does not include Procursus utilities.
# sign_installed.sh intentionally uses ordinary shell tools such as mktemp and
# rm, so publish the same deterministic PATH used by every package installer
# before invoking it.
export PATH=/var/jb/usr/bin:/var/jb/usr/sbin:/usr/bin:/bin:/usr/sbin:/sbin

cleanup() {
    [ -n "$CEF_WORK" ] && rm -rf "$CEF_WORK"
}
trap cleanup EXIT HUP INT TERM

ensure_cef_va_geometry() {
    [ -f "$CEF" ] || {
        echo "Steam trust preflight: missing CEF image $CEF" >&2
        return 1
    }
    [ -f "$CEF_PATCHER" ] || {
        echo "Steam trust preflight: missing CEF VA port $CEF_PATCHER" >&2
        return 1
    }
    CEF_WORK=$(mktemp -d \
        "$ROOTFS/private/tmp/macws-steam-cef.XXXXXX") || return 1
    arm64="$CEF_WORK/cef-arm64"
    patched="$CEF_WORK/cef-arm64-patched"
    universal="$CEF_WORK/cef-universal"
    "$LIPO" -thin arm64 "$CEF" -output "$arm64" || return 1
    if "$PYTHON" "$CEF_PATCHER" --verify-patched "$arm64" \
            >/dev/null 2>&1; then
        echo "Steam trust preflight: CEF iPadOS VA geometry verified"
        return 0
    fi

    # Valve's updater legitimately restores its signed macOS slice.  Port the
    # exact UUID/hash-locked arm64 code before it can enter dyld.  The patcher
    # validates all 54,310 affected instructions and the final __text digest;
    # an unknown client build fails closed instead of receiving a partial port.
    "$PYTHON" "$CEF_PATCHER" "$arm64" "$patched" || return 1
    "$LIPO" "$CEF" -replace arm64 "$patched" -output "$universal" || \
        return 1
    chmod 0755 "$universal" || return 1
    chown mobile:mobile "$universal" 2>/dev/null || true
    mv -f "$universal" "$CEF" || return 1
    echo "Steam trust preflight: restored CEF iPadOS VA geometry"
}

resign_and_trust_cef() {
    # The geometry edit invalidates Valve's old CodeDirectory.  Sign the final
    # universal container before consulting the trustcache; merely finding the
    # old CDHash there does not prove that its page hashes match the new code.
    "$LDID" -S "$CEF" || return 1
    trust_inventory=$($JBCTL trustcache info 2>/dev/null |
        /var/jb/usr/bin/tr '[:upper:]' '[:lower:]') || return 1
    found=0
    for arch in arm64 x86_64; do
        hash=$($LDID -arch "$arch" -h "$CEF" 2>/dev/null |
            /var/jb/usr/bin/grep 'CDHash=' |
            /var/jb/usr/bin/cut -c8- |
            /var/jb/usr/bin/tr '[:upper:]' '[:lower:]')
        [ -n "$hash" ] || continue
        found=1
        case "$trust_inventory" in
            *"$hash"*) ;;
            *) $JBCTL trustcache add "$hash" >/dev/null || return 1 ;;
        esac
    done
    [ "$found" -eq 1 ] || return 1

    # Valve verifies size/mtime/CRC32 and a footer SHA-1 in addition to Mach-O
    # signatures.  Update only this exact existing code record.
    "$PYTHON" "$CEF_REFRESHER" "$BUNDLE" "$CEF_RELATIVE" || return 1
    rm -f "$STAMP"
}

ensure_arm64_insert_slice() {
    source=$1
    destination=$2
    [ -f "$source" ] || {
        echo "Steam trust preflight: missing insert image $source" >&2
        return 1
    }
    mkdir -p "$STEAM_THIN_DIR" || return 1
    temporary="$ROOTFS/private/tmp/.$(basename "$destination").new.$$"
    rm -f "$temporary"
    "$LIPO" -thin arm64 "$source" -output "$temporary" || return 1
    "$LDID" -S "$temporary" || return 1
    chmod 0755 "$temporary" || return 1
    chown root:wheel "$temporary" 2>/dev/null || true
    if [ -f "$destination" ] && cmp -s "$temporary" "$destination"; then
        rm -f "$temporary"
    else
        mv -f "$temporary" "$destination" || return 1
    fi

    hash=$($LDID -arch arm64 -h "$destination" 2>/dev/null |
        /var/jb/usr/bin/grep 'CDHash=' |
        /var/jb/usr/bin/cut -c8- |
        /var/jb/usr/bin/tr '[:upper:]' '[:lower:]')
    [ -n "$hash" ] || return 1
    trust_inventory=$($JBCTL trustcache info 2>/dev/null |
        /var/jb/usr/bin/tr '[:upper:]' '[:lower:]') || return 1
    case "$trust_inventory" in
        *"$hash"*) ;;
        *) $JBCTL trustcache add "$hash" >/dev/null || return 1 ;;
    esac
    echo "Steam trust preflight: arm64 insert ready $(basename "$destination")"
}

ensure_overlay_insert_slices() {
    ensure_arm64_insert_slice \
        "$STEAM_MACOS/steamloader.dylib" \
        "$STEAM_THIN_DIR/macws-steamloader-arm64.dylib" || return 1
    ensure_arm64_insert_slice \
        "$STEAM_MACOS/gameoverlayrenderer.dylib" \
        "$STEAM_THIN_DIR/macws-gameoverlayrenderer-arm64.dylib" || return 1
}

[ -d "$BUNDLE/Contents" ] || exit 0
[ -x "$SIGNER" ] || {
    echo "Steam trust preflight: missing signer $SIGNER" >&2
    exit 1
}

ensure_cef_va_geometry || exit 1
ensure_overlay_insert_slices || exit 1

boot_id=$(/var/jb/usr/sbin/sysctl -n kern.boottime 2>/dev/null |
    /var/jb/usr/bin/tr -cd '0-9,=' )

bundle_fingerprint() {
    "$PYTHON" - "$BUNDLE" <<'PY'
import glob
import hashlib
import os
import sys

bundle = sys.argv[1]
macos = os.path.join(bundle, "Contents", "MacOS")
paths = glob.glob(os.path.join(macos, "package", "*.installed"))
paths += glob.glob(os.path.join(macos, "package", "*.manifest"))
paths += [
    os.path.join(macos, "steam_osx"),
    os.path.join(
        macos,
        "Frameworks",
        "Steam Helper.app",
        "Contents",
        "MacOS",
        "Steam Helper",
    ),
]
digest = hashlib.sha256()
for path in sorted(set(paths)):
    if not os.path.isfile(path):
        continue
    digest.update(os.path.relpath(path, bundle).encode("utf-8", "surrogateescape"))
    digest.update(b"\0")
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    digest.update(b"\0")
print(digest.hexdigest())
PY
}

primary_hashes_are_trusted() {
    trust_inventory=$($JBCTL trustcache info 2>/dev/null |
        /var/jb/usr/bin/tr '[:upper:]' '[:lower:]') || return 1
    found=0
    for executable in \
        "$BUNDLE/Contents/MacOS/steam_osx" \
        "$BUNDLE/Contents/MacOS/gameoverlayui" \
        "$BUNDLE/Contents/MacOS/Frameworks/Steam Helper.app/Contents/MacOS/Steam Helper" \
        "$CEF"; do
        [ -f "$executable" ] || return 1
        executable_found=0
        for arch in arm64 arm64e x86_64; do
            hash=$($LDID -arch "$arch" -h "$executable" 2>/dev/null |
                /var/jb/usr/bin/grep 'CDHash=' |
                /var/jb/usr/bin/cut -c8- |
                /var/jb/usr/bin/tr '[:upper:]' '[:lower:]')
            [ -n "$hash" ] || continue
            executable_found=1
            found=1
            case "$trust_inventory" in
                *"$hash"*) ;;
                *) return 1 ;;
            esac
        done
        [ "$executable_found" -eq 1 ] || return 1
    done
    [ "$found" -eq 1 ]
}

fingerprint=$(bundle_fingerprint) || exit 1
expected="v1:${boot_id}:${fingerprint}"
if [ "$(/var/jb/usr/bin/sed -n '1p' "$STAMP" 2>/dev/null)" = "$expected" ] &&
   primary_hashes_are_trusted; then
    echo "Steam trust preflight: exact bundle already trusted"
    exit 0
fi

echo "Steam trust preflight: updater or boot changed; repairing exact bundle"
# Always create a fresh CodeDirectory for the exact verified CEF code on a
# repair path.  This covers both a new boot (empty live trustcache) and an
# interrupted prior transaction which reached the atomic geometry replacement
# but not its signature step.
resign_and_trust_cef || exit 1
/var/jb/usr/bin/bash "$SIGNER" "$BUNDLE" || exit 1
primary_hashes_are_trusted || {
    echo "Steam trust preflight: primary executable trust verification failed" >&2
    exit 1
}

# sign_installed.sh refreshes Valve's installed-code inventory after changing
# CodeDirectories, so fingerprint the committed post-sign state rather than
# the input state.
fingerprint=$(bundle_fingerprint) || exit 1
expected="v1:${boot_id}:${fingerprint}"
temporary="${STAMP}.new.$$"
/var/jb/usr/bin/printf '%s\n' "$expected" > "$temporary" || exit 1
/var/jb/usr/bin/chmod 0644 "$temporary" || exit 1
/var/jb/usr/bin/mv -f "$temporary" "$STAMP" || exit 1
echo "Steam trust preflight: bundle ready"

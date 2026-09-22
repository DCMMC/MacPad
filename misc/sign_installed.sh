#!/bin/sh
# sign_installed.sh — Sign and trustcache Mach-O binaries installed by MacPorts / Homebrew.
#
# Run from the iOS shell (NOT inside the chroot) after installing software with `port` or `brew`.
# Trustcache entries are lost on every reboot; this script is safe to re-run at any time.
# Already-trusted CDHashes are detected and skipped without calling jbctl again.
#
# Usage:
#   sudo bash /var/jb/usr/macOS/bin/sign_installed.sh              # sign both MacPorts + Homebrew
#   sudo bash /var/jb/usr/macOS/bin/sign_installed.sh macports     # MacPorts only
#   sudo bash /var/jb/usr/macOS/bin/sign_installed.sh homebrew     # Homebrew only
#   sudo bash /var/jb/usr/macOS/bin/sign_installed.sh /some/path   # arbitrary directory

ENT=/var/jb/usr/macOS/bin/entitlements.plist
LDID=/var/jb/usr/bin/ldid
JBCTL=/var/jb/usr/bin/jbctl
PYTHON=/var/jb/usr/bin/python3
ENTITLEMENT_MERGER=/var/jb/usr/macOS/bin/merge_third_party_entitlements.py
ROOTFS=/var/mnt/rootfs
SIGN_ENT=$ENT
SIGN_PROFILE=system
APP_ENT=""

# ── snapshot current trustcache so we can skip already-trusted entries ────────

TC_CACHE=$(mktemp /tmp/tc_cache.XXXXXX)
# Dopamine's jbctl `list` subcommand silently returns an empty result on the
# target.  `info` is its authoritative read-only inventory.
"$JBCTL" trustcache info 2>/dev/null | tr '[:upper:]' '[:lower:]' > "$TC_CACHE"
printf 'Loaded %d existing trustcache entries.\n' "$(wc -l < "$TC_CACHE")"

# ── counters (written to a tmp file because subshells can't update parent vars) ──

COUNTS=$(mktemp /tmp/tc_counts.XXXXXX)
printf '0 0 0 0\n' > "$COUNTS"   # signed  already_trusted  skipped  no_cdhash

cleanup() {
    rm -f "$TC_CACHE" "$COUNTS"
    [ -n "$APP_ENT" ] && rm -f "$APP_ENT"
}
trap cleanup EXIT

inc_counter() {
    # inc_counter <field 1-4>
    read -r s a k n < "$COUNTS"
    case "$1" in
        1) s=$((s+1)) ;;
        2) a=$((a+1)) ;;
        3) k=$((k+1)) ;;
        4) n=$((n+1)) ;;
    esac
    printf '%d %d %d %d\n' "$s" "$a" "$k" "$n" > "$COUNTS"
}

# Return success only when every signed architecture currently present in the
# file is already in Dopamine's live trustcache.  This is a byte-level witness
# that the exact file has completed signing.  Preserve it verbatim: a second
# ldid pass is not reliably idempotent for very large universal frameworks
# (Steam CEF is the runtime witness), and can also invalidate Valve's package
# inventory after that inventory was synchronized.
current_file_is_trusted() {
    local f="$1" found=0 h
    for arch in arm64 arm64e x86_64; do
        h=$("$LDID" -arch "$arch" -h "$f" 2>/dev/null | \
            grep 'CDHash=' | cut -c8- | tr '[:upper:]' '[:lower:]')
        [ -n "$h" ] || continue
        found=1
        grep -qF "$h" "$TC_CACHE" 2>/dev/null || return 1
    done
    [ "$found" -eq 1 ]
}

executable_has_compat_profile() {
    local f="$1" ent
    ent=$(mktemp /tmp/macws_exec_entitlements.XXXXXX) || return 1
    : > "$ent"
    for arch in arm64 arm64e x86_64; do
        if "$LDID" -arch "$arch" -e "$f" > "$ent" 2>/dev/null &&
           [ -s "$ent" ]; then
            break
        fi
        : > "$ent"
    done
    # These are the exec-policy essentials demonstrated by Steam.  Also reject
    # platform-application, which previously caused duplicate-platform-main
    # panics in third-party processes.
    if grep -q '<key>com.apple.private.security.no-container</key>' "$ent" &&
       grep -q '<key>com.apple.private.security.no-sandbox</key>' "$ent" &&
       ! grep -q '<key>platform-application</key>' "$ent"; then
        rm -f "$ent"
        return 0
    fi
    rm -f "$ent"
    return 1
}

# Main executables need the MacWS compatibility profile; libraries must not
# carry executable entitlements.  Runtime evidence from Steam showed iPadOS
# rejecting Breakpad/CoreImage dylibs with "has entitlements but is not a main
# binary".  Read the Mach-O header (including fat containers) using the iOS
# Python already required by this installer.  MH_EXECUTE is filetype 2.
macho_contains_executable_slice() {
    "$PYTHON" - "$1" <<'PY'
import struct, sys

data = open(sys.argv[1], "rb").read(4096)
if len(data) < 16:
    raise SystemExit(1)
magic = data[:4]
if magic in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe"):
    endian = "<"
    offset = 0
elif magic in (b"\xfe\xed\xfa\xcf", b"\xfe\xed\xfa\xce"):
    endian = ">"
    offset = 0
elif magic in (b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"):
    endian = ">"
    count = struct.unpack_from(">I", data, 4)[0]
    width = 32 if magic == b"\xca\xfe\xba\xbf" else 20
    offsets = [struct.unpack_from(">Q" if width == 32 else ">I",
                                  data, 8 + i * width + 8)[0]
               for i in range(count)]
    with open(sys.argv[1], "rb") as stream:
        for candidate in offsets:
            stream.seek(candidate)
            header = stream.read(16)
            if len(header) >= 16 and struct.unpack_from("<I", header, 12)[0] == 2:
                raise SystemExit(0)
    raise SystemExit(1)
elif magic in (b"\xbe\xba\xfe\xca", b"\xbf\xba\xfe\xca"):
    endian = "<"
    count = struct.unpack_from("<I", data, 4)[0]
    width = 32 if magic == b"\xbf\xba\xfe\xca" else 20
    offsets = [struct.unpack_from("<Q" if width == 32 else "<I",
                                  data, 8 + i * width + 8)[0]
               for i in range(count)]
    with open(sys.argv[1], "rb") as stream:
        for candidate in offsets:
            stream.seek(candidate)
            header = stream.read(16)
            if len(header) >= 16 and struct.unpack_from(">I", header, 12)[0] == 2:
                raise SystemExit(0)
    raise SystemExit(1)
else:
    raise SystemExit(1)
raise SystemExit(0 if struct.unpack_from(endian + "I", data, offset + 12)[0] == 2 else 1)
PY
}
# ── core sign + trustcache function ──────────────────────────────────────────

# Steam validates gameoverlayrenderer*.dylib itself before creating the game
# process. Replacing Valve's embedded CMS signature with an ad-hoc CodeDirectory
# makes that validator fail and Steam maps the failure to AppError 49 before it
# calls NSWorkspace. These two vendor dylibs ship without entitlements, so the
# iOS kernel can admit their original bytes through the trustcache alone.
# Keep the exception deliberately narrow: other third-party dylibs may carry
# entitlements which iPadOS rejects on non-main images.
macho_has_embedded_cms_signature() {
    "$PYTHON" - "$1" <<'PY'
import struct
import sys

path = sys.argv[1]
with open(path, "rb") as stream:
    data = stream.read()

def slices(blob):
    magic = blob[:4]
    if magic in (b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"):
        count = struct.unpack_from(">I", blob, 4)[0]
        width = 32 if magic == b"\xca\xfe\xba\xbf" else 20
        for index in range(count):
            entry = 8 + index * width
            offset = struct.unpack_from(">Q" if width == 32 else ">I",
                                        blob, entry + 8)[0]
            size = struct.unpack_from(">Q" if width == 32 else ">I",
                                      blob, entry + (16 if width == 32 else 12))[0]
            yield offset, size
    else:
        yield 0, len(blob)

def has_cms(blob, base, size):
    magic = blob[base:base + 4]
    if magic == b"\xcf\xfa\xed\xfe":
        endian, header_size = "<", 32
    elif magic == b"\xfe\xed\xfa\xcf":
        endian, header_size = ">", 32
    elif magic == b"\xce\xfa\xed\xfe":
        endian, header_size = "<", 28
    elif magic == b"\xfe\xed\xfa\xce":
        endian, header_size = ">", 28
    else:
        return False
    ncmds = struct.unpack_from(endian + "I", blob, base + 16)[0]
    cursor = base + header_size
    for _ in range(ncmds):
        command, command_size = struct.unpack_from(endian + "II", blob, cursor)
        if command == 0x1d:  # LC_CODE_SIGNATURE
            dataoff, datasize = struct.unpack_from(endian + "II", blob, cursor + 8)
            signature = base + dataoff
            if signature + datasize > base + size or datasize < 12:
                return False
            super_magic, _, count = struct.unpack_from(">III", blob, signature)
            if super_magic != 0xfade0cc0:
                return False
            for index in range(count):
                slot, offset = struct.unpack_from(">II", blob,
                                                  signature + 12 + index * 8)
                if slot != 0x10000:  # CSSLOT_SIGNATURE / CMS wrapper
                    continue
                cms_magic, cms_size = struct.unpack_from(">II", blob,
                                                         signature + offset)
                return cms_magic == 0xfade0b01 and cms_size > 8
            return False
        if command_size < 8:
            return False
        cursor += command_size
    return False

found = False
for base, size in slices(data):
    found = True
    if not has_cms(data, base, size):
        raise SystemExit(1)
raise SystemExit(0 if found else 1)
PY
}

is_vendor_signed_steam_overlay() {
    local f="$1"
    case "$f" in
        */Steam.AppBundle/Steam/Contents/MacOS/gameoverlayrenderer.dylib|\
        */Steam.AppBundle/Steam/Contents/MacOS/gameoverlayrenderer32.dylib)
            macho_has_embedded_cms_signature "$f"
            ;;
        *) return 1 ;;
    esac
}

sign_one() {
    local f="$1"
    local effective_ent="$SIGN_ENT"
    local current_ent="" merged_ent=""
    local sign_result=0
    [ -f "$f" ] || return 0

    local is_executable=1
    if [ "$SIGN_PROFILE" = third-party-nonplatform ]; then
        if macho_contains_executable_slice "$f"; then
            is_executable=1
        else
            is_executable=0
        fi
    fi

    if [ "$SIGN_PROFILE" = third-party-nonplatform ] &&
       [ "$is_executable" -eq 0 ] &&
       is_vendor_signed_steam_overlay "$f"; then
        local preserved_added=0 preserved_existing=0 preserved_hash
        for arch in arm64 arm64e x86_64; do
            preserved_hash=$("$LDID" -arch "$arch" -h "$f" 2>/dev/null | \
                grep 'CDHash=' | cut -c8- | tr '[:upper:]' '[:lower:]')
            [ -n "$preserved_hash" ] || continue
            if grep -qF "$preserved_hash" "$TC_CACHE" 2>/dev/null; then
                preserved_existing=$((preserved_existing+1))
            else
                "$JBCTL" trustcache add "$preserved_hash" 2>/dev/null
                printf '%s\n' "$preserved_hash" >> "$TC_CACHE"
                preserved_added=$((preserved_added+1))
            fi
        done
        if [ "$preserved_added" -gt 0 ]; then
            inc_counter 1
            printf '  +trust-vendor  %s\n' "$(basename "$f")"
        elif [ "$preserved_existing" -gt 0 ]; then
            inc_counter 2
            printf '  keep-vendor    %s\n' "$(basename "$f")"
        else
            inc_counter 4
            printf '  noarch         %s\n' "$f"
        fi
        return 0
    fi

    if current_file_is_trusted "$f" &&
       { [ "$SIGN_PROFILE" != third-party-nonplatform ] ||
         [ "$is_executable" -eq 0 ] ||
         executable_has_compat_profile "$f"; }; then
        inc_counter 2
        printf '  keep    %s\n' "$(basename "$f")"
        return 0
    fi

    if [ "$SIGN_PROFILE" = third-party-nonplatform ] &&
       [ "$is_executable" -eq 1 ]; then
        current_ent=$(mktemp /tmp/macws_current_entitlements.XXXXXX) || return 1
        merged_ent=$(mktemp /tmp/macws_merged_entitlements.XXXXXX) || {
            rm -f "$current_ent"
            return 1
        }
        : > "$current_ent"
        for entitlement_arch in arm64 arm64e x86_64; do
            if "$LDID" -arch "$entitlement_arch" -e "$f" \
                    > "$current_ent" 2>/dev/null &&
               [ -s "$current_ent" ]; then
                break
            fi
            : > "$current_ent"
        done
        if [ ! -f "$ENTITLEMENT_MERGER" ] ||
           ! "$PYTHON" "$ENTITLEMENT_MERGER" \
                "$SIGN_ENT" "$current_ent" "$merged_ent"
        then
            rm -f "$current_ent" "$merged_ent"
            return 1
        fi
        effective_ent=$merged_ent
    fi

    # `-M` cannot remove an entitlement which polluted an earlier signature.
    # The non-platform profile above is already a complete vendor+project merge,
    # so sign it directly. Other targets retain the historical merge behavior.
    if [ "$SIGN_PROFILE" = third-party-nonplatform ] &&
       [ "$is_executable" -eq 1 ]; then
        "$LDID" -S"$effective_ent" "$f" 2>/dev/null
        sign_result=$?
    elif [ "$SIGN_PROFILE" = third-party-nonplatform ]; then
        # Framework/dylib/bundle slices receive an empty ad-hoc signature.
        # Entitlements on non-main images violate iPadOS exec policy.
        "$LDID" -S "$f" 2>/dev/null
        sign_result=$?
    else
        "$LDID" -S"$effective_ent" -M "$f" 2>/dev/null
        sign_result=$?
    fi
    rm -f "$current_ent" "$merged_ent"
    if [ "$sign_result" -ne 0 ]; then
        inc_counter 3   # skipped (non-Mach-O)
        return 0
    fi

    local added=0 already=0
    for arch in arm64 arm64e x86_64; do
        local h
        h=$("$LDID" -arch "$arch" -h "$f" 2>/dev/null | grep 'CDHash=' | cut -c8- | tr '[:upper:]' '[:lower:]')
        [ -n "$h" ] || continue

        if grep -qF "$h" "$TC_CACHE" 2>/dev/null; then
            already=$((already+1))
        else
            "$JBCTL" trustcache add "$h" 2>/dev/null
            # Add to in-memory cache so later slices of the same file aren't re-added.
            printf '%s\n' "$h" >> "$TC_CACHE"
            added=$((added+1))
        fi
    done

    if [ "$added" -gt 0 ]; then
        inc_counter 1   # signed + newly trustcached
        printf '  +trust  %s\n' "$(basename "$f")"
    elif [ "$already" -gt 0 ]; then
        inc_counter 2   # already trusted, signing refreshed but no jbctl call needed
        printf '  ok      %s\n' "$(basename "$f")"
    else
        inc_counter 4   # Mach-O but no CDHash (unusual)
        printf '  noarch  %s\n' "$f"
    fi
}

sign_tree() {
    local dir="$1"
    [ -d "$dir" ] || { printf 'skip (not found): %s\n' "$dir"; return 0; }
    printf '\n==> %s\n' "$dir"
    # File mode is not a reliable code predicate in third-party bundles:
    # Office marks thousands of inert resources executable, while Valheim ships
    # its 57 MB PlayFab Mach-O bundle as mode 0644. Probe every regular file's
    # four-byte Mach-O/fat magic in one Python process so ldid only sees code.
    # Office 16.91 Word is the large-tree witness: executable-bit filtering sent
    # 3,640 resources through ldid and still would not catch Valheim's plugin.
    "$PYTHON" -c '
import os, stat, sys

magics = {
    b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",
    b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca",
}
for base, _, names in os.walk(sys.argv[1]):
    for name in names:
        path = os.path.join(base, name)
        try:
            mode = os.stat(path, follow_symlinks=False).st_mode
            if not stat.S_ISREG(mode):
                continue
            with open(path, "rb") as stream:
                if stream.read(4) not in magics:
                    continue
            os.write(1, os.fsencode(path) + b"\0")
        except OSError:
            pass
' "$dir" | while IFS= read -r -d '' f; do
        sign_one "$f"
    done
}

# Steam's bootstrapper owns a second integrity database in addition to the
# Mach-O code signature. Each line in package/*.installed stores
# relative-path,size,mtime,CRC32. Most executable images need the MacWS
# compatibility signature before the iOS kernel will admit them into the macOS
# chroot. The overlay dylibs above retain Valve's CMS signature and enter via
# the trustcache without being rewritten. Re-signing every other binary changes
# all four recorded inventory values; without refreshing them, Steam can restore
# Valve's original executable and create an updater -> AMFI -> updater loop.
#
# Update only records that already exist and whose current target is a Mach-O.
# Non-code resources, package manifests, versions, and download hashes remain
# untouched, so a real upstream Steam update is still discovered normally.
refresh_steam_installed_records() {
    local app_root="$1"
    local package_dir="$app_root/Contents/MacOS/package"
    [ -d "$package_dir" ] || return 0
    set -- "$package_dir"/*.installed
    [ -f "$1" ] || return 0
    printf '\n==> refreshing Steam signed-code inventory\n'
    "$PYTHON" - "$app_root/Contents/MacOS" "$@" <<'PY'
import os
import re
import stat
import sys
import zlib
import hashlib

root = os.path.realpath(sys.argv[1])
magics = {
    b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",
    b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca",
}
record = re.compile(r"^(.*),(-?\d+);(-?\d+);(\d+)(\r?\n)?$")

for inventory in sys.argv[2:]:
    with open(inventory, "r", encoding="utf-8") as stream:
        lines = stream.readlines()
    output = []
    changed = 0
    for line in lines:
        match = record.match(line)
        if not match or int(match.group(2)) < 0:
            output.append(line)
            continue
        relative = match.group(1)
        path = os.path.realpath(os.path.join(root, relative))
        # A malicious/corrupt inventory must not make the privileged installer
        # inspect or rewrite metadata for a path outside Steam's MacOS root.
        if path != root and not path.startswith(root + os.sep):
            output.append(line)
            continue
        try:
            status = os.stat(path)
            if not stat.S_ISREG(status.st_mode):
                output.append(line)
                continue
            with open(path, "rb") as stream:
                if stream.read(4) not in magics:
                    output.append(line)
                    continue
            checksum = 0
            with open(path, "rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    checksum = zlib.crc32(chunk, checksum)
        except OSError:
            output.append(line)
            continue
        newline = (
            f"{relative},{status.st_size};{int(status.st_mtime)};"
            f"{checksum & 0xffffffff}{match.group(5) or ''}"
        )
        changed += newline != line
        output.append(newline)

    # The footer authenticates every byte before its own SHA1 line. This is
    # format evidence from Valve's untouched inventory: hashing that prefix
    # exactly reproduces the shipped footer. Keep the inventory self-consistent
    # after replacing only its signed-code records.
    for index, line in enumerate(output):
        if not line.startswith("SHA1="):
            continue
        line_ending = "\r\n" if line.endswith("\r\n") else (
            "\n" if line.endswith("\n") else ""
        )
        digest = hashlib.sha1(
            "".join(output[:index]).encode("utf-8")
        ).hexdigest().upper()
        replacement = f"SHA1={digest}{line_ending}"
        changed += replacement != line
        output[index] = replacement
        break

    old_status = os.stat(inventory)
    temporary = inventory + ".macws-new"
    with open(temporary, "w", encoding="utf-8", newline="") as stream:
        stream.writelines(output)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temporary, stat.S_IMODE(old_status.st_mode))
    try:
        os.chown(temporary, old_status.st_uid, old_status.st_gid)
    except PermissionError:
        pass
    os.replace(temporary, inventory)
    print(f"  inventory {os.path.basename(inventory)} code-records={changed}")
PY
}

# A third-party application is not an iOS platform process.  Giving its main
# executable `platform-application` makes PMAP_CS treat it as a platform main
# binary. Runtime-confirmed with CleanMyMac X 4.15.8: the iPadOS 16.3.1 panic
# named CleanMyMac as the panicked task and reported "attempted associating
# duplicate platform main binary without matching address space layout".
#
# Retain the project's other compatibility permissions and `-M` vendor merge,
# but remove only this incorrect identity bit for /Applications/*.app trees.
prepare_third_party_app_profile() {
    APP_ENT=$(mktemp /tmp/macws_app_entitlements.XXXXXX) || return 1
    "$PYTHON" - "$ENT" "$APP_ENT" <<'PY'
import plistlib, sys

with open(sys.argv[1], "rb") as stream:
    entitlements = plistlib.load(stream)
entitlements.pop("platform-application", None)
with open(sys.argv[2], "wb") as stream:
    plistlib.dump(entitlements, stream, fmt=plistlib.FMT_XML, sort_keys=True)
PY
    SIGN_ENT=$APP_ENT
    SIGN_PROFILE=third-party-nonplatform
}

# ── target selection ──────────────────────────────────────────────────────────

TARGET="${1:-both}"

case "$TARGET" in
    macports|mp)   DO_MACPORTS=1; DO_HOMEBREW=0 ;;
    homebrew|brew) DO_MACPORTS=0; DO_HOMEBREW=1 ;;
    both|"")       DO_MACPORTS=1; DO_HOMEBREW=1 ;;
    /*)
        # Steam's native updater deliberately keeps its live application at
        # ~/Library/Application Support/Steam/Steam.AppBundle/Steam rather
        # than below /Applications. Detect application bundles by structure,
        # not by one installation prefix, so every third-party GUI app keeps
        # the non-platform signing profile. System applications remain on the
        # historical system profile when explicitly targeted.
        case "$TARGET" in
            "$ROOTFS"/System/*) ;;
            *)
                if [ -f "$TARGET/Contents/Info.plist" ]; then
                    prepare_third_party_app_profile || exit 1
                fi
                ;;
        esac
        printf '=== Signing custom path: %s (profile=%s) ===\n' \
               "$TARGET" "$SIGN_PROFILE"
        sign_tree "$TARGET"
        # Structural detection keeps this valid for Steam's live bundle below
        # Application Support and for a conventional /Applications symlink.
        if [ -f "$TARGET/Contents/MacOS/package/steam_client_osx.installed" ] ||
           [ -f "$TARGET/Contents/MacOS/package/steam_client_signed_osx.installed" ] ||
           [ -f "$TARGET/Contents/MacOS/package/steam_client_signed-2_osx.installed" ]; then
            refresh_steam_installed_records "$TARGET" || exit 1
        fi
        read -r s a k n < "$COUNTS"
        printf '\nDone. newly-trusted=%d  already-trusted=%d  skipped(non-Mach-O)=%d  no-cdhash=%d\n' \
               "$s" "$a" "$k" "$n"
        exit 0 ;;
    *)
        printf 'Usage: %s [macports|homebrew|/absolute/path]\n' "$0" >&2
        exit 1 ;;
esac

printf '=== sign_installed.sh  rootfs=%s ===\n' "$ROOTFS"

# ── MacPorts (/opt/local) ─────────────────────────────────────────────────────

if [ "$DO_MACPORTS" -eq 1 ]; then
    printf '\n--- MacPorts ---\n'

    sign_tree "$ROOTFS/opt/local/libexec/macports/bin"
    sign_tree "$ROOTFS/opt/local/bin"
    sign_tree "$ROOTFS/opt/local/sbin"
    sign_tree "$ROOTFS/opt/local/lib"
    sign_tree "$ROOTFS/opt/local/libexec"

    # Python framework — all versions installed via MacPorts
    for pyver in "$ROOTFS/opt/local/Library/Frameworks/Python.framework/Versions"/*/; do
        [ -d "$pyver" ] || continue
        printf '\n--- Python framework: %s ---\n' "$(basename "$pyver")"
        sign_tree "$pyver"
    done
fi

# ── Homebrew (/opt/homebrew) ──────────────────────────────────────────────────

if [ "$DO_HOMEBREW" -eq 1 ]; then
    printf '\n--- Homebrew ---\n'

    sign_tree "$ROOTFS/opt/homebrew/bin"
    sign_tree "$ROOTFS/opt/homebrew/sbin"
    sign_tree "$ROOTFS/opt/homebrew/lib"
    sign_tree "$ROOTFS/opt/homebrew/libexec"
    sign_tree "$ROOTFS/opt/homebrew/Cellar"

    # Homebrew vendor Ruby (used by the brew CLI itself)
    PRUBY="$ROOTFS/opt/homebrew/Library/Homebrew/vendor/portable-ruby"
    if [ -d "$PRUBY" ]; then
        printf '\n--- Homebrew portable Ruby ---\n'
        sign_tree "$PRUBY"
    fi
fi

# ── summary ───────────────────────────────────────────────────────────────────

read -r s a k n < "$COUNTS"
printf '\n=== Done ===\n'
printf 'newly-trusted=%d  already-trusted(skipped jbctl)=%d  non-Mach-O=%d  no-cdhash=%d\n' \
       "$s" "$a" "$k" "$n"
printf '\nTrustcache entries added for this session.\n'
printf 'Run postinst.sh on next reboot to re-register (trustcache is not persistent).\n'

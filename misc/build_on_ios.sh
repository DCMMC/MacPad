#!/bin/bash
# Build and install MacWSBootingGuide on-device (iOS shell with Theos)
# Usage: bash misc/build_on_ios.sh [--fast|--fast-force|--resume|--package-only]
#
# This is the on-device equivalent of misc/build.sh (which builds from macOS).
# All files (scripts, libmachook.dylib) are installed via the .deb package.

set -e

# Non-interactive SSH does not inherit Procursus' login PATH. Theos invokes
# both Procursus tools and Apple's /usr/sbin/sysctl by name, so make the build
# environment deterministic before running any guardrail or make phase.
export PATH=/var/jb/usr/bin:/var/jb/usr/sbin:/usr/local/bin:/usr/bin:/usr/sbin:/bin:/sbin

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

# Incremental mode: skip `make clean` and only rebuild changed files.
# Triggered by env FAST=1 or argument --fast. The first build of the day
# should still be a full one; subsequent edits to libmachook/*.m can use
# the fast path to cut build time from ~20s to a few seconds.
FAST=${FAST:-0}
FAST_FORCE=${FAST_FORCE:-0}
SKIP_CLEAN=${SKIP_CLEAN:-0}
PACKAGE_ONLY=0
for arg in "$@"; do [ "$arg" = "--fast" ] && FAST=1; done
for arg in "$@"; do [ "$arg" = "--fast-force" ] && FAST=1 && FAST_FORCE=1; done
for arg in "$@"; do [ "$arg" = "--resume" ] && SKIP_CLEAN=1; done
for arg in "$@"; do [ "$arg" = "--package-only" ] && PACKAGE_ONLY=1; done
if [ "$PACKAGE_ONLY" = 1 ]; then FAST=0; fi

# MacWSWindowing is injected into arm64e SpringBoard. The iPad's lld emits
# unauthenticated plain binds for __CFConstantStringClassReference; merely
# enabling LC_DYLD_CHAINED_FIXUPS does not repair them. A full on-device build
# must therefore package the Apple-ld64 artifact produced on the Mac and cached
# by misc/deploy_macwswindowing.sh. Verify its whole-file hash before allowing
# dpkg anywhere near SpringBoard.
WINDOWING_CROSS_DIR=/var/jb/var/mobile/macws-cross-build
WINDOWING_CROSS_BINARY="$WINDOWING_CROSS_DIR/MacWSWindowing.dylib"
WINDOWING_CROSS_SHA="$WINDOWING_CROSS_DIR/MacWSWindowing.sha256"
WINDOWING_CROSS_MANIFEST="$WINDOWING_CROSS_DIR/MacWSWindowing.build.json"

# Guardrail: FAST only copies libmachook.{arm64,arm64e}.dylib to the rootfs.
# Any other build artefact (CydiaSubstrate tweak under TweakInject, iOS-side
# binaries like launchdchrootexec / autosignd / MTLSimDriverHost, scripts in
# layout/) gets shipped exclusively via `make package` + `dpkg -i`, which the
# FAST path skips. If we silently do FAST while one of those sources is dirty,
# the device runs stale code (most painful case: MTLCompilerBypassOSCheck/Tweak.x
# changes never reach /var/jb/usr/lib/TweakInject/, so MTLCompilerService keeps
# failing the OS check). Detect that and force FAST=0 with a warning.
if [ "$FAST" = "1" ] && [ "$FAST_FORCE" != "1" ]; then
    MARKER=/var/jb/usr/lib/TweakInject/MTLCompilerBypassOSCheck.dylib
    if [ -f "$MARKER" ]; then
        STALE=$(find MTLCompilerBypassOSCheck MacWSWindowing \
                     MacWSCatalystLaunch MTLSimDriverHost \
                     launchdchrootexec autosignd macwsallocd macwshostd \
                     macwskeychaind \
                     macwsthermal macwslocationd macwsaudiooutd \
                     mountdevfs ViewBridgeChrootProxy \
                     HIServicesChrootProxy OpenAndSavePanelChrootProxy \
                     QuickLookUIServiceChrootProxy \
                     DockHelperChrootProxy \
                     ExtensionKitChrootProxy \
                     SettingsExtensionChrootProxy \
                     FileCoordinationChrootProxy \
                     FileCoordinationMachBridge \
                     GeodChrootProxy \
                     WriteConfigChrootProxy \
                     LocationdChrootProxy \
                     mtl_keepalive \
                     MacWSHost MacWSCatalystLauncher \
                     SettingsExtensionMetadata misc/PingMTLCompilerService \
                     macwsinputd macwsdisplayd macwsinteropd \
                     macwsworkspacectl macwsneofetch launchservicesd \
                     Makefile control layout include config \
                     -type f -newer "$MARKER" 2>/dev/null \
                | grep -v '/\._' | head -3)
        if [ -z "$STALE" ] && [ -f misc/iosclear_ref.m ] &&
           [ misc/iosclear_ref.m -nt "$MARKER" ]; then
            STALE=misc/iosclear_ref.m
        fi
        if [ -n "$STALE" ]; then
            echo "==> FAST guardrail tripped: source files newer than last dpkg-installed tweak:"
            echo "$STALE" | sed 's/^/      /'
            echo "==> Forcing full build (FAST only ships libmachook; deb-installed bits would stay stale)"
            FAST=0
        fi
    else
        echo "==> FAST guardrail: no installed tweak found at $MARKER — forcing full build"
        FAST=0
    fi
fi
if [ "$FAST" = "1" ] && [ "$FAST_FORCE" = "1" ]; then
    echo "==> FAST_FORCE: explicitly shipping libmachook only; non-libmachook changes are not packaged"
fi

# The FAST guardrail above may promote an incremental request to a full package
# build. Perform this check only after that decision so no promoted build can
# bypass the SpringBoard linker invariant.
if [ "$FAST" != "1" ]; then
    if [ ! -s "$WINDOWING_CROSS_BINARY" ] || [ ! -s "$WINDOWING_CROSS_SHA" ]; then
        echo "Error: validated Apple-ld64 MacWSWindowing artifact is missing." >&2
        echo "Run misc/deploy_macwswindowing.sh from the Mac before a full on-device build." >&2
        exit 1
    fi
    windowing_expected_sha=$(sed -n '1s/[[:space:]].*//p' "$WINDOWING_CROSS_SHA")
    windowing_actual_sha=$(sha256sum "$WINDOWING_CROSS_BINARY" | sed 's/[[:space:]].*//')
    if [ -z "$windowing_expected_sha" ] || \
       [ "$windowing_actual_sha" != "$windowing_expected_sha" ]; then
        echo "Error: cached MacWSWindowing hash mismatch; refusing unsafe SpringBoard package." >&2
        exit 1
    fi
    # A self-consistent old binary/SHA pair is not evidence that it implements
    # this checkout's Host protocol. Include transitive shared headers so a
    # source migration cannot package the Sep-15 tweak with a Sep-19 Host.
    python3 "$SCRIPT_DIR/macws_artifact_contract.py" verify \
        --root "$PROJECT_DIR" --binary "$WINDOWING_CROSS_BINARY" \
        --manifest "$WINDOWING_CROSS_MANIFEST"
    echo "==> SpringBoard linker invariant: validated cached Apple-ld64 MacWSWindowing"
fi

if [ "$FAST" != "1" ] && [ "$SKIP_CLEAN" != "1" ]; then
    echo "==> Cleaning previous build..."
    make MACWS_WINDOWING_CROSS_PREBUILT="$WINDOWING_CROSS_BINARY" \
        clean 2>/dev/null || true
elif [ "$FAST" != "1" ]; then
    echo "==> RESUME mode: preserving successful objects from the interrupted full build"
else
    echo "==> FAST mode: skipping make clean (incremental build)"
    # Source synchronization (scp, git reset, restored worktrees) can install
    # correct content with a timestamp older than a cached Theos object. GNU
    # make then silently relinks that stale object. This previously shipped a
    # pre-12-function QuartzCore compatibility hash and made a cold
    # WindowServer reject the exact current metallib. FAST is already scoped
    # to libmachook, so make every translation unit newer than its object and
    # rebuild this one library deterministically; packaging remains skipped.
    find libmachook \
        \( -path 'libmachook/.theos' -o -path 'libmachook/.theos/*' \) \
            -prune -o -type f \
        \( -name '*.m' -o -name '*.mm' -o -name '*.c' -o -name '*.x' \) \
        -exec touch {} +
    echo "==> FAST invariant: refreshed all libmachook source mtimes"
fi

if [ "$FAST" != "1" ]; then
    # The controller and MacWSMetalView are one Objective-C ABI even though
    # Theos compiles them as independent translation units.  Source sync tools
    # preserve the Mac's old mtimes; an on-device object can therefore be newer
    # than newly copied source with different contents.  Runtime-confirmed on
    # 2026-08-18: main.m called -hasFinalCompositeFrame while the packaged
    # MacWSMetalView method table came from an older object and did not contain
    # that selector, crashing macPad in -applyStatus: as soon as the first
    # IOSurface arrived.  A normal clean build happened to repair it, but
    # --resume could recreate the invalid mixed binary.  Refresh the complete
    # Host ABI on every package build; this is deliberately narrower than
    # throwing away successful objects for the much larger libmachook target.
    find MacWSHost -type f \
        \( -name '*.m' -o -name '*.mm' -o -name '*.c' -o -name '*.h' \) \
        -exec touch {} +
    echo "==> Host ABI invariant: refreshed all macPad source/header mtimes"
fi

echo "==> Building..."
# Pass LIBMACHOOK_ON_DEVICE_BUILD=1 so libmachook/Makefile adds, for the
# on-device lld only:
#   * -DLIBMACHOOK_ON_DEVICE_BUILD=1  (lets Metal_hooks.x include arm64e ObjC)
#   * -Wl,-fixup_chains               (LC_DYLD_CHAINED_FIXUPS for arm64e)
# Why -fixup_chains is required on-device: on-device lld emits the arm64e
# __interpose tuples (and ObjC class_t) as authenticated pointers under the
# classic LC_DYLD_INFO_ONLY format, which macOS arm64e dyld mis-processes.  The
# DYLD_INTERPOSE of os_variant_has_internal_diagnostics() then never registers,
# so the real implementation runs and traps (brk 1) in libSystem_initializer
# before any hook loads.  -fixup_chains switches to the chained-fixup format
# that macOS dyld re-signs/re-binds correctly at load.  libmachook/Makefile also
# sets TARGET=iphone:clang:latest:14.0 so macOS dyld accepts the load commands
# (Theos would otherwise default to iOS 9.0 / LC_VERSION_MIN_IPHONEOS, rejected
# by macOS dyld).  The macOS cross-compile (misc/build.sh) uses ld64 and must
# NOT get the flag.
#
# Keep symbols for the project's LLDB workflow, but do not let Theos infer
# -O0 from STRIP=0.  FINALPACKAGE does not override that rule: common.mk picks
# OPTFLAG=-O0 whenever stripping is disabled.  Pass -O2 explicitly so both a
# full build and every later FAST incremental object use production codegen.
PACKAGE_READY=0
if [ "$FAST" = "1" ]; then
    # The aggregate Makefile walks every subproject even when FAST will ship
    # only libmachook.  Build the library subproject directly while preserving
    # the root project/build directories that the aggregate invocation uses;
    # this both keeps `interpose.h` on the project include path and reuses the
    # existing root .theos object cache.
    THEOS_PROJECT_DIR="$PROJECT_DIR" THEOS_BUILD_DIR="$PROJECT_DIR" \
        make -C libmachook \
        FINALPACKAGE=1 STRIP=0 OPTFLAG=-O2 THEOS_PACKAGE_SCHEME=rootless \
        GO_EASY_ON_ME=1 LIBMACHOOK_ON_DEVICE_BUILD=1
else
    # Theos's package target already depends on compilation and staging. A
    # separate aggregate `make` immediately before it caused Logos targets
    # (notably Metal_hooks.x) to preprocess and compile twice, adding roughly
    # two minutes of CPU heat without producing a different object. Build,
    # stage and package in one dependency graph, then verify the resulting
    # binary below before dpkg sees it.
    echo "==> Building, staging, and packaging in one pass..."
    make FINALPACKAGE=1 STRIP=0 OPTFLAG=-O2 \
        THEOS_PACKAGE_SCHEME=rootless GO_EASY_ON_ME=1 \
        LIBMACHOOK_ON_DEVICE_BUILD=1 \
        MACWS_WINDOWING_CROSS_PREBUILT="$WINDOWING_CROSS_BINARY" package
    PACKAGE_READY=1
fi

if [ "$FAST" != "1" ]; then
    # Do not let packaging install another half-new/half-stale Host.  These
    # selectors cross source-file boundaries and are exercised only after the
    # display service publishes a frame, so link success alone is insufficient.
    HOST_BINARY=$(find .theos/obj -type f \
        -path '*/MacWSHost.app/MacWSHost' | head -1)
    if [ -z "$HOST_BINARY" ] || [ ! -x "$HOST_BINARY" ]; then
        echo "Error: built macPad binary not found for Objective-C ABI verification" >&2
        exit 1
    fi
    for host_method in \
        '-[MacWSMetalView hasDirectSurfaceFrame]' \
        '-[MacWSMetalView hasFinalCompositeFrame]' \
        '-[MacWSMetalView configureStreamMode:windowID:]' \
        '-[MacWSViewController repairDesktopAction]' \
        '-[MacWSViewController applyStatus:]'; do
        if ! nm -nm "$HOST_BINARY" 2>/dev/null | grep -Fq -- "$host_method"; then
            echo "Error: macPad Objective-C ABI contract missing $host_method" >&2
            exit 1
        fi
    done
    if ! grep -Fqa -- 'repair-desktop' "$HOST_BINARY"; then
        echo "Error: macPad Control Center is missing the repair-desktop operation" >&2
        exit 1
    fi
    echo "==> Host ABI invariant: packaged selector contract verified"
fi

if [ "$FAST" = "1" ]; then
    BUILT=$(find .theos/obj -name libmachook.dylib | head -1)
    if [ -z "$BUILT" ]; then
        echo "FAST: built libmachook not found in .theos/obj — falling back to full"
        FAST=0
    else
        echo "==> FAST: copying built libmachook to /var/jb/usr/macOS/lib/"
        # rm before cp so target gets a FRESH INODE — avoids the stale-codesign-cache
        # AMFI Invalid Page bug (see [[ondevice-arm64e-libmachook-invalidpage-regression]]).
        sudo rm -f /var/jb/usr/macOS/lib/libmachook.dylib
        sudo cp "$BUILT" /var/jb/usr/macOS/lib/libmachook.dylib
    fi
fi

if [ "$FAST" != "1" ]; then
if [ "$PACKAGE_READY" != "1" ]; then
    # Only the FAST missing-object fallback reaches this branch.
    echo "==> Packaging fallback..."
    make FINALPACKAGE=1 STRIP=0 OPTFLAG=-O2 \
        THEOS_PACKAGE_SCHEME=rootless GO_EASY_ON_ME=1 \
        LIBMACHOOK_ON_DEVICE_BUILD=1 \
        MACWS_WINDOWING_CROSS_PREBUILT="$WINDOWING_CROSS_BINARY" package
fi

# Find the built .deb
DEB=$(ls -t packages/*.deb 2>/dev/null | head -1)
if [ -z "$DEB" ]; then
    echo "Error: No .deb package found in packages/"
    exit 1
fi

# Verify the archive dpkg will actually install, not only .theos/obj. Missing
# audio jobs and stale staged Host/tweak files must stop the build transaction.
python3 "$SCRIPT_DIR/macws_artifact_contract.py" verify-package \
    --root "$PROJECT_DIR" --binary "$WINDOWING_CROSS_BINARY" \
    --manifest "$WINDOWING_CROSS_MANIFEST" \
    --staging "$PROJECT_DIR/.theos/_" --package "$DEB"

if [ "$PACKAGE_ONLY" = 1 ]; then
    echo "==> Verified package candidate: $DEB"
    echo '==> Package-only build complete; nothing was installed or restarted.'
    exit 0
fi

echo "==> Installing $DEB..."
# sudo normally drops caller environment. Pass the explicit no-respring
# request to the package maintainer script so an on-device verification build
# never restarts SpringBoard behind the operator's back.
sudo env MACWS_POSTINST_NO_RESPRING="${MACWS_POSTINST_NO_RESPRING:-0}" \
    dpkg -i "$DEB"
fi  # end !FAST

if [ "$FAST" != "1" ]; then
    # dpkg has already run layout/DEBIAN/postinst.  That transaction patches
    # LC_BUILD_VERSION, splits/signs/trustcaches both libmachook slices and
    # synchronizes the runtime into the mounted rootfs.  Repeating postinst
    # here used to scan every installed application (including thousands of
    # Office proofing-tool Mach-Os) a second time, add minutes of CPU heat, and
    # needlessly change libmachook's just-trusted signatures again.
    for installed_slice in \
        /var/jb/usr/macOS/lib/libmachook.dylib \
        /var/jb/usr/macOS/lib/libmachook_arm64.dylib \
        /var/mnt/rootfs/usr/local/lib/libmachook.dylib \
        /var/mnt/rootfs/usr/local/lib/libmachook_arm64.dylib; do
        [ -s "$installed_slice" ] || {
            echo "Error: package postinst did not install $installed_slice" >&2
            exit 1
        }
    done
    echo "==> Full package postinst completed during dpkg; skipping duplicate scan"
    echo "==> Done! Package installed successfully."
    exit 0
fi

# Set the macOS build version on the FAT libmachook (both slices; equivalent to
# vtool) — without this macOS dyld rejects the library as an iOS binary.
echo "==> Setting macOS build version on libmachook.dylib (fat)..."
sudo python3 "$SCRIPT_DIR/set_macos_version.py" /var/jb/usr/macOS/lib/libmachook.dylib

# Split the fat libmachook into two THIN dylibs and ship BOTH.
#
# On this device's dyld (iOS 16.x) a *fat* DYLD_INSERT_LIBRARIES dylib fails to
# load into a chrooted macOS process — DYLD_PRINT_SEARCHING reports
#   "fat file, but missing compatible architecture (have 'arm64,arm64e', need '')"
# so the insert is silently dropped and the real
# os_variant_has_internal_diagnostics() traps (brk 1) in libSystem_initializer.
# A *thin* slice loads fine, so we install both slices. launchdchrootexec and
# exec_hooks inspect the target Mach-O and insert exactly one: arm64e for bash,
# Terminal, git, python3; arm64 for WindowServer and MacPorts ARM64/ALL tools.
# Runtime evidence (WindowServer-2026-07-22-234833.ips) disproved the previous
# claim that dyld silently skips the other subtype: it loaded BOTH, duplicated
# constructors/stateful hooks, and started two VNC capture threads.
LIB=/var/jb/usr/macOS/lib/libmachook.dylib
LIB_ARM64=/var/jb/usr/macOS/lib/libmachook_arm64.dylib
echo "==> Splitting fat libmachook into thin arm64e + thin arm64..."
if lipo -info "$LIB" 2>&1 | grep -q 'Architectures in the fat file'; then
    sudo cp "$LIB" "/tmp/libmachook_fat.$$.dylib"
    sudo lipo "/tmp/libmachook_fat.$$.dylib" -thin arm64e -output "$LIB"
    sudo lipo "/tmp/libmachook_fat.$$.dylib" -thin arm64  -output "$LIB_ARM64"
    sudo rm -f "/tmp/libmachook_fat.$$.dylib"
else
    echo "==> Package postinst already installed thin slices; keeping them."
    [ -f "$LIB_ARM64" ] || {
        echo "Error: package postinst left no arm64 libmachook slice" >&2
        exit 1
    }
fi

# Re-sign both thin dylibs (signature was invalidated by set_macos_version + split).
#
# CRITICAL: each thin slice must be signed TWICE.  A single `ldid -S` on a slice
# fresh out of `lipo -thin` produces a CodeDirectory whose page hashes do NOT
# match the final on-disk file — ldid hashes the layout before it finishes
# growing __LINKEDIT / LC_CODE_SIGNATURE for the just-split slice.  The cdhash
# looks fine and trustcaches OK (postinst reads it back without complaint), but
# AMFI rejects every mmap of the dylib with "Invalid Page" (SIGKILL CODESIGNING).
# A bad slice SIGKILLs every chrooted process of that subtype.
# Signing a second time settles the layout and yields page hashes that match the
# bytes — verified: 2 signs -> chroot smoke test passes; 1 sign -> Killed: 9.
# (`ldid -S` is not idempotent here, so the cdhash drifts between signs; that is
# harmless because postinst re-reads the final cdhash before trustcaching it.)
echo "==> Re-signing both thin dylibs (twice each: works around ldid+lipo first-sign Invalid-Page bug)..."
sudo ldid -S "$LIB";       sudo ldid -S "$LIB"
sudo ldid -S "$LIB_ARM64"; sudo ldid -S "$LIB_ARM64"

echo "==> Running postinst (copy dylibs to rootfs, update trustcache)..."
if [ "$FAST" = "1" ]; then
    # Fast postinst — disable set -e for this block (sign_and_trust may have
    # non-zero exits we can tolerate).
    set +e
    ENT="/var/jb/usr/macOS/bin/entitlements.plist"

    trust_existing() {
        local p="$1"
        for arch in arm64 arm64e x86_64; do
            local h=$(ldid -arch "$arch" -h "$p" 2>/dev/null | grep CDHash= | cut -c8-)
            [ -n "$h" ] && sudo /var/jb/usr/bin/jbctl trustcache add "$h" >/dev/null 2>&1
        done
        return 0
    }
    sign_and_trust() {
        local p="$1"
        # The first ldid pass may grow/rewrite __LINKEDIT.  On the device's
        # ldid build its page hashes can still describe the pre-growth file,
        # so a second pass is required after the layout has settled.  This
        # must also be true for FAST builds; signing once here used to undo
        # the valid two-pass signature produced immediately above.
        sudo ldid -S"$ENT" -M "$p" || return 1
        sudo ldid -S"$ENT" -M "$p" || return 1
        trust_existing "$p"
    }
    sign_and_trust /var/jb/usr/macOS/lib/libmachook.dylib || {
        echo "Error: failed to sign arm64e libmachook for FAST install" >&2
        exit 1
    }
    if [ -f /var/jb/usr/macOS/lib/libmachook_arm64.dylib ]; then
        sign_and_trust /var/jb/usr/macOS/lib/libmachook_arm64.dylib || {
            echo "Error: failed to sign arm64 libmachook for FAST install" >&2
            exit 1
        }
    fi

    echo "==> FAST postinst: cp libmachook → /var/mnt/rootfs/usr/local/lib/"
    sudo rm -f /var/mnt/rootfs/usr/local/lib/libmachook.dylib
    sudo cp /var/jb/usr/macOS/lib/libmachook.dylib /var/mnt/rootfs/usr/local/lib/libmachook.dylib
    # Preserve the already-verified bytes.  Re-signing the copy once would
    # recreate the invalid-page signature and SIGKILL every injected process.
    trust_existing /var/mnt/rootfs/usr/local/lib/libmachook.dylib
    if [ -f /var/jb/usr/macOS/lib/libmachook_arm64.dylib ]; then
        sudo rm -f /var/mnt/rootfs/usr/local/lib/libmachook_arm64.dylib
        sudo cp /var/jb/usr/macOS/lib/libmachook_arm64.dylib /var/mnt/rootfs/usr/local/lib/libmachook_arm64.dylib
        trust_existing /var/mnt/rootfs/usr/local/lib/libmachook_arm64.dylib
    fi
    ls -la /var/mnt/rootfs/usr/local/lib/libmachook*.dylib 2>/dev/null | head -3
    echo "==> FAST postinst done"
    set -e
else
    sudo bash /var/jb/usr/macOS/bin/postinst.sh
fi

echo "==> Done! Package installed successfully."

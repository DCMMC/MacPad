#!/bin/bash
# Build and install MacWSBootingGuide from macOS (cross-compile)
# Usage: bash misc/build.sh
#
# All scripts and binaries are installed via the .deb package.
# Only libmachook.dylib needs post-processing with vtool (macOS build version).

set -e

DEVICE_IP="172.20.10.3"
DEVICE_PORT=2222

# Setup SSH key (default password: alpine)
ssh-copy-id -p $DEVICE_PORT root@$DEVICE_IP 2>/dev/null || true

# Theos only searches $THEOS/sdks and the Xcode platform SDK dirs, not the
# Command Line Tools SDK dir. launchservicesd (macosx target) needs a macOS
# SDK, so symlink the CLT/Xcode macOS SDK into $THEOS/sdks if it's not there.
THEOS=${THEOS:-$HOME/theos}
if ! ls "$THEOS"/sdks/MacOSX*.sdk >/dev/null 2>&1; then
  MACOS_SDK_PATH=$(xcrun --sdk macosx --show-sdk-path 2>/dev/null || true)
  MACOS_SDK_VER=$(xcrun --sdk macosx --show-sdk-version 2>/dev/null || true)
  if [ -n "$MACOS_SDK_PATH" ] && [ -n "$MACOS_SDK_VER" ]; then
    ln -sfn "$MACOS_SDK_PATH" "$THEOS/sdks/MacOSX${MACOS_SDK_VER}.sdk"
    echo "==> Linked macOS SDK $MACOS_SDK_VER into $THEOS/sdks"
  else
    echo "WARNING: no macOS SDK found via xcrun; launchservicesd may fail to build" >&2
  fi
fi

# Build, package, and install
gmake FINALPACKAGE=1 STRIP=0 OPTFLAG=-O2 THEOS_PACKAGE_SCHEME=rootless package install \
  THEOS_DEVICE_IP=$DEVICE_IP THEOS_DEVICE_PORT=$DEVICE_PORT GO_EASY_ON_ME=1

# Post-process libmachook.dylib with macOS build version
cp .theos/obj/libmachook.dylib .
python3 misc/set_macos_version.py libmachook.dylib
ldid -S libmachook.dylib
codesign -f -s - libmachook.dylib

# Replace the package-installed version with the post-processed one
scp -P $DEVICE_PORT libmachook.dylib root@$DEVICE_IP:/var/jb/usr/macOS/lib/libmachook.dylib
rm libmachook.dylib

echo "==> Running postinst..."
ssh -p $DEVICE_PORT root@$DEVICE_IP 'echo alpine | sudo -S bash /var/jb/usr/macOS/bin/postinst.sh'

echo "==> Done!"

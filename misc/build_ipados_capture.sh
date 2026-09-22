#!/bin/bash
# Build only. No app restart, installation, or device state changes.
set -euo pipefail
capture_repo_root="$(cd "$(dirname "$0")/.." && pwd)"
capture_sdk="${1:-${THEOS:-/Users/dcmmc/theos}/sdks/iPhoneOS16.5.sdk}"
capture_output="${2:-$capture_repo_root/tmp/macws_ipados_capture}"
if [ ! -d "$capture_sdk" ]; then
    echo "Missing iOS SDK: $capture_sdk" >&2
    exit 64
fi
mkdir -p "$(dirname "$capture_output")"
clang -target arm64-apple-ios16.0 -isysroot "$capture_sdk" \
    -fobjc-arc -O2 -Wall -Wextra \
    "$capture_repo_root/misc/macws_ipados_capture.m" \
    -framework UIKit -framework Foundation -framework CoreGraphics \
    -framework IOSurface -framework QuartzCore -o "$capture_output"
ldid -S"$capture_repo_root/misc/macws_ipados_capture.entitlements.plist" "$capture_output"
printf '%s\n' "$capture_output"

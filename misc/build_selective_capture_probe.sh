#!/bin/bash
# LOCAL BUILD ONLY. Does not sign, deploy, launch, or establish capture.
set -euo pipefail
probe_repo_root="$(cd "$(dirname "$0")/.." && pwd)"
probe_sdk="${1:-$(xcrun --sdk macosx --show-sdk-path)}"
probe_output="${2:-$probe_repo_root/tmp/macws_selective_capture_probe-v1}"
if [ ! -d "$probe_sdk" ]; then
    echo "Missing macOS SDK: $probe_sdk" >&2
    exit 64
fi
mkdir -p "$(dirname "$probe_output")"
clang -target arm64-apple-macos13.0 -isysroot "$probe_sdk" \
    -fobjc-arc -fblocks -O2 -Wall -Wextra -Werror \
    -Wno-deprecated-declarations \
    "$probe_repo_root/misc/macws_selective_capture_probe.m" \
    -framework Foundation -framework AppKit -framework CoreGraphics -framework IOSurface \
    -framework ImageIO -o "$probe_output"
printf '%s\n' "$probe_output"

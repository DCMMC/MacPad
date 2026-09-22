#!/bin/bash
# Authoritative MacBook-side entry point for Aquarium measurements. It keeps the
# existing CDP harness unchanged and places the complete invocation under the
# five-minute whole-system thermal guard.

set -u

MACWS_SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec bash "$MACWS_SCRIPT_DIR/macbook_thermal_watchdog.sh" -- \
    node "$MACWS_SCRIPT_DIR/aquarium_cdp_benchmark.mjs" "$@"

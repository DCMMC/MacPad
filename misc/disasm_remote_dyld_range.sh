#!/bin/bash
# Disassemble a raw byte range from a dyld shared-cache subcache on the iPad.
#
# This is intentionally read-only.  It avoids extracting multi-gigabyte cache
# files and preserves the static VM address needed to compare LLDB PCs with the
# exact on-device image.
#
# Usage:
#   bash misc/disasm_remote_dyld_range.sh \
#     <host> <cache-path> <cache-mapping-vm> <vm-address> <length>

set -euo pipefail

HOST=${1:-}
CACHE=${2:-}
CACHE_MAPPING_VM=${3:-}
VM_ADDRESS=${4:-}
LENGTH=${5:-}
SSH_PORT=${SSH_PORT:-22}

if [ -z "$HOST" ] || [ -z "$CACHE" ] || [ -z "$CACHE_MAPPING_VM" ] || \
   [ -z "$VM_ADDRESS" ] || [ -z "$LENGTH" ]; then
    echo "usage: bash $0 <host> <cache-path> <cache-mapping-vm> <vm-address> <length>" >&2
    exit 1
fi

FILE_OFFSET=$((VM_ADDRESS - CACHE_MAPPING_VM))
if [ "$FILE_OFFSET" -lt 0 ]; then
    echo "error: VM address precedes the cache mapping" >&2
    exit 2
fi

case "$CACHE" in
    *"'"*|*$'\n'*)
        echo "error: cache path contains unsupported characters" >&2
        exit 3
        ;;
esac

REMOTE_COMMAND="echo __MACWS_RAW__; \
/var/jb/usr/bin/xxd -p -c 4 -s $FILE_OFFSET -l $LENGTH '$CACHE'; \
echo __MACWS_DISASSEMBLY__; \
/var/jb/usr/bin/xxd -i -c 4 -s $FILE_OFFSET -l $LENGTH '$CACHE' | \
/var/jb/usr/bin/awk '/^  0x/{sub(/^  /,\"[\"); sub(/,\$/,\"]\"); print}' | \
/var/jb/usr/bin/llvm-mc-16 --disassemble --show-encoding \
    --triple=aarch64-apple-darwin"

ssh -p "$SSH_PORT" "root@$HOST" "$REMOTE_COMMAND" 2>/dev/null | \
    awk -v start="$VM_ADDRESS" '
        $0 == "__MACWS_RAW__" { section = "raw"; next }
        $0 == "__MACWS_DISASSEMBLY__" { section = "disassembly"; next }

        section == "raw" {
            raw[++raw_count] = $0
            next
        }

        section == "disassembly" && /; encoding: \[/ {
            assembly = $0
            sub(/[ \t]*; encoding:.*$/, "", assembly)
            encoding = $0
            sub(/^.*encoding: \[/, "", encoding)
            sub(/\].*$/, "", encoding)
            gsub(/0x|,|[ \t]/, "", encoding)
            valid_assembly[++valid_count] = assembly
            valid_encoding[valid_count] = encoding
        }

        END {
            valid_index = 1
            for (i = 1; i <= raw_count; i++) {
                address = start + (i - 1) * 4
                if (valid_index <= valid_count &&
                    raw[i] == valid_encoding[valid_index]) {
                    printf("0x%x %s\n", address,
                           valid_assembly[valid_index++])
                } else {
                    printf("0x%x <invalid encoding: %s>\n", address, raw[i])
                }
            }
        }
    '

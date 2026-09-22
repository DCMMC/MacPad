# No shebang: invoke with bash. This jailbreak's AMFI rejects execve of
# shebang scripts.

# Provision the exact secondary QuartzCore library needed by the native iOS
# AGX driver. This intentionally does not replace Ventura's process-wide
# default.metallib. It is small enough to run from both package installation
# and the complete rootfs repair path, closing the upgrade case where all
# trust sentinels remain valid but a newly discovered shader is still absent.
ROOTFS=/var/mnt/rootfs
QC_DEFAULT="$ROOTFS/System/Library/Frameworks/QuartzCore.framework/Versions/A/Resources/default.metallib"
QC_ORIGINAL="$QC_DEFAULT.macws-macos13.4-original"
QC_EXPECTED_SHA256=ac8014164c7784395f86ac2926c62b67c96faa2a3c789f231b4b22b64024bfba
QC_COMPAT_DIR="$ROOTFS/usr/local/share/macws/quartzcore"
QC_COMPAT_TARGET="$QC_COMPAT_DIR/default-desktop-effects-macabi.metallib"
METAL2METAL_ROUTE_DIR="$ROOTFS/usr/local/share/macws/metal2metal/routes"
QC_MANIFEST_TARGET="$METAL2METAL_ROUTE_DIR/quartzcore-default.route.plist"
METAL2METAL=/var/jb/usr/macOS/bin/metal2metal.py
QC_LLVM_DIS=/var/jb/usr/lib/llvm-16/bin/llvm-dis
QC_LLVM_AS=/var/jb/usr/lib/llvm-16/bin/llvm-as

qc_compat_sha256() {
	sha256sum "$1" 2>/dev/null | awk '{print $1}'
}

if [ "$(qc_compat_sha256 "$QC_ORIGINAL")" != "$QC_EXPECTED_SHA256" ]; then
	if [ "$(qc_compat_sha256 "$QC_DEFAULT")" != "$QC_EXPECTED_SHA256" ]; then
		echo "[ERROR] QuartzCore default.metallib is not the supported macOS 13.4 library." >&2
		exit 1
	fi
	cp "$QC_DEFAULT" "$QC_ORIGINAL.new.$$" || exit 1
	chmod 0644 "$QC_ORIGINAL.new.$$" || exit 1
	mv -f "$QC_ORIGINAL.new.$$" "$QC_ORIGINAL" || exit 1
fi

# Undo an interrupted diagnostic replacement before any GUI process can see
# it. The supported system library hash is the authoritative source.
if [ "$(qc_compat_sha256 "$QC_DEFAULT")" != "$QC_EXPECTED_SHA256" ]; then
	cp "$QC_ORIGINAL" "$QC_DEFAULT.new.$$" || exit 1
	chmod 0644 "$QC_DEFAULT.new.$$" || exit 1
	mv -f "$QC_DEFAULT.new.$$" "$QC_DEFAULT" || exit 1
fi

if [ ! -f "$METAL2METAL" ] || [ ! -x "$QC_LLVM_DIS" ] ||
   [ ! -x "$QC_LLVM_AS" ]; then
	echo "[ERROR] metal2metal or device LLVM 16 is unavailable." >&2
	exit 1
fi

# A manifest whose source and output identities both match is a complete
# byte-level witness for this translator/profile pair. Avoid LLVM work on every
# package reinstall and cold repair.
if python3 "$METAL2METAL" verify-runtime-manifest "$QC_MANIFEST_TARGET" \
	--source "$QC_ORIGINAL" --output "$QC_COMPAT_TARGET" >/dev/null 2>&1; then
	echo '[INFO] complete QuartzCore metal2metal library already installed'
	exit 0
fi

mkdir -p "$QC_COMPAT_DIR" "$METAL2METAL_ROUTE_DIR" || exit 1
QC_COMPAT_TMP="$QC_COMPAT_TARGET.new.$$"
QC_MANIFEST_TMP="$QC_MANIFEST_TARGET.new.$$"
python3 "$METAL2METAL" translate "$QC_ORIGINAL" "$QC_COMPAT_TMP" \
	--llvm-dis "$QC_LLVM_DIS" --llvm-as "$QC_LLVM_AS" \
	--auto-lower-known-air \
	--runtime-manifest "$QC_MANIFEST_TMP" \
	--runtime-source-path "/System/Library/Frameworks/QuartzCore.framework/Versions/A/Resources/default.metallib" \
	--runtime-output-path "/usr/local/share/macws/quartzcore/default-desktop-effects-macabi.metallib" || {
	rm -f "$QC_COMPAT_TMP" "$QC_MANIFEST_TMP"
	exit 1
}
if ! python3 "$METAL2METAL" verify-runtime-manifest "$QC_MANIFEST_TMP" \
     --source "$QC_ORIGINAL" --output "$QC_COMPAT_TMP"; then
	echo "[ERROR] Generated QuartzCore metal2metal manifest failed validation." >&2
	rm -f "$QC_COMPAT_TMP" "$QC_MANIFEST_TMP"
	exit 1
fi
chmod 0644 "$QC_COMPAT_TMP" || exit 1
chmod 0644 "$QC_MANIFEST_TMP" || exit 1
mv -f "$QC_COMPAT_TMP" "$QC_COMPAT_TARGET" || exit 1
mv -f "$QC_MANIFEST_TMP" "$QC_MANIFEST_TARGET" || exit 1
echo '[INFO] installed complete QuartzCore metal2metal library'

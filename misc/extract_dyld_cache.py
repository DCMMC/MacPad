#!/usr/bin/env python3
"""
Extract individual dylibs from an iOS dyld shared cache by calling
/usr/lib/dsc_extractor.bundle's dyld_shared_cache_extract_dylibs_progress.

Usage:
    python3 misc/extract_dyld_cache.py <cache-path> <output-dir>

Where:
    cache-path  is the dyld_shared_cache_arm64e file (or .01/.02/...)
    output-dir  is where to extract dylibs (will create the layout:
                  System/Library/Frameworks/.../Foo.framework/Foo
                  System/Library/PrivateFrameworks/.../Bar
                  System/Library/Extensions/Baz.bundle/Contents/MacOS/Baz
                  usr/lib/libcrypto.dylib
                  ...)

The bundle's signature (from dyld sources):
    extern int dyld_shared_cache_extract_dylibs_progress(
        const char* shared_cache_file_path,
        const char* extraction_root_path,
        void (^progress)(unsigned current, unsigned total));

Returns 0 on success, non-zero on error.
"""
import ctypes
import ctypes.util
import os
import sys


def main():
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} <cache-path> <output-dir>", file=sys.stderr)
        sys.exit(2)
    cache_path = os.path.abspath(sys.argv[1])
    out_dir = os.path.abspath(sys.argv[2])

    if not os.path.isfile(cache_path):
        print(f"error: cache file not found: {cache_path}", file=sys.stderr)
        sys.exit(1)
    os.makedirs(out_dir, exist_ok=True)

    bundle_path = "/usr/lib/dsc_extractor.bundle"
    if not os.path.isfile(bundle_path):
        print(f"error: {bundle_path} not found", file=sys.stderr)
        sys.exit(1)

    bundle = ctypes.CDLL(bundle_path)
    fn = bundle.dyld_shared_cache_extract_dylibs_progress
    fn.restype = ctypes.c_int
    # Third arg is an Objective-C block (void (^)(unsigned, unsigned)). The
    # easy way without writing a real block is to pass NULL — the bundle's
    # implementation checks for nullness and skips progress callbacks.
    fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p]

    print(f"extracting {cache_path} -> {out_dir}", file=sys.stderr)
    rc = fn(cache_path.encode("utf-8"), out_dir.encode("utf-8"), None)
    if rc != 0:
        print(f"extraction failed: rc={rc}", file=sys.stderr)
        sys.exit(1)
    print(f"done", file=sys.stderr)


if __name__ == "__main__":
    main()

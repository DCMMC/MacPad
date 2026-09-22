#!/usr/bin/env python3
"""Stable CLI for MacWS's profile-driven Metal AIR-to-AIR translator."""

from __future__ import annotations

import argparse
import pathlib
import sys

import repack_metallib_macabi
from metal2metal_manifest import verify_runtime_manifest


def main(argv: list[str] | None = None) -> None:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] in ("-h", "--help"):
        print(
            "usage: metal2metal.py translate INPUT OUTPUT [options]\n"
            "       metal2metal.py verify-runtime-manifest MANIFEST "
            "--source SOURCE --output OUTPUT"
        )
        return
    command = arguments.pop(0)
    if command == "translate":
        repack_metallib_macabi.main(arguments)
        return
    if command == "verify-runtime-manifest":
        parser = argparse.ArgumentParser(prog=(
            "metal2metal.py verify-runtime-manifest"
        ))
        parser.add_argument("manifest", type=pathlib.Path)
        parser.add_argument("--source", required=True, type=pathlib.Path)
        parser.add_argument("--output", required=True, type=pathlib.Path)
        args = parser.parse_args(arguments)
        manifest = verify_runtime_manifest(
            args.manifest, args.source, args.output
        )
        print(
            f"verified profile={manifest['profile']} "
            f"translated={len(manifest['translated_functions'])} "
            f"manifest={args.manifest}"
        )
        return
    raise SystemExit(f"unknown metal2metal command: {command}")


if __name__ == "__main__":
    main()

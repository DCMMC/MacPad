"""Refresh selected Steam package inventory records after a signed port.

Valve's ``package/*.installed`` files record ``path,size;mtime;crc32`` and a
SHA-1 footer over the preceding bytes.  A compatibility port or ad-hoc code
signature legitimately changes those values.  Update only explicitly named
paths; package manifests and unrelated resources remain Valve-controlled.

Invoke with Python.  There is intentionally no shebang because AMFI on the
target device rejects direct execution of scripts carrying one.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import sys
import zlib


RECORD = re.compile(r"^(.*),(-?\d+);(-?\d+);(\d+)(\r?\n)?$")


def file_crc32(path: Path) -> int:
    checksum = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checksum = zlib.crc32(chunk, checksum)
    return checksum & 0xFFFFFFFF


def refresh(app_root: Path, relative_paths: set[str]) -> int:
    macos_root = (app_root / "Contents/MacOS").resolve()
    package_root = macos_root / "package"
    missing = set(relative_paths)
    changed_files = 0
    for inventory in sorted(package_root.glob("*.installed")):
        lines = inventory.read_text(encoding="utf-8").splitlines(keepends=True)
        output: list[str] = []
        changed = False
        for line in lines:
            match = RECORD.match(line)
            if not match or match.group(1) not in relative_paths:
                output.append(line)
                continue
            relative = match.group(1)
            target = (macos_root / relative).resolve()
            if target != macos_root and macos_root not in target.parents:
                raise ValueError(f"inventory path escapes Steam root: {relative}")
            status = target.stat()
            ending = match.group(5) or ""
            replacement = (
                f"{relative},{status.st_size};{int(status.st_mtime)};"
                f"{file_crc32(target)}{ending}"
            )
            output.append(replacement)
            changed |= replacement != line
            missing.discard(relative)

        if not changed:
            continue
        for index, line in enumerate(output):
            if not line.startswith("SHA1="):
                continue
            ending = "\r\n" if line.endswith("\r\n") else (
                "\n" if line.endswith("\n") else ""
            )
            digest = hashlib.sha1(
                "".join(output[:index]).encode("utf-8")
            ).hexdigest().upper()
            output[index] = f"SHA1={digest}{ending}"
            break
        temporary = inventory.with_name(inventory.name + ".macws-new")
        # Device-side Procursus Python predates pathlib.Path.write_text's
        # ``newline`` argument.  Open explicitly so Valve's original CRLF/LF
        # bytes remain under our control on every supported deployment image.
        with temporary.open("w", encoding="utf-8", newline="") as stream:
            stream.write("".join(output))
        os.chmod(temporary, inventory.stat().st_mode & 0o7777)
        os.replace(temporary, inventory)
        changed_files += 1
        print(f"refreshed {inventory.name}")
    if missing:
        raise ValueError("paths absent from inventories: " + ", ".join(sorted(missing)))
    return changed_files


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("app_root", type=Path)
    parser.add_argument("relative_path", nargs="+")
    arguments = parser.parse_args()
    try:
        changed = refresh(arguments.app_root, set(arguments.relative_path))
    except (OSError, ValueError) as error:
        print(f"refresh_steam_inventory: {error}", file=sys.stderr)
        return 1
    print(f"inventories_changed={changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

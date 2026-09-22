"""Build the ad-hoc entitlement profile for a third-party macOS binary.

The MacWS compatibility profile grants the hardware and service rights needed
inside the iPad chroot. Third-party applications can additionally rely on
vendor identity rights such as application identifiers and Keychain access
groups. A dictionary update is wrong for list-valued rights because it drops
the vendor's identity storage contract.

Usage: python3 merge_third_party_entitlements.py PROJECT VENDOR OUTPUT
"""

from __future__ import annotations

import plistlib
import sys
from pathlib import Path


MERGED_STRING_ARRAY_KEYS = frozenset({
    "keychain-access-groups",
})


def _load_optional(path: Path) -> dict:
    try:
        with path.open("rb") as stream:
            value = plistlib.load(stream)
    except (EOFError, OSError, plistlib.InvalidFileException):
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"entitlements root must be a dictionary: {path}")
    return value


def _merged_strings(vendor_value: object, project_value: object) -> list[str]:
    values: list[str] = []
    for candidate in (vendor_value, project_value):
        if candidate is None:
            continue
        if not isinstance(candidate, list) or not all(
                isinstance(item, str) for item in candidate):
            raise ValueError("merged entitlement must be an array of strings")
        for item in candidate:
            if item not in values:
                values.append(item)
    return values


def merge_entitlements(project: dict, vendor: dict) -> dict:
    merged = dict(vendor)
    merged.update(project)
    for key in MERGED_STRING_ARRAY_KEYS:
        value = _merged_strings(vendor.get(key), project.get(key))
        if value:
            merged[key] = value
    # A third-party process is never an iOS platform main binary. This bit is
    # intentionally removed even if an older MacWS signature polluted the
    # current executable and is subsequently used as the vendor input.
    merged.pop("platform-application", None)
    return merged


def main(arguments: list[str]) -> int:
    if len(arguments) != 4:
        print(
            f"usage: {arguments[0]} PROJECT_ENTITLEMENTS "
            "VENDOR_ENTITLEMENTS OUTPUT",
            file=sys.stderr,
        )
        return 64
    project = _load_optional(Path(arguments[1]))
    vendor = _load_optional(Path(arguments[2]))
    merged = merge_entitlements(project, vendor)
    with Path(arguments[3]).open("wb") as stream:
        plistlib.dump(merged, stream, fmt=plistlib.FMT_XML, sort_keys=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

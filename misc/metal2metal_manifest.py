"""Build and verify fail-closed runtime routing manifests for metal2metal."""

from __future__ import annotations

import hashlib
import pathlib
import plistlib
from typing import Any


MANIFEST_SCHEMA_VERSION = 1
TRANSLATOR_NAME = "macws-metal2metal"
TRANSLATOR_VERSION = 1
FNV1A64_OFFSET_BASIS = 1469598103934665603
FNV1A64_PRIME = 1099511628211


def fnv1a64(data: bytes) -> int:
    """Match libmachook's historical FNV-1a implementation exactly."""
    value = FNV1A64_OFFSET_BASIS
    for byte in data:
        value ^= byte
        value = (value * FNV1A64_PRIME) & 0xFFFFFFFFFFFFFFFF
    return value


def _identity(data: bytes) -> dict[str, Any]:
    return {
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        # Property-list integers are signed. A fixed-width string preserves
        # every uint64 value and is directly parseable by NSScanner/strtoull.
        "fnv1a64": f"{fnv1a64(data):016x}",
    }


def build_runtime_manifest(
    *,
    source: bytes,
    output: bytes,
    source_runtime_path: str,
    output_runtime_path: str,
    profile: str,
    function_reports: list[dict[str, object]],
) -> dict[str, Any]:
    names = [str(function["name"]) for function in function_reports]
    if len(names) != len(set(names)):
        raise ValueError(
            "runtime manifest requires unique MTL function names"
        )

    translated: dict[str, Any] = {}
    for function in function_reports:
        if not function.get("selected"):
            continue
        name = str(function["name"])
        translated[name] = {
            "function_type": function.get("function_type_name") or "unknown",
            "needs_function_constants": bool(
                function.get("function_constants")
            ),
            "function_constants": function.get("function_constants", []),
            "applied_lowerings": function.get("applied_lowerings", []),
            "input_sha256": function["input_sha256"],
            "output_sha256": function["output_sha256"],
        }
    if not translated:
        raise ValueError("runtime manifest has no translated functions")
    complete = len(translated) == len(names)
    if not complete:
        raise ValueError(
            "runtime routing manifests require complete-library translation"
        )

    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "translator": TRANSLATOR_NAME,
        "translator_version": TRANSLATOR_VERSION,
        "profile": profile,
        "translation": {
            "selection_policy": "all-functions",
            "source_function_count": len(names),
            "translated_function_count": len(translated),
            "complete": True,
        },
        "source": {
            **_identity(source),
            "runtime_path": source_runtime_path,
            "function_names": sorted(names),
        },
        "output": {
            **_identity(output),
            "runtime_path": output_runtime_path,
        },
        "translated_functions": translated,
    }


def write_runtime_manifest(path: pathlib.Path,
                           manifest: dict[str, Any]) -> None:
    path.write_bytes(plistlib.dumps(
        manifest, fmt=plistlib.FMT_XML, sort_keys=True
    ))


def load_runtime_manifest(path: pathlib.Path) -> dict[str, Any]:
    manifest = plistlib.loads(path.read_bytes())
    if not isinstance(manifest, dict):
        raise ValueError("metal2metal manifest root is not a dictionary")
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ValueError("unsupported metal2metal manifest schema")
    if manifest.get("translator") != TRANSLATOR_NAME:
        raise ValueError("unexpected metal2metal manifest translator")
    if manifest.get("translator_version") != TRANSLATOR_VERSION:
        raise ValueError("unsupported metal2metal translator version")
    for key in (
        "translation", "source", "output", "translated_functions"
    ):
        if not isinstance(manifest.get(key), dict):
            raise ValueError(f"metal2metal manifest has invalid {key}")
    source = manifest["source"]
    names = source.get("function_names")
    translated = manifest["translated_functions"]
    translation = manifest["translation"]
    if (not isinstance(names, list) or not names or
            any(not isinstance(name, str) for name in names)):
        raise ValueError("metal2metal manifest has invalid function names")
    if len(names) != len(set(names)):
        raise ValueError("metal2metal manifest has duplicate function names")
    if not translated or not set(translated).issubset(names):
        raise ValueError(
            "translated functions are not a non-empty source subset"
        )
    if (
        translation.get("selection_policy") != "all-functions" or
        translation.get("complete") is not True or
        translation.get("source_function_count") != len(names) or
        translation.get("translated_function_count") != len(translated) or
        len(translated) != len(names)
    ):
        raise ValueError(
            "runtime manifest is not a complete-library translation"
        )
    return manifest


def verify_runtime_manifest(
    path: pathlib.Path,
    source_path: pathlib.Path,
    output_path: pathlib.Path,
) -> dict[str, Any]:
    manifest = load_runtime_manifest(path)
    for label, artifact_path in (
        ("source", source_path), ("output", output_path)
    ):
        data = artifact_path.read_bytes()
        expected = manifest[label]
        observed = _identity(data)
        for field in ("size", "sha256", "fnv1a64"):
            if observed[field] != expected.get(field):
                raise ValueError(
                    f"{label} {field} mismatch: "
                    f"{observed[field]} != {expected.get(field)}"
                )
    return manifest

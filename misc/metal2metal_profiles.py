"""Versioned translation profiles for MacWS's AIR-to-AIR compiler layer."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Metal2MetalProfile:
    name: str
    source_family: str
    target_triple: str
    container_target: str
    target_major: int
    target_minor: int


DEFAULT_PROFILE = "ventura13-ios19-macabi"

PROFILES = {
    DEFAULT_PROFILE: Metal2MetalProfile(
        name=DEFAULT_PROFILE,
        source_family="macOS 13.4 Apple AIR",
        # This is the runtime-confirmed Catalyst target used by the existing
        # iOS 16.3 MTLCompilerService bridge. It is deliberately not inferred
        # from the device OS marketing version.
        target_triple="air64-apple-ios19.0.0-macabi",
        container_target="macabi",
        target_major=19,
        target_minor=0,
    ),
}


def get_profile(name: str) -> Metal2MetalProfile:
    try:
        return PROFILES[name]
    except KeyError as error:
        raise ValueError(f"unknown metal2metal profile: {name}") from error

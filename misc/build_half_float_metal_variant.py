#!/usr/bin/env python3
"""Build a format-selected macOS half-float texture-write Metal variant.

The source MTLB is analyzed, not identified by an application or function
allowlist.  For every AIR function that actually calls a float texture-write
intrinsic, the tool joins the intrinsic's pointer argument to that function's
AIR resource metadata and emits the exact writable texture-slot mask.  It then
asks ``repack_metallib_macabi.py`` to retarget the complete library while
saturating only those functions.

The output index is consumed by libmachook's generic runtime selector.  It
chooses the variant only when all rewritten writable slots are bound to
R/RG/RGBA16Float textures; ordinary float formats retain the ordinary
pipeline.  Ambiguous pointer derivations or metadata fail closed.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys
import tempfile

import repack_metallib_macabi as repack


FLOAT_TEXTURE_WRITE = re.compile(
    r"@air\.write_texture_[123]d\.v4f32\("
    r"(?P<pointer>[^,\n]+?\s+"
    r"(?P<argument>%(?:\"(?:\\.|[^\"])*\"|[-a-zA-Z$._0-9]+))),"
)
METADATA_NODE = re.compile(r"^!(?P<id>[0-9]+) = !\{(?P<body>.*)\}$",
                           re.MULTILINE)


def fnv1a64(data: bytes) -> int:
    value = 1469598103934665603
    for byte in data:
        value ^= byte
        value = (value * 1099511628211) & ((1 << 64) - 1)
    return value


def function_definition(text: str, name: str) -> tuple[str, str]:
    pattern = re.compile(
        rf"^define\b[^\n]*@{re.escape(name)}\("
        rf"(?P<parameters>.*)\)[^\n]*\{{\n"
        rf"(?P<body>.*?)^\}}$",
        re.MULTILINE | re.DOTALL,
    )
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise ValueError(
            f"{name}: expected one AIR function definition, "
            f"found {len(matches)}"
        )
    return matches[0].group("parameters"), matches[0].group("body")


def split_llvm_parameters(parameters: str) -> list[str]:
    """Split an LLVM parameter list without splitting aggregate types."""
    result: list[str] = []
    start = 0
    depths = {"(": 0, "[": 0, "{": 0, "<": 0}
    closing = {")": "(", "]": "[", "}": "{", ">": "<"}
    quoted = False
    escaped = False
    for index, character in enumerate(parameters):
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
            continue
        if character == '"':
            quoted = True
        elif character in depths:
            depths[character] += 1
        elif character in closing:
            opener = closing[character]
            if depths[opener] == 0:
                raise ValueError("unbalanced LLVM function parameters")
            depths[opener] -= 1
        elif character == "," and not any(depths.values()):
            result.append(parameters[start:index].strip())
            start = index + 1
    if quoted or any(depths.values()):
        raise ValueError("unbalanced LLVM function parameters")
    tail = parameters[start:].strip()
    if tail:
        result.append(tail)
    return result


LLVM_IDENTIFIER = re.compile(
    r'%(?:"(?:\\.|[^\"])*"|[-a-zA-Z$._0-9]+)'
)


def formal_argument_indices(parameters: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for index, parameter in enumerate(split_llvm_parameters(parameters)):
        identifiers = LLVM_IDENTIFIER.findall(parameter)
        if not identifiers:
            if parameter == "...":
                continue
            raise ValueError(
                f"formal argument {index} has no LLVM identifier: "
                f"{parameter}"
            )
        identifier = identifiers[-1]
        if identifier in result:
            raise ValueError(f"duplicate LLVM argument {identifier}")
        result[identifier] = index
    return result


def writable_texture_locations(text: str, name: str) -> list[int]:
    parameters, body = function_definition(text, name)
    formal_indices = formal_argument_indices(parameters)
    write_arguments = {
        match.group("argument")
        for match in FLOAT_TEXTURE_WRITE.finditer(body)
    }
    if not write_arguments:
        return []

    argument_locations: dict[int, int] = {}
    for match in METADATA_NODE.finditer(text):
        metadata = match.group("body")
        if ('!"air.texture"' not in metadata or
                '!"air.write"' not in metadata):
            continue
        argument = re.match(r"i32 ([0-9]+),", metadata)
        location = re.search(
            r'!"air\.location_index", i32 ([0-9]+)', metadata
        )
        if not argument or not location:
            continue
        argument_index = int(argument.group(1))
        location_index = int(location.group(1))
        if argument_index in argument_locations:
            raise ValueError(
                f"{name}: duplicate writable texture metadata for "
                f"argument %{argument_index}"
            )
        argument_locations[argument_index] = location_index

    indirect = write_arguments - formal_indices.keys()
    if indirect:
        formatted = ", ".join(sorted(indirect))
        raise ValueError(
            f"{name}: float texture write pointer(s) {formatted} do not "
            "resolve to direct writable AIR texture arguments"
        )
    missing = {
        argument for argument in write_arguments
        if formal_indices[argument] not in argument_locations
    }
    if missing:
        formatted = ", ".join(sorted(missing))
        raise ValueError(
            f"{name}: float texture write pointer(s) {formatted} have no "
            "writable AIR texture metadata"
        )
    locations = sorted(
        argument_locations[formal_indices[argument]]
        for argument in write_arguments
    )
    if len(locations) != len(set(locations)):
        raise ValueError(f"{name}: duplicate writable texture locations")
    if any(location < 0 or location >= 32 for location in locations):
        raise ValueError(f"{name}: writable texture location exceeds mask")
    return locations


def analyze(input_path: pathlib.Path,
            llvm_dis: pathlib.Path) -> list[tuple[str, int]]:
    data = input_path.read_bytes()
    records = repack.parse_function_records(bytearray(data))
    bitcode_offset = repack.u64(data, 72)
    variants: list[tuple[str, int]] = []
    with tempfile.TemporaryDirectory(prefix="macws-half-variant-") as temp:
        scratch = pathlib.Path(temp)
        for record in records:
            name = str(record["name"])
            offset = int(record["bitcode_offset"])
            size = int(record["bitcode_size"])
            air = scratch / f"{record['index']}.air"
            assembly = scratch / f"{record['index']}.ll"
            air.write_bytes(data[
                bitcode_offset + offset:bitcode_offset + offset + size
            ])
            subprocess.run(
                [llvm_dis, air, "-o", assembly], check=True
            )
            locations = writable_texture_locations(
                assembly.read_text(encoding="utf-8"), name
            )
            if not locations:
                continue
            mask = sum(1 << location for location in locations)
            variants.append((name, mask))
    if not variants:
        raise ValueError("library has no direct float texture-write function")
    return variants


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("output")
    parser.add_argument("index")
    parser.add_argument(
        "--llvm-dis", default="/var/jb/usr/lib/llvm-16/bin/llvm-dis"
    )
    parser.add_argument(
        "--llvm-as", default="/var/jb/usr/lib/llvm-16/bin/llvm-as"
    )
    parser.add_argument(
        "--repack",
        default=str(pathlib.Path(__file__).with_name(
            "repack_metallib_macabi.py"
        )),
    )
    args = parser.parse_args()

    input_path = pathlib.Path(args.input)
    output_path = pathlib.Path(args.output)
    index_path = pathlib.Path(args.index)
    source = input_path.read_bytes()
    variants = analyze(input_path, pathlib.Path(args.llvm_dis))
    command = [
        sys.executable, args.repack, str(input_path), str(output_path),
        "--llvm-dis", args.llvm_dis,
        "--llvm-as", args.llvm_as,
        "--target-triple", "air64-apple-ios19.0.0-macabi",
        "--container-target", "macabi",
        "--target-major", "19",
        "--target-minor", "0",
    ]
    for name, _mask in variants:
        command.extend([
            "--saturate-half-float-texture-writes-function", name
        ])
    subprocess.run(command, check=True)

    variant = output_path.read_bytes()
    source_hash = fnv1a64(source)
    variant_hash = fnv1a64(variant)
    lines = [
        "# sourceLength sourceFNV variantLength variantFNV "
        "function writeTextureMask\n"
    ]
    for name, mask in variants:
        lines.append(
            f"{len(source)} {source_hash:016x} {len(variant)} "
            f"{variant_hash:016x} {name} {mask:08x}\n"
        )
    index_path.write_text("".join(lines), encoding="ascii")
    print(
        f"source={len(source)}/{source_hash:016x} "
        f"variant={len(variant)}/{variant_hash:016x} "
        f"functions={len(variants)} index={index_path}"
    )


if __name__ == "__main__":
    main()

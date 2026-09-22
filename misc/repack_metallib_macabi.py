#!/usr/bin/env python3
"""Reassemble AIR modules in an iOS/macOS MTLB for another Apple target.

This is a structural target conversion, not a loader/check bypass. Selected
AIR modules are normally round-tripped through llvm-dis/llvm-as with their
target triple changed from iOS/macOS to macabi. For legacy Apple AIR whose
bitcode dialect an upstream llvm-as cannot reproduce, a same-length target
triple can instead be patched in place. The MTLB function records receive the
real new byte sizes, SHA-256 hashes, and bitcode offsets; the container header
and trailing-section offsets are then rebuilt around the new bitcode section.

Known incompatibilities can be lowered by stable AIR/LLVM symbol and IR shape,
without a function-name allowlist. An optional JSON report inventories each
function's MTLB type/version fields and its AIR target, stage, layout, ABI
metadata keys/symbols, and applied lowerings so additions and coverage gaps are
machine-reviewable.

The parser follows the tag/section layout documented by YuAo's
MetalLibraryArchive and validates each input function hash before touching it.
It accepts iOS, macOS, and already-Mac-Catalyst executable MTLBs and fails
closed on tags or section layouts that it does not understand.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import struct
import subprocess
import tempfile

from metal2metal_manifest import (
    build_runtime_manifest,
    verify_runtime_manifest,
    write_runtime_manifest,
)
from metal2metal_profiles import DEFAULT_PROFILE, PROFILES, get_profile


MACOS_PLATFORM = 0x8001
IOS_PLATFORM = 0x0001
MACOS_TARGET_OS = 0x81
IOS_TARGET_OS = 0x82
MACABI_TARGET_OS = 0x86
UNKNOWN_TARGET_OS = 0x00
CURRENT_MTLB_FILE_VERSION = (2, 7)
MTLB_HEADER_SIZE = 88
OFFSET_SECTION_TAGS = {"HDYN", "VLST", "ILST", "HSRD", "HSRC", "RLST"}
ABI_REPORT_VERSION = 1
FUNCTION_TYPES = {
    0x00: "vertex",
    0x01: "fragment",
    0x02: "kernel",
    0x03: "unqualified",
    0x04: "visible",
    0x05: "extern",
    0x06: "intersection",
    0x07: "mesh",
    0x08: "object",
}
OUTPUT_CONTAINER_TARGETS = {
    "macabi": (MACOS_PLATFORM, MACABI_TARGET_OS),
    "ios": (IOS_PLATFORM, IOS_TARGET_OS),
    "macos": (MACOS_PLATFORM, MACOS_TARGET_OS),
}

AIR_TARGET_LINE = re.compile(
    r'^target triple = "(?P<triple>air64(?:_v[0-9]+)?-apple-'
    r'(?:ios|macosx)[^\"]*)"$',
    re.MULTILINE,
)
AIR_DATALAYOUT_LINE = re.compile(
    r'^target datalayout = "(?P<layout>[^\"]+)"$',
    re.MULTILINE,
)
AIR_STAGE_METADATA = {
    "vertex": re.compile(r"^!air\.vertex\s*=", re.MULTILINE),
    "fragment": re.compile(r"^!air\.fragment\s*=", re.MULTILINE),
    "kernel": re.compile(r"^!air\.kernel\s*=", re.MULTILINE),
    "mesh": re.compile(r"^!air\.mesh\s*=", re.MULTILINE),
    "object": re.compile(r"^!air\.object\s*=", re.MULTILINE),
}
AIR_ABI_SYMBOL = re.compile(
    r"@(?P<symbol>(?:air|llvm)\.[-a-zA-Z$._0-9]+)\("
)
AIR_ABI_METADATA_KEY = re.compile(
    r'!"(?P<key>air\.[-a-zA-Z$._0-9]+)"'
)
AIR_TARGET_BYTES = re.compile(
    rb"air64(?:_v[0-9]+)?-apple-(?:ios|macosx)[0-9]+"
    rb"(?:\.[0-9]+){0,2}(?:-macabi)?"
)


def target_triple_matches_container(target_triple: str,
                                    container_target: str) -> bool:
    if container_target == "macabi":
        return re.fullmatch(
            r"air64(?:_v[0-9]+)?-apple-ios[0-9]+"
            r"(?:\.[0-9]+){0,2}-macabi",
            target_triple,
        ) is not None
    if container_target == "ios":
        return re.fullmatch(
            r"air64(?:_v[0-9]+)?-apple-ios[0-9]+"
            r"(?:\.[0-9]+){0,2}",
            target_triple,
        ) is not None
    if container_target == "macos":
        return re.fullmatch(
            r"air64(?:_v[0-9]+)?-apple-macosx[0-9]+"
            r"(?:\.[0-9]+){0,2}",
            target_triple,
        ) is not None
    return False


def retarget_air_in_place(module: bytes, name: str,
                          target_triple: str) -> bytes:
    """Replace one same-length AIR target without re-encoding Apple bitcode."""
    matches = list(AIR_TARGET_BYTES.finditer(module))
    if len(matches) != 1:
        raise ValueError(
            f"{name}: expected exactly one AIR target triple, "
            f"found {len(matches)}"
        )
    replacement = target_triple.encode("ascii")
    match = matches[0]
    if len(replacement) != match.end() - match.start():
        source = match.group().decode("ascii")
        raise ValueError(
            f"{name}: in-place AIR target must remain {len(source)} bytes "
            f"({source!r} -> {target_triple!r})"
        )
    rebuilt = bytearray(module)
    rebuilt[match.start():match.end()] = replacement
    return bytes(rebuilt)


def u16(data: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def u32(data: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def u64(data: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from("<Q", data, offset)[0]


def parse_function_records(data: bytes | bytearray) -> list[dict[str, object]]:
    function_offset = u64(data, 24)
    function_size = u64(data, 32)
    if function_offset < MTLB_HEADER_SIZE or function_offset + function_size >= len(data):
        raise ValueError("invalid function-list bounds")
    cursor = function_offset
    count = u32(data, cursor)
    cursor += 4
    records: list[dict[str, object]] = []
    for index in range(count):
        group_start = cursor
        group_size = u32(data, cursor)
        cursor += 4
        tags: dict[str, tuple[int, int]] = {}
        while True:
            name_bytes = bytes(data[cursor : cursor + 4])
            cursor += 4
            try:
                name = name_bytes.decode("ascii")
            except UnicodeDecodeError as error:
                raise ValueError(f"function {index}: invalid tag name") from error
            if name == "ENDT":
                break
            size = u16(data, cursor)
            cursor += 2
            if name in tags:
                raise ValueError(f"function {index}: duplicate {name} tag")
            tags[name] = (cursor, size)
            cursor += size
        if cursor - group_start != group_size:
            raise ValueError(f"function {index}: tag-group size mismatch")
        required = {"NAME", "HASH", "OFFT", "MDSZ"}
        if not required.issubset(tags):
            raise ValueError(f"function {index}: incomplete function record")
        if tags["HASH"][1] != 32 or tags["OFFT"][1] != 24 or tags["MDSZ"][1] != 8:
            raise ValueError(f"function {index}: unexpected fixed tag size")
        if "TYPE" in tags and tags["TYPE"][1] != 1:
            raise ValueError(f"function {index}: unexpected TYPE tag size")
        if "VERS" in tags and tags["VERS"][1] != 8:
            raise ValueError(f"function {index}: unexpected VERS tag size")
        name_offset, name_size = tags["NAME"]
        name_bytes = bytes(data[name_offset : name_offset + name_size])
        if not name_bytes.endswith(b"\0"):
            raise ValueError(f"function {index}: NAME is not NUL-terminated")
        function_type = None
        if "TYPE" in tags:
            function_type = data[tags["TYPE"][0]]
        versions = None
        if "VERS" in tags:
            versions = struct.unpack_from("<HHHH", data, tags["VERS"][0])
        records.append({
            "index": index,
            "name": name_bytes[:-1].decode("utf-8"),
            "tags": tags,
            "function_type": function_type,
            "function_type_name": FUNCTION_TYPES.get(
                function_type, "unknown" if function_type is not None else None
            ),
            "air_version": list(versions[:2]) if versions else None,
            "language_version": list(versions[2:]) if versions else None,
            "bitcode_offset": u64(data, tags["OFFT"][0] + 16),
            "bitcode_size": u64(data, tags["MDSZ"][0]),
        })
    # The header's function-list size excludes the initial UInt32 count but
    # includes every group.  Therefore the cursor ends four bytes after
    # function_offset + function_size, with the final group's ENDT occupying
    # the four bytes at that advertised end position.
    if cursor != function_offset + function_size + 4:
        raise ValueError("function-list size does not end after final group")
    if bytes(data[cursor - 4 : cursor]) != b"ENDT":
        raise ValueError("function-list ENDT marker is missing")
    return records


def parse_header_extension_tags(data: bytearray) -> list[tuple[str, int, int]]:
    function_end = u64(data, 24) + u64(data, 32) + 4
    public_offset = u64(data, 40)
    if function_end == public_offset:
        return []
    if function_end > public_offset:
        raise ValueError("header extension overlaps public metadata")
    tags: list[tuple[str, int, int]] = []
    cursor = function_end
    while cursor < public_offset:
        name = bytes(data[cursor : cursor + 4]).decode("ascii")
        cursor += 4
        if name == "ENDT":
            if cursor != public_offset:
                raise ValueError("unexpected bytes after header extension ENDT")
            return tags
        size = u16(data, cursor)
        cursor += 2
        tags.append((name, cursor, size))
        cursor += size
    raise ValueError("header extension has no ENDT marker")


def analyze_air_assembly(text: str, name: str) -> dict[str, object]:
    """Return the target and stable ABI vocabulary declared by one AIR module.

    This mirrors the useful boundary in reims-vgpu/metal2vulkan: metadata and
    stable AIR/LLVM symbols describe the shader contract; a function name does
    not.  The report is intentionally read-only and never decides whether a
    transform is safe.
    """
    target_matches = list(AIR_TARGET_LINE.finditer(text))
    if len(target_matches) != 1:
        raise ValueError(
            f"{name}: expected exactly one AIR target triple, "
            f"found {len(target_matches)}"
        )
    datalayout_matches = list(AIR_DATALAYOUT_LINE.finditer(text))
    if len(datalayout_matches) > 1:
        raise ValueError(
            f"{name}: expected at most one AIR datalayout, "
            f"found {len(datalayout_matches)}"
        )
    return {
        "target_triple": target_matches[0].group("triple"),
        "datalayout": (
            datalayout_matches[0].group("layout")
            if datalayout_matches else None
        ),
        "stage_metadata": [
            stage for stage, pattern in AIR_STAGE_METADATA.items()
            if pattern.search(text)
        ],
        "abi_symbols": sorted({
            match.group("symbol") for match in AIR_ABI_SYMBOL.finditer(text)
        }),
        "abi_metadata_keys": sorted({
            match.group("key")
            for match in AIR_ABI_METADATA_KEY.finditer(text)
        }),
        "function_constants": parse_function_constants(text),
    }


def parse_function_constants(text: str) -> list[dict[str, object]]:
    """Read stable AIR function-constant initializer globals.

    Apple's AIR ABI emits one module-scope symbol named
    ``<base>.MTL_FC_INIT_<index>_<type>`` in the ``air.fc_initializer``
    section. This is the same structural marker consumed by metal2vulkan;
    shader names and observed workload names are deliberately irrelevant.
    """
    constants: dict[int, dict[str, object]] = {}
    for line in text.splitlines():
        stripped = line.lstrip()
        if (not stripped.startswith("@") or
                ".MTL_FC_INIT_" not in stripped or
                'section "air.fc_initializer"' not in stripped):
            continue
        declaration_parts = stripped.split(" = ", 1)
        if len(declaration_parts) != 2:
            continue
        symbol, declaration = declaration_parts
        marker_parts = symbol[1:].split(".MTL_FC_INIT_", 1)
        if len(marker_parts) != 2:
            continue
        marker = marker_parts[1]
        # Only the leading run is the index; ABI encodings may also contain
        # digits (for example Dv4_j).
        leading_digits = re.match(r"[0-9]+", marker)
        if not leading_digits:
            continue
        index_text = leading_digits.group(0)
        index = int(index_text)
        suffix = marker[len(index_text):]
        abi_type = suffix[1:] if suffix.startswith("_") else ""
        type_name = ""
        for keyword in (" constant ", " global "):
            if keyword not in declaration:
                continue
            remainder = declaration.split(keyword, 1)[1].lstrip()
            if remainder.startswith("<") and ">" in remainder:
                type_name = remainder[:remainder.index(">") + 1]
            elif remainder:
                type_name = remainder.split(None, 1)[0]
            break
        constants.setdefault(index, {
            "index": index,
            "name": marker_parts[0],
            "type": type_name,
            "abi_type": abi_type,
        })
    return [constants[index] for index in sorted(constants)]


def rewrite_fract_v3f16(text: str, name: str) -> str:
    """Lower unsupported half3 fract calls without weakening AGX.

    Ventura QuartzCore's fixed_frag_lph_cpf contains one
    air.fract.v3f16(x).  The iOS 16.3 AGX compiler renames that declaration
    to agx.air.fract.v3f16.fast but has no matching lowering-table entry.
    The compiler's implemented regular fract lowering is x - floor(x), so
    express that same operation directly in AIR and leave verifier/codegen
    validation enabled.
    """
    call_pattern = re.compile(
        r"^(?P<indent>\s*)(?P<result>%[-a-zA-Z$._0-9]+) = "
        r"tail call fast <3 x half> @air\.fract\.v3f16\("
        r"<3 x half> (?P<argument>%[-a-zA-Z$._0-9]+)\) "
        r"(?P<attributes>#[0-9]+)$",
        re.MULTILINE,
    )

    if "%macws.fract.v3f16.floor" in text:
        raise ValueError(f"{name}: reserved fract lowering name already exists")
    call_index = 0

    def replace_call(match: re.Match[str]) -> str:
        nonlocal call_index
        indent = match.group("indent")
        result = match.group("result")
        argument = match.group("argument")
        attributes = match.group("attributes")
        floor_result = "%macws.fract.v3f16.floor"
        if call_index:
            floor_result += f".{call_index}"
        call_index += 1
        return (
            f"{indent}{floor_result} = tail call fast <3 x half> "
            f"@air.floor.v3f16(<3 x half> {argument}) {attributes}\n"
            f"{indent}{result} = fsub fast <3 x half> {argument}, "
            f"{floor_result}"
        )

    text, call_count = call_pattern.subn(replace_call, text)
    declaration_pattern = re.compile(
        r"^declare <3 x half> @air\.fract\.v3f16\(<3 x half>\) "
        r"local_unnamed_addr (?P<attributes>#[0-9]+)$",
        re.MULTILINE,
    )
    text, declaration_count = declaration_pattern.subn(
        r"declare <3 x half> @air.floor.v3f16(<3 x half>) "
        r"local_unnamed_addr \g<attributes>",
        text,
    )
    if call_count < 1 or declaration_count != 1:
        raise ValueError(
            f"{name}: expected one or more fract.v3f16 calls and exactly "
            "one declaration, "
            f"found {call_count}/{declaration_count}"
        )
    if "@air.fract.v3f16" in text:
        raise ValueError(f"{name}: residual fract.v3f16 reference")
    return text


def lower_zero_memset_24(text: str, name: str) -> str:
    """Lower fixed 24-byte zero memsets to ordinary AIR stores.

    Ventura's Metal frontend emits this LLVM intrinsic while copying a
    three-element float2 varying array.  The iOS 16.3 AGX compiler accepts the
    surrounding AIR but returns an internal compiler error for the intrinsic.
    Three aligned i64 stores are byte-for-byte equivalent and remain subject
    to the normal AIR verifier and AGX code generator.
    """
    if "%macws.memset." in text:
        raise ValueError(f"{name}: reserved memset lowering name already exists")
    call_pattern = re.compile(
        r"^(?P<indent>\s*)call void @llvm\.memset\.p0i8\.i64\("
        r"i8\* nonnull align 8 dereferenceable\(24\) "
        r"(?P<pointer>%[-a-zA-Z$._0-9]+), i8 0, i64 24, i1 false\)$",
        re.MULTILINE,
    )

    call_index = 0

    def lowered_name(kind: str, word_index: int, lowering_index: int) -> str:
        if lowering_index == 0:
            return f"%macws.memset.{kind}.{word_index}"
        return f"%macws.memset.{kind}.{lowering_index}.{word_index}"

    def replace_call(match: re.Match[str]) -> str:
        nonlocal call_index
        indent = match.group("indent")
        pointer = match.group("pointer")
        lowering_index = call_index
        call_index += 1
        lines = []
        for index, offset in enumerate((0, 8, 16)):
            byte_pointer = pointer
            if offset:
                byte_pointer = lowered_name(
                    "byte", index, lowering_index
                )
                lines.append(
                    f"{indent}{byte_pointer} = getelementptr inbounds i8, "
                    f"i8* {pointer}, i64 {offset}"
                )
            word_pointer = lowered_name("word", index, lowering_index)
            lines.append(
                f"{indent}{word_pointer} = bitcast i8* {byte_pointer} to i64*"
            )
            lines.append(
                f"{indent}store i64 0, i64* {word_pointer}, align 8"
            )
        return "\n".join(lines)

    text, typed_call_count = call_pattern.subn(replace_call, text)

    # LLVM 17+ prints the same AIR intrinsic with opaque pointers.  Metal
    # 32023.35 emits both spellings across Stray's cached shader set, so keep
    # the semantic lowering identical while emitting valid opaque-pointer IR.
    opaque_call_pattern = re.compile(
        r"^(?P<indent>\s*)call void @llvm\.memset\.p0\.i64\("
        r"ptr nonnull align 8 dereferenceable\(24\) "
        r"(?P<pointer>%[-a-zA-Z$._0-9]+), i8 0, i64 24, i1 false\)$",
        re.MULTILINE,
    )

    def replace_opaque_call(match: re.Match[str]) -> str:
        nonlocal call_index
        indent = match.group("indent")
        pointer = match.group("pointer")
        lowering_index = call_index
        call_index += 1
        lines = []
        for index, offset in enumerate((0, 8, 16)):
            word_pointer = pointer
            if offset:
                word_pointer = lowered_name(
                    "word", index, lowering_index
                )
                lines.append(
                    f"{indent}{word_pointer} = getelementptr inbounds i8, "
                    f"ptr {pointer}, i64 {offset}"
                )
            lines.append(
                f"{indent}store i64 0, ptr {word_pointer}, align 8"
            )
        return "\n".join(lines)

    text, opaque_call_count = opaque_call_pattern.subn(
        replace_opaque_call, text
    )
    declaration_pattern = re.compile(
        r"^declare void @llvm\.memset\.p0i8\.i64\(i8\* nocapture "
        r"writeonly, i8, i64, i1 immarg\) #[0-9]+\n?",
        re.MULTILINE,
    )
    text, typed_declaration_count = declaration_pattern.subn("", text)
    opaque_declaration_pattern = re.compile(
        r"^declare void @llvm\.memset\.p0\.i64\(ptr writeonly "
        r"captures\(none\), i8, i64, i1 immarg\) #[0-9]+\n?",
        re.MULTILINE,
    )
    text, opaque_declaration_count = opaque_declaration_pattern.subn(
        "", text
    )
    call_count = typed_call_count + opaque_call_count
    declaration_count = (
        typed_declaration_count + opaque_declaration_count
    )
    if call_count < 1 or declaration_count < 1:
        raise ValueError(
            f"{name}: expected one or more fixed zero memset calls and "
            "at least one matching declaration, "
            f"found {call_count}/{declaration_count}"
        )
    if ("@llvm.memset.p0i8.i64" in text or
            "@llvm.memset.p0.i64" in text):
        raise ValueError(f"{name}: residual llvm.memset reference")
    return text


KNOWN_AIR_LOWERINGS = (
    {
        "name": "fract-v3f16",
        "detector": re.compile(
            r"\bcall\b[^\n]*@air\.fract\.v3f16\("
        ),
        "apply": rewrite_fract_v3f16,
    },
    {
        "name": "zero-memset-24",
        "detector": re.compile(
            r"\bcall\b[^\n]*@llvm\.memset\.p0(?:i8)?\.i64\("
            r"[^\n]*i64 24, i1 false\)"
        ),
        "apply": lower_zero_memset_24,
    },
)


def apply_air_lowerings(
    text: str,
    name: str,
    explicitly_requested: set[str],
    auto_lower_known_air: bool,
) -> tuple[str, list[str]]:
    """Apply semantic lowerings selected by stable IR shape, never by name."""
    known_names = {
        str(lowering["name"]) for lowering in KNOWN_AIR_LOWERINGS
    }
    unknown_names = explicitly_requested - known_names
    if unknown_names:
        raise ValueError(
            f"{name}: unknown AIR lowering(s): " +
            ", ".join(sorted(unknown_names))
        )
    selected = set(explicitly_requested)
    if auto_lower_known_air:
        for lowering in KNOWN_AIR_LOWERINGS:
            detector = lowering["detector"]
            assert isinstance(detector, re.Pattern)
            if detector.search(text):
                selected.add(str(lowering["name"]))

    applied: list[str] = []
    for lowering in KNOWN_AIR_LOWERINGS:
        lowering_name = str(lowering["name"])
        if lowering_name not in selected:
            continue
        apply = lowering["apply"]
        text = apply(text, name)
        applied.append(lowering_name)
    return text, applied


def scratch_stem(name: str) -> str:
    readable = re.sub(r"[^-a-zA-Z0-9._]", "_", name)[:80] or "module"
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]
    return f"{readable}-{digest}"


def inspect_air(
    module: bytes,
    name: str,
    llvm_dis: pathlib.Path,
    scratch: pathlib.Path,
) -> dict[str, object]:
    stem = scratch_stem(name)
    source = scratch / f"{stem}.inspect.air"
    assembly = scratch / f"{stem}.inspect.ll"
    source.write_bytes(module)
    subprocess.run([llvm_dis, source, "-o", assembly], check=True)
    return analyze_air_assembly(
        assembly.read_text(encoding="utf-8"), name
    )


def saturate_half_float_texture_writes(text: str, name: str) -> str:
    """Build a macOS-compatible variant for half-float texture bindings.

    macOS Metal converts finite float values beyond the binary16 range to
    +/-65504 when a float write intrinsic targets an R/RG/RGBA16Float
    texture.  iOS Metal converts the same value to infinity.  Pixel format is
    a runtime binding property, so this rewrite is intentionally a *variant*:
    the runtime must select it only when every rewritten writable texture is
    actually a half-float format.  R32/RG32/RGBA32 bindings must keep the
    ordinary translated function.
    """
    if "@air.fast_clamp.f32" in text:
        declaration_present = re.search(
            r"^declare float @air\.fast_clamp\.f32\(",
            text, re.MULTILINE,
        ) is not None
    else:
        declaration_present = False
    attribute = re.search(
        r"^attributes #(\d+) = \{ "
        r"(?=[^}]*\bnounwind\b)"
        r"(?=[^}]*(?:memory\(none\)|\breadnone\b))[^}]*\}$",
        text, re.MULTILINE,
    )
    if attribute:
        attribute_number = attribute.group(1)
        clamp_attribute_syntax = None
    else:
        attribute_definitions = list(re.finditer(
            r"^attributes #(\d+) = \{[^\n]*\}$", text, re.MULTILINE
        ))
        if not attribute_definitions:
            raise ValueError(f"{name}: AIR attribute table is missing")
        attribute_number = str(max(
            int(match.group(1)) for match in attribute_definitions
        ) + 1)
        clamp_attribute_syntax = (
            "nounwind memory(none)" if "memory(" in text
            else "nounwind readnone"
        )
        insert_offset = attribute_definitions[-1].end()
        text = (
            text[:insert_offset] +
            f"\nattributes #{attribute_number} = "
            f"{{ {clamp_attribute_syntax} }}" +
            text[insert_offset:]
        )
    pattern = re.compile(
        r"^(?P<indent>\s*)(?P<prefix>(?:tail )?call void "
        r"@air\.write_texture_[123]d\.v4f32\(.*"
        r"<4 x float> )(?P<value>%[-a-zA-Z$._0-9]+|<float [^>]+>)"
        r"(?P<suffix>,.*)$",
        re.MULTILINE,
    )
    sequence = 0

    def replace_write(match: re.Match[str]) -> str:
        nonlocal sequence
        value = match.group("value")
        write_sequence = sequence
        sequence += 1
        lines = []
        aggregate = "undef"
        for lane in range(4):
            extracted = f"%macws.half.write.extract.{write_sequence}.{lane}"
            clamped = f"%macws.half.write.clamp.{write_sequence}.{lane}"
            inserted = f"%macws.half.write.value.{write_sequence}.{lane}"
            lines.append(
                f"{match.group('indent')}{extracted} = extractelement "
                f"<4 x float> {value}, i64 {lane}"
            )
            lines.append(
                f"{match.group('indent')}{clamped} = tail call fast float "
                f"@air.fast_clamp.f32(float {extracted}, "
                f"float -6.550400e+04, float 6.550400e+04) "
                f"#{attribute_number}"
            )
            lines.append(
                f"{match.group('indent')}{inserted} = insertelement "
                f"<4 x float> {aggregate}, float {clamped}, i64 {lane}"
            )
            aggregate = inserted
        write = (
            f"{match.group('indent')}{match.group('prefix')}{aggregate}"
            f"{match.group('suffix')}"
        )
        lines.append(write)
        return "\n".join(lines)

    text = pattern.sub(replace_write, text)
    if sequence == 0:
        raise ValueError(
            f"{name}: no float texture write intrinsic for half variant"
        )
    if not declaration_present:
        attribute_description = (
            clamp_attribute_syntax or "nounwind memory(none)"
        )
        declaration = (
            f"; Function Attrs: {attribute_description}\n"
            "declare float @air.fast_clamp.f32(float, float, float) "
            f"local_unnamed_addr #{attribute_number}\n\n"
        )
        attributes_offset = text.find("attributes #")
        if attributes_offset < 0:
            raise ValueError(f"{name}: AIR attribute table is missing")
        text = text[:attributes_offset] + declaration + text[attributes_offset:]
    return text


def retarget_air(
    module: bytes,
    name: str,
    llvm_dis: pathlib.Path,
    llvm_as: pathlib.Path,
    target_triple: str,
    scratch: pathlib.Path,
    lower_fract_v3f16: bool,
    lower_zero_memset: bool,
    auto_lower_known_air: bool,
    saturate_half_float_writes: bool,
) -> tuple[bytes, dict[str, object]]:
    stem = scratch_stem(name)
    source = scratch / f"{stem}.input.air"
    assembly = scratch / f"{stem}.ll"
    output = scratch / f"{stem}.output.air"
    source.write_bytes(module)
    subprocess.run([llvm_dis, source, "-o", assembly], check=True)
    text = assembly.read_text(encoding="utf-8")
    analysis = analyze_air_assembly(text, name)
    text, count = AIR_TARGET_LINE.subn(
        f'target triple = "{target_triple}"', text
    )
    if count != 1:
        raise ValueError(
            f"{name}: expected one iOS/macOS AIR target triple, found {count}"
        )
    requested_lowerings = set()
    if lower_fract_v3f16:
        requested_lowerings.add("fract-v3f16")
    if lower_zero_memset:
        requested_lowerings.add("zero-memset-24")
    text, applied_lowerings = apply_air_lowerings(
        text,
        name,
        requested_lowerings,
        auto_lower_known_air,
    )
    if saturate_half_float_writes:
        text = saturate_half_float_texture_writes(text, name)
        applied_lowerings.append("half-float-write-saturation")
    assembly.write_text(text, encoding="utf-8")
    subprocess.run([llvm_as, assembly, "-o", output], check=True)
    rebuilt = output.read_bytes()
    if rebuilt[:4] != b"\xde\xc0\x17\x0b":
        raise ValueError(f"{name}: llvm-as did not emit wrapped LLVM bitcode")
    padding = (-len(rebuilt)) % 16
    analysis["output_target_triple"] = target_triple
    analysis["applied_lowerings"] = applied_lowerings
    return rebuilt + b"\0" * padding, analysis


def convert(args: argparse.Namespace) -> None:
    profile = get_profile(args.profile)
    if args.target_triple is None:
        args.target_triple = profile.target_triple
    if args.container_target is None:
        args.container_target = profile.container_target
    if args.target_major is None:
        args.target_major = profile.target_major
    if args.target_minor is None:
        args.target_minor = profile.target_minor
    runtime_manifest_fields = (
        args.runtime_manifest,
        args.runtime_source_path,
        args.runtime_output_path,
    )
    if any(runtime_manifest_fields) and not all(runtime_manifest_fields):
        raise ValueError(
            "--runtime-manifest, --runtime-source-path and "
            "--runtime-output-path must be supplied together"
        )
    if args.runtime_manifest and args.in_place_air_target:
        raise ValueError(
            "runtime manifests require decoded AIR metadata; "
            "--in-place-air-target is target-only"
        )
    if args.runtime_manifest and args.function:
        raise ValueError(
            "runtime routing manifests require complete-library translation; "
            "--function is accepted only for diagnostics and offline assets"
        )
    if args.runtime_manifest and (
        args.target_triple != profile.target_triple or
        args.container_target != profile.container_target or
        args.target_major != profile.target_major or
        args.target_minor != profile.target_minor
    ):
        raise ValueError(
            "runtime routing manifests must use the selected profile without "
            "target overrides"
        )
    input_path = pathlib.Path(args.input)
    output_path = pathlib.Path(args.output)
    original = input_path.read_bytes()
    if len(original) < MTLB_HEADER_SIZE or original[:4] != b"MTLB":
        raise ValueError("input is not an MTLB container")
    input_platform = u16(original, 4)
    input_file_version = (u16(original, 6), u16(original, 8))
    input_target = (input_platform, original[11])
    legacy_unknown_target = (
        input_platform in {IOS_PLATFORM, MACOS_PLATFORM}
        and original[11] == UNKNOWN_TARGET_OS
        and input_file_version == (2, 3)
    )
    if input_target not in {
        (IOS_PLATFORM, IOS_TARGET_OS),
        (MACOS_PLATFORM, MACOS_TARGET_OS),
        (MACOS_PLATFORM, MACABI_TARGET_OS),
    } and not legacy_unknown_target:
        raise ValueError(
            "input must be an iOS, macOS, or Mac Catalyst executable MTLB"
        )
    if original[10] != 0:
        raise ValueError("input must be an executable MTLB")
    if (not args.preserve_container_target and
            not target_triple_matches_container(
                args.target_triple, args.container_target
            )):
        raise ValueError(
            f"AIR target {args.target_triple!r} does not match "
            f"MTLB container target {args.container_target!r}"
        )
    if u64(original, 16) != len(original):
        raise ValueError("input file-size field is invalid")
    bitcode_offset = u64(original, 72)
    bitcode_size = u64(original, 80)
    bitcode_end = bitcode_offset + bitcode_size
    if bitcode_offset < MTLB_HEADER_SIZE or bitcode_end > len(original):
        raise ValueError("input bitcode-section bounds are invalid")

    selected_names = set(args.function or [])
    fract_rewrite_names = set(args.rewrite_fract_v3f16_function or [])
    zero_memset_names = set(args.lower_zero_memset_function or [])
    half_float_write_names = set(
        args.saturate_half_float_texture_writes_function or []
    )
    if args.in_place_air_target:
        if not legacy_unknown_target:
            raise ValueError(
                "--in-place-air-target currently accepts only the validated "
                "legacy 2.3/unknown-target MTLB format"
            )
        if (args.preserve_container_target or selected_names or
                fract_rewrite_names or zero_memset_names or
                args.auto_lower_known_air or half_float_write_names):
            raise ValueError(
                "--in-place-air-target cannot be combined with selective "
                "AIR conversion, semantic lowering, or "
                "--preserve-container-target"
            )
    if (args.preserve_container_target and not selected_names
            and not legacy_unknown_target):
        raise ValueError(
            "--preserve-container-target without --function is accepted only "
            "for the validated legacy 2.3/unknown-target MTLB format"
        )
    if selected_names and not fract_rewrite_names.issubset(selected_names):
        raise ValueError(
            "--rewrite-fract-v3f16-function must also be selected by "
            "--function"
        )
    if selected_names and not zero_memset_names.issubset(selected_names):
        raise ValueError(
            "--lower-zero-memset-function must also be selected by "
            "--function"
        )
    if (selected_names and
            not half_float_write_names.issubset(selected_names)):
        raise ValueError(
            "--saturate-half-float-texture-writes-function must also be "
            "selected by --function"
        )

    rebuilt = bytearray(original[:bitcode_offset])
    records = parse_function_records(rebuilt)
    if legacy_unknown_target:
        # Metal 902.1 emits MTLB 2.3 containers whose target-OS header byte is
        # zero.  In that format the authoritative target is the AIR triple in
        # every module.  Fail closed unless all modules agree with the header's
        # platform; this keeps an arbitrary unknown-target container from being
        # promoted merely because it happens to use the old header version.
        expected_triple = (b"air64-apple-macosx" if
                           input_platform == MACOS_PLATFORM else
                           b"air64-apple-ios")
        for record in records:
            module_offset = int(record["bitcode_offset"])
            module_size = int(record["bitcode_size"])
            module = original[
                bitcode_offset + module_offset:
                bitcode_offset + module_offset + module_size
            ]
            if expected_triple not in module:
                raise ValueError(
                    f"{record['name']}: legacy MTLB AIR target does not match "
                    "the container platform"
                )
    available_names = {str(record["name"]) for record in records}
    missing_names = selected_names - available_names
    if missing_names:
        raise ValueError(
            "requested function(s) not present: " + ", ".join(sorted(missing_names))
        )
    next_bitcode_offset = 0
    rebuilt_modules: list[bytes] = []
    function_reports: list[dict[str, object]] = []
    converted_count = 0
    lowering_count = 0
    with tempfile.TemporaryDirectory(prefix="macws-macabi-mtlb-") as temp:
        scratch = pathlib.Path(temp)
        for record in records:
            module_offset = int(record["bitcode_offset"])
            module_size = int(record["bitcode_size"])
            module = original[
                bitcode_offset + module_offset :
                bitcode_offset + module_offset + module_size
            ]
            tags = record["tags"]
            assert isinstance(tags, dict)
            hash_offset = tags["HASH"][0]
            if hashlib.sha256(module).digest() != original[hash_offset : hash_offset + 32]:
                raise ValueError(f"{record['name']}: input SHA-256 mismatch")
            logical_name = f"{record['index']}-{record['name']}"
            selected = (
                args.in_place_air_target or not selected_names or
                str(record["name"]) in selected_names
            )
            analysis: dict[str, object] | None = None
            if args.in_place_air_target:
                # Keep Apple's AIR record layout byte-for-byte. Upstream
                # llvm-as
                # emits records (for example ATTR_KIND_CAPTURES=102) that the
                # iOS 16 Apple LLVM reader cannot decode, even when the IR is
                # otherwise unchanged. A same-length target replacement
                # changes only the original string payload; hashes and the
                # outer MTLB target are rebuilt normally below.
                converted = retarget_air_in_place(
                    module,
                    logical_name,
                    args.target_triple,
                )
                target_matches = list(AIR_TARGET_BYTES.finditer(module))
                analysis = {
                    "target_triple": target_matches[0].group().decode("ascii"),
                    "output_target_triple": args.target_triple,
                    "datalayout": None,
                    "stage_metadata": [],
                    "abi_symbols": [],
                    "abi_metadata_keys": [],
                    "function_constants": [],
                    "applied_lowerings": [],
                    "inspection": "in-place-target-only",
                }
                converted_count += 1
            elif not selected_names or str(record["name"]) in selected_names:
                converted, analysis = retarget_air(
                    module,
                    logical_name,
                    pathlib.Path(args.llvm_dis),
                    pathlib.Path(args.llvm_as),
                    args.target_triple,
                    scratch,
                    str(record["name"]) in fract_rewrite_names,
                    str(record["name"]) in zero_memset_names,
                    args.auto_lower_known_air,
                    str(record["name"]) in half_float_write_names,
                )
                applied = analysis["applied_lowerings"]
                assert isinstance(applied, list)
                lowering_count += len(applied)
                converted_count += 1
            else:
                converted = module
                if args.abi_report:
                    analysis = inspect_air(
                        module,
                        logical_name,
                        pathlib.Path(args.llvm_dis),
                        scratch,
                    )
                    analysis["output_target_triple"] = analysis[
                        "target_triple"
                    ]
                    analysis["applied_lowerings"] = []
            function_report = {
                "index": record["index"],
                "name": record["name"],
                "function_type": record["function_type"],
                "function_type_name": record["function_type_name"],
                "air_version": record["air_version"],
                "language_version": record["language_version"],
                "selected": selected,
                "input_size": len(module),
                "input_sha256": hashlib.sha256(module).hexdigest(),
                "output_size": len(converted),
                "output_sha256": hashlib.sha256(converted).hexdigest(),
            }
            if analysis:
                function_report.update(analysis)
                expected_stage = record["function_type_name"]
                stages = analysis.get("stage_metadata", [])
                if expected_stage in AIR_STAGE_METADATA and stages:
                    function_report["stage_contract_matches"] = (
                        expected_stage in stages
                    )
                else:
                    # Absence of stage metadata is not evidence of a match.
                    function_report["stage_contract_matches"] = None
            function_reports.append(function_report)
            struct.pack_into("<Q", rebuilt, tags["MDSZ"][0], len(converted))
            rebuilt[hash_offset : hash_offset + 32] = hashlib.sha256(converted).digest()
            struct.pack_into("<Q", rebuilt, tags["OFFT"][0] + 16,
                             next_bitcode_offset)
            rebuilt_modules.append(converted)
            next_bitcode_offset += len(converted)

    new_bitcode = b"".join(rebuilt_modules)
    delta = len(new_bitcode) - bitcode_size
    rebuilt.extend(new_bitcode)
    rebuilt.extend(original[bitcode_end:])
    if not args.preserve_container_target:
        output_platform, output_target_os = OUTPUT_CONTAINER_TARGETS[
            args.container_target
        ]
        struct.pack_into("<H", rebuilt, 4, output_platform)
        if legacy_unknown_target:
            # MTLB 2.3 predates the explicit target-OS header field.  Merely
            # writing macabi into byte 11 leaves iOS Metal rejecting the whole
            # archive as an old tool format before it inspects a function.
            # The tag/section layout above has already been fully parsed and
            # rebuilt, so advertise the modern executable-container revision
            # alongside the now-explicit macabi target.
            struct.pack_into("<HH", rebuilt, 6,
                             *CURRENT_MTLB_FILE_VERSION)
        rebuilt[11] = output_target_os
        struct.pack_into("<H", rebuilt, 12, args.target_major)
        struct.pack_into("<H", rebuilt, 14, args.target_minor)
    struct.pack_into("<Q", rebuilt, 16, len(rebuilt))
    struct.pack_into("<Q", rebuilt, 80, len(new_bitcode))

    for name, content_offset, size in parse_header_extension_tags(rebuilt):
        if name not in OFFSET_SECTION_TAGS:
            continue
        if size != 16:
            raise ValueError(f"{name}: expected offset/size pair")
        offset = u64(rebuilt, content_offset)
        if offset >= bitcode_end:
            struct.pack_into("<Q", rebuilt, content_offset, offset + delta)

    # Reparse the final function list and verify all rebuilt module hashes.
    final_records = parse_function_records(rebuilt)
    for record in final_records:
        tags = record["tags"]
        assert isinstance(tags, dict)
        offset = int(record["bitcode_offset"])
        size = int(record["bitcode_size"])
        module = rebuilt[bitcode_offset + offset : bitcode_offset + offset + size]
        if hashlib.sha256(module).digest() != rebuilt[
            tags["HASH"][0] : tags["HASH"][0] + 32
        ]:
            raise ValueError(f"{record['name']}: output SHA-256 mismatch")
    output_path.write_bytes(rebuilt)
    if args.runtime_manifest:
        runtime_manifest = build_runtime_manifest(
            source=original,
            output=bytes(rebuilt),
            source_runtime_path=args.runtime_source_path,
            output_runtime_path=args.runtime_output_path,
            profile=args.profile,
            function_reports=function_reports,
        )
        write_runtime_manifest(
            pathlib.Path(args.runtime_manifest), runtime_manifest
        )
    if args.abi_report:
        report_path = pathlib.Path(args.abi_report)
        report = {
            "abi_report_version": ABI_REPORT_VERSION,
            "input": {
                "path": str(input_path),
                "size": len(original),
                "sha256": hashlib.sha256(original).hexdigest(),
                "platform": input_platform,
                "file_version": list(input_file_version),
                "target_os": original[11],
            },
            "output": {
                "path": str(output_path),
                "size": len(rebuilt),
                "sha256": hashlib.sha256(rebuilt).hexdigest(),
                "platform": u16(rebuilt, 4),
                "file_version": [u16(rebuilt, 6), u16(rebuilt, 8)],
                "target_os": rebuilt[11],
            },
            "requested_target_triple": args.target_triple,
            "container_target": (
                "preserved" if args.preserve_container_target
                else args.container_target
            ),
            "converted_functions": converted_count,
            "applied_lowerings": lowering_count,
            "functions": function_reports,
        }
        report_path.write_text(
            json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) +
            "\n",
            encoding="utf-8",
        )
    print(
        f"converted={converted_count}/{len(final_records)} "
        f"lowerings={lowering_count} "
        f"target={args.target_triple} "
        f"container_target={'preserved' if args.preserve_container_target else args.container_target} "
        f"bitcode={bitcode_size}->{len(new_bitcode)} bytes "
        f"file={len(original)}->{len(rebuilt)} bytes output={output_path}" +
        (f" report={args.abi_report}" if args.abi_report else "") +
        (f" runtime_manifest={args.runtime_manifest}"
         if args.runtime_manifest else "")
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("output")
    parser.add_argument("--llvm-dis", default="/opt/homebrew/opt/llvm/bin/llvm-dis")
    parser.add_argument("--llvm-as", default="/opt/homebrew/opt/llvm/bin/llvm-as")
    parser.add_argument(
        "--profile", choices=sorted(PROFILES), default=DEFAULT_PROFILE
    )
    parser.add_argument("--target-triple")
    parser.add_argument("--target-major", type=int)
    parser.add_argument("--target-minor", type=int)
    parser.add_argument(
        "--in-place-air-target",
        action="store_true",
        help=(
            "for validated legacy 2.3 archives, replace a same-length AIR "
            "target string without re-encoding Apple's bitcode dialect"
        ),
    )
    parser.add_argument(
        "--container-target",
        choices=sorted(OUTPUT_CONTAINER_TARGETS),
        help=(
            "target encoded in the rebuilt MTLB header (default: profile); "
            "the AIR target triple must describe the same target"
        ),
    )
    parser.add_argument(
        "--function",
        action="append",
        help="retarget only this function (repeatable; default: every function)",
    )
    parser.add_argument(
        "--preserve-container-target",
        action="store_true",
        help="leave the MTLB container target unchanged for a selective rebuild",
    )
    parser.add_argument(
        "--auto-lower-known-air",
        action="store_true",
        help=(
            "apply every registered semantic lowering whose exact AIR/LLVM "
            "call shape occurs; unknown shapes fail closed"
        ),
    )
    parser.add_argument(
        "--abi-report",
        help=(
            "write a JSON inventory of every function's MTLB TYPE/VERS, "
            "AIR target/stage/datalayout/ABI keys and symbols, transforms, "
            "sizes and hashes"
        ),
    )
    parser.add_argument(
        "--runtime-manifest",
        help="write the versioned plist consumed by libmachook routing",
    )
    parser.add_argument(
        "--runtime-source-path",
        help="source MTLLibrary path as seen inside the macOS chroot",
    )
    parser.add_argument(
        "--runtime-output-path",
        help="companion MTLLibrary path as seen inside the macOS chroot",
    )
    parser.add_argument(
        "--rewrite-fract-v3f16-function",
        action="append",
        help=(
            "within this selected function, rewrite the exact unsupported "
            "air.fract.v3f16 call as x - air.floor.v3f16(x)"
        ),
    )
    parser.add_argument(
        "--lower-zero-memset-function",
        action="append",
        help=(
            "within this selected function, lower the exact fixed 24-byte "
            "zero memset to three verified i64 stores"
        ),
    )
    parser.add_argument(
        "--saturate-half-float-texture-writes-function",
        action="append",
        help=(
            "build a runtime-selectable variant whose float texture writes "
            "use macOS finite saturation for half-float bindings; repeatable"
        ),
    )
    convert(parser.parse_args(argv))


if __name__ == "__main__":
    main()

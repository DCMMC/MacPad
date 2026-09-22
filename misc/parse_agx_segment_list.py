"""Decode captured IOGPU segment/resource-list records.

Invoke with the interpreter because AMFI blocks shebang execution on the
target device:

    python3 misc/parse_agx_segment_list.py post_kcmd.bin post_segments.bin

The layout is taken from the runtime Objective-C type encoding in the actual
iOS 16.3 IOGPU image:

    IOGPUSegmentListHeader=QII[0{...=QIIIIII[0{...=[6I][6I][6S]SS}]}]

Names not present in that encoding remain deliberately neutral below.  The
last uint16 in every 0x40-byte group is treated as the valid-entry count only
after validating that it is <= 6 and that all group counts sum to the resource
count in the enclosing header.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import struct


def u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def u64(data: bytes, offset: int) -> int:
    return struct.unpack_from("<Q", data, offset)[0]


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Decode an IOGPU KCMD segment list captured by libmachook"
    )
    parser.add_argument("kcmd", type=Path)
    parser.add_argument("segments", type=Path)
    return parser.parse_args()


def flatten_type5_chain_for_decode(
    commands: bytes, segments: bytes
) -> tuple[bytes, bytes, str] | None:
    """Validate and flatten the observed two-stage macOS signal chain.

    This is an analysis-only mirror of libmachook's protocol translation.  It
    removes the two type-5 command/list wrappers and the repeated second list
    header so the existing direct-list decoder can inspect both preserved
    resource descriptors.  It deliberately does not perform either vendor
    record's macOS-to-iOS ABI shrink.
    """
    if len(commands) < 0x900 or len(segments) < 0x90:
        return None
    first_list_length = u32(segments, 0x0C)
    if first_list_length < 0x30:
        return None
    first_signal_offset = first_list_length
    second_list_offset = first_signal_offset + 0x18
    if second_list_offset + 0x30 + 0x18 > len(segments):
        return None
    second_list_length = u32(segments, second_list_offset + 0x0C)
    if second_list_length < 0x30:
        return None
    second_signal_offset = second_list_offset + second_list_length
    if second_signal_offset + 0x18 != len(segments):
        return None

    list_token = u64(segments, 0)
    first_start = u32(segments, 0x18)
    first_end = u32(segments, 0x1C)
    first_signal_start = u32(segments, first_signal_offset + 0x10)
    first_signal_end = u32(segments, first_signal_offset + 0x14)
    second_start = u32(segments, second_list_offset + 0x18)
    second_end = u32(segments, second_list_offset + 0x1C)
    second_signal_start = u32(segments, second_signal_offset + 0x10)
    second_signal_end = u32(segments, second_signal_offset + 0x14)
    if not (
        u32(segments, 8) == 1
        and u64(segments, first_signal_offset) == list_token
        and u32(segments, first_signal_offset + 8) == 1
        and u32(segments, first_signal_offset + 0x0C) == 0x40000001
        and u64(segments, second_list_offset) == list_token
        and u32(segments, second_list_offset + 8) == 1
        and u64(segments, second_signal_offset) == list_token
        and u32(segments, second_signal_offset + 8) == 1
        and u32(segments, second_signal_offset + 0x0C) == 0xC0000001
        and first_start == 0
        and first_signal_start == first_end
        and first_signal_end == first_end + 0x18
        and second_start == first_signal_end
        and second_signal_start == second_end
        and second_signal_end == len(commands)
        and second_end + 0x18 == len(commands)
    ):
        return None

    def signal_ok(
        offset: int, ordinal: int, prior_signal_bytes: tuple[int, ...]
    ) -> bool:
        return (
            u32(commands, offset) == 5
            and u32(commands, offset + 4) == 0x18
            and u32(commands, offset + 8) == ordinal
            and u32(commands, offset + 0x0C) in prior_signal_bytes
            and u32(commands, offset + 0x10) == 1
            and u32(commands, offset + 0x14) == 0
        )

    if not (
        u32(commands, 0) in (0x10000, 0x10001)
        and u32(commands, 4) == first_end
        and signal_ok(first_end, 1, (0,))
        and u32(commands, second_start) in (0x10000, 0x10001)
        and u32(commands, second_start + 4) == second_end - second_start
        and signal_ok(second_end, 2, (0, 0x18))
    ):
        return None

    first_entry = segments[0x10:first_list_length]
    second_entry = segments[
        second_list_offset + 0x10 : second_list_offset + second_list_length
    ]
    direct_commands = commands[:first_end] + commands[second_start:second_end]
    direct_segments = bytearray(segments[:0x10] + first_entry + second_entry)
    struct.pack_into("<I", direct_segments, 8, 2)
    struct.pack_into("<I", direct_segments, 0x0C, 0x80000000 | len(direct_segments))
    struct.pack_into("<II", direct_segments, 0x18, 0, first_end)
    second_entry_offset = first_list_length
    struct.pack_into(
        "<II",
        direct_segments,
        second_entry_offset + 8,
        first_end,
        len(direct_commands),
    )
    description = (
        "type5-chain "
        f"kcmd={len(commands):#x}->{len(direct_commands):#x} "
        f"list={len(segments):#x}->{len(direct_segments):#x} "
        "wrappers=ordinal-1,ordinal-2"
    )
    return direct_commands, bytes(direct_segments), description


def flatten_fragmented_lists_for_decode(
    commands: bytes, segments: bytes
) -> tuple[bytes, bytes, str] | None:
    """Flatten validated vendor/event fragments for offline decoding."""
    if len(commands) < 0x100 or len(segments) < 0x60:
        return None
    token = u64(segments, 0)
    entries: list[tuple[int, int, int, int]] = []
    list_cursor = 0
    command_cursor = 0
    fragment_count = 0
    signal_count = 0

    while list_cursor + 0x10 <= len(segments):
        if u64(segments, list_cursor) != token:
            return None
        count = u32(segments, list_cursor + 8)
        encoded = u32(segments, list_cursor + 0x0C)
        if encoded in (0x40000001, 0xC0000001):
            if count != 1 or list_cursor + 0x18 > len(segments):
                return None
            event_start = u32(segments, list_cursor + 0x10)
            event_end = u32(segments, list_cursor + 0x14)
            if not (
                event_start == command_cursor
                and event_end > event_start
                and event_end <= len(commands)
                and event_end - event_start <= 0x30
                and (event_end - event_start) % 0x18 == 0
            ):
                return None
            for event_offset in range(event_start, event_end, 0x18):
                event_type = u32(commands, event_offset)
                if not (
                    event_type in (3, 4, 5, 6, 10)
                    and u32(commands, event_offset + 4) == 0x18
                    and u32(commands, event_offset + 8) != 0
                    # RE-confirmed in mac_hooks.m from the macOS 13.4
                    # IOGPUMTLEvent producer: type-5 +0x0c is unwritten
                    # padding. _MTLSharedEvent type 3 writes zero there.
                    and (event_type != 3 or
                         u32(commands, event_offset + 0x0C) == 0)
                ):
                    return None
            signal_count += 1
            command_cursor = event_end
            list_cursor += 0x18
            continue
        terminal = bool(encoded & 0x80000000)
        chunk_length = encoded & 0x7FFFFFFF
        if count < 1 or count > 1024 or chunk_length < 0x30:
            return None
        if list_cursor + chunk_length > len(segments):
            return None

        entry_cursor = 0x10
        for _ in range(count):
            entry = list_cursor + entry_cursor
            if entry + 0x20 > list_cursor + chunk_length:
                return None
            resource_count = u32(segments, entry + 0x18)
            group_count = u32(segments, entry + 0x1C)
            maximum_groups = (
                list_cursor + chunk_length - entry - 0x20
            ) // 0x40
            if group_count > maximum_groups:
                return None
            entry_length = 0x20 + group_count * 0x40
            if entry + entry_length > list_cursor + chunk_length:
                return None
            valid_counts = [
                u16(segments, entry + 0x20 + group * 0x40 + 0x3E)
                for group in range(group_count)
            ]
            if any(valid > 6 for valid in valid_counts):
                return None
            if sum(valid_counts) != resource_count:
                return None
            start = u32(segments, entry + 8)
            end = u32(segments, entry + 0x0C)
            if not (
                start == command_cursor
                and end > start
                and end <= len(commands)
                and u32(commands, start) in (0x10000, 0x10001)
                and u32(commands, start + 4) == end - start
            ):
                return None
            entries.append((entry, entry_length, start, end))
            command_cursor = end
            entry_cursor += entry_length
        if entry_cursor != chunk_length:
            return None
        fragment_count += 1
        list_cursor += chunk_length

        if terminal:
            if list_cursor != len(segments):
                return None
            break

    # Stray r23 serial 390 is the minimal valid member of this framing:
    # event range, one vendor fragment, event range.  Its list walks exactly
    # to the 0x1e0-byte boundary and its KCMD ranges cover 0..0x338 without a
    # gap.  Requiring two vendor fragments made the offline decoder reject
    # that captured producer even though every structural invariant above had
    # passed.  This only changes offline evidence decoding; the runtime
    # translator continues to use its own validation.
    if not (
        fragment_count >= 1
        and signal_count >= 1
        and len(entries) >= 1
        and command_cursor == len(commands)
        and list_cursor == len(segments)
    ):
        return None

    command_parts: list[bytes] = []
    rewritten_ranges: list[tuple[int, int]] = []
    direct_command_cursor = 0
    for _, _, start, end in entries:
        command_parts.append(commands[start:end])
        rewritten_start = direct_command_cursor
        direct_command_cursor += end - start
        rewritten_ranges.append((rewritten_start, direct_command_cursor))
    direct_commands = b"".join(command_parts)

    direct_segments = bytearray(segments[:0x10])
    for (entry, entry_length, _, _), (start, end) in zip(
        entries, rewritten_ranges
    ):
        direct_entry = bytearray(segments[entry : entry + entry_length])
        struct.pack_into("<II", direct_entry, 8, start, end)
        direct_segments.extend(direct_entry)
    struct.pack_into("<I", direct_segments, 8, len(entries))
    struct.pack_into(
        "<I", direct_segments, 0x0C, 0x80000000 | len(direct_segments)
    )
    description = (
        f"fragmented-list fragments={fragment_count} signals={signal_count} "
        f"records={len(entries)} kcmd={len(commands):#x}->{len(direct_commands):#x} "
        f"list={len(segments):#x}->{len(direct_segments):#x}"
    )
    return direct_commands, bytes(direct_segments), description


def interleaved_event_records(
    commands: bytes, segments: bytes
) -> list[tuple[int, int, int, int, int]]:
    """Return neutral fields for validated 0x18-byte event-list ranges.

    Event commands are load-bearing synchronization records, so the ordinary
    direct-list view must not make them invisible during failure analysis.
    Keep the two producer-specific dwords neutral: only the type-5 producer's
    +0x0c padding and +0x10 value have independently been RE-confirmed.
    """
    if len(segments) < 0x10:
        return []
    token = u64(segments, 0)
    cursor = 0
    records: list[tuple[int, int, int, int, int]] = []
    while cursor + 0x10 <= len(segments):
        if u64(segments, cursor) != token:
            return []
        count = u32(segments, cursor + 8)
        encoded = u32(segments, cursor + 0x0C)
        if encoded in (0x40000001, 0xC0000001):
            if count != 1 or cursor + 0x18 > len(segments):
                return []
            start = u32(segments, cursor + 0x10)
            end = u32(segments, cursor + 0x14)
            if (
                end <= start
                or end > len(commands)
                or (end - start) % 0x18 != 0
            ):
                return []
            for offset in range(start, end, 0x18):
                if u32(commands, offset + 4) != 0x18:
                    return []
                records.append(
                    (
                        offset,
                        u32(commands, offset),
                        u32(commands, offset + 8),
                        u32(commands, offset + 0x0C),
                        u64(commands, offset + 0x10),
                    )
                )
            cursor += 0x18
            continue
        chunk_length = encoded & 0x7FFFFFFF
        if count < 1 or chunk_length < 0x30 or (
            cursor + chunk_length > len(segments)
        ):
            return []
        cursor += chunk_length
        if encoded & 0x80000000:
            break
    return records if cursor == len(segments) else []


def main() -> None:
    arguments = parse_arguments()
    raw_commands = arguments.kcmd.read_bytes()
    raw_segments = arguments.segments.read_bytes()
    event_records = interleaved_event_records(raw_commands, raw_segments)
    commands = raw_commands
    segments = raw_segments
    if len(segments) < 0x10:
        raise ValueError("segment list is shorter than its 0x10-byte header")

    chain_description = None
    chain_form = None
    flattened_chain = flatten_fragmented_lists_for_decode(commands, segments)
    if flattened_chain is not None:
        chain_form = "fragmented-list->direct"
    if flattened_chain is None:
        flattened_chain = flatten_type5_chain_for_decode(commands, segments)
        if flattened_chain is not None:
            chain_form = "type5-chain->direct"
    if flattened_chain is not None:
        commands, segments, chain_description = flattened_chain

    leading_wrapper_list = (
        len(segments) >= 0x38
        and u32(segments, 8) == 1
        and u32(segments, 0x0C) == 0x40000001
        and u32(segments, 0x10) == 0
        and u32(segments, 0x14) == 0x10
        and u32(segments, 0x18) == u32(segments, 0)
        and u32(segments, 0x1C) == u32(segments, 4)
    )
    list_bytes = segments[0x18:] if leading_wrapper_list else segments
    list_token = u64(list_bytes, 0)
    segment_count = u32(list_bytes, 8)
    encoded_length = u32(list_bytes, 0x0C)
    expected_direct_length = 0x80000000 | len(list_bytes)
    direct_list = encoded_length == expected_direct_length
    trailing_wrapper_list = (
        not leading_wrapper_list
        and encoded_length >= 0x20
        and encoded_length + 0x18 == len(list_bytes)
    )
    if not direct_list and not trailing_wrapper_list:
        raise ValueError(
            f"encoded length {encoded_length:#x} is neither direct "
            f"{expected_direct_length:#x} nor a 0x18-byte trailing-wrapper offset"
        )
    if segment_count == 0 or segment_count > 1024:
        raise ValueError(f"implausible segment count {segment_count}")

    print(
        f"kcmd={arguments.kcmd} bytes={len(raw_commands):#x} "
        f"sha256={hashlib.sha256(raw_commands).hexdigest()}"
    )
    print(
        f"segments={arguments.segments} bytes={len(raw_segments):#x} "
        f"sha256={hashlib.sha256(raw_segments).hexdigest()}"
    )
    if chain_description is not None:
        print(f"source_form={chain_description}")
    for offset, event_type, event_id, word_0c, value in event_records:
        print(
            f"event kcmd={offset:#x} type={event_type:#x} "
            f"id={event_id:#x} word0c={word_0c:#x} value={value:#x}"
        )
    form_name = (
        "leading-wrapper"
        if leading_wrapper_list
        else (
            chain_form
            if chain_description is not None
            else ("direct" if direct_list else "trailing-wrapper")
        )
    )
    print(
        f"list_token={list_token:#x} count={segment_count} "
        f"encoded_length={encoded_length:#x} "
        f"form={form_name}"
    )

    descriptor_bytes = list_bytes if direct_list else list_bytes[:encoded_length]
    command_start = 0
    if leading_wrapper_list:
        if len(commands) < 0x10:
            raise ValueError("leading-wrapper KCMD record is truncated")
        if (
            u32(commands, 0) != 9
            or u32(commands, 4) != 0x10
            or u32(commands, 8) != 1
        ):
            raise ValueError("leading-wrapper KCMD framing is unknown")
        print(
            f"leading_wrapper generation={u32(segments, 4)} "
            "kcmd=0x0..0x10 type=0x9"
        )
        command_start = 0x10
    unique_gids: set[int] = set()
    # IOGPUSegmentListHeader's runtime type encoding makes this an ordered
    # variable-length array, not a bag of byte patterns.  Walking from 0x10
    # by each entry's 0x20 + groupCount*0x40 extent is both stricter and avoids
    # a false ambiguity when an opaque resource payload happens to repeat a
    # valid [KCMD start,end) pair.  Stray submit serial 7558 is the witness:
    # its first 0..0x8e8 range occurs twice as raw bytes, while the structured
    # 18-entry walk lands exactly at list 0x2b10 and KCMD 0x8370.
    descriptor_cursor = 0x10
    for segment_index in range(segment_count):
        if command_start + 8 > len(commands):
            raise ValueError(f"segment {segment_index} KCMD header is truncated")
        command_type = u32(commands, command_start)
        command_span = u32(commands, command_start + 4)
        command_end = command_start + command_span
        if command_span < 8 or command_end > len(commands):
            raise ValueError(
                f"segment {segment_index} invalid KCMD span {command_span:#x}"
            )

        header_offset = descriptor_cursor
        if header_offset + 0x20 > len(descriptor_bytes):
            raise ValueError(f"segment {segment_index} header is truncated")

        descriptor_start = u32(descriptor_bytes, header_offset + 8)
        descriptor_end = u32(descriptor_bytes, header_offset + 0x0C)
        if (descriptor_start, descriptor_end) != (command_start, command_end):
            raise ValueError(
                f"segment {segment_index} structured range "
                f"{descriptor_start:#x}..{descriptor_end:#x} does not match "
                f"KCMD {command_start:#x}..{command_end:#x}"
            )

        segment_token = u64(descriptor_bytes, header_offset)
        field_10 = u32(descriptor_bytes, header_offset + 0x10)
        field_14 = u32(descriptor_bytes, header_offset + 0x14)
        resource_count = u32(descriptor_bytes, header_offset + 0x18)
        group_count = u32(descriptor_bytes, header_offset + 0x1C)
        maximum_groups = (
            len(descriptor_bytes) - header_offset - 0x20
        ) // 0x40
        if group_count > maximum_groups:
            raise ValueError(
                f"segment {segment_index} group count {group_count} exceeds "
                f"the remaining-list bound {maximum_groups}"
            )
        entry_end = header_offset + 0x20 + group_count * 0x40
        if entry_end > len(descriptor_bytes):
            raise ValueError(
                f"segment {segment_index} descriptor groups are truncated"
            )

        resources: list[tuple[int, int, int]] = []
        for group_index in range(group_count):
            group = header_offset + 0x20 + group_index * 0x40
            valid_count = u16(descriptor_bytes, group + 0x3E)
            if valid_count > 6:
                raise ValueError(
                    f"segment {segment_index} group {group_index} "
                    f"valid count {valid_count} > 6"
                )
            for item_index in range(valid_count):
                gid = u32(descriptor_bytes, group + item_index * 4)
                value = u32(
                    descriptor_bytes, group + 0x18 + item_index * 4
                )
                flags = u16(
                    descriptor_bytes, group + 0x30 + item_index * 2
                )
                resources.append((gid, value, flags))
                unique_gids.add(gid)

        if len(resources) != resource_count:
            raise ValueError(
                f"segment {segment_index} header count {resource_count} "
                f"!= decoded count {len(resources)}"
            )
        print(
            f"segment={segment_index} token={segment_token:#x} "
            f"kcmd={command_start:#x}..{command_end:#x} type={command_type:#x} "
            f"list_header={header_offset:#x} fields10/14={field_10:#x}/{field_14:#x} "
            f"resources={resource_count} groups={group_count}"
        )
        for gid, value, flags in resources:
            print(f"  gid={gid:#x} value={value:#x} flags={flags:#x}")
        command_start = command_end
        descriptor_cursor = entry_end

    if descriptor_cursor != len(descriptor_bytes):
        raise ValueError(
            f"structured descriptor walk ends at {descriptor_cursor:#x}, "
            f"expected {len(descriptor_bytes):#x}"
        )

    expected_command_end = len(commands)
    if trailing_wrapper_list:
        wrapper = list_bytes[encoded_length:]
        list_magic = u32(list_bytes, 0)
        list_generation = u32(list_bytes, 4)
        wrapper_start = u32(wrapper, 0x10)
        wrapper_end = u32(wrapper, 0x14)
        # Runtime-confirmed by VS Code Simple Browser Aquarium GPU submit 108
        # on 2026-07-30: generation 4 retains the same trailing-wrapper list
        # framing and carries the range [0x210,0x228).  The first mapped submit
        # from an empty post-reboot profile uses the same framing with both
        # generation fields zero and range [0x840,0x870).  VSCode video GPU
        # serials 2 and 3 on 2026-08-01 supplied the missing generation-1
        # witnesses with the same framing and exact outer/tail equality.
        if list_generation not in (0, 1, 2, 3, 4):
            raise ValueError(
                f"unobserved trailing-wrapper list generation {list_generation}"
            )
        if u32(wrapper, 0) != list_magic:
            raise ValueError("trailing-wrapper magic does not match outer list")
        if u32(wrapper, 4) != list_generation:
            raise ValueError(
                "trailing-wrapper generation does not match outer list"
            )
        if u32(wrapper, 8) != 1 or u32(wrapper, 0x0C) != 0xC0000001:
            raise ValueError("trailing-wrapper record framing is unknown")
        if wrapper_start != command_start or wrapper_end != len(commands):
            raise ValueError(
                f"trailing-wrapper KCMD range {wrapper_start:#x}..{wrapper_end:#x} "
                f"does not follow decoded chain at {command_start:#x} or file end"
            )
        wrapper_bytes = wrapper_end - wrapper_start
        if wrapper_bytes not in (0x18, 0x30):
            raise ValueError(
                f"trailing-wrapper KCMD size {wrapper_bytes:#x} is not one/two records"
            )
        wrapper_type = u32(commands, wrapper_start)
        wrapper_opcode = u32(commands, wrapper_start + 8)
        if wrapper_type == 3:
            for wrapper_index in range(wrapper_bytes // 0x18):
                offset = wrapper_start + wrapper_index * 0x18
                if (
                    u32(commands, offset) != 3
                    or u32(commands, offset + 4) != 0x18
                    or u32(commands, offset + 8) != wrapper_opcode
                ):
                    raise ValueError(
                        f"trailing KCMD wrapper {wrapper_index} framing is unknown"
                    )
        elif not (
            wrapper_bytes == 0x18
            and wrapper_type == 5
            and u32(commands, wrapper_start + 4) == 0x18
            # RE-confirmed via macOS 13.4 IOGPU disassembly: +0x08 is
            # the event identifier, while +0x0c is unwritten padding.  The
            # runtime has captured valid framing with padding 0 and 0x15, so
            # only require a realized event plus the observed signal value.
            and wrapper_opcode != 0
            and u32(commands, wrapper_start + 0x10) == 1
            and u32(commands, wrapper_start + 0x14) == 0
        ):
            raise ValueError("trailing type-5 KCMD wrapper framing is unknown")
        print(
            f"trailing_wrapper generation={list_generation} "
            f"kcmd={wrapper_start:#x}..{wrapper_end:#x} "
            f"records={wrapper_bytes // 0x18} type={wrapper_type:#x} "
            f"opcode_or_event_id={wrapper_opcode:#x}"
        )
        expected_command_end = wrapper_start

    if command_start != expected_command_end:
        raise ValueError(
            f"decoded KCMD chain ends at {command_start:#x}, "
            f"expected descriptor-backed end is {expected_command_end:#x}"
        )
    gids = " ".join(f"{gid:#x}" for gid in sorted(unique_gids))
    print(f"unique_gids={len(unique_gids)} {gids}")


if __name__ == "__main__":
    main()

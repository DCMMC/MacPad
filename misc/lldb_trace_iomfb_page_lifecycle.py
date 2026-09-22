"""Bounded, read-only QuartzCore IOMFB page lifecycle trace.

This is imported by lldb_trace_iomfb_page_lifecycle.lldb.  It deliberately
uses direct addresses from the exact macOS 13.4 QuartzCore image because
loading the on-device 3.3-GiB shared-cache symbol table is prohibitively slow.
The image UUID and static addresses are documented in the command file.
"""

import lldb


STATIC_IMAGE_BASE = 0x1879BE000
STATIC_CURRENT_DECISION = 0x187C9AC14
STATIC_CURRENT_CREATE = 0x187C9AC2C
STATIC_CURRENT_FIRST_UNREF = 0x187C9A9CC
STATIC_CURRENT_FIRST_UNREF_RETURN = 0x187C9A9D4
STATIC_CURRENT_ALLOCATE_RETURN = 0x187C9AC90
STATIC_CURRENT_REPLACE_UNREF = 0x187C9AD54
STATIC_CURRENT_REPLACE_UNREF_RETURN = 0x187C9AD58
STATIC_CURRENT_COMMON = 0x187C9ADDC
STATIC_CURRENT_RETURN = 0x187C9AEFC
STATIC_PURGE_ENTRY = 0x187B202FC
STATIC_PURGE_RETURN = 0x187B203AC
STATIC_MARK_NON_STATIC_ENTRY = 0x187C87E94
STATIC_MARK_NON_STATIC_ID_RETURN = 0x187C87EE8
STATIC_MARK_NON_STATIC_CLEAR = 0x187C87F6C
STATIC_CLONE_SWAPBEGIN_RETURN = 0x187C8FE94
STATIC_CLONE_SWAPEND_RETURN = 0x187C9129C
STATIC_IDLE_ENTRY = 0x187C8BE4C
STATIC_IDLE_SWAPWAIT_RETURN = 0x187C8BE90

_state = {
    "current": 0,
    "create": 0,
    "first_unref": 0,
    "allocate_return": 0,
    "replace_unref": 0,
    "common": 0,
    "current_return": 0,
    "purge": 0,
    "mark_non_static": 0,
    "mark_id": 0,
    "mark_clear": 0,
    "swapbegin": 0,
    "swapend": 0,
    "idle": 0,
    "idle_wait": 0,
    "limit": 24,
}


def _reg(frame, name):
    return frame.FindRegister(name).GetValueAsUnsigned()


def _read(process, address, size):
    error = lldb.SBError()
    value = process.ReadUnsignedFromMemory(address, size, error)
    return value if error.Success() else None


def _hex(value):
    return "ERR" if value is None else f"{value:#x}"


def _surface_snapshot(process, surface):
    if not surface:
        return "surface=nil"
    flags_lo = _read(process, surface + 0x38, 4)
    flags_hi = _read(process, surface + 0x3C, 1)
    owner = _read(process, surface + 0x8, 8)
    next_surface = _read(process, surface + 0x10, 8)
    width = _read(process, surface + 0x18, 4)
    height = _read(process, surface + 0x1C, 4)
    pixel_format = _read(process, surface + 0x20, 4)
    if flags_lo is None or flags_hi is None:
        return f"surface={surface:#x} flags=ERR"
    flags = flags_lo | (flags_hi << 32)
    return (
        f"surface={surface:#x} ref={flags & 0xffff} "
        f"age={(flags >> 16) & 0xff} static25={(flags >> 25) & 1} "
        f"static26={(flags >> 26) & 1} flags={flags:#x} "
        f"owner={_hex(owner)} next={_hex(next_surface)} "
        f"size={_hex(width)}x{_hex(height)} pf={_hex(pixel_format)}"
    )


def _purge_snapshot(process, display):
    if not display:
        return "display=nil purge=ERR"
    node = _read(process, display + 0xD8, 8)
    tail = _read(process, display + 0xE0, 8)
    count = 0
    static25 = 0
    static26 = 0
    bytes4 = 0
    first = node
    seen = set()
    while node and node not in seen and count < 4096:
        seen.add(node)
        flags_lo = _read(process, node + 0x38, 4)
        flags_hi = _read(process, node + 0x3C, 1)
        width = _read(process, node + 0x18, 4)
        height = _read(process, node + 0x1C, 4)
        if flags_lo is not None and flags_hi is not None:
            flags = flags_lo | (flags_hi << 32)
            static25 += (flags >> 25) & 1
            static26 += (flags >> 26) & 1
        if width is not None and height is not None:
            bytes4 += width * height * 4
        node = _read(process, node + 0x10, 8)
        count += 1
    suffix = "+" if node else ""
    return (
        f"display={display:#x} purge={count}{suffix} "
        f"static25={static25} static26={static26} "
        f"approx4={bytes4 // (1024 * 1024)}MB "
        f"head={_hex(first)} tail={_hex(tail)}"
    )


def _buffer_snapshot(process, display):
    if not display:
        return "buffers=ERR"
    node = _read(process, display + 0x568, 8)
    count = 0
    static26 = 0
    first_surface = None
    seen = set()
    while node and node not in seen and count < 4096:
        seen.add(node)
        surface = _read(process, node + 0x20, 8)
        if first_surface is None:
            first_surface = surface
        if surface:
            flags_lo = _read(process, surface + 0x38, 4)
            flags_hi = _read(process, surface + 0x3C, 1)
            if flags_lo is not None and flags_hi is not None:
                flags = flags_lo | (flags_hi << 32)
                static26 += (flags >> 26) & 1
        node = _read(process, node, 8)
        count += 1
    suffix = "+" if node else ""
    return (
        f"buffers={count}{suffix} static26={static26} "
        f"firstSurface={_hex(first_surface)}"
    )


def _page_snapshot(frame, display=None, surface=None):
    process = frame.GetThread().GetProcess()
    if display is None:
        display = _reg(frame, "x20")
    index = _read(process, display + 0x41C, 4)
    page_count = _read(process, display + 0x424, 4)
    mode_740 = _read(process, display + 0x740, 4)
    if index is None:
        return f"display={display:#x} index=ERR"

    slot = display + index * 0x30
    page_record = _read(process, slot + 0x478, 8)
    slot_count = _read(process, slot + 0x488, 4)
    slot_flags = _read(process, slot + 0x498, 1)
    record_surface = None
    record_flags = None
    if page_record:
        record_surface = _read(process, page_record + 0x28, 8)
        record_flags = _read(process, page_record + 0x84, 1)
    if surface is None:
        surface = record_surface
    surface_lo = None
    surface_hi = None
    if surface:
        surface_lo = _read(process, surface + 0x38, 4)
        surface_hi = _read(process, surface + 0x3C, 1)

    return (
        f"display={display:#x} index={index} pages={_hex(page_count)} "
        f"mode740={_hex(mode_740)} slotCount={_hex(slot_count)} "
        f"slotFlags={_hex(slot_flags)} record={_hex(page_record)} "
        f"recordFlags={_hex(record_flags)} surface={_hex(surface)} "
        f"recordSurface={_hex(record_surface)} "
        f"surfaceFlags={_hex(surface_lo)}/{_hex(surface_hi)}"
    )


def current_decision(frame, _bp_loc, _dict):
    _state["current"] += 1
    n = _state["current"]
    print(f"IOMFB-LIFE current-decision #{n}: " +
          _page_snapshot(frame, _reg(frame, "x20"), _reg(frame, "x21")))
    return n >= _state["limit"]


def current_create(frame, _bp_loc, _dict):
    _state["create"] += 1
    n = _state["create"]
    if n <= 12:
        print(f"IOMFB-LIFE current-create #{n}: " +
              _page_snapshot(frame, _reg(frame, "x20"), _reg(frame, "x21")))
    # A nil old page surface branches directly to creation and never reaches
    # current_decision.  Stop on the observed event itself so this trace stays
    # bounded even when every update allocates a new IOSurface.
    return n >= _state["limit"]


def current_first_unref(frame, _bp_loc, _dict):
    _state["first_unref"] += 1
    n = _state["first_unref"]
    if n <= 16:
        process = frame.GetThread().GetProcess()
        surface = _reg(frame, "x21")
        print(
            f"IOMFB-LIFE current-first-unref #{n}: "
            + _surface_snapshot(process, surface)
            + " " + _purge_snapshot(process, _reg(frame, "x20"))
        )
    return False


def current_first_unref_return(frame, _bp_loc, _dict):
    n = _state["first_unref"]
    if n <= 16:
        process = frame.GetThread().GetProcess()
        print(
            f"IOMFB-LIFE current-first-unref-return #{n}: "
            + _surface_snapshot(process, _reg(frame, "x21"))
            + " " + _purge_snapshot(process, _reg(frame, "x20"))
        )
    return False


def current_allocate_return(frame, _bp_loc, _dict):
    _state["allocate_return"] += 1
    n = _state["allocate_return"]
    if n <= 16:
        process = frame.GetThread().GetProcess()
        print(
            f"IOMFB-LIFE current-allocate-return #{n}: "
            + _surface_snapshot(process, _reg(frame, "x0"))
            + " " + _purge_snapshot(process, _reg(frame, "x20"))
        )
    return False


def current_replace_unref(frame, _bp_loc, _dict):
    _state["replace_unref"] += 1
    n = _state["replace_unref"]
    if n <= 16:
        process = frame.GetThread().GetProcess()
        print(
            f"IOMFB-LIFE current-replace-unref #{n}: old="
            + _surface_snapshot(process, _reg(frame, "x0"))
            + " new=" + _surface_snapshot(process, _reg(frame, "x21"))
        )
    return False


def current_replace_unref_return(frame, _bp_loc, _dict):
    n = _state["replace_unref"]
    if n <= 16:
        process = frame.GetThread().GetProcess()
        print(
            f"IOMFB-LIFE current-replace-unref-return #{n}: new="
            + _surface_snapshot(process, _reg(frame, "x21"))
            + " " + _purge_snapshot(process, _reg(frame, "x20"))
        )
    return False


def current_common(frame, _bp_loc, _dict):
    _state["common"] += 1
    n = _state["common"]
    if n <= 12:
        print(f"IOMFB-LIFE current-common #{n}: " +
              _page_snapshot(frame, _reg(frame, "x20"), _reg(frame, "x21")))
    return False


def current_return(frame, _bp_loc, _dict):
    _state["current_return"] += 1
    n = _state["current_return"]
    if n <= 16:
        process = frame.GetThread().GetProcess()
        print(
            f"IOMFB-LIFE current-return #{n}: "
            + _surface_snapshot(process, _reg(frame, "x21"))
            + " " + _purge_snapshot(process, _reg(frame, "x20"))
        )
    return False


def purge_entry(frame, _bp_loc, _dict):
    _state["purge"] += 1
    n = _state["purge"]
    if n <= 32:
        process = frame.GetThread().GetProcess()
        print(
            f"IOMFB-LIFE purge-entry #{n}: force={_reg(frame, 'w1')} "
            + _purge_snapshot(process, _reg(frame, "x0"))
        )
    return False


def purge_return(frame, _bp_loc, _dict):
    n = _state["purge"]
    if n <= 32:
        process = frame.GetThread().GetProcess()
        print(
            f"IOMFB-LIFE purge-return #{n}: "
            + _purge_snapshot(process, _reg(frame, "x19"))
        )
    return False


def mark_non_static_entry(frame, _bp_loc, _dict):
    _state["mark_non_static"] += 1
    n = _state["mark_non_static"]
    if n <= 24:
        process = frame.GetThread().GetProcess()
        print(
            f"IOMFB-LIFE mark-non-static #{n}: keepID={_reg(frame, 'x1'):#x} "
            + _buffer_snapshot(process, _reg(frame, "x0")) + " "
            + _purge_snapshot(process, _reg(frame, "x0"))
        )
    return False


def mark_non_static_id_return(frame, _bp_loc, _dict):
    _state["mark_id"] += 1
    n = _state["mark_id"]
    if n <= 40:
        process = frame.GetThread().GetProcess()
        surface = _read(process, _reg(frame, "x21") + 0x20, 8)
        print(
            f"IOMFB-LIFE mark-id #{n}: keepID={_reg(frame, 'x19'):#x} "
            f"surfaceID={_reg(frame, 'x0'):#x} "
            + _surface_snapshot(process, surface)
        )
    return False


def mark_non_static_clear(frame, _bp_loc, _dict):
    _state["mark_clear"] += 1
    n = _state["mark_clear"]
    if n <= 40:
        process = frame.GetThread().GetProcess()
        surface = _read(process, _reg(frame, "x21") + 0x20, 8)
        print(
            f"IOMFB-LIFE mark-clear #{n}: keepID={_reg(frame, 'x19'):#x} "
            + _surface_snapshot(process, surface)
        )
    return False


def clone_swapbegin_return(frame, _bp_loc, _dict):
    _state["swapbegin"] += 1
    n = _state["swapbegin"]
    if n <= 12:
        print(f"IOMFB-LIFE clone-swapbegin-return #{n}: status={_reg(frame, 'w0'):#x} " +
              _page_snapshot(frame, _reg(frame, "x19")))
    return False


def clone_swapend_return(frame, _bp_loc, _dict):
    _state["swapend"] += 1
    n = _state["swapend"]
    if n <= 12:
        print(f"IOMFB-LIFE clone-swapend-return #{n}: status={_reg(frame, 'w0'):#x} " +
              _page_snapshot(frame, _reg(frame, "x19")))
    return False


def idle_entry(frame, _bp_loc, _dict):
    _state["idle"] += 1
    n = _state["idle"]
    if n <= 12:
        print(f"IOMFB-LIFE idle-entry #{n}: " +
              _page_snapshot(frame, _reg(frame, "x0")))
    return False


def idle_swapwait_return(frame, _bp_loc, _dict):
    _state["idle_wait"] += 1
    n = _state["idle_wait"]
    if n <= 12:
        print(f"IOMFB-LIFE idle-swapwait-return #{n}: status={_reg(frame, 'w0'):#x} " +
              _page_snapshot(frame, _reg(frame, "x20")))
    return False


def _quartzcore_header(target):
    for module in target.modules:
        if module.file.basename == "QuartzCore":
            address = module.GetObjectFileHeaderAddress()
            return address.GetLoadAddress(target)
    return lldb.LLDB_INVALID_ADDRESS


def _break(target, runtime_address, callback):
    bp = target.BreakpointCreateByAddress(runtime_address)
    bp.SetScriptCallbackFunction(
        f"{__name__}.{callback.__name__}"
    )
    return bp


def install(target, limit=24):
    header = _quartzcore_header(target)
    if header == lldb.LLDB_INVALID_ADDRESS:
        raise RuntimeError("QuartzCore is not loaded")
    slide = header - STATIC_IMAGE_BASE
    _state["limit"] = limit
    table = (
        (STATIC_CURRENT_DECISION, current_decision),
        (STATIC_CURRENT_CREATE, current_create),
        (STATIC_CURRENT_FIRST_UNREF, current_first_unref),
        (STATIC_CURRENT_FIRST_UNREF_RETURN, current_first_unref_return),
        (STATIC_CURRENT_ALLOCATE_RETURN, current_allocate_return),
        (STATIC_CURRENT_REPLACE_UNREF, current_replace_unref),
        (STATIC_CURRENT_REPLACE_UNREF_RETURN, current_replace_unref_return),
        (STATIC_CURRENT_COMMON, current_common),
        (STATIC_CURRENT_RETURN, current_return),
        (STATIC_PURGE_ENTRY, purge_entry),
        (STATIC_PURGE_RETURN, purge_return),
        (STATIC_MARK_NON_STATIC_ENTRY, mark_non_static_entry),
        (STATIC_MARK_NON_STATIC_ID_RETURN, mark_non_static_id_return),
        (STATIC_MARK_NON_STATIC_CLEAR, mark_non_static_clear),
        (STATIC_CLONE_SWAPBEGIN_RETURN, clone_swapbegin_return),
        (STATIC_CLONE_SWAPEND_RETURN, clone_swapend_return),
        (STATIC_IDLE_ENTRY, idle_entry),
        (STATIC_IDLE_SWAPWAIT_RETURN, idle_swapwait_return),
    )
    for static_address, callback in table:
        _break(target, static_address + slide, callback)
    print(
        f"IOMFB-LIFE installed header={header:#x} slide={slide:#x} "
        f"limit={limit} breakpoints={len(table)}"
    )


def dump_summary():
    print("IOMFB-LIFE summary " + " ".join(
        f"{key}={value}" for key, value in _state.items()
    ))

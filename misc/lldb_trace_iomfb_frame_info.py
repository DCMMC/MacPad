"""Bounded QuartzCore/IOMFB FrameInfo lifecycle trace.

The addresses below come from the exact macOS 13.4 QuartzCore image with UUID
CF853BBD-01B6-3F46-ADA1-EC70FD2DC9DC.  Callbacks only read registers and the
IOMFBDisplay pending-FrameInfo vector; they never modify process state.
"""

import lldb


STATIC_IMAGE_BASE = 0x1879BE000
STATIC_REGISTER_RETURN = 0x187AC8D48
STATIC_SET_ENABLED_ENTRY = 0x187CA3108
STATIC_CALLBACK_ENTRY = 0x187C5009C
STATIC_CALLBACK_AFTER_COLLECT = 0x187C50138
STATIC_COLLECT_ENTRY = 0x187C881A8
STATIC_CURRENT_CREATE = 0x187C9AC2C
STATIC_CLONE_SWAPBEGIN_RETURN = 0x187C8FE94
STATIC_CLONE_SWAPEND_RETURN = 0x187C9129C

_state = {
    "register": 0,
    "enabled": 0,
    "callback": 0,
    "callback_after": 0,
    "collect": 0,
    "create": 0,
    "swapbegin": 0,
    "swapend": 0,
    "max_pending": 0,
    "swap_limit": 16,
    "create_limit": 24,
}


def _reg(frame, name):
    return frame.FindRegister(name).GetValueAsUnsigned()


def _read(process, address, size):
    error = lldb.SBError()
    value = process.ReadUnsignedFromMemory(address, size, error)
    return value if error.Success() else None


def _display_from_server(frame, server):
    return _read(frame.GetThread().GetProcess(), server + 0x58, 8)


def _pending(frame, display):
    if not display:
        return "display=nil pending=ERR", None
    process = frame.GetThread().GetProcess()
    begin = _read(process, display + 0x510, 8)
    end = _read(process, display + 0x518, 8)
    capacity = _read(process, display + 0x520, 8)
    if begin is None or end is None or capacity is None or end < begin:
        return (
            f"display={display:#x} begin={begin} end={end} cap={capacity} "
            "pending=ERR",
            None,
        )
    count = (end - begin) // 8
    _state["max_pending"] = max(_state["max_pending"], count)
    first_id = None
    last_id = None
    if count:
        first = _read(process, begin, 8)
        last = _read(process, end - 8, 8)
        if first:
            first_id = _read(process, first, 4)
        if last:
            last_id = _read(process, last, 4)
    return (
        f"display={display:#x} pending={count} begin={begin:#x} "
        f"end={end:#x} cap={capacity:#x} firstID={first_id} lastID={last_id}",
        count,
    )


def registration_return(frame, _bp_loc, _dict):
    _state["register"] += 1
    server = _reg(frame, "x19")
    display = _display_from_server(frame, server)
    pending, _ = _pending(frame, display)
    source = _read(frame.GetThread().GetProcess(), server + 0x2C0, 8)
    print(
        f"IOMFB-FRAME registration #{_state['register']}: status={_reg(frame, 'w0'):#x} "
        f"server={server:#x} source3={source} {pending}"
    )
    return False


def set_enabled_entry(frame, _bp_loc, _dict):
    _state["enabled"] += 1
    pending, _ = _pending(frame, _reg(frame, "x0"))
    print(
        f"IOMFB-FRAME set-enabled #{_state['enabled']}: value={_reg(frame, 'w1')} "
        f"{pending}"
    )
    return False


def callback_entry(frame, _bp_loc, _dict):
    _state["callback"] += 1
    server = _reg(frame, "x3")
    display = _display_from_server(frame, server)
    pending, _ = _pending(frame, display)
    n = _state["callback"]
    if n <= 24 or n % 120 == 0:
        print(
            f"IOMFB-FRAME callback #{n}: swapID={_reg(frame, 'w1')} "
            f"dictionary={_reg(frame, 'x2'):#x} server={server:#x} {pending}"
        )
    return False


def callback_after_collect(frame, _bp_loc, _dict):
    _state["callback_after"] += 1
    display = _display_from_server(frame, _reg(frame, "x19"))
    pending, _ = _pending(frame, display)
    n = _state["callback_after"]
    if n <= 24 or n % 120 == 0:
        print(f"IOMFB-FRAME callback-after-collect #{n}: {pending}")
    return False


def collect_entry(frame, _bp_loc, _dict):
    _state["collect"] += 1
    pending, _ = _pending(frame, _reg(frame, "x1"))
    n = _state["collect"]
    if n <= 32 or n % 120 == 0:
        print(
            f"IOMFB-FRAME collect #{n}: throughSwapID={_reg(frame, 'w2')} {pending}"
        )
    return False


def current_create(frame, _bp_loc, _dict):
    _state["create"] += 1
    n = _state["create"]
    pending, _ = _pending(frame, _reg(frame, "x20"))
    if n <= 24:
        print(f"IOMFB-FRAME page-create #{n}: {pending}")
    return n >= _state["create_limit"]


def clone_swapbegin_return(frame, _bp_loc, _dict):
    _state["swapbegin"] += 1
    n = _state["swapbegin"]
    pending, _ = _pending(frame, _reg(frame, "x19"))
    if n <= 24:
        print(
            f"IOMFB-FRAME SwapBegin-return #{n}: status={_reg(frame, 'w0'):#x} {pending}"
        )
    return False


def clone_swapend_return(frame, _bp_loc, _dict):
    _state["swapend"] += 1
    n = _state["swapend"]
    pending, _ = _pending(frame, _reg(frame, "x19"))
    if n <= 24:
        print(
            f"IOMFB-FRAME SwapEnd-return #{n}: status={_reg(frame, 'w0'):#x} {pending}"
        )
    return n >= _state["swap_limit"]


def _quartzcore_header(target):
    for module in target.modules:
        if module.file.basename == "QuartzCore":
            return module.GetObjectFileHeaderAddress().GetLoadAddress(target)
    return lldb.LLDB_INVALID_ADDRESS


def _break(target, runtime_address, callback):
    bp = target.BreakpointCreateByAddress(runtime_address)
    bp.SetScriptCallbackFunction(f"{__name__}.{callback.__name__}")
    return bp


def install(target, swap_limit=16, create_limit=24):
    header = _quartzcore_header(target)
    if header == lldb.LLDB_INVALID_ADDRESS:
        raise RuntimeError("QuartzCore is not loaded")
    slide = header - STATIC_IMAGE_BASE
    _state["swap_limit"] = swap_limit
    _state["create_limit"] = create_limit
    table = (
        (STATIC_REGISTER_RETURN, registration_return),
        (STATIC_SET_ENABLED_ENTRY, set_enabled_entry),
        (STATIC_CALLBACK_ENTRY, callback_entry),
        (STATIC_CALLBACK_AFTER_COLLECT, callback_after_collect),
        (STATIC_COLLECT_ENTRY, collect_entry),
        (STATIC_CURRENT_CREATE, current_create),
        (STATIC_CLONE_SWAPBEGIN_RETURN, clone_swapbegin_return),
        (STATIC_CLONE_SWAPEND_RETURN, clone_swapend_return),
    )
    for static_address, callback in table:
        _break(target, static_address + slide, callback)
    print(
        f"IOMFB-FRAME installed header={header:#x} slide={slide:#x} "
        f"swap_limit={swap_limit} create_limit={create_limit} "
        f"breakpoints={len(table)}"
    )
    return True


def wait_for_quartzcore_and_install(debugger, swap_limit=16, create_limit=24):
    """Continue only between dyld image notifications until QuartzCore exists."""
    target = debugger.GetSelectedTarget()
    process = target.GetProcess()
    for event_count in range(1025):
        if _quartzcore_header(target) != lldb.LLDB_INVALID_ADDRESS:
            print(
                f"IOMFB-FRAME QuartzCore loaded after {event_count} image events"
            )
            return install(target, swap_limit, create_limit)
        error = process.Continue()
        if error.Fail():
            print(f"IOMFB-FRAME FATAL continue failed: {error}")
            return False
        if process.GetState() != lldb.eStateStopped:
            print(
                f"IOMFB-FRAME FATAL process state={process.GetState()} "
                "before QuartzCore load"
            )
            return False
    print("IOMFB-FRAME FATAL QuartzCore absent after 1024 image events")
    return False


def dump_summary():
    print("IOMFB-FRAME summary " + " ".join(
        f"{key}={value}" for key, value in _state.items()
    ))

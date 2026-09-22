"""
LLDB tracer for the macOS 13.4 AGX texture CPU/GPU-address binding path.

Run after connecting macOS LLDB to the iOS debugserver with:
    command script import misc/lldb_trace_initimpl.py
    continue

The addresses below are macOS-static addresses from the exact AGXMetal13_3
image in the iPad's macOS 13.4 dyld shared cache.  The tracer computes the
runtime slide before arming breakpoints.  It traces every IOSurface-backed
call; the target 32x32 backdrop-noise call is identified afterwards by joining
its IOSurface pointer to Metal_hooks.x's ``POOL-NEW key=32x32`` runtime line.

Static RE anchors:
  initImpl +0x4b0 allocates the 0x208-byte C++ Texture object.
  initImpl +0x618 returns from its Texture constructor.
  initImpl +0x640 skips address allocation when Texture+0xe8 is NULL.
  initImpl +0x6a4/+0x6b0 call the CPU/GPU address accessors.
  initImpl +0x6b4/+0x6b8 store those results at child Texture+0x130/+0x40.
  address helper 0x1e576fb74 ultimately adds Texture+0x130 to its offset.
  writeRegion 0x1e5772220 calls memmove(dst=x0, src=x1, len=x2).
"""

import lldb


LOG = "/tmp/lldb_initimpl_hits.log"
TEXT_STATIC_BASE = 0x1E53DD000

ADDR_INITIMPL_ENTRY = 0x1E5A4A284
ADDR_AFTER_CTOR = 0x1E5A4A61C
ADDR_CHILD_CHECK = 0x1E5A4A640
ADDR_CPU_MAP_RETURN = 0x1E5A4A6A8
ADDR_GPU_MAP_RETURN = 0x1E5A4A6B4
ADDR_MAPPING_JOIN = 0x1E5A4A700
ADDR_INITIMPL_EPILOGUE = 0x1E5A4AF4C
ADDR_ADDRESS_HELPER_ENTRY = 0x1E576FB74
ADDR_WRITEREGION_MEMMOVE = 0x1E5772220

_sequence = 0
_active = {}
_interesting_impls = set()


def L(msg):
    with open(LOG, "a") as f:
        f.write(msg + "\n")


def _regs(frame, names):
    out = {}
    wanted = set(names)
    for regset in frame.GetRegisters():
        for reg in regset:
            name = reg.GetName()
            if name not in wanted:
                continue
            value = reg.GetValue()
            try:
                out[name] = int(value, 16) if value else 0
            except (ValueError, TypeError):
                out[name] = 0
    return out


def _tid(frame):
    return frame.GetThread().GetThreadID()


def _read_unsigned(frame, address, size):
    if not isinstance(address, int) or address < 0x1000:
        return 0
    error = lldb.SBError()
    value = frame.GetThread().GetProcess().ReadUnsignedFromMemory(
        address, size, error
    )
    return value if error.Success() else 0


def _texture_fields(frame, impl):
    if not isinstance(impl, int) or impl < 0x1000:
        return "impl=0x0"
    return (
        f"impl={hex(impl)} "
        f"gpu40={hex(_read_unsigned(frame, impl + 0x40, 8))} "
        f"ios_a0={hex(_read_unsigned(frame, impl + 0xA0, 8))} "
        f"plane_a8={hex(_read_unsigned(frame, impl + 0xA8, 4))} "
        f"child_e8={hex(_read_unsigned(frame, impl + 0xE8, 8))} "
        f"cpu130={hex(_read_unsigned(frame, impl + 0x130, 8))} "
        f"layout184={hex(_read_unsigned(frame, impl + 0x184, 1))}"
    )


def _state(frame):
    return _active.get(_tid(frame))


def _detail(frame):
    state = _state(frame)
    return state is not None and state["detail"]


def bp_entry(frame, _bp_loc, _internal_dict):
    global _sequence
    regs = _regs(frame, ("x0", "x2", "x3", "x4", "x5", "x6", "x7", "lr"))
    _sequence += 1
    state = {
        "seq": _sequence,
        "self": regs.get("x0", 0),
        "ios": regs.get("x4", 0),
        # The selector labels this ABI slot bytesPerRow, but the 2026-07-23
        # runtime trace produced values such as 0xfa1800 for most calls.  Keep
        # it as a raw observed argument until the exact call-site ABI is RE'd.
        "raw_x7": regs.get("x7", 0),
        "impl": 0,
        "detail": regs.get("x4", 0) != 0,
    }
    _active[_tid(frame)] = state
    if _sequence <= 100 or state["detail"]:
        L(
            f"[entry #{_sequence} tid={_tid(frame)} detail={int(state['detail'])}] "
            f"self={hex(state['self'])} device={hex(regs.get('x2', 0))} "
            f"desc={hex(regs.get('x3', 0))} ios={hex(state['ios'])} "
            f"plane={regs.get('x5', 0)} buffer={hex(regs.get('x6', 0))} "
            f"raw_x7={state['raw_x7']} lr={hex(regs.get('lr', 0))}"
        )
    return False


def bp_after_ctor(frame, _bp_loc, _internal_dict):
    if not _detail(frame):
        return False
    impl = _regs(frame, ("x28",)).get("x28", 0)
    state = _state(frame)
    state["impl"] = impl
    _interesting_impls.add(impl)
    L(f"[after-ctor #{state['seq']}] {_texture_fields(frame, impl)}")
    return False


def bp_child_check(frame, _bp_loc, _internal_dict):
    if not _detail(frame):
        return False
    regs = _regs(frame, ("x21", "x23"))
    state = _state(frame)
    L(
        f"[child-check #{state['seq']}] x23(primary)={hex(regs.get('x23', 0))} "
        f"x21(primary+e8)={hex(regs.get('x21', 0))} "
        f"{_texture_fields(frame, regs.get('x23', 0))}"
    )
    return False


def bp_cpu_map_return(frame, _bp_loc, _internal_dict):
    if not _detail(frame):
        return False
    regs = _regs(frame, ("x0", "x19", "x23"))
    state = _state(frame)
    L(
        f"[cpu-map-return #{state['seq']}] cpu={hex(regs.get('x0', 0))} "
        f"child={hex(regs.get('x19', 0))} primary={hex(regs.get('x23', 0))}"
    )
    return False


def bp_gpu_map_return(frame, _bp_loc, _internal_dict):
    if not _detail(frame):
        return False
    regs = _regs(frame, ("x0", "x19", "x21", "x23"))
    state = _state(frame)
    L(
        f"[gpu-map-return #{state['seq']}] gpu={hex(regs.get('x0', 0))} "
        f"saved_cpu={hex(regs.get('x21', 0))} child={hex(regs.get('x19', 0))} "
        f"primary={hex(regs.get('x23', 0))}"
    )
    return False


def bp_mapping_join(frame, _bp_loc, _internal_dict):
    if not _detail(frame):
        return False
    primary = _regs(frame, ("x23",)).get("x23", 0)
    state = _state(frame)
    L(f"[mapping-join #{state['seq']}] {_texture_fields(frame, primary)}")
    return False


def bp_epilogue(frame, _bp_loc, _internal_dict):
    regs = _regs(frame, ("x0",))
    state = _active.pop(_tid(frame), None)
    if state and (state["seq"] <= 100 or state["detail"]):
        L(
            f"[exit #{state['seq']}] result={hex(regs.get('x0', 0))} "
            f"{_texture_fields(frame, state['impl'])}"
        )
    return False


def bp_address_helper(frame, _bp_loc, _internal_dict):
    regs = _regs(frame, ("x0", "x1", "x2", "x3", "lr"))
    impl = regs.get("x0", 0)
    if impl not in _interesting_impls:
        return False
    L(
        f"[address-helper] {_texture_fields(frame, impl)} "
        f"slice={regs.get('x1', 0)} row={regs.get('x2', 0)} plane={regs.get('x3', 0)} "
        f"lr={hex(regs.get('lr', 0))}"
    )
    return False


def bp_memmove(frame, _bp_loc, _internal_dict):
    regs = _regs(frame, ("x0", "x1", "x2", "x24", "lr"))
    impl = regs.get("x24", 0)
    if impl not in _interesting_impls:
        return False
    destination = regs.get("x0", 0)
    L(
        f"[writeRegion-memmove] dst={hex(destination)} src={hex(regs.get('x1', 0))} "
        f"len={regs.get('x2', 0)} x24={hex(impl)} "
        f"{_texture_fields(frame, impl)}"
    )
    if destination == 0:
        L("[STOP] writeRegion is about to call memmove with dst=NULL")
        return True
    return False


def __lldb_init_module(debugger, _internal_dict):
    global _sequence
    _sequence = 0
    _active.clear()
    _interesting_impls.clear()
    open(LOG, "w").write("=== AGX texture mapping trace start ===\n")

    target = debugger.GetSelectedTarget()
    if not target.IsValid():
        L("FATAL: no target; connect to debugserver before importing this module")
        return

    slide = None
    for module in target.module_iter():
        if (module.GetFileSpec().GetFilename() or "") != "AGXMetal13_3":
            continue
        section = module.FindSection("__TEXT")
        if section.IsValid():
            load = section.GetLoadAddress(target)
            slide = load - TEXT_STATIC_BASE
            L(f"AGXMetal13_3 slide={hex(slide)} __TEXT={hex(load)}")
            break
    if slide is None:
        names = [m.GetFileSpec().GetFilename() or "?" for m in target.module_iter()]
        L(f"FATAL: AGXMetal13_3 is not loaded; modules={names[:50]}")
        return

    specs = [
        (ADDR_INITIMPL_ENTRY, "bp_entry", "initImpl entry"),
        (ADDR_AFTER_CTOR, "bp_after_ctor", "after Texture constructor"),
        (ADDR_CHILD_CHECK, "bp_child_check", "Texture+0xe8 branch"),
        (ADDR_CPU_MAP_RETURN, "bp_cpu_map_return", "CPU accessor return"),
        (ADDR_GPU_MAP_RETURN, "bp_gpu_map_return", "GPU accessor return"),
        (ADDR_MAPPING_JOIN, "bp_mapping_join", "mapping branch join"),
        (ADDR_INITIMPL_EPILOGUE, "bp_epilogue", "initImpl epilogue"),
        (ADDR_ADDRESS_HELPER_ENTRY, "bp_address_helper", "write address helper"),
        (ADDR_WRITEREGION_MEMMOVE, "bp_memmove", "writeRegion memmove call"),
    ]
    for static_address, callback, label in specs:
        runtime_address = static_address + slide
        bp = target.BreakpointCreateByAddress(runtime_address)
        if not bp.IsValid():
            L(f"FAILED breakpoint at {hex(runtime_address)} ({label})")
            continue
        bp.SetScriptCallbackFunction(f"lldb_trace_initimpl.{callback}")
        L(
            f"bp{bp.GetID()} runtime={hex(runtime_address)} "
            f"static={hex(static_address)} {label} callback={callback}"
        )
    L("=== breakpoints armed ===")

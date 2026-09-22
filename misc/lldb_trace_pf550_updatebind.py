"""Stop in macOS AGX updateBindData for the 2388x1668 pf550 texture.

This tracer is intentionally descriptor-driven: it scans the private C++
texture object for the hardware width/height encoding instead of relying on
the macOS (+0x190) or iOS (+0x180) descriptor offset as a cross-OS ABI.
"""

import lldb
import struct


TEXT_STATIC_BASE = 0x1E53DD000
UPDATE_BIND_STATIC = 0x1E57716B4
COMPRESSION_OFFSET_HELPER_STATIC = 0x1E57711B0
UPDATE_BIND_THREE_ARG_STATIC = 0x1E5770C1C
UPDATE_BIND_TWO_ARG_STATIC = 0x1E5770C14
# Stop after x0/x2/x4/x6 have been restored, so the register dump is the
# actual five-argument dispatch tuple rather than the wrapper's saved inputs.
THREE_ARG_DISPATCH_STATIC = 0x1E5770C8C
THREE_ARG_DEVICE_GATE_STATIC = 0x1E5770C50
COMPRESSION_QUERY_STUB_STATIC = 0x1E5A5D5F0
COMPRESSION_QUERY_RETURN_STATIC = 0x1E5A5AE58
AFTER_COMPRESSIBLE_STORES_STATIC = 0x1E5771718
COMPRESSIBLE_VIRTUAL_CALL_STATIC = 0x1E577173C
PARENT_VIRTUAL_TARGET_LOAD_STATIC = 0x1E5771834
UNCOMPRESSIBLE_VIRTUAL_CALL_STATIC = 0x1E5771868

_slide = None
_target_thread = 0
_compressible = False
_target_texture = 0
_target_impl = 0
_target_return = 0
_target_metadata = 0
_entry_count = 0
_three_entry_count = 0
_compression_query_count = 0
_helper_path_count = 0

# Basic-block entries from the exact macOS 13.4 AGXMetal13_3 helper at
# 0x1e57711b0. These breakpoints are observation-only: the callback records
# the path and immediately resumes without changing a register or memory byte.
HELPER_PATH_BLOCKS = {
    0x1E57711B0: "entry",
    0x1E5771214: "parent-f8-zero",
    0x1E5771248: "parent-f8-nonzero-format",
    0x1E577125C: "format-bit16",
    0x1E5771280: "parent-layer-count",
    0x1E5771294: "parent-aligned-size",
    0x1E57712C8: "parent-fc-test",
    0x1E577134C: "parent-format-low-byte",
    0x1E577135C: "parent-format-zero",
    0x1E5771374: "format-bit16-size",
    0x1E5771398: "multiply-parent-layers",
    0x1E57713A0: "load-metadata-child",
    0x1E57713BC: "child-e0-test",
    0x1E57713D0: "child-f8-test",
    0x1E5771450: "iosurface-format-three",
    0x1E5771464: "iosurface-getter-one-return",
    0x1E5771478: "iosurface-getter-two-return",
    0x1E577148C: "iosurface-getter-three-return",
    0x1E5771494: "parent-format-nonzero",
    0x1E57714B0: "child-alignment-small",
    0x1E57714DC: "child-alignment-selected",
    0x1E5771574: "parent-format-high",
    0x1E5771590: "no-iosurface-format-zero",
    0x1E57715B4: "no-iosurface-format-three",
    0x1E57715DC: "fallback-row-bytes",
    0x1E57715E0: "multiply-plane-height",
    0x1E5771604: "last-plane-adjustment",
    0x1E577161C: "child-alignment-large",
    0x1E5771638: "align-parent-size",
    0x1E5771644: "optional-child-recursion",
    0x1E5771660: "final-layer-alignment",
    0x1E5771688: "return",
}

HELPER_IMPORT_STUBS = {
    0x1E5A5D57C: "parent-plane-delta-getter",
    0x1E5A5D60C: "getter-one-branch",
    0x1E5A5D63C: "getter-two-branch",
    0x1E5A5D5AC: "getter-three-branch",
}

PARENT_ACCEL_HELPER_STATIC = 0x1E576E1E8
PARENT_ACCEL_RETURN_STATIC = 0x1E576E4D0
PARENT_ACCEL_GETTER_RETURNS = {
    0x1E576E244: "getter-plane-zero-first",
    0x1E576E254: "getter-plane-one",
    0x1E576E268: "getter-plane-zero-second",
    0x1E576E278: "getter-plane-base-index",
}


def _reg(frame, name):
    return frame.FindRegister(name).GetValueAsUnsigned()


def _read(process, address, size):
    error = lldb.SBError()
    data = process.ReadMemory(address, size, error)
    return data if error.Success() else b""


def _read_u64(process, address):
    data = _read(process, address, 8)
    return struct.unpack("<Q", data)[0] if len(data) == 8 else None


def _read_u32(process, address):
    data = _read(process, address, 4)
    return struct.unpack("<I", data)[0] if len(data) == 4 else None


def _read_u16(process, address):
    data = _read(process, address, 2)
    return struct.unpack("<H", data)[0] if len(data) == 2 else None


def _read_u8(process, address):
    data = _read(process, address, 1)
    return data[0] if len(data) == 1 else None


def _fmt(value):
    return "UNREAD" if value is None else "%#x" % value


def _is_pf550_impl(process, impl):
    """Match the private pixel-format field owned by TextureGen4.

    The exact macOS constructor trace records Texture+0x20 == 0x226 for the
    private pf550 texture before its hardware descriptor exists.  This proves
    the private pixel format, not the dimensions; callers additionally keep the
    selected ObjC object/implementation pair stable across follow-up stops.
    """
    return impl >= 0x1000 and _read_u64(process, impl + 0x20) == 0x226


def _descriptor(process, impl):
    data = _read(process, impl + 0x140, 0x119)
    for relative in range(max(0, len(data) - 23)):
        value = int.from_bytes(data[relative:relative + 24], "little")
        width = ((value >> 28) & 0x3FFF) + 1
        height = ((value >> 42) & 0x3FFF) + 1
        if (width, height) != (2388, 1668):
            continue
        raw = (value >> 128) & ((1 << 64) - 1)
        return {
            "offset": relative + 0x140,
            "layout": (value >> 4) & 3,
            "compressed": (value >> 103) & 1,
            "extended": (value >> 127) & 1,
            "address": ((value >> 66) & ((1 << 36) - 1)) << 4,
            "acceleration": (raw & ((1 << 36) - 1)) << 4,
            "raw": raw,
            "bytes": data[relative:relative + 24].hex(),
        }
    return None


def _print_descriptor(frame, label, impl):
    descriptor = _descriptor(frame.GetThread().GetProcess(), impl)
    if not descriptor:
        print("PF550-UPDATEBIND %s impl=%#x descriptor=NOT-FOUND" %
              (label, impl))
        return
    print("PF550-UPDATEBIND %s impl=%#x descOff=%#x layout=%d "
          "compressed=%d extended=%d address=%#x acceleration=%#x "
          "accelerationRaw=%#x bytes=%s" %
          (label, impl, descriptor["offset"], descriptor["layout"],
           descriptor["compressed"], descriptor["extended"],
           descriptor["address"], descriptor["acceleration"],
           descriptor["raw"], descriptor["bytes"]))


def entry_callback(frame, _bp_location, _internal_dict):
    global _target_thread, _compressible, _target_texture, _target_impl
    global _target_return, _entry_count
    process = frame.GetThread().GetProcess()
    texture = _reg(frame, "x0")
    object_data = _read(process, texture + 0x208, 8)
    if len(object_data) != 8:
        return False
    impl = struct.unpack("<Q", object_data)[0]
    _entry_count += 1
    if not _is_pf550_impl(process, impl):
        return False
    descriptor = _descriptor(process, impl)
    if not descriptor:
        # At method entry the address-bearing descriptor may not have been
        # finalized yet.  Log the actual +0x190 candidate from the macOS
        # object so that this tracer does not silently assume otherwise.
        candidate = _read(process, impl + 0x190, 24)
        if len(candidate) == 24:
            value = int.from_bytes(candidate, "little")
            width = ((value >> 28) & 0x3FFF) + 1
            height = ((value >> 42) & 0x3FFF) + 1
            if _entry_count <= 128 or width >= 1000 or height >= 600:
                print("PF550-UPDATEBIND SEEN count=%d texture=%#x impl=%#x "
                      "candidate=%dx%d layout=%d compressed=%d extended=%d "
                      "gpuVA(x4)=%#x isCompressible(x5)=%d raw=%s" %
                      (_entry_count, texture, impl, width, height,
                       (value >> 4) & 3, (value >> 103) & 1,
                       (value >> 127) & 1, _reg(frame, "x4"),
                       _reg(frame, "x5") & 0xFF, candidate.hex()))
        # The descriptor is legitimately still zero at this entry.  The
        # Texture+0x20 filter above identifies pf550 without relying on call
        # order or on a descriptor that this method has not constructed yet.
    _target_thread = frame.GetThread().GetThreadID()
    _compressible = bool(_reg(frame, "x5") & 0xFF)
    _target_texture = texture
    _target_impl = impl
    _target_return = _reg(frame, "lr")
    print("PF550-UPDATEBIND ENTRY thread=%#x texture=%#x impl=%#x "
          "cpuAddress(x2)=%#x cpuMetadata(x3)=%#x gpuVA(x4)=%#x "
          "isCompressible(x5)=%d shouldInitMetadata(x6)=%d lr=%#x" %
          (_target_thread, texture, impl, _reg(frame, "x2"),
           _reg(frame, "x3"), _reg(frame, "x4"),
           _reg(frame, "x5") & 0xFF, _reg(frame, "x6") & 0xFF,
           _reg(frame, "lr")))
    if _slide is not None:
        print("PF550-UPDATEBIND ENTRY caller runtime=%#x static=%#x" %
              (_reg(frame, "lr"), _reg(frame, "lr") - _slide))
    _print_descriptor(frame, "entry", impl)
    return True


def three_arg_entry_callback(frame, _bp_location, _internal_dict):
    global _target_thread, _target_texture, _target_impl, _three_entry_count
    process = frame.GetThread().GetProcess()
    texture = _reg(frame, "x0")
    object_data = _read(process, texture + 0x208, 8)
    if len(object_data) != 8:
        return False
    impl = struct.unpack("<Q", object_data)[0]
    _three_entry_count += 1
    if not _is_pf550_impl(process, impl):
        return False
    descriptor = _descriptor(process, impl)
    # The descriptor may still be zero; Texture+0x20 already established that
    # this is the private pf550 path.
    _target_thread = frame.GetThread().GetThreadID()
    _target_texture = texture
    _target_impl = impl
    print("PF550-UPDATEBIND THREE-ARG ENTRY count=%d thread=%#x "
          "texture=%#x impl=%#x cpuAddress(x2)=%#x gpuVA(x3)=%#x "
          "shouldInitMetadata(x4)=%d lr=%#x" %
          (_three_entry_count, _target_thread, texture, impl,
           _reg(frame, "x2"), _reg(frame, "x3"), _reg(frame, "x4") & 0xFF,
           _reg(frame, "lr")))
    _print_descriptor(frame, "three-arg-entry", impl)
    return True


def compression_query_callback(frame, _bp_location, _internal_dict):
    global _target_thread, _compression_query_count
    _compression_query_count += 1
    if _compression_query_count != 1:
        return False
    _target_thread = frame.GetThread().GetThreadID()
    print("PF550-COMPRESSION-QUERY ENTRY count=%d thread=%#x "
          "iosurface(x0)=%#x plane(x1)=%#x lr=%#x" %
          (_compression_query_count, _target_thread, _reg(frame, "x0"),
           _reg(frame, "x1"), _reg(frame, "lr")))
    if _slide is not None:
        print("PF550-COMPRESSION-QUERY caller runtime=%#x static=%#x" %
              (_reg(frame, "lr"), _reg(frame, "lr") - _slide))
    return True


def _arm(debugger, static_address, label):
    target = debugger.GetSelectedTarget()
    address = static_address + _slide
    breakpoint = target.BreakpointCreateByAddress(address)
    breakpoint.SetThreadID(_target_thread)
    breakpoint.SetOneShot(True)
    print("PF550-UPDATEBIND armed %s bp=%d address=%#x thread=%#x" %
          (label, breakpoint.GetID(), address, _target_thread))


def arm_after_update(debugger):
    address = (AFTER_COMPRESSIBLE_STORES_STATIC if _compressible
               else UNCOMPRESSIBLE_VIRTUAL_CALL_STATIC)
    _arm(debugger, address,
         "after-compressible-stores" if _compressible
         else "uncompressible-virtual-call")


def arm_three_arg_dispatch(debugger):
    _arm(debugger, THREE_ARG_DISPATCH_STATIC, "three-arg-dispatch")


def arm_three_arg_device_gate(debugger):
    _arm(debugger, THREE_ARG_DEVICE_GATE_STATIC, "three-arg-device-gate")


def arm_compression_query_return(debugger):
    _arm(debugger, COMPRESSION_QUERY_RETURN_STATIC,
         "compression-query-return")


def arm_compressible_virtual_call(debugger):
    _arm(debugger, COMPRESSIBLE_VIRTUAL_CALL_STATIC,
         "compressible-virtual-call")


def arm_helper_return(debugger):
    _arm(debugger, UPDATE_BIND_STATIC + 0x50, "compression-offset-return")


def arm_after_metadata_virtual(debugger):
    _arm(debugger, UPDATE_BIND_STATIC + 0x8C,
         "after-metadata-virtual-call")


def arm_parent_virtual_target_load(debugger):
    _arm(debugger, PARENT_VIRTUAL_TARGET_LOAD_STATIC,
         "parent-virtual-target-load")


def capture_and_arm_parent_virtual(debugger):
    process = debugger.GetSelectedTarget().GetProcess()
    target = process.GetTarget()
    frame = process.GetSelectedThread().GetFrameAtIndex(0)
    signed_target = _reg(frame, "x1")
    stripped_target = signed_target & 0x0000FFFFFFFFFFFF
    address = target.ResolveLoadAddress(stripped_target)
    module = address.GetModule() if address.IsValid() else lldb.SBModule()
    filename = (module.GetFileSpec().GetFilename()
                if module.IsValid() else None)
    static_target = (stripped_target - _slide
                     if filename == "AGXMetal13_3" else None)
    print("PF550-UPDATEBIND PARENT-VIRTUAL parent(x0)=%#x "
          "vtableSlot(x16)=%#x signedTarget(x1)=%#x strippedTarget=%#x "
          "module=%s static=%s" %
          (_reg(frame, "x0"), _reg(frame, "x16"), signed_target,
           stripped_target, filename or "UNKNOWN",
           "N/A" if static_target is None else "%#x" % static_target))
    breakpoint = target.BreakpointCreateByAddress(stripped_target)
    breakpoint.SetThreadID(_target_thread)
    breakpoint.SetOneShot(True)
    print("PF550-UPDATEBIND armed parent-virtual-entry bp=%d address=%#x "
          "thread=%#x" %
          (breakpoint.GetID(), stripped_target, _target_thread))


def dump_parent_and_child_addresses(debugger):
    process = debugger.GetSelectedTarget().GetProcess()
    parent = _target_impl
    child = _read_u64(process, parent + 0x1D8)
    print("PF550-UPDATEBIND PARENT-FIELDS parent=%#x gpu+40=%s cpu+130=%s "
          "child+1d8=%s childGpu+40=%s childCpu+130=%s" %
          (parent, _fmt(_read_u64(process, parent + 0x40)),
           _fmt(_read_u64(process, parent + 0x130)), _fmt(child),
           _fmt(_read_u64(process, child + 0x40))
               if child and child >= 0x1000 else "INVALID",
           _fmt(_read_u64(process, child + 0x130))
               if child and child >= 0x1000 else "INVALID"))


def arm_caller_return(debugger):
    target = debugger.GetSelectedTarget()
    breakpoint = target.BreakpointCreateByAddress(_target_return)
    breakpoint.SetThreadID(_target_thread)
    breakpoint.SetOneShot(True)
    print("PF550-UPDATEBIND armed caller-return bp=%d address=%#x thread=%#x" %
          (breakpoint.GetID(), _target_return, _target_thread))


def dump_impl_from_x24(debugger, label):
    process = debugger.GetSelectedTarget().GetProcess()
    frame = process.GetSelectedThread().GetFrameAtIndex(0)
    _print_descriptor(frame, label, _reg(frame, "x24"))


def dump_target_impl(debugger, label):
    process = debugger.GetSelectedTarget().GetProcess()
    frame = process.GetSelectedThread().GetFrameAtIndex(0)
    _print_descriptor(frame, label, _target_impl)


def dump_helper_return(debugger):
    process = debugger.GetSelectedTarget().GetProcess()
    frame = process.GetSelectedThread().GetFrameAtIndex(0)
    offset = _reg(frame, "x0")
    gpu_address = _reg(frame, "x19")
    print("PF550-UPDATEBIND OFFSET helperReturn(x0)=%#x gpuVA(x19)=%#x "
          "sum=%#x" % (offset, gpu_address,
                        (offset + gpu_address) & 0xFFFFFFFFFFFFFFFF))


def _dump_field_set(process, label, base, fields):
    if base is None or base < 0x1000:
        print("PF550-HELPER INPUT %s base=%s INVALID" % (label, _fmt(base)))
        return
    readers = {1: _read_u8, 2: _read_u16, 4: _read_u32, 8: _read_u64}
    values = []
    for offset, size in fields:
        value = readers[size](process, base + offset)
        values.append("+%#x/%d=%s" % (offset, size, _fmt(value)))
    print("PF550-HELPER INPUT %s base=%#x %s" %
          (label, base, " ".join(values)))


def dump_helper_inputs(debugger):
    """Snapshot every field read by the 0x1e57711b0 helper and its caller."""
    process = debugger.GetSelectedTarget().GetProcess()
    parent = _target_impl
    parent_fields = (
        (0x20, 8), (0x40, 8), (0x78, 4), (0x7C, 4), (0x80, 4),
        (0xA0, 8), (0xA8, 4), (0xC8, 8), (0xF8, 4), (0xFC, 1),
        (0x130, 8), (0x148, 8), (0x180, 4), (0x184, 4),
        (0x188, 2), (0x1D8, 8),
    )
    child_fields = (
        (0x10, 1), (0x40, 8), (0xE0, 1), (0xF8, 4), (0x130, 8),
        (0x148, 8), (0x184, 1), (0x185, 1), (0x188, 2),
        (0x410, 8), (0x438, 8), (0x440, 8), (0x448, 8),
    )
    layout_fields = ((0x18, 4), (0x48, 4), (0x58, 1), (0x59, 1))
    child = _read_u64(process, parent + 0x1D8)
    layout = _read_u64(process, parent + 0xC8)
    _dump_field_set(process, "parent", parent, parent_fields)
    _dump_field_set(process, "metadata-child", child, child_fields)
    _dump_field_set(process, "layout-object", layout, layout_fields)
    print("PF550-HELPER INPUT call plane(x1)=0 recurse(x2)=0")


def helper_path_callback(frame, _bp_location, _internal_dict):
    global _helper_path_count
    _helper_path_count += 1
    runtime_pc = _reg(frame, "pc")
    static_pc = runtime_pc - _slide
    label = HELPER_PATH_BLOCKS.get(static_pc, "unknown")
    print("PF550-HELPER PATH seq=%d static=%#x label=%s "
          "x0=%#x x8=%#x x9=%#x x19=%#x x20=%#x x21=%#x "
          "x22=%#x x23=%#x x24=%#x" %
          (_helper_path_count, static_pc, label, _reg(frame, "x0"),
           _reg(frame, "x8"), _reg(frame, "x9"), _reg(frame, "x19"),
           _reg(frame, "x20"), _reg(frame, "x21"), _reg(frame, "x22"),
           _reg(frame, "x23"), _reg(frame, "x24")))
    return False


def helper_import_callback(frame, _bp_location, _internal_dict):
    target = frame.GetThread().GetProcess().GetTarget()
    runtime_pc = _reg(frame, "pc")
    static_pc = runtime_pc - _slide
    signed_target = _reg(frame, "x16")
    stripped_target = signed_target & 0x0000FFFFFFFFFFFF
    address = target.ResolveLoadAddress(stripped_target)
    symbol = address.GetSymbol() if address.IsValid() else lldb.SBSymbol()
    function = address.GetFunction() if address.IsValid() else lldb.SBFunction()
    name = None
    if function.IsValid():
        name = function.GetName()
    if not name and symbol.IsValid():
        name = symbol.GetName()
    module = address.GetModule() if address.IsValid() else lldb.SBModule()
    filename = (module.GetFileSpec().GetFilename()
                if module.IsValid() else None)
    text_section = module.FindSection("__TEXT") if module.IsValid() else None
    module_base = (text_section.GetLoadAddress(target)
                   if text_section and text_section.IsValid()
                   else lldb.LLDB_INVALID_ADDRESS)
    module_offset = (stripped_target - module_base
                     if module_base != lldb.LLDB_INVALID_ADDRESS else None)
    print("PF550-HELPER IMPORT static=%#x label=%s gotSlot(x17)=%#x "
          "signedTarget(x16)=%#x strippedTarget=%#x module=%s "
          "moduleBase=%s moduleOffset=%s symbol=%s" %
          (static_pc, HELPER_IMPORT_STUBS.get(static_pc, "unknown"),
           _reg(frame, "x17"), signed_target, stripped_target,
           filename or "UNKNOWN",
           "UNKNOWN" if module_base == lldb.LLDB_INVALID_ADDRESS
               else "%#x" % module_base,
           "UNKNOWN" if module_offset is None else "%#x" % module_offset,
           name or "UNKNOWN"))
    return False


def parent_accel_getter_return_callback(frame, _bp_location, _internal_dict):
    static_pc = _reg(frame, "pc") - _slide
    print("PF550-PARENT-ACCEL GETTER static=%#x label=%s return(x0)=%#x "
          "planeIndex(x20)=%#x first0(x21)=%#x second0(x23)=%#x "
          "delta(x24)=%#x" %
          (static_pc, PARENT_ACCEL_GETTER_RETURNS.get(static_pc, "unknown"),
           _reg(frame, "x0"), _reg(frame, "x20"), _reg(frame, "x21"),
           _reg(frame, "x23"), _reg(frame, "x24")))
    return False


def arm_parent_accel_helper_entry(debugger):
    _arm(debugger, PARENT_ACCEL_HELPER_STATIC, "parent-accel-helper-entry")


def arm_parent_accel_observers(debugger):
    target = debugger.GetSelectedTarget()
    stub = target.BreakpointCreateByAddress(0x1E5A5D57C + _slide)
    stub.SetThreadID(_target_thread)
    stub.SetOneShot(True)
    stub.SetScriptCallbackFunction(
        "lldb_trace_pf550_updatebind.helper_import_callback")
    for static_address in sorted(PARENT_ACCEL_GETTER_RETURNS):
        breakpoint = target.BreakpointCreateByAddress(static_address + _slide)
        breakpoint.SetThreadID(_target_thread)
        breakpoint.SetOneShot(True)
        breakpoint.SetScriptCallbackFunction(
            "lldb_trace_pf550_updatebind.parent_accel_getter_return_callback")
    _arm(debugger, PARENT_ACCEL_RETURN_STATIC, "parent-accel-helper-return")
    print("PF550-PARENT-ACCEL armed getter-returns=%d thread=%#x" %
          (len(PARENT_ACCEL_GETTER_RETURNS), _target_thread))


def arm_helper_path(debugger):
    target = debugger.GetSelectedTarget()
    for static_address in sorted(HELPER_PATH_BLOCKS):
        breakpoint = target.BreakpointCreateByAddress(static_address + _slide)
        breakpoint.SetThreadID(_target_thread)
        breakpoint.SetOneShot(True)
        breakpoint.SetScriptCallbackFunction(
            "lldb_trace_pf550_updatebind.helper_path_callback")
    for static_address in sorted(HELPER_IMPORT_STUBS):
        breakpoint = target.BreakpointCreateByAddress(static_address + _slide)
        breakpoint.SetThreadID(_target_thread)
        breakpoint.SetOneShot(True)
        breakpoint.SetScriptCallbackFunction(
            "lldb_trace_pf550_updatebind.helper_import_callback")
    print("PF550-HELPER armed path-blocks=%d import-stubs=%d thread=%#x" %
          (len(HELPER_PATH_BLOCKS), len(HELPER_IMPORT_STUBS), _target_thread))


def capture_metadata(debugger, label):
    global _target_metadata
    process = debugger.GetSelectedTarget().GetProcess()
    frame = process.GetSelectedThread().GetFrameAtIndex(0)
    _target_metadata = _reg(frame, "x24")
    gpu = _read_u64(process, _target_metadata + 0x40)
    cpu = _read_u64(process, _target_metadata + 0x130)
    print("PF550-UPDATEBIND METADATA %s object=%#x gpu+40=%s cpu+130=%s "
          "x25=%#x x23=%#x" %
          (label, _target_metadata,
           "UNREAD" if gpu is None else "%#x" % gpu,
           "UNREAD" if cpu is None else "%#x" % cpu,
           _reg(frame, "x25"), _reg(frame, "x23")))
    _print_descriptor(frame, label, _target_metadata)


def dump_saved_metadata(debugger, label):
    process = debugger.GetSelectedTarget().GetProcess()
    frame = process.GetSelectedThread().GetFrameAtIndex(0)
    gpu = _read_u64(process, _target_metadata + 0x40)
    cpu = _read_u64(process, _target_metadata + 0x130)
    print("PF550-UPDATEBIND METADATA %s object=%#x gpu+40=%s cpu+130=%s" %
          (label, _target_metadata,
           "UNREAD" if gpu is None else "%#x" % gpu,
           "UNREAD" if cpu is None else "%#x" % cpu))
    _print_descriptor(frame, label, _target_metadata)


def dump_three_arg_device_gate(debugger):
    process = debugger.GetSelectedTarget().GetProcess()
    frame = process.GetSelectedThread().GetFrameAtIndex(0)
    inner = _reg(frame, "x0")
    mac_child = _read_u64(process, inner + 0x1D8)
    ios_child = _read_u64(process, inner + 0x1C8)

    def field(pointer, offset):
        if pointer is None or pointer < 0x1000:
            return None
        return _read_u64(process, pointer + offset)

    wrapper_texture = _reg(frame, "x22")
    print("PF550-UPDATEBIND GATE texture(x22)=%#x target=%#x match=%s "
          "inner(x0)=%#x x8=%#x "
          "inner+1c8=%s inner+1d8=%s" %
          (wrapper_texture, _target_texture,
           "YES" if wrapper_texture == _target_texture else "NO",
           inner, _reg(frame, "x8"),
           "UNREAD" if ios_child is None else "%#x" % ios_child,
           "UNREAD" if mac_child is None else "%#x" % mac_child))
    print("PF550-UPDATEBIND GATE macChild+3f8=%s macChild+418=%s "
          "iosChild+3f8=%s iosChild+418=%s" %
          tuple("UNREAD" if value is None else "%#x" % value for value in (
              _read_u32(process, mac_child + 0x3F8)
                  if mac_child and mac_child >= 0x1000 else None,
              _read_u32(process, mac_child + 0x418)
                  if mac_child and mac_child >= 0x1000 else None,
              _read_u32(process, ios_child + 0x3F8)
                  if ios_child and ios_child >= 0x1000 else None,
              _read_u32(process, ios_child + 0x418)
                  if ios_child and ios_child >= 0x1000 else None)))

    # Capture the complete neighborhood used by the macOS/iOS gate and by the
    # following metadata-address calculation.  Values are observations only;
    # no alternate offset is written back into the live object.
    for child_label, child in (("mac", mac_child), ("ios", ios_child)):
        if child is None or child < 0x1000:
            continue
        values = []
        for offset in range(0x3D0, 0x431, 8):
            value = _read_u64(process, child + offset)
            values.append("%#x=%s" % (
                offset, "UNREAD" if value is None else "%#x" % value))
        print("PF550-UPDATEBIND GATE %sChild fields %s" %
              (child_label, " ".join(values)))


def continue_until_target(debugger):
    """Ignore unrelated first-chance exceptions until our callback matches."""
    process = debugger.GetSelectedTarget().GetProcess()
    for stop_count in range(1, 257):
        error = process.Continue()
        if error.Fail():
            print("PF550-UPDATEBIND FATAL target continue failed: %s" % error)
            return False
        if _target_thread:
            print("PF550-UPDATEBIND target selected after %d stops" % stop_count)
            return True
        state = process.GetState()
        if state != lldb.eStateStopped:
            print("PF550-UPDATEBIND FATAL target process state=%d" % state)
            return False
        thread = process.GetSelectedThread()
        print("PF550-UPDATEBIND ignored stop=%d reason=%d description=%s" %
              (stop_count, thread.GetStopReason(),
               thread.GetStopDescription(256)))
    print("PF550-UPDATEBIND FATAL target absent after 256 stops")
    return False


def _find_slide(target):
    for module in target.module_iter():
        if (module.GetFileSpec().GetFilename() or "") != "AGXMetal13_3":
            continue
        section = module.FindSection("__TEXT")
        if not section.IsValid():
            continue
        load = section.GetLoadAddress(target)
        if load != lldb.LLDB_INVALID_ADDRESS:
            return load - TEXT_STATIC_BASE
    return None


def install(debugger):
    global _slide
    target = debugger.GetSelectedTarget()
    _slide = _find_slide(target)
    if _slide is None:
        print("PF550-UPDATEBIND FATAL AGXMetal13_3 module not found")
        return False
    breakpoint = target.BreakpointCreateByAddress(UPDATE_BIND_STATIC + _slide)
    breakpoint.SetScriptCallbackFunction(
        "lldb_trace_pf550_updatebind.entry_callback")
    print("PF550-UPDATEBIND installed entry=%#x slide=%#x bp=%d" %
          (UPDATE_BIND_STATIC + _slide, _slide, breakpoint.GetID()))
    three = target.BreakpointCreateByAddress(
        UPDATE_BIND_THREE_ARG_STATIC + _slide)
    three.SetScriptCallbackFunction(
        "lldb_trace_pf550_updatebind.three_arg_entry_callback")
    print("PF550-UPDATEBIND installed three-arg=%#x bp=%d two-arg-thunk=%#x" %
          (UPDATE_BIND_THREE_ARG_STATIC + _slide, three.GetID(),
           UPDATE_BIND_TWO_ARG_STATIC + _slide))
    return True


def install_five_only(debugger):
    global _slide
    target = debugger.GetSelectedTarget()
    _slide = _find_slide(target)
    if _slide is None:
        print("PF550-UPDATEBIND FATAL AGXMetal13_3 module not found")
        return False
    breakpoint = target.BreakpointCreateByAddress(UPDATE_BIND_STATIC + _slide)
    breakpoint.SetScriptCallbackFunction(
        "lldb_trace_pf550_updatebind.entry_callback")
    print("PF550-UPDATEBIND installed five-only entry=%#x slide=%#x bp=%d" %
          (UPDATE_BIND_STATIC + _slide, _slide, breakpoint.GetID()))
    return True


def install_three_only(debugger):
    global _slide
    target = debugger.GetSelectedTarget()
    _slide = _find_slide(target)
    if _slide is None:
        print("PF550-UPDATEBIND FATAL AGXMetal13_3 module not found")
        return False
    breakpoint = target.BreakpointCreateByAddress(
        UPDATE_BIND_THREE_ARG_STATIC + _slide)
    breakpoint.SetScriptCallbackFunction(
        "lldb_trace_pf550_updatebind.three_arg_entry_callback")
    print("PF550-UPDATEBIND installed three-only entry=%#x slide=%#x bp=%d" %
          (UPDATE_BIND_THREE_ARG_STATIC + _slide, _slide,
           breakpoint.GetID()))
    return True


def install_compression_query(debugger):
    global _slide
    target = debugger.GetSelectedTarget()
    _slide = _find_slide(target)
    if _slide is None:
        print("PF550-COMPRESSION-QUERY FATAL AGXMetal13_3 module not found")
        return False
    breakpoint = target.BreakpointCreateByAddress(
        COMPRESSION_QUERY_STUB_STATIC + _slide)
    breakpoint.SetScriptCallbackFunction(
        "lldb_trace_pf550_updatebind.compression_query_callback")
    print("PF550-COMPRESSION-QUERY installed stub=%#x slide=%#x bp=%d" %
          (COMPRESSION_QUERY_STUB_STATIC + _slide, _slide,
           breakpoint.GetID()))
    return True


def wait_for_agx_and_install(debugger):
    """Continue only between dyld image-load stops until AGX is present."""
    target = debugger.GetSelectedTarget()
    process = target.GetProcess()
    for event_count in range(1, 1025):
        if _find_slide(target) is not None:
            print("PF550-UPDATEBIND AGX loaded after %d image events" %
                  (event_count - 1))
            return install(debugger)
        error = process.Continue()
        if error.Fail():
            print("PF550-UPDATEBIND FATAL continue failed: %s" % error)
            return False
        if process.GetState() != lldb.eStateStopped:
            print("PF550-UPDATEBIND FATAL process state=%d before AGX load" %
                  process.GetState())
            return False
    print("PF550-UPDATEBIND FATAL AGX absent after 1024 image events")
    return False


def wait_for_agx_and_install_five(debugger):
    target = debugger.GetSelectedTarget()
    process = target.GetProcess()
    for event_count in range(1, 1025):
        if _find_slide(target) is not None:
            print("PF550-UPDATEBIND AGX loaded after %d image events" %
                  (event_count - 1))
            return install_five_only(debugger)
        error = process.Continue()
        if error.Fail():
            print("PF550-UPDATEBIND FATAL continue failed: %s" % error)
            return False
        if process.GetState() != lldb.eStateStopped:
            print("PF550-UPDATEBIND FATAL process state=%d before AGX load" %
                  process.GetState())
            return False
    print("PF550-UPDATEBIND FATAL AGX absent after 1024 image events")
    return False


def wait_for_agx_and_install_three(debugger):
    target = debugger.GetSelectedTarget()
    process = target.GetProcess()
    for event_count in range(1, 1025):
        if _find_slide(target) is not None:
            print("PF550-UPDATEBIND AGX loaded after %d image events" %
                  (event_count - 1))
            return install_three_only(debugger)
        error = process.Continue()
        if error.Fail():
            print("PF550-UPDATEBIND FATAL continue failed: %s" % error)
            return False
        if process.GetState() != lldb.eStateStopped:
            print("PF550-UPDATEBIND FATAL process state=%d before AGX load" %
                  process.GetState())
            return False
    print("PF550-UPDATEBIND FATAL AGX absent after 1024 image events")
    return False


def wait_for_agx_and_install_compression_query(debugger):
    target = debugger.GetSelectedTarget()
    process = target.GetProcess()
    for event_count in range(1, 1025):
        if _find_slide(target) is not None:
            print("PF550-COMPRESSION-QUERY AGX loaded after %d image events" %
                  (event_count - 1))
            return install_compression_query(debugger)
        error = process.Continue()
        if error.Fail():
            print("PF550-COMPRESSION-QUERY FATAL continue failed: %s" % error)
            return False
        if process.GetState() != lldb.eStateStopped:
            print("PF550-COMPRESSION-QUERY FATAL process state=%d before AGX load" %
                  process.GetState())
            return False
    print("PF550-COMPRESSION-QUERY FATAL AGX absent after 1024 image events")
    return False

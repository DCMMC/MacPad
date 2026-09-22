"""Trace the first pf550 AGXTexture IOSurface initializer to its exit.

All addresses are from the exact macOS 13.4 AGXMetal13_3 image.  The tracer is
read-only: callbacks record return registers at each RE-confirmed branch and
only stop at the common epilogue of the first IOSurface-backed texture call.
"""

import lldb


TEXT_STATIC_BASE = 0x1E53DD000
INIT_ENTRY = 0x1E5A5AE18
STAGES = (
    (0x1E5A5AE58, "compression-query-return", "x0"),
    (0x1E5A5AE94, "descriptor-validation-return", "x0"),
    (0x1E5A5AEA0, "surface-gate-return", "x0"),
    (0x1E5A5AED4, "initImpl-call", "x7"),
    (0x1E5A5AED8, "initImpl-return", "x0"),
    (0x1E5A5AF38, "super-init-return", "x0"),
    (0x1E5A5AFE0, "footer-validation-return", "x0"),
    (0x1E5A5AF70, "pre-super-failure-dealloc", "x0"),
    (0x1E5A5AF78, "nil-result-path", "x23"),
    (0x1E5A5AF7C, "common-return", "x0"),
)

# Branch and construction landmarks from the exact macOS 13.4
# -[AGXG13GFamilyTexture initImplWithDevice:Descriptor:iosurface:...].
# None of these callbacks stop or modify the process.  The outer common-return
# breakpoint remains the only intentional stop.
INNER_ENTRY = 0x1E5A4A284
INNER_STAGES = (
    (0x1E5A4A430, "compression-path-select"),
    (0x1E5A4A468, "early-error-check-a"),
    (0x1E5A4A4BC, "cpp-allocation-result"),
    (0x1E5A4A61C, "texture-constructor-return"),
    (0x1E5A4A640, "child-pointer-check"),
    (0x1E5A4A700, "child-ready-check"),
    (0x1E5A4A740, "compression-footprint-match"),
    (0x1E5A4A768, "device-feature-path"),
    (0x1E5A4A7A0, "early-error-check-b"),
    (0x1E5A4A864, "early-error-check-c"),
    (0x1E5A4A894, "explicit-failure-a"),
    (0x1E5A4A974, "compression-feature-gate"),
    (0x1E5A4A980, "compression-feature-pointer"),
    (0x1E5A4A994, "compression-feature-decision"),
    (0x1E5A4A998, "explicit-failure-b"),
    (0x1E5A4AF28, "success-tail"),
    (0x1E5A4AF48, "success-result"),
    (0x1E5A4AF4C, "inner-epilogue"),
)

# Branch landmarks inside the exact macOS 13.4 TextureGen4 constructor called
# at initImpl+0x394.  The compressed-IOSurface metadata object is installed at
# Texture+0x1d8 only if this path reaches metadata-allocation/install.  These
# breakpoints are observational and never alter registers or memory.
CONSTRUCTOR_ENTRY = 0x1E5A46868
CONSTRUCTOR_STAGES = (
    (0x1E5A471BC, "metadata-fields-reset"),
    (0x1E5A471D0, "texture-c0-gate"),
    (0x1E5A471F0, "global-feature-gate-a"),
    (0x1E5A47200, "global-feature-gate-b"),
    (0x1E5A47208, "texture-byte10-gate"),
    (0x1E5A472D4, "metadata-eligibility-entry"),
    (0x1E5A472F8, "texture-flags-global-gate"),
    (0x1E5A47304, "global-disable-gate"),
    (0x1E5A4731C, "memory-order-gate"),
    (0x1E5A47328, "compression-settings-low-gate"),
    (0x1E5A47334, "format-info-gate"),
    (0x1E5A47348, "compression-settings-high-gate"),
    (0x1E5A47350, "texture-fc-gate"),
    (0x1E5A47354, "metadata-fallback"),
    (0x1E5A47934, "metadata-allocation-eligibility"),
    (0x1E5A4793C, "compression-kind-gate"),
    (0x1E5A47940, "texture-flags-gate"),
    (0x1E5A47978, "metadata-condition-gate"),
    (0x1E5A4797C, "texture-flags-bit4-gate"),
    (0x1E5A47980, "metadata-allocation"),
    (0x1E5A4801C, "dimension-fallback"),
    (0x1E5A4805C, "metadata-install"),
    (0x1E5A473DC, "constructor-return"),
)

_slide = None
_target_thread = 0
_target_self = 0
_sequence = 0
_done = False
_constructor_self = 0


def _reg(frame, name):
    return frame.FindRegister(name).GetValueAsUnsigned()


def _read(frame, address, size):
    if address < 0x1000:
        return 0
    error = lldb.SBError()
    value = frame.GetThread().GetProcess().ReadUnsignedFromMemory(
        address, size, error
    )
    return value if error.Success() else 0


def _find_slide(target):
    for module in target.module_iter():
        if (module.GetFileSpec().GetFilename() or "") != "AGXMetal13_3":
            continue
        section = module.FindSection("__TEXT")
        if section.IsValid():
            load = section.GetLoadAddress(target)
            if load != lldb.LLDB_INVALID_ADDRESS:
                return load - TEXT_STATIC_BASE
    return None


def entry_callback(frame, _location, _dict):
    global _target_thread, _target_self, _sequence
    _sequence += 1
    if _target_thread:
        return False
    _target_thread = frame.GetThread().GetThreadID()
    _target_self = _reg(frame, "x0")
    print(
        "PF550-INIT entry sequence=%d thread=%#x self=%#x device(x2)=%#x "
        "descriptor(x3)=%#x iosurface(x4)=%#x plane(x5)=%d lr=%#x"
        % (
            _sequence,
            _target_thread,
            _target_self,
            _reg(frame, "x2"),
            _reg(frame, "x3"),
            _reg(frame, "x4"),
            _reg(frame, "x5"),
            _reg(frame, "lr"),
        )
    )
    return False


def stage_callback(frame, _location, _internal_dict):
    global _done
    if not _target_thread or frame.GetThread().GetThreadID() != _target_thread:
        return False
    static_pc = _reg(frame, "pc") - _slide
    stage = next((item for item in STAGES if item[0] == static_pc), None)
    if stage is None:
        return False
    _, label, register = stage
    print(
        "PF550-INIT stage=%s pc=%#x %s=%#x x19=%#x x22=%#x x23=%#x"
        % (
            label,
            _reg(frame, "pc"),
            register,
            _reg(frame, register),
            _reg(frame, "x19"),
            _reg(frame, "x22"),
            _reg(frame, "x23"),
        )
    )
    if label == "common-return":
        _done = True
        return True
    return False


def inner_entry_callback(frame, _location, _internal_dict):
    if not _target_thread or frame.GetThread().GetThreadID() != _target_thread:
        return False
    sp = _reg(frame, "sp")
    print(
        "PF550-INNER entry self=%#x device=%#x descriptor=%#x iosurface=%#x "
        "plane=%d buffer=%#x bytesPerRow=%d stack0=%#x stack8=%#x "
        "stack10=%#x lr=%#x"
        % (
            _reg(frame, "x0"), _reg(frame, "x2"), _reg(frame, "x3"),
            _reg(frame, "x4"), _reg(frame, "x5"), _reg(frame, "x6"),
            _reg(frame, "x7"), _read(frame, sp, 8),
            _read(frame, sp + 8, 8), _read(frame, sp + 0x10, 2),
            _reg(frame, "lr"),
        )
    )
    return False


def inner_stage_callback(frame, _location, _internal_dict):
    if not _target_thread or frame.GetThread().GetThreadID() != _target_thread:
        return False
    static_pc = _reg(frame, "pc") - _slide
    stage = next((item for item in INNER_STAGES if item[0] == static_pc), None)
    if stage is None:
        return False
    _, label = stage
    x23 = _reg(frame, "x23")
    print(
        "PF550-INNER stage=%s pc=%#x x0=%#x x8=%#x x19=%#x x20=%#x "
        "x21=%#x x22=%#x x23=%#x x24=%#x x25=%#x x26=%#x x27=%#x "
        "x28=%#x child(e8=%#x c0=%#x f8=%#x 1d8=%#x)"
        % (
            label, _reg(frame, "pc"), _reg(frame, "x0"),
            _reg(frame, "x8"), _reg(frame, "x19"), _reg(frame, "x20"),
            _reg(frame, "x21"), _reg(frame, "x22"), x23,
            _reg(frame, "x24"), _reg(frame, "x25"), _reg(frame, "x26"),
            _reg(frame, "x27"), _reg(frame, "x28"),
            _read(frame, x23 + 0xE8, 8), _read(frame, x23 + 0xC0, 1),
            _read(frame, x23 + 0xF8, 4), _read(frame, x23 + 0x1D8, 8),
        )
    )
    return False


def constructor_entry_callback(frame, _location, _internal_dict):
    global _constructor_self
    if not _target_thread or frame.GetThread().GetThreadID() != _target_thread:
        return False
    # The metadata constructor recursively invokes TextureGen4 for internal
    # texture records.  Record only the outer constructor belonging to the
    # pf550 initImpl call.
    if _constructor_self:
        return False
    _constructor_self = _reg(frame, "x0")
    sp = _reg(frame, "sp")
    print(
        "PF550-CTOR entry self=%#x x1=%#x w2=%#x w3=%#x x4=%#x x5=%#x "
        "x6=%#x x7=%#x stack0=%#x stack8=%#x stack10=%#x stack18=%#x "
        "lr=%#x"
        % (
            _constructor_self, _reg(frame, "x1"), _reg(frame, "x2"),
            _reg(frame, "x3"), _reg(frame, "x4"), _reg(frame, "x5"),
            _reg(frame, "x6"), _reg(frame, "x7"), _read(frame, sp, 8),
            _read(frame, sp + 8, 8), _read(frame, sp + 0x10, 8),
            _read(frame, sp + 0x18, 8), _reg(frame, "lr"),
        )
    )
    return False


def constructor_stage_callback(frame, _location, _internal_dict):
    if not _constructor_self or frame.GetThread().GetThreadID() != _target_thread:
        return False
    # x19 is the constructor's `this` after its prologue.  This also filters
    # recursive TextureGen4 calls made while building the metadata object.
    if _reg(frame, "x19") != _constructor_self:
        return False
    static_pc = _reg(frame, "pc") - _slide
    stage = next(
        (item for item in CONSTRUCTOR_STAGES if item[0] == static_pc), None
    )
    if stage is None:
        return False
    _, label = stage
    texture = _constructor_self
    format_info = _read(frame, texture + 0xC8, 8)
    settings = _reg(frame, "x25")
    print(
        "PF550-CTOR stage=%s pc=%#x x0=%#x x8=%#x x9=%#x x10=%#x "
        "x11=%#x x19=%#x x21=%#x x22=%#x x25=%#x x27=%#x "
        "tex(10=%#x 20=%#x 28=%#x 30=%#x 38=%#x 3a=%#x c0=%#x "
        "e8=%#x fc=%#x 1d8=%#x) fmt=%#x fmt(18=%#x 38=%#x) "
        "settings(48=%#x 4c=%#x)"
        % (
            label, _reg(frame, "pc"), _reg(frame, "x0"),
            _reg(frame, "x8"), _reg(frame, "x9"), _reg(frame, "x10"),
            _reg(frame, "x11"), _reg(frame, "x19"), _reg(frame, "x21"),
            _reg(frame, "x22"), settings, _reg(frame, "x27"),
            _read(frame, texture + 0x10, 1),
            _read(frame, texture + 0x20, 8),
            _read(frame, texture + 0x28, 8),
            _read(frame, texture + 0x30, 8),
            _read(frame, texture + 0x38, 2),
            _read(frame, texture + 0x3A, 1),
            _read(frame, texture + 0xC0, 1),
            _read(frame, texture + 0xE8, 1),
            _read(frame, texture + 0xFC, 1),
            _read(frame, texture + 0x1D8, 8),
            format_info, _read(frame, format_info + 0x18, 4),
            _read(frame, format_info + 0x38, 4),
            _read(frame, settings + 0x48, 4),
            _read(frame, settings + 0x4C, 2),
        )
    )
    return False


def install(debugger):
    global _slide
    target = debugger.GetSelectedTarget()
    _slide = _find_slide(target)
    if _slide is None:
        print("PF550-INIT FATAL AGXMetal13_3 module not found")
        return False
    entry = target.BreakpointCreateByAddress(INIT_ENTRY + _slide)
    entry.SetScriptCallbackFunction("lldb_trace_pf550_init.entry_callback")
    for address, label, register in STAGES:
        bp = target.BreakpointCreateByAddress(address + _slide)
        bp.SetScriptCallbackFunction("lldb_trace_pf550_init.stage_callback")
    inner_entry = target.BreakpointCreateByAddress(INNER_ENTRY + _slide)
    inner_entry.SetScriptCallbackFunction(
        "lldb_trace_pf550_init.inner_entry_callback"
    )
    for address, label in INNER_STAGES:
        bp = target.BreakpointCreateByAddress(address + _slide)
        bp.SetScriptCallbackFunction(
            "lldb_trace_pf550_init.inner_stage_callback"
        )
    constructor_entry = target.BreakpointCreateByAddress(
        CONSTRUCTOR_ENTRY + _slide
    )
    constructor_entry.SetScriptCallbackFunction(
        "lldb_trace_pf550_init.constructor_entry_callback"
    )
    for address, label in CONSTRUCTOR_STAGES:
        bp = target.BreakpointCreateByAddress(address + _slide)
        bp.SetScriptCallbackFunction(
            "lldb_trace_pf550_init.constructor_stage_callback"
        )
    print(
        "PF550-INIT installed entry=%#x slide=%#x outerStages=%d "
        "innerStages=%d constructorStages=%d"
        % (INIT_ENTRY + _slide, _slide, len(STAGES), len(INNER_STAGES),
           len(CONSTRUCTOR_STAGES))
    )
    return True


def wait_for_agx_and_install(debugger):
    target = debugger.GetSelectedTarget()
    process = target.GetProcess()
    for event_count in range(1, 1025):
        if _find_slide(target) is not None:
            print("PF550-INIT AGX loaded after %d image events" % (event_count - 1))
            return install(debugger)
        error = process.Continue()
        if error.Fail() or process.GetState() != lldb.eStateStopped:
            print("PF550-INIT FATAL continue=%s state=%d" %
                  (error, process.GetState()))
            return False
    print("PF550-INIT FATAL AGX absent after 1024 image events")
    return False


def continue_until_return(debugger):
    process = debugger.GetSelectedTarget().GetProcess()
    for stop_count in range(1, 257):
        error = process.Continue()
        if error.Fail():
            print("PF550-INIT FATAL continue failed: %s" % error)
            return False
        if _done:
            print("PF550-INIT target returned after %d debugger stops" % stop_count)
            return True
        if process.GetState() != lldb.eStateStopped:
            print("PF550-INIT FATAL process state=%d" % process.GetState())
            return False
        thread = process.GetSelectedThread()
        print("PF550-INIT ignored stop=%d reason=%d description=%s" %
              (stop_count, thread.GetStopReason(),
               thread.GetStopDescription(256)))
    print("PF550-INIT FATAL return absent after 256 stops")
    return False

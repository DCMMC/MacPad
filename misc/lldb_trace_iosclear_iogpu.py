"""Trace native-iosclear IOGPU setup from the first IOConnect call.

Loaded by lldb_trace_iosclear_iogpu.lldb after iosclear_ref stops at its
diagnostic EARLY-HOLD.  This is deliberately read-only: it records the real
iOS 16.3 user-space ABI and stops at the first command-buffer submission.
"""

import hashlib
import lldb
import struct
from pathlib import Path


_calls = 0
_selector_counts = {}
_resource_counts = {}
_resource_bytes = {}
_resource_records = []
_queue_records = []
_dump_dir = Path("/tmp/iosclear-native-iogpu")
_device_options = []
_api_create_options = []
_api_create_results = []
_agx_open_records = []
_api_property_records = []
_queue_function_records = []
_queue_function_results = []
_return_contexts = {}
_awaiting_agx_open = 0
_stop_on_type82 = False
_stop_on_compressed_type82 = False


# Exact unslid function VAs from the iPad13,6 iOS 16.3.1 (20D67) binaries.
# They are a fallback for Command Line Tools LLDB, which can enumerate the
# remote dyld shared cache in partial mode but has no local cache symbol file.
# Normal symbol breakpoints remain preferred whenever they resolve.
_IOS_20D67_IOKIT_TEXT_BASE = 0x18EE63000
_IOS_20D67_FALLBACKS = {
    "IOConnectCallMethod": 0x18EE664F4,
    "IOConnectCallStructMethod": 0x18EE664B0,
    "IOServiceOpen": 0x18EE79FAC,
    "-[IOGPUMetalDevice initWithAcceleratorPort:options:]": 0x1EEC5EFF4,
    "IOGPUDeviceCreateWithAPIProperty": 0x1EEC63A18,
    "IOGPUCommandQueueCreateWithQoS": 0x1EEC62A00,
}


def _reg(frame, name):
    return frame.FindRegister(name).GetValueAsUnsigned()


def _read(process, address, size):
    if not address or not size:
        return b""
    error = lldb.SBError()
    data = process.ReadMemory(address, size, error)
    if not error.Success():
        print("IOSCLEAR_IOGPU read-failed address=%#x size=%#x error=%s" %
              (address, size, error))
        return b""
    return data


def _u32(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


def _u64(data, offset):
    return struct.unpack_from("<Q", data, offset)[0]


def _hex(data, limit=0x80):
    return data[:limit].hex(" ")


def _dump(prefix, index, data):
    _dump_dir.mkdir(parents=True, exist_ok=True)
    path = _dump_dir / ("%s-%03d.bin" % (prefix, index))
    path.write_bytes(data)
    return "%s sha256=%s" % (path, hashlib.sha256(data).hexdigest())


def _install_return_breakpoint(frame, kind, details=None):
    """Record the result after the current call returns to its caller."""
    thread = frame.GetThread()
    if thread.GetNumFrames() < 2:
        print("IOSCLEAR_IOGPU RETURN-ARM kind=%s failed=no-caller" % kind)
        return
    caller = thread.GetFrameAtIndex(1)
    return_address = caller.GetPC()
    if return_address == lldb.LLDB_INVALID_ADDRESS:
        print("IOSCLEAR_IOGPU RETURN-ARM kind=%s failed=invalid-address" %
              kind)
        return
    target = thread.GetProcess().GetTarget()
    breakpoint = target.BreakpointCreateByAddress(return_address)
    breakpoint.SetThreadID(thread.GetThreadID())
    breakpoint.SetOneShot(True)
    breakpoint.SetScriptCallbackFunction(
        "lldb_trace_iosclear_iogpu.return_callback")
    _return_contexts[breakpoint.GetID()] = (kind, details or {})
    print("IOSCLEAR_IOGPU RETURN-ARM kind=%s bp=%d address=%#x" %
          (kind, breakpoint.GetID(), return_address))


def return_callback(frame, bp_location, internal_dict):
    del internal_dict
    breakpoint = bp_location.GetBreakpoint()
    kind, details = _return_contexts.pop(
        breakpoint.GetID(), ("unknown", {}))
    result = _reg(frame, "x0")
    status = result & 0xFFFFFFFF
    if kind == "api-create":
        _api_create_results.append(result)
        print("IOSCLEAR_IOGPU RETURN kind=%s result=%#x" % (kind, result))
        return False
    if kind == "queue-function":
        _queue_function_results.append(result)
        print("IOSCLEAR_IOGPU RETURN kind=%s result=%#x" % (kind, result))
        return False
    if kind == "agx-open":
        out_pointer = details.get("out_pointer", 0)
        out_data = _read(frame.GetThread().GetProcess(), out_pointer, 4)
        connect = _u32(out_data, 0) if len(out_data) == 4 else None
        if _agx_open_records:
            _agx_open_records[-1]["status"] = status
            _agx_open_records[-1]["connect"] = connect
        print("IOSCLEAR_IOGPU RETURN kind=%s status=%#x type=%#x "
              "connect=%s" %
              (kind, status, details.get("type", 0),
               "%#x" % connect if connect is not None else "unreadable"))
        return False
    extra = " ".join("%s=%s" % (key, value)
                     for key, value in sorted(details.items()))
    print("IOSCLEAR_IOGPU RETURN kind=%s status=%#x%s%s" %
          (kind, status, " " if extra else "", extra))
    return False


def _trace_resource(data):
    index = len(_resource_records) + 1
    if len(data) < 0x60:
        print("IOSCLEAR_IOGPU RES #%d short=%#x bytes=%s" %
              (index, len(data), _hex(data)))
        return

    resource_type = data[0]
    f14 = _u32(data, 0x14)
    f15 = data[0x15]
    fields = {offset: _u64(data, offset)
              for offset in (0x08, 0x18, 0x20, 0x28, 0x30, 0x38,
                             0x40, 0x48, 0x50, 0x58)}
    # This is an accounting aid, not a claim about every union arm.  For the
    # native type=0 heap arm, iOS IOGPU and the iOS kernel both consume +0x40
    # as the requested byte count.  Other types are printed but not folded
    # into the heap-byte total.
    requested = fields[0x40] if resource_type == 0 else 0
    _resource_counts[resource_type] = \
        _resource_counts.get(resource_type, 0) + 1
    _resource_bytes[resource_type] = \
        _resource_bytes.get(resource_type, 0) + requested
    record = (resource_type, f14, f15, fields, len(data))
    _resource_records.append(record)

    saved = _dump("res", index, data) if index <= 128 else "not-saved"
    print(
        "IOSCLEAR_IOGPU RES #%d type=%#x len=%#x f14=%#x f15=%#x "
        "+08=%#x +18=%#x +20=%#x +28=%#x +30=%#x +38=%#x "
        "+40=%#x +48=%#x +50=%#x +58=%#x %s" %
        (index, resource_type, len(data), f14, f15,
         fields[0x08], fields[0x18], fields[0x20], fields[0x28],
         fields[0x30], fields[0x38], fields[0x40], fields[0x48],
         fields[0x50], fields[0x58], saved))


def _trace_queue(selector, data):
    index = len(_queue_records) + 1
    qos = _u32(data, 0x400) if len(data) >= 0x408 else 0
    priority = _u32(data, 0x404) if len(data) >= 0x408 else 0
    nonzero = [(offset, value) for offset, value in enumerate(data)
               if value != 0]
    _queue_records.append((selector, qos, priority, nonzero))
    print("IOSCLEAR_IOGPU QUEUE #%d sel=%#x len=%#x qos=%#x "
          "priority=%#x nonzero-bytes=%d first=%s last=%s %s" %
          (index, selector, len(data), qos, priority, len(nonzero),
           _hex(data[:0x40]), _hex(data[-0x40:]),
           _dump("queue-sel%x" % selector, index, data)))


def print_summary():
    print("IOSCLEAR_IOGPU SUMMARY calls=%d selectors=%s queues=%d "
          "resources=%d" %
          (_calls, sorted(_selector_counts.items()), len(_queue_records),
           len(_resource_records)))
    print("IOSCLEAR_IOGPU SUMMARY device-options=%s api-create-options=%s "
          "api-create-results=%s agx-opens=%s api-property=%s "
          "queue-functions=%s queue-function-results=%s" %
          (_device_options, _api_create_options, _api_create_results,
           _agx_open_records, _api_property_records,
           _queue_function_records, _queue_function_results))
    for resource_type in sorted(_resource_counts):
        print("IOSCLEAR_IOGPU SUMMARY type=%#x count=%d native-type0-bytes=%#x" %
              (resource_type, _resource_counts[resource_type],
               _resource_bytes.get(resource_type, 0)))


def enable_type82_stop():
    """Stop at the first native IOSurface resource request for RE/disasm."""
    global _stop_on_type82
    _stop_on_type82 = True
    print("IOSCLEAR_IOGPU type82-stop enabled")


def enable_compressed_type82_stop():
    """Stop at the first compressed IOSurface resource request.

    A BGRA destination texture is also represented by resource type 0x82 and
    can be created before the pf550 source.  The native pf550 reference has a
    non-zero compression-header span at +0x58, so use that observed ABI field
    to select the source request without relying on resource ordering.
    """
    global _stop_on_compressed_type82
    _stop_on_compressed_type82 = True
    print("IOSCLEAR_IOGPU compressed-type82-stop enabled")


def device_init_callback(frame, bp_location, internal_dict):
    del bp_location, internal_dict
    accelerator_port = _reg(frame, "x2")
    options = _reg(frame, "x3")
    _device_options.append(options)
    print("IOSCLEAR_IOGPU DEVICE-INIT accelerator-port=%#x options=%#x" %
          (accelerator_port, options))
    return False


def api_create_callback(frame, bp_location, internal_dict):
    del bp_location, internal_dict
    global _awaiting_agx_open
    accelerator_port = _reg(frame, "x0")
    api_string = _reg(frame, "x1")
    options = _reg(frame, "x2")
    process = frame.GetThread().GetProcess()
    api = _read(process, api_string, 16).split(b"\0", 1)[0]
    expected_type = 1 | ((options & 0xFFFF) << 16)
    _api_create_options.append(options)
    _awaiting_agx_open += 1
    print("IOSCLEAR_IOGPU API-CREATE accelerator-port=%#x options=%#x "
          "expected-open-type=%#x api=%r" %
          (accelerator_port, options, expected_type, api))
    _install_return_breakpoint(frame, "api-create")
    return False


def service_open_callback(frame, bp_location, internal_dict):
    del bp_location, internal_dict
    global _awaiting_agx_open
    if not _awaiting_agx_open:
        return False
    _awaiting_agx_open -= 1
    service = _reg(frame, "x0")
    owning_task = _reg(frame, "x1")
    open_type = _reg(frame, "x2") & 0xFFFFFFFF
    output_pointer = _reg(frame, "x3")
    _agx_open_records.append({"service": service,
                              "task": owning_task,
                              "type": open_type,
                              "out_pointer": output_pointer,
                              "status": None,
                              "connect": None})
    print("IOSCLEAR_IOGPU AGX-OPEN service=%#x task=%#x type=%#x "
          "connect-out=%#x" %
          (service, owning_task, open_type, output_pointer))
    _install_return_breakpoint(
        frame, "agx-open", {"type": open_type,
                            "out_pointer": output_pointer})
    return False


def queue_function_callback(frame, bp_location, internal_dict):
    del bp_location, internal_dict
    device = _reg(frame, "x0")
    qos = _reg(frame, "x1") & 0xFFFFFFFF
    priority = _reg(frame, "x2") & 0xFF
    _queue_function_records.append((qos, priority))
    print("IOSCLEAR_IOGPU QUEUE-FUNCTION device=%#x qos=%#x priority=%#x" %
          (device, qos, priority))
    _install_return_breakpoint(frame, "queue-function")
    return False


def io_connect_struct_callback(frame, bp_location, internal_dict):
    del bp_location, internal_dict
    selector = _reg(frame, "x1") & 0xFFFFFFFF
    struct_address = _reg(frame, "x2")
    struct_length = _reg(frame, "x3")
    if selector != 0x6 or struct_length != 0x10:
        return False
    process = frame.GetThread().GetProcess()
    data = _read(process, struct_address, struct_length)
    _api_property_records.append(data)
    print("IOSCLEAR_IOGPU API-PROPERTY sel=%#x len=%#x bytes=%s" %
          (selector, struct_length, _hex(data, 0x10)))
    _install_return_breakpoint(frame, "api-property", {"selector": "0x6"})
    return False


def io_connect_callback(frame, bp_location, internal_dict):
    del bp_location, internal_dict
    global _calls
    _calls += 1
    selector = _reg(frame, "x1")
    struct_address = _reg(frame, "x4")
    struct_length = _reg(frame, "x5")
    _selector_counts[selector] = _selector_counts.get(selector, 0) + 1

    process = frame.GetThread().GetProcess()
    # Native iOS 16.3 selectors: 0x6 device/API setup, 0x7 queue create,
    # 0x9 resource create, 0x1a command-buffer submit.
    if selector in (0x6, 0x7) and struct_length == 0x408:
        _trace_queue(selector, _read(process, struct_address, struct_length))
        _install_return_breakpoint(
            frame, "queue-create", {"selector": "%#x" % selector})
    elif selector == 0x9 and 0x60 <= struct_length <= 0x1000:
        data = _read(process, struct_address, struct_length)
        _trace_resource(data)
        is_type82 = bool(data) and data[0] == 0x82
        is_compressed_type82 = (is_type82 and len(data) >= 0x60 and
                                _u64(data, 0x58) != 0)
        if ((_stop_on_type82 and is_type82) or
                (_stop_on_compressed_type82 and is_compressed_type82)):
            print("IOSCLEAR_IOGPU TYPE82-STOP call=%d resource=%d "
                  "compressed=%s" %
                  (_calls, len(_resource_records),
                   "YES" if is_compressed_type82 else "NO"))
            return True
        _install_return_breakpoint(
            frame, "resource-create",
            {"index": str(len(_resource_records))})
    elif selector == 0x1A:
        print("IOSCLEAR_IOGPU SUBMIT call=%d inStruct=%#x len=%#x" %
              (_calls, struct_address, struct_length))
        print_summary()
        return True
    elif _selector_counts[selector] <= 4:
        print("IOSCLEAR_IOGPU CALL #%d sel=%#x scalar-count=%#x "
              "inStruct=%#x len=%#x" %
              (_calls, selector, _reg(frame, "x3"), struct_address,
               struct_length))

    return False


def _ios_20d67_shared_cache_slide(target):
    """Derive the shared-cache slide from the already-loaded IOKit image."""
    for module in target.module_iter():
        if module.GetFileSpec().GetFilename() != "IOKit":
            continue
        header = module.GetObjectFileHeaderAddress()
        load_address = header.GetLoadAddress(target)
        if load_address in (0, lldb.LLDB_INVALID_ADDRESS):
            continue
        slide = load_address - _IOS_20D67_IOKIT_TEXT_BASE
        print("IOSCLEAR_IOGPU shared-cache IOKit-load=%#x slide=%#x" %
              (load_address, slide))
        return slide
    print("IOSCLEAR_IOGPU shared-cache slide unavailable: IOKit not found")
    return None


def install(debugger):
    target = debugger.GetSelectedTarget()
    specifications = (
        ("IOConnectCallMethod", "io_connect_callback"),
        ("IOConnectCallStructMethod", "io_connect_struct_callback"),
        ("IOServiceOpen", "service_open_callback"),
        ("-[IOGPUMetalDevice initWithAcceleratorPort:options:]",
         "device_init_callback"),
        ("IOGPUDeviceCreateWithAPIProperty", "api_create_callback"),
        ("IOGPUCommandQueueCreateWithQoS", "queue_function_callback"),
    )
    slide = None
    for name, callback in specifications:
        breakpoint = target.BreakpointCreateByName(name)
        breakpoint.SetScriptCallbackFunction(
            "lldb_trace_iosclear_iogpu.%s" % callback)
        print("IOSCLEAR_IOGPU installed name=%s breakpoint=%d locations=%d" %
              (name, breakpoint.GetID(), breakpoint.GetNumLocations()))
        if breakpoint.GetNumLocations() != 0:
            continue
        if slide is None:
            slide = _ios_20d67_shared_cache_slide(target)
        file_address = _IOS_20D67_FALLBACKS.get(name)
        if slide is None or file_address is None:
            continue
        runtime_address = file_address + slide
        fallback = target.BreakpointCreateByAddress(runtime_address)
        fallback.SetScriptCallbackFunction(
            "lldb_trace_iosclear_iogpu.%s" % callback)
        print("IOSCLEAR_IOGPU installed-address name=%s file=%#x "
              "runtime=%#x breakpoint=%d locations=%d" %
              (name, file_address, runtime_address, fallback.GetID(),
               fallback.GetNumLocations()))


def install_submit_only(debugger):
    """Install only the selector-0x1a capture breakpoint by address.

    This avoids BreakpointCreateByName(), which makes host LLDB parse every
    remote shared-cache symbol table when no matching local cache is
    available.  The address is RE-confirmed for iPad13,6 iOS 16.3.1 (20D67),
    and the runtime slide is derived from the loaded IOKit image header.
    """
    target = debugger.GetSelectedTarget()
    slide = _ios_20d67_shared_cache_slide(target)
    if slide is None:
        raise RuntimeError("IOKit image unavailable; cannot derive slide")
    file_address = _IOS_20D67_FALLBACKS["IOConnectCallMethod"]
    runtime_address = file_address + slide
    breakpoint = target.BreakpointCreateByAddress(runtime_address)
    breakpoint.SetScriptCallbackFunction(
        "lldb_trace_iosclear_iogpu.io_connect_callback")
    print("IOSCLEAR_IOGPU submit-only file=%#x runtime=%#x "
          "breakpoint=%d locations=%d" %
          (file_address, runtime_address, breakpoint.GetID(),
           breakpoint.GetNumLocations()))

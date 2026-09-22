"""Read-only trace of native iOS IOGPUResourceCreate kernel outputs.

Attach after Metal/IOGPU have loaded.  The breakpoint is immediately after
IOGPUResourceCreate's IOConnectCallMethod on iPad13,6 iOS 16.3.1 (20D67), so
x19 is the input args pointer, x21 is the 0x50-byte output, and w0 is the
kernel status.  This avoids the colliding caller-return breakpoints used by
the broader trace and gives an exact native GPU-VA witness.
"""

import lldb
import struct


_IOKIT_FILE_BASE = 0x18EE63000
_AFTER_RESOURCE_CALL = 0x1EEC60130
_count = 0


def _reg(frame, name):
    return frame.FindRegister(name).GetValueAsUnsigned()


def _read(process, address, size):
    error = lldb.SBError()
    data = process.ReadMemory(address, size, error)
    if not error.Success():
        print("IOGPU_RES_RETURN read-failed address=%#x size=%#x error=%s" %
              (address, size, error))
        return b""
    return data


def _u32(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


def _u64(data, offset):
    return struct.unpack_from("<Q", data, offset)[0]


def callback(frame, bp_location, internal_dict):
    del bp_location, internal_dict
    global _count
    _count += 1
    process = frame.GetThread().GetProcess()
    args = _read(process, _reg(frame, "x19"), 0x68)
    output = _read(process, _reg(frame, "x21"), 0x50)
    status = _reg(frame, "x0") & 0xFFFFFFFF
    if len(args) != 0x68 or len(output) != 0x50:
        print("IOGPU_RES_RETURN #%d status=%#x short-read" %
              (_count, status))
        return False
    print(
        "IOGPU_RES_RETURN #%d status=%#x type=%#x f14=%#x f15=%#x "
        "in+20=%#x in+28=%#x in+30=%#x in+38=%#x in+40=%#x "
        "in+48=%#x in+50=%#x in+58=%#x "
        "out+00=%#x out+08=%#x out+10=%#x out+1c=%#x "
        "out+20=%#x out+48=%#x" %
        (_count, status, args[0], _u32(args, 0x14), args[0x15],
         _u64(args, 0x20), _u64(args, 0x28), _u64(args, 0x30),
         _u64(args, 0x38), _u64(args, 0x40), _u64(args, 0x48),
         _u64(args, 0x50), _u64(args, 0x58),
         _u64(output, 0x00), _u64(output, 0x08),
         _u64(output, 0x10), _u32(output, 0x1c),
         _u64(output, 0x20), _u64(output, 0x48)))
    if args[0] in (0, 0x82):
        print("IOGPU_RES_RETURN #%d type%02x-args=%s" %
              (_count, args[0], args.hex()))
    return False


def install(debugger):
    target = debugger.GetSelectedTarget()
    slide = None
    for module in target.module_iter():
        if module.GetFileSpec().GetFilename() == "IOKit":
            load = module.GetObjectFileHeaderAddress().GetLoadAddress(target)
            if load != lldb.LLDB_INVALID_ADDRESS:
                slide = load - _IOKIT_FILE_BASE
                print("IOGPU_RES_RETURN IOKit-load=%#x slide=%#x" %
                      (load, slide))
                break
    if slide is None:
        print("IOGPU_RES_RETURN install-failed: IOKit module unavailable")
        return
    address = _AFTER_RESOURCE_CALL + slide
    breakpoint = target.BreakpointCreateByAddress(address)
    breakpoint.SetScriptCallbackFunction(
        "lldb_trace_iogpu_resource_returns.callback")
    print("IOGPU_RES_RETURN installed address=%#x bp=%d locations=%d" %
          (address, breakpoint.GetID(), breakpoint.GetNumLocations()))

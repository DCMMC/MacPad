import lldb
import struct


_start = 0


def _reg(frame, name):
    return frame.FindRegister(name).GetValueAsUnsigned()


def _u64(process, address):
    error = lldb.SBError()
    data = process.ReadMemory(address, 8, error)
    if not error.Success() or len(data) != 8:
        print("IOSCLEAR_FIELD_WATCH read-failed address=%#x error=%s" %
              (address, error))
        return 0
    return struct.unpack("<Q", data)[0] & 0x0000FFFFFFFFFFFF


def capture_base(debugger, storage_offset=0x1F0):
    global _start
    target = debugger.GetSelectedTarget()
    process = target.GetProcess()
    frame = process.GetSelectedThread().GetFrameAtIndex(0)
    command_buffer = _reg(frame, "x0") & 0x0000FFFFFFFFFFFF
    storage = _u64(process, command_buffer + storage_offset)
    _start = _u64(process, storage + 0x28) if storage else 0
    current = _u64(process, storage + 0x30) if storage else 0
    end = _u64(process, storage + 0x38) if storage else 0
    print("IOSCLEAR_FIELD_WATCH captured commandBuffer=%#x "
          "storageOffset=%#x storage=%#x start=%#x current=%#x end=%#x" %
          (command_buffer, storage_offset, storage, _start, current, end))


def arm(debugger, offset, expected=None):
    target = debugger.GetSelectedTarget()
    if not _start:
        print("IOSCLEAR_FIELD_WATCH arm-failed no-base")
        return
    error = lldb.SBError()
    watchpoint = target.WatchAddress(_start + offset, 4, False, True, error)
    if error.Success():
        if expected is not None:
            watchpoint.SetCondition(
                "*(unsigned int *)%#x == %#x" %
                (_start + offset, expected))
        print("IOSCLEAR_FIELD_WATCH armed id=%d offset=%#x address=%#x "
              "expected=%s" %
              (watchpoint.GetID(), offset, _start + offset,
               "ANY" if expected is None else "%#x" % expected))
    else:
        print("IOSCLEAR_FIELD_WATCH arm-failed offset=%#x error=%s" %
              (offset, error))

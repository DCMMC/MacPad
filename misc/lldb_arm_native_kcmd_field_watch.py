import lldb
import struct

target = lldb.debugger.GetSelectedTarget()
process = target.GetProcess()
frame = process.GetSelectedThread().GetFrameAtIndex(0)


def reg(name):
    return frame.FindRegister(name).GetValueAsUnsigned()


def u64(address):
    error = lldb.SBError()
    data = process.ReadMemory(address, 8, error)
    if not error.Success() or len(data) != 8:
        print("IOSCLEAR_FIELD_WATCH read-failed address=%#x error=%s" %
              (address, error))
        return 0
    return struct.unpack("<Q", data)[0] & 0x0000FFFFFFFFFFFF


command_buffer = reg("x0") & 0x0000FFFFFFFFFFFF
# RE-confirmed via iOS 16.3
# -[IOGPUMetalCommandBuffer fillCommandBufferArgs:commandQueue:]: the
# AGXG13GFamilyCommandBuffer's storage ivar is self+0x1f0.  Storage +0x28 is
# the current KCMD allocation's start pointer.
storage = u64(command_buffer + 0x1F0)
start = u64(storage + 0x28) if storage else 0
current = u64(storage + 0x30) if storage else 0
end = u64(storage + 0x38) if storage else 0
print("IOSCLEAR_FIELD_WATCH commandBuffer=%#x storage=%#x "
      "start=%#x current=%#x end=%#x" %
      (command_buffer, storage, start, current, end))

if start:
    # These stable native values differ from the normalized macOS producer at
    # the same 1140x798 clear geometry.  Conditions skip the initial whole-
    # record zeroing and stop at the write that publishes the final value.
    for offset, expected in ((0x3A0, 4), (0x5E0, 1), (0x6BC, 8)):
        error = lldb.SBError()
        watchpoint = target.WatchAddress(start + offset, 4, False, True,
                                         error)
        if error.Success():
            watchpoint.SetCondition(
                "*(unsigned int *)%#x == %#x" %
                (start + offset, expected))
            print("IOSCLEAR_FIELD_WATCH armed id=%d offset=%#x "
                  "address=%#x expected=%#x" %
                  (watchpoint.GetID(), offset, start + offset, expected))
        else:
            print("IOSCLEAR_FIELD_WATCH arm-failed offset=%#x error=%s" %
                  (offset, error))

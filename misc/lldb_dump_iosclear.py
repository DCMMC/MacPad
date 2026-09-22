import lldb
import struct
import hashlib
from pathlib import Path

target = lldb.debugger.GetSelectedTarget()
process = target.GetProcess()
thread = process.GetSelectedThread()
frame = thread.GetFrameAtIndex(0)


def reg(name):
    return frame.FindRegister(name).GetValueAsUnsigned()


def read(addr, size):
    error = lldb.SBError()
    data = process.ReadMemory(addr, size, error)
    if not error.Success():
        print("IOSCLEAR_LLDB read failed addr=%#x size=%#x: %s" %
              (addr, size, error))
        return b""
    return data


def u64(addr):
    data = read(addr, 8)
    return struct.unpack("<Q", data)[0] if len(data) == 8 else 0


def u32_from(data, off):
    return struct.unpack_from("<I", data, off)[0]


def strip(raw):
    return raw & 0x0000FFFFFFFFFFFF


submit = reg("x4")
submit_len = reg("x5")
print("IOSCLEAR_LLDB submit=%#x len=%#x" % (submit, submit_len))
submit_bytes = read(submit, min(submit_len, 0x40))
print("IOSCLEAR_LLDB submit-bytes=" + submit_bytes.hex(" "))

seen = set()
for index, field in enumerate((0x10, 0x18)):
    descriptor_raw = u64(submit + field)
    descriptor = strip(descriptor_raw)
    self_raw = u64(descriptor + 0x20)
    self_ptr = strip(self_raw)
    # RE-confirmed via -[IOGPUMetalCommandBuffer
    # fillCommandBufferArgs:commandQueue:] in the iOS 16.3 IOGPU binary:
    # this ivar is IOGPUMetalCommandBufferStorage*, not a generic "state".
    # macOS 13.4 places the corresponding ivar at self+0x250.
    storage_raw = u64(self_ptr + 0x1F0)
    storage = strip(storage_raw)
    print("IOSCLEAR_LLDB descriptor[%d]=%#x raw=%#x self=%#x storage=%#x" %
          (index, descriptor, descriptor_raw, self_ptr, storage))
    if not storage or storage in seen:
        continue
    seen.add(storage)

    storage_bytes = read(storage, 0x378)
    storage_path = Path("/tmp/iosclear-native-storage.bin")
    storage_path.write_bytes(storage_bytes)
    print("IOSCLEAR_LLDB storage-saved=%s sha256=%s" %
          (storage_path, hashlib.sha256(storage_bytes).hexdigest()))

    segment_start = strip(u64(storage + 0x68))
    segment_limit = strip(u64(storage + 0x70))
    segment_current = strip(u64(storage + 0x328))
    segment_mode_data = read(storage + 0x340, 4)
    segment_mode = (struct.unpack("<i", segment_mode_data)[0]
                    if len(segment_mode_data) == 4 else 0)
    segment_length = (segment_current - segment_start
                      if segment_start <= segment_current <= segment_limit
                      else 0)
    print("IOSCLEAR_LLDB SEGMENT start=%#x current=%#x limit=%#x "
          "length=%#x mode=%d" %
          (segment_start, segment_current, segment_limit, segment_length,
           segment_mode))
    if 0 < segment_length <= 0x10000:
        segment_bytes = read(segment_start, segment_length)
        segment_path = Path("/tmp/iosclear-native-segment-list.bin")
        segment_path.write_bytes(segment_bytes)
        print("IOSCLEAR_LLDB segment-saved=%s sha256=%s bytes=%s" %
              (segment_path, hashlib.sha256(segment_bytes).hexdigest(),
               segment_bytes.hex(" ")))

    start = strip(u64(storage + 0x28))
    current = strip(u64(storage + 0x30))
    end = strip(u64(storage + 0x38))
    length = current - start if current > start else 0
    print("IOSCLEAR_LLDB KCMD start=%#x current=%#x end=%#x length=%#x" %
          (start, current, end, length))
    if not start or not length or length > 0x10000:
        continue
    commands = read(start, length)
    output_path = Path("/tmp/iosclear-native-kcmd.bin")
    output_path.write_bytes(commands)
    print("IOSCLEAR_LLDB saved=%s sha256=%s" %
          (output_path, hashlib.sha256(commands).hexdigest()))
    for off in range(0, len(commands), 32):
        print("IOSCLEAR_LLDB +%04x: %s" %
              (off, commands[off:off + 32].hex(" ")))
    off = 0
    record = 0
    while off + 0x38 <= len(commands) and record < 16:
        typ = u32_from(commands, off)
        nxt = u32_from(commands, off + 0x28)
        size = u32_from(commands, off + 0x2C)
        inner = u32_from(commands, off + 0x30)
        subtype = u32_from(commands, off + 0x34)
        print("IOSCLEAR_LLDB record[%d] off=%#x type=%#x next=%#x "
              "size=%#x inner=%#x subtype=%d" %
              (record, off, typ, nxt, size, inner, subtype))
        record += 1
        if typ not in (0x10000, 0x10001) or nxt < 0x38 or \
                nxt > len(commands) - off:
            break
        off += nxt

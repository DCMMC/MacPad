"""Dump the first native pf550 AGX texture at its IOSurface initializer return.

The command file stops at the iOS 16.3.1 AGXMetal13_3 call site immediately
after ``-[IOGPUMetalTexture initWithDevice:descriptor:iosurface:...]``.  This
helper keeps the LLDB command file small and records raw bytes so descriptor
interpretation can be repeated offline without another device run.
"""

import hashlib
import lldb
import struct
from pathlib import Path


_OUTPUT = Path("/tmp/iosclear-native-pf550-texture")


def _read(process, address, size):
    error = lldb.SBError()
    data = process.ReadMemory(address, size, error)
    if not error.Success():
        print("NATIVE-TEXTURE read-failed address=%#x size=%#x error=%s" %
              (address, size, error))
        return b""
    return data


def _save(name, data):
    _OUTPUT.mkdir(parents=True, exist_ok=True)
    path = _OUTPUT / name
    path.write_bytes(data)
    print("NATIVE-TEXTURE saved=%s bytes=%#x sha256=%s" %
          (path, len(data), hashlib.sha256(data).hexdigest()))


def _texture_descriptors(data, base, owner):
    """Print exact-size candidates for the 2388x1668 reference surface."""
    for offset in range(max(0, len(data) - 23)):
        value = int.from_bytes(data[offset:offset + 24], "little")
        width = ((value >> 28) & 0x3FFF) + 1
        height = ((value >> 42) & 0x3FFF) + 1
        if (width, height) != (2388, 1668):
            continue
        address = ((value >> 66) & ((1 << 36) - 1)) << 4
        acceleration_raw = (value >> 128) & ((1 << 64) - 1)
        # Apple's compressed descriptor carries additional, currently
        # unknown bits above bit 35 of this word.  AGX virtual addresses on
        # this G13G run use the same 36-bit shifted payload as Address.
        acceleration_low36 = (acceleration_raw & ((1 << 36) - 1)) << 4
        print("NATIVE-TEXTURE descriptor owner=%s offset=%#x va=%#x "
              "layout=%d compressed=%d extended=%d address=%#x "
              "acceleration-low36=%#x acceleration-raw=%#x "
              "bytes=%s" %
              (owner, offset, base + offset, (value >> 4) & 3,
               (value >> 103) & 1, (value >> 127) & 1, address,
               acceleration_low36, acceleration_raw,
               data[offset:offset + 24].hex()))


def arm_caller_return(debugger):
    """Stop after AGX returns the fully initialized texture to the app."""
    target = debugger.GetSelectedTarget()
    process = target.GetProcess()
    thread = process.GetSelectedThread()
    if thread.GetNumFrames() < 2:
        print("NATIVE-TEXTURE caller-return arm failed: no caller")
        return
    address = thread.GetFrameAtIndex(1).GetPC()
    breakpoint = target.BreakpointCreateByAddress(address)
    breakpoint.SetThreadID(thread.GetThreadID())
    breakpoint.SetOneShot(True)
    print("NATIVE-TEXTURE caller-return armed bp=%d address=%#x thread=%#x" %
          (breakpoint.GetID(), address, thread.GetThreadID()))


def dump_current(debugger):
    target = debugger.GetSelectedTarget()
    process = target.GetProcess()
    thread = process.GetSelectedThread()
    frame = thread.GetFrameAtIndex(0)
    texture = frame.FindRegister("x0").GetValueAsUnsigned()
    print("NATIVE-TEXTURE stop pc=%#x texture(x0)=%#x" %
          (frame.GetPC(), texture))

    object_data = _read(process, texture, 0x300)
    _save("texture-object.bin", object_data)
    _texture_descriptors(object_data, texture, "objc-object")
    if len(object_data) < 0x210:
        return

    impl = struct.unpack_from("<Q", object_data, 0x208)[0]
    print("NATIVE-TEXTURE impl(+0x208)=%#x" % impl)
    impl_data = _read(process, impl, 0x400)
    _save("texture-impl.bin", impl_data)
    _texture_descriptors(impl_data, impl, "impl")
    if len(impl_data) >= 0x18C:
        print("NATIVE-TEXTURE impl-fields gpu+40=%#x iosurface+a0=%#x "
              "plane+a8=%#x cpu+130=%#x descriptor-byte+184=%#x" %
              (struct.unpack_from("<Q", impl_data, 0x40)[0],
               struct.unpack_from("<Q", impl_data, 0xA0)[0],
               struct.unpack_from("<I", impl_data, 0xA8)[0],
               struct.unpack_from("<Q", impl_data, 0x130)[0],
               impl_data[0x184]))

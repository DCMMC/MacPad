"""Read-only dump of the live macOS AGX render-encoder producer.

The macOS AGX bundle is loaded from the dyld shared cache and its load address
changes between WindowServer processes.  Resolve the live __TEXT address from
LLDB instead of carrying a current-boot (or current-process) absolute address.
"""

import lldb


RENDER_ENCODER_OFFSET = 0x23008C
NEXT_METHOD_OFFSET = 0x23043C


def _agx_text_base(target):
    for module in target.module_iter():
        if (module.GetFileSpec().GetFilename() or "") != "AGXMetal13_3":
            continue
        section = module.FindSection("__TEXT")
        if not section.IsValid():
            continue
        address = section.GetLoadAddress(target)
        if address != lldb.LLDB_INVALID_ADDRESS:
            return address
    return None


def dump(debugger, output="/tmp/macws-macos-agx-render-live.bin"):
    target = debugger.GetSelectedTarget()
    base = _agx_text_base(target)
    if base is None:
        print("MACWS_AGX_RENDER_DUMP failed: AGXMetal13_3 __TEXT unavailable")
        return False

    start = base + RENDER_ENCODER_OFFSET
    end = base + NEXT_METHOD_OFFSET
    print("MACWS_AGX_RENDER_DUMP base=%#x start=%#x end=%#x size=%#x" %
          (base, start, end, end - start))
    debugger.HandleCommand(
        "memory read --force --binary --outfile %s %#x %#x" %
        (output, start, end))
    debugger.HandleCommand(
        "disassemble --start-address %#x --end-address %#x" % (start, end))
    return True


def break_render_encoder(debugger):
    """Install a one-shot breakpoint at the live process's real IMP."""
    target = debugger.GetSelectedTarget()
    base = _agx_text_base(target)
    if base is None:
        print("MACWS_AGX_RENDER_BREAK failed: AGXMetal13_3 __TEXT unavailable")
        return False
    address = base + RENDER_ENCODER_OFFSET
    breakpoint = target.BreakpointCreateByAddress(address)
    breakpoint.SetOneShot(True)
    print("MACWS_AGX_RENDER_BREAK base=%#x address=%#x bp=%d locations=%d" %
          (base, address, breakpoint.GetID(), breakpoint.GetNumLocations()))
    return breakpoint.GetNumLocations() != 0

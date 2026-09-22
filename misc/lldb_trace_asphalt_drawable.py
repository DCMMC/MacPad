import hashlib
import lldb


_records = []


def _evaluate(frame, expression):
    options = lldb.SBExpressionOptions()
    options.SetTimeoutInMicroSeconds(800000)
    value = frame.EvaluateExpression(expression, options)
    error = value.GetError()
    if not error.Success():
        return None, str(error)
    return value.GetValueAsUnsigned(), None


def _property(frame, object_address, selector):
    return _evaluate(
        frame,
        "(unsigned long)[(id)%#x %s]" % (object_address, selector),
    )


def present_callback(frame, breakpoint_location, internal_dict):
    del breakpoint_location, internal_dict
    process = frame.GetThread().GetProcess()
    drawable = frame.FindRegister("x2").GetValueAsUnsigned()
    texture, texture_error = _evaluate(
        frame, "(void *)[(id)%#x texture]" % drawable)
    record = {
        "hit": len(_records) + 1,
        "drawable": drawable,
        "texture": texture or 0,
        "texture_error": texture_error,
    }
    if texture:
        for selector in ("width", "height", "pixelFormat", "storageMode",
                         "usage", "mipmapLevelCount"):
            record[selector], record[selector + "_error"] = _property(
                frame, texture, selector)
        surface, surface_error = _evaluate(
            frame, "(void *)[(id)%#x iosurface]" % texture)
        record["iosurface"] = surface or 0
        record["iosurface_error"] = surface_error
        if surface:
            base, base_error = _evaluate(
                frame,
                "(void *)IOSurfaceGetBaseAddress((IOSurfaceRef)%#x)" % surface,
            )
            size, size_error = _evaluate(
                frame,
                "(unsigned long)IOSurfaceGetAllocSize((IOSurfaceRef)%#x)" %
                surface,
            )
            record["base"] = base or 0
            record["base_error"] = base_error
            record["alloc_size"] = size or 0
            record["alloc_size_error"] = size_error
            if base and size:
                read_size = min(size, 4 * 1024 * 1024)
                error = lldb.SBError()
                payload = process.ReadMemory(base, read_size, error)
                if error.Success():
                    record["sample_bytes"] = len(payload)
                    record["nonzero"] = sum(byte != 0 for byte in payload)
                    record["sha256"] = hashlib.sha256(payload).hexdigest()
                else:
                    record["read_error"] = str(error)
    _records.append(record)
    print("ASPHALT-DRAWABLE %r" % record)
    # Several buffered frames are needed before a recycled drawable is a
    # useful content witness. Stop briefly on the sixth present, then the
    # command file prints the summary and detaches immediately.
    return len(_records) >= 6


def install(debugger):
    target = debugger.GetSelectedTarget()
    breakpoint = target.BreakpointCreateByName(
        "-[_MTLCommandBuffer presentDrawable:]")
    if breakpoint.GetNumLocations() == 0:
        breakpoint = target.BreakpointCreateByRegex("presentDrawable:")
    breakpoint.SetScriptCallbackFunction(
        "lldb_trace_asphalt_drawable.present_callback")
    print("ASPHALT-DRAWABLE installed id=%d locations=%d" %
          (breakpoint.GetID(), breakpoint.GetNumLocations()))


def summary():
    print("ASPHALT-DRAWABLE SUMMARY count=%d" % len(_records))
    for record in _records:
        print("ASPHALT-DRAWABLE SUMMARY %r" % record)

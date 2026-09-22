"""Print the live CoreGraphics window catalog from a macOS chroot process.

Invoke with the macOS Python interpreter through run_bash.sh.  The helper is
read-only and avoids PyObjC so it remains usable in the minimal chroot.
"""

import ctypes
import json
import sys


cg = ctypes.CDLL(
    "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
)
cf = ctypes.CDLL(
    "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
)


class CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class CGSize(ctypes.Structure):
    _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]


class CGRect(ctypes.Structure):
    _fields_ = [("origin", CGPoint), ("size", CGSize)]


def symbol(name):
    return ctypes.c_void_p.in_dll(cg, name).value


cg.CGWindowListCopyWindowInfo.restype = ctypes.c_void_p
cg.CGWindowListCopyWindowInfo.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
cg.CGRectMakeWithDictionaryRepresentation.restype = ctypes.c_bool
cg.CGRectMakeWithDictionaryRepresentation.argtypes = [
    ctypes.c_void_p,
    ctypes.POINTER(CGRect),
]
cf.CFArrayGetCount.restype = ctypes.c_long
cf.CFArrayGetCount.argtypes = [ctypes.c_void_p]
cf.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
cf.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]
cf.CFDictionaryGetValue.restype = ctypes.c_void_p
cf.CFDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
cf.CFNumberGetValue.restype = ctypes.c_bool
cf.CFNumberGetValue.argtypes = [
    ctypes.c_void_p,
    ctypes.c_int,
    ctypes.c_void_p,
]
cf.CFStringGetCString.restype = ctypes.c_bool
cf.CFStringGetCString.argtypes = [
    ctypes.c_void_p,
    ctypes.c_char_p,
    ctypes.c_long,
    ctypes.c_uint32,
]
cf.CFRelease.argtypes = [ctypes.c_void_p]


KEYS = {
    name: symbol(name)
    for name in (
        "kCGWindowNumber",
        "kCGWindowOwnerPID",
        "kCGWindowOwnerName",
        "kCGWindowName",
        "kCGWindowLayer",
        "kCGWindowAlpha",
        "kCGWindowIsOnscreen",
        "kCGWindowBounds",
    )
}


def dictionary_value(dictionary, key):
    return cf.CFDictionaryGetValue(dictionary, KEYS[key])


def number(dictionary, key, floating=False):
    value = dictionary_value(dictionary, key)
    if not value:
        return None
    if floating:
        result = ctypes.c_double()
        kind = 6  # kCFNumberFloat64Type
    else:
        result = ctypes.c_longlong()
        kind = 4  # kCFNumberSInt64Type
    if not cf.CFNumberGetValue(value, kind, ctypes.byref(result)):
        return None
    return result.value


def string(dictionary, key):
    value = dictionary_value(dictionary, key)
    if not value:
        return ""
    buffer = ctypes.create_string_buffer(4096)
    # kCFStringEncodingUTF8
    if not cf.CFStringGetCString(value, buffer, len(buffer), 0x08000100):
        return ""
    return buffer.value.decode("utf-8", "replace")


def bounds(dictionary):
    value = dictionary_value(dictionary, "kCGWindowBounds")
    rect = CGRect()
    if not value or not cg.CGRectMakeWithDictionaryRepresentation(
        value, ctypes.byref(rect)
    ):
        return None
    return [
        rect.origin.x,
        rect.origin.y,
        rect.size.width,
        rect.size.height,
    ]


def main():
    requested_pid = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    # kCGWindowListOptionAll: include the offscreen NSWindows used by AppKit
    # tab groups; filtering to on-screen windows would hide the identity swap
    # that this tool is meant to diagnose.
    array = cg.CGWindowListCopyWindowInfo(0, 0)
    if not array:
        raise SystemExit("CGWindowListCopyWindowInfo returned NULL")
    output = []
    try:
        for index in range(cf.CFArrayGetCount(array)):
            dictionary = cf.CFArrayGetValueAtIndex(array, index)
            owner_pid = number(dictionary, "kCGWindowOwnerPID")
            if requested_pid and owner_pid != requested_pid:
                continue
            output.append(
                {
                    "window": number(dictionary, "kCGWindowNumber"),
                    "pid": owner_pid,
                    "owner": string(dictionary, "kCGWindowOwnerName"),
                    "name": string(dictionary, "kCGWindowName"),
                    "layer": number(dictionary, "kCGWindowLayer"),
                    "alpha": number(dictionary, "kCGWindowAlpha", floating=True),
                    "onscreen": bool(number(dictionary, "kCGWindowIsOnscreen")),
                    "bounds": bounds(dictionary),
                }
            )
    finally:
        cf.CFRelease(array)
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

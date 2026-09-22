import ctypes
ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
objc.objc_getClass.restype = ctypes.c_void_p
objc.objc_getClass.argtypes = [ctypes.c_char_p]
for name in ["CGVirtualDisplay","CGVirtualDisplayDescriptor","CGVirtualDisplayMode","CGVirtualDisplaySettings"]:
    c = objc.objc_getClass(name.encode())
    print(" ", name, "EXISTS" if c else "MISSING")

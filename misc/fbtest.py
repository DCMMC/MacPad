import ctypes, time, sys
mode = sys.argv[1] if len(sys.argv) > 1 else "probe"
cg = ctypes.CDLL('/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
for nm, rt, at in [
    ("CGMainDisplayID", ctypes.c_uint32, []),
    ("CGDisplayBaseAddress", ctypes.c_void_p, [ctypes.c_uint32]),
    ("CGDisplayBytesPerRow", ctypes.c_size_t, [ctypes.c_uint32]),
    ("CGDisplayPixelsWide", ctypes.c_size_t, [ctypes.c_uint32]),
    ("CGDisplayPixelsHigh", ctypes.c_size_t, [ctypes.c_uint32]),
]:
    f = getattr(cg, nm); f.restype = rt; f.argtypes = at
did = cg.CGMainDisplayID()
base = cg.CGDisplayBaseAddress(did)
bpr = cg.CGDisplayBytesPerRow(did)
w = cg.CGDisplayPixelsWide(did); h = cg.CGDisplayPixelsHigh(did)
print("did=%#x base=%s bpr=%d w=%d h=%d" % (did, hex(base) if base else "NULL", bpr, w, h), flush=True)
if not base:
    print("BASE NULL"); sys.exit(2)
if mode == "fill":
    bw = min(500, w); bh = min(500, h); rowbytes = min(bw*4, bpr)
    end = time.time() + float(sys.argv[2] if len(sys.argv) > 2 else 18); n = 0
    while time.time() < end:
        for y in range(bh):
            ctypes.memset(base + y*bpr, 0xFF, rowbytes)   # white block top-left
        n += 1; time.sleep(0.08)
    print("filled %d times, block %dx%d rowbytes=%d" % (n, bw, bh, rowbytes))

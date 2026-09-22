// One-shot diagnostic; no injection, activation, debugger subscription or
// mutation of the input file. Each invocation queries ONE operator-selected
// file URL and forces its real icon to render (NSImage alone may be lazy).
// Optional output is one new diagnostic PNG, never an overwrite.
#import <AppKit/AppKit.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <unistd.h>

int main(int argc, const char *argv[]) {
    if ((argc != 2 && argc != 3) || argv[1][0] != '/' ||
        (argc == 3 && argv[2][0] != '/')) return 64;
    alarm(10);
    @autoreleasepool {
        NSString *path = [NSString stringWithUTF8String:argv[1]];
        NSURL *url = [NSURL fileURLWithPath:path];
        NSError *error = nil;
        NSDictionary *values = [url resourceValuesForKeys:@[
            NSURLIsDirectoryKey, NSURLIsSymbolicLinkKey, NSURLIsAliasFileKey,
            NSURLIsVolumeKey, NSURLCanonicalPathKey, NSURLVolumeURLKey]
            error:&error];
        printf("icon-probe path=%s properties=%s error=%s\n", argv[1],
               values.description.UTF8String ?: "nil", error.description.UTF8String ?: "nil");
        fflush(stdout);
        NSImage *icon = [[NSWorkspace sharedWorkspace] iconForFile:path];
        printf("icon-probe completed image=%s size=%.1fx%.1f\n",
               icon ? "yes" : "no", icon.size.width, icon.size.height);
        fflush(stdout);
        if (!icon) return 1;
        NSBitmapImageRep *bitmap = [[NSBitmapImageRep alloc]
            initWithBitmapDataPlanes:NULL pixelsWide:64 pixelsHigh:64
            bitsPerSample:8 samplesPerPixel:4 hasAlpha:YES isPlanar:NO
            colorSpaceName:NSDeviceRGBColorSpace bytesPerRow:256 bitsPerPixel:32];
        NSGraphicsContext *context = [NSGraphicsContext
            graphicsContextWithBitmapImageRep:bitmap];
        if (!bitmap || !context) return 2;
        [NSGraphicsContext saveGraphicsState];
        [NSGraphicsContext setCurrentContext:context];
        [icon drawInRect:NSMakeRect(0, 0, 64, 64) fromRect:NSZeroRect
              operation:NSCompositingOperationCopy fraction:1.0
         respectFlipped:NO hints:nil];
        [context flushGraphics];
        [NSGraphicsContext restoreGraphicsState];
        unsigned visible = 0, colored = 0;
        for (NSUInteger y = 0; y < 64; ++y) {
            const unsigned char *row = bitmap.bitmapData + y * bitmap.bytesPerRow;
            for (NSUInteger x = 0; x < 64; ++x) {
                const unsigned char *pixel = row + x * 4;
                visible += pixel[3] != 0;
                colored += pixel[3] && (pixel[0] != pixel[1] || pixel[1] != pixel[2]);
            }
        }
        printf("icon-probe rendered pixels=4096 visible=%u colored=%u\n", visible, colored);
        fflush(stdout);
        if (argc == 3) {
            NSData *png = [bitmap representationUsingType:NSBitmapImageFileTypePNG
                                               properties:@{}];
            if (!png || png.length > 1048576) return 3;
            int fd = open(argv[2], O_WRONLY | O_CREAT | O_EXCL, 0600);
            if (fd < 0) return 4;
            size_t written = 0;
            while (written < png.length) {
                ssize_t amount = write(fd, (const char *)png.bytes + written,
                                       png.length - written);
                if (amount < 0 && errno == EINTR) continue;
                if (amount <= 0) break;
                written += (size_t)amount;
            }
            close(fd);
            if (written != png.length) { unlink(argv[2]); return 5; }
        }
        return visible ? 0 : 6;
    }
}

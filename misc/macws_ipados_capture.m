// Read-only, one-shot full iPadOS display capture. This executable has its own
// capture entitlement and never activates an app, changes geometry, or resprings.
// A PNG file existing is NOT success: reject empty/uniform captures first.
#import <UIKit/UIKit.h>
#import <dlfcn.h>
#import <IOSurface/IOSurfaceRef.h>
#import <objc/message.h>
#import <objc/runtime.h>
#import <math.h>
#import <errno.h>
#import <signal.h>
#import <sys/stat.h>
#import <time.h>
#import <unistd.h>

static double MonotonicTime(void) {
    struct timespec value;
    clock_gettime(CLOCK_MONOTONIC, &value);
    return (double)value.tv_sec + (double)value.tv_nsec / 1e9;
}

static void CaptureTimedOut(int signalNumber) {
    (void)signalNumber;
    static const char message[] = "capture FAILED: 30-second deadline; sequence remains incomplete\n";
    write(STDERR_FILENO, message, sizeof(message) - 1);
    _exit(124);
}

static UIImage *CaptureWithRenderServer(void) {
    void (*render)(mach_port_t, CFStringRef, IOSurfaceRef, int, int) =
        (void (*)(mach_port_t, CFStringRef, IOSurfaceRef, int, int))
        dlsym(RTLD_DEFAULT, "CARenderServerRenderDisplay");
    Class cls = NSClassFromString(@"CADisplay");
    SEL main = NSSelectorFromString(@"mainDisplay");
    id display = [cls respondsToSelector:main]
        ? ((id (*)(id, SEL))objc_msgSend)(cls, main) : nil;
    SEL boundsSelector = NSSelectorFromString(@"bounds");
    Method boundsMethod = class_getInstanceMethod([display class], boundsSelector);
    if (!render || !boundsMethod) return nil;
    fprintf(stderr, "CADisplay=%s boundsEncoding=%s\n",
        [display description].UTF8String, method_getTypeEncoding(boundsMethod));
    CGRect bounds = ((CGRect (*)(id, SEL))objc_msgSend)(display, boundsSelector);
    size_t width = (size_t)bounds.size.width, height = (size_t)bounds.size.height;
    if (!width || !height || width > 4096 || height > 4096) return nil;
    NSString *name = ((id (*)(id, SEL))objc_msgSend)(display, NSSelectorFromString(@"name"));
    fprintf(stderr, "CARenderServer display=%s bounds=%s\n",
        name.UTF8String, NSStringFromCGRect(bounds).UTF8String);
    size_t rowBytes = IOSurfaceAlignProperty(kIOSurfaceBytesPerRow, width * 4);
    NSDictionary *properties = @{
        (__bridge id)kIOSurfaceWidth: @(width),
        (__bridge id)kIOSurfaceHeight: @(height),
        (__bridge id)kIOSurfaceBytesPerElement: @4,
        (__bridge id)kIOSurfaceBytesPerRow: @(rowBytes),
        (__bridge id)kIOSurfaceAllocSize: @(IOSurfaceAlignProperty(kIOSurfaceAllocSize, rowBytes * height)),
        (__bridge id)kIOSurfacePixelFormat: @((uint32_t)'BGRA'),
    };
    IOSurfaceRef surface = IOSurfaceCreate((__bridge CFDictionaryRef)properties);
    if (!surface) {
        fprintf(stderr, "capture surface FAILED: rowBytes=%zu\n", rowBytes);
        return nil;
    }
    render(0, (__bridge CFStringRef)name, surface, 0, 0);
    IOSurfaceLock(surface, kIOSurfaceLockReadOnly, NULL);
    NSData *data = [NSData dataWithBytes:IOSurfaceGetBaseAddress(surface)
        length:IOSurfaceGetBytesPerRow(surface) * height];
    IOSurfaceUnlock(surface, kIOSurfaceLockReadOnly, NULL);
    CGDataProviderRef provider = CGDataProviderCreateWithCFData((__bridge CFDataRef)data);
    CGColorSpaceRef color = CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
    CGImageRef cg = CGImageCreate(width, height, 8, 32,
        IOSurfaceGetBytesPerRow(surface), color,
        kCGBitmapByteOrder32Little | kCGImageAlphaNoneSkipFirst,
        provider, NULL, false, kCGRenderingIntentDefault);
    UIImage *image = cg ? [UIImage imageWithCGImage:cg] : nil;
    if (cg) CGImageRelease(cg);
    CGColorSpaceRelease(color);
    CGDataProviderRelease(provider);
    CFRelease(surface);
    return image;
}

static int CaptureOne(NSString *path, NSUInteger frameIndex) {
    @autoreleasepool {
        UIImage *(*capture)(void) = (UIImage *(*)(void))
            dlsym(RTLD_DEFAULT, "_UICreateScreenUIImage");
        if (!capture) {
            fprintf(stderr, "capture FAILED: _UICreateScreenUIImage unavailable\n");
            return 69;
        }
        double startedMonotonic = MonotonicTime();
        NSTimeInterval startedWall = [NSDate date].timeIntervalSince1970;
        UIImage *image = capture();
        NSString *api = @"_UICreateScreenUIImage";
        if (!image.CGImage) {
            image = CaptureWithRenderServer();
            api = @"CARenderServerRenderDisplay";
        }
        double capturedMonotonic = MonotonicTime();
        CGImageRef cg = image.CGImage;
        if (!cg) {
            fprintf(stderr, "capture FAILED: no CGImage\n");
            return 70;
        }
        const size_t sourceWidth = CGImageGetWidth(cg), sourceHeight = CGImageGetHeight(cg);
        if (!sourceWidth || !sourceHeight || sourceWidth > 4096 || sourceHeight > 4096) {
            fprintf(stderr, "capture FAILED: invalid source dimensions %zux%zu\n",
                sourceWidth, sourceHeight);
            return 70;
        }
        const UIImageOrientation sourceOrientation = image.imageOrientation;
        const CGFloat sourceScale = image.scale;
        // PNG encoders/viewers do not reliably honor UIImage orientation.
        // Resolve rotation at native pixel dimensions (no downsampling), so
        // screen/window coordinates remain directly measurable in the artifact.
        if (sourceOrientation != UIImageOrientationUp) {
            BOOL swapped = sourceOrientation == UIImageOrientationLeft ||
                sourceOrientation == UIImageOrientationRight ||
                sourceOrientation == UIImageOrientationLeftMirrored ||
                sourceOrientation == UIImageOrientationRightMirrored;
            CGSize outputSize = swapped ? CGSizeMake(sourceHeight, sourceWidth)
                                        : CGSizeMake(sourceWidth, sourceHeight);
            // Runtime-confirmed on iOS 16.3.1: this capture SPI reports the
            // display's rotation (Right) while its raw raster is already
            // rotated right. Undo that rotation; treating it as ordinary
            // camera UIImage metadata turns readable landscape upside down.
            UIImageOrientation uprightOrientation = sourceOrientation;
            if ([api isEqualToString:@"_UICreateScreenUIImage"]) {
                if (sourceOrientation == UIImageOrientationLeft)
                    uprightOrientation = UIImageOrientationRight;
                else if (sourceOrientation == UIImageOrientationRight)
                    uprightOrientation = UIImageOrientationLeft;
            }
            UIImage *upright = [UIImage imageWithCGImage:cg scale:1
                orientation:uprightOrientation];
            UIGraphicsBeginImageContextWithOptions(outputSize, YES, 1);
            CGContextSetInterpolationQuality(UIGraphicsGetCurrentContext(), kCGInterpolationNone);
            [upright drawInRect:(CGRect){CGPointZero, outputSize}];
            image = UIGraphicsGetImageFromCurrentImageContext();
            UIGraphicsEndImageContext();
            cg = image.CGImage;
            if (!cg) return 70;
        }
        double orientationFinishedMonotonic = MonotonicTime();
        size_t width = CGImageGetWidth(cg), height = CGImageGetHeight(cg);
        if (!width || !height || width > 4096 || height > 4096) {
            fprintf(stderr, "capture FAILED: invalid dimensions %zux%zu\n", width, height);
            return 70;
        }
        // The validity check needs a representative sample, not a second
        // full-display raster. Only this disposable diagnostic thumbnail is
        // reduced; the encoded PNG below remains the untouched native raster.
        size_t sampleWidth = MIN(width, (size_t)160), sampleHeight = MIN(height, (size_t)160);
        CGColorSpaceRef space = CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
        CGContextRef bitmap = CGBitmapContextCreate(NULL, sampleWidth, sampleHeight, 8,
            sampleWidth * 4, space, kCGImageAlphaPremultipliedLast | kCGBitmapByteOrder32Big);
        CGColorSpaceRelease(space);
        if (!bitmap) return 71;
        CGContextDrawImage(bitmap, CGRectMake(0, 0, sampleWidth, sampleHeight), cg);
        const uint8_t *pixels = CGBitmapContextGetData(bitmap);
        size_t count = 0, nonblack = 0;
        double sum = 0, squared = 0;
        for (size_t y = 0; y < sampleHeight; y++) {
            for (size_t x = 0; x < sampleWidth; x++) {
                const uint8_t *p = pixels + 4 * (y * sampleWidth + x);
                double value = (p[0] + p[1] + p[2]) / 3.0;
                sum += value;
                squared += value * value;
                nonblack += value > 8;
                count++;
            }
        }
        double mean = sum / count;
        double deviation = sqrt(fmax(0, squared / count - mean * mean));
        double fraction = (double)nonblack / count;
        CGContextRelease(bitmap);
        BOOL useful = fraction >= 0.001 && deviation >= 2.0;
        fprintf(stderr, "capture pixels=%zux%zu image-scale=%.3f orientation=%ld "
            "nonblack=%.6f mean=%.3f stddev=%.3f elapsed=%.3f useful=%s\n",
            width, height, image.scale, (long)image.imageOrientation, fraction,
            mean, deviation, MonotonicTime() - startedMonotonic,
            useful ? "YES" : "NO");
        if (!useful) {
            fprintf(stderr, "capture FAILED: black/uniform output; PNG not written\n");
            return 65;
        }
        double validationFinishedMonotonic = MonotonicTime();
        NSData *png = UIImagePNGRepresentation(image);
        NSError *error = nil;
        if (!png.length || ![png writeToFile:path options:NSDataWritingAtomic error:&error]) {
            fprintf(stderr, "capture FAILED: %s\n", error.description.UTF8String ?: "PNG encoding");
            return 74;
        }
        NSDictionary *receipt = @{
            @"capture_api": api,
            @"scope": @"ipados-full-display",
            @"width": @(width), @"height": @(height),
            @"image_scale": @(image.scale),
            @"orientation": @(image.imageOrientation),
            @"source_width": @(sourceWidth), @"source_height": @(sourceHeight),
            @"source_image_scale": @(sourceScale),
            @"source_orientation": @(sourceOrientation),
            @"validation_sample_width": @(sampleWidth),
            @"validation_sample_height": @(sampleHeight),
            @"nonblack_fraction": @(fraction), @"luma_stddev": @(deviation),
            @"frame_index": @(frameIndex),
            @"unix_time": @(startedWall),
            @"capture_started_unix_time": @(startedWall),
            @"capture_started_monotonic_seconds": @(startedMonotonic),
            @"capture_finished_monotonic_seconds": @(capturedMonotonic),
            @"orientation_finished_monotonic_seconds": @(orientationFinishedMonotonic),
            @"validation_finished_monotonic_seconds": @(validationFinishedMonotonic),
            @"written_monotonic_seconds": @(MonotonicTime()),
            @"png_bytes": @(png.length),
            @"pixel_check_passed": @YES,
            @"visual_acceptance_passed": @NO,
        };
        NSData *json = [NSJSONSerialization dataWithJSONObject:receipt
            options:NSJSONWritingPrettyPrinted error:&error];
        if (![json writeToFile:[path stringByAppendingString:@".json"]
            options:NSDataWritingAtomic error:&error]) return 74;
        printf("%s\n", path.UTF8String);
        fflush(stdout);
        return 0;
    }
}

static BOOL WriteSequenceReceipt(NSString *directory, NSUInteger requested,
    NSUInteger completed, double interval, double startedWall,
    double startedMonotonic, int errorCode) {
    NSDictionary *receipt = @{
        @"requested_frames": @(requested), @"completed_frames": @(completed),
        @"requested_interval_seconds": @(interval),
        @"started_unix_time": @(startedWall),
        @"started_monotonic_seconds": @(startedMonotonic),
        @"updated_monotonic_seconds": @(MonotonicTime()),
        @"complete": @(completed == requested && errorCode == 0),
        @"error_code": @(errorCode),
        @"pixel_check_passed": @(completed == requested && errorCode == 0),
        @"visual_acceptance_passed": @NO,
    };
    NSError *error = nil;
    NSData *json = [NSJSONSerialization dataWithJSONObject:receipt
        options:NSJSONWritingPrettyPrinted error:&error];
    BOOL ok = [json writeToFile:[directory stringByAppendingPathComponent:@"sequence.json"]
        options:NSDataWritingAtomic error:&error];
    if (!ok) fprintf(stderr, "sequence receipt FAILED: %s\n", error.description.UTF8String);
    return ok;
}

int main(int argc, char **argv) {
    @autoreleasepool {
        signal(SIGALRM, CaptureTimedOut);
        alarm(30);
        if (argc == 2) {
            NSString *path = [NSString stringWithUTF8String:argv[1]];
            return [path isAbsolutePath] ? CaptureOne(path, 0) : 64;
        }
        if (argc != 5 || strcmp(argv[1], "--sequence") != 0) {
            fprintf(stderr, "usage: macws_ipados_capture /absolute/output.png\n"
                "   or: macws_ipados_capture --sequence /new/directory COUNT INTERVAL\n"
                "COUNT=1..12; INTERVAL=0.2..1.0 seconds; total deadline 30 seconds\n");
            return 64;
        }
        NSString *directory = [NSString stringWithUTF8String:argv[2]];
        char *countEnd = NULL, *intervalEnd = NULL;
        long requested = strtol(argv[3], &countEnd, 10);
        double interval = strtod(argv[4], &intervalEnd);
        if (![directory isAbsolutePath] || !*argv[3] || *countEnd ||
            !*argv[4] || *intervalEnd || requested < 1 || requested > 12 ||
            !isfinite(interval) || interval < 0.2 || interval > 1.0) return 64;
        // Require a new directory so an old successful receipt cannot make a
        // partial or failed observation look complete.
        if (mkdir(directory.fileSystemRepresentation, 0755) != 0) {
            fprintf(stderr, "sequence FAILED: create new directory: %s\n", strerror(errno));
            return 73;
        }
        double startedMonotonic = MonotonicTime();
        double startedWall = [NSDate date].timeIntervalSince1970;
        if (!WriteSequenceReceipt(directory, requested, 0, interval,
            startedWall, startedMonotonic, 0)) return 74;
        for (NSUInteger index = 0; index < (NSUInteger)requested; index++) {
            @autoreleasepool {
                double remaining = startedMonotonic + index * interval - MonotonicTime();
                if (remaining > 0) {
                    struct timespec pause = { (time_t)remaining,
                        (long)((remaining - floor(remaining)) * 1e9) };
                    while (nanosleep(&pause, &pause) != 0 && errno == EINTR) {}
                }
                NSString *path = [directory stringByAppendingPathComponent:
                    [NSString stringWithFormat:@"frame-%03lu.png", (unsigned long)index]];
                int status = CaptureOne(path, index);
                NSUInteger completed = index + (status == 0 ? 1 : 0);
                if (!WriteSequenceReceipt(directory, requested, completed, interval,
                    startedWall, startedMonotonic, status)) return 74;
                if (status) {
                    fprintf(stderr, "sequence FAILED: frame=%lu completed=%lu/%ld code=%d\n",
                        (unsigned long)index, (unsigned long)completed, requested, status);
                    return status;
                }
            }
        }
        fprintf(stderr, "sequence complete: frames=%ld elapsed=%.3f\n",
            requested, MonotonicTime() - startedMonotonic);
        return 0;
    }
}

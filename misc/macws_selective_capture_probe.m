// Diagnostic only; never linked into the product. No activation, input, picker,
// geometry mutation, or fallback to unfiltered/full-display capture.
//
// RE-confirmed for SkyLight UUID 96676A53-B1E0-3D7E-B98B-B73873CD1880:
// - SLContentFilter display+sets initializer: 0x1850f9ae0 (filterType = 3).
// - SLContentStream initializer: 0x1850fb97c; creates an UNSTARTED stream.
// - Session.content: 0x1850ffd1c; reads back content via WindowServer MIG.
// - Content-stream handler: 0x1850fbfdc passes one SLContentStreamUpdate object.
// - display-sharing -> CGXDisplayStreamCreate: 0x185167910.
// - window allowlist -> WSRedrawWindowsToDestinationAndRegion: 0x18510516c.
// A nonblack PNG proves only that pixels arrived, NOT blur/animation/isolation.
#import <Foundation/Foundation.h>
#import <AppKit/AppKit.h>
#import <CoreGraphics/CoreGraphics.h>
#import <ImageIO/ImageIO.h>
#import <IOSurface/IOSurfaceRef.h>
#import <objc/runtime.h>
#include <dlfcn.h>
#include <errno.h>
#include <mach-o/dyld.h>
#include <mach-o/loader.h>
#include <math.h>
#include <signal.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>

@protocol ProbeFilter
- (instancetype)initWithDisplay:(uint32_t)display shareAll:(BOOL)all
    includedWindows:(NSSet *)windows includedApplications:(NSSet *)apps
    includedPIDS:(NSSet *)pids excludedWindows:(NSSet *)excludedWindows
    excludedApplications:(NSSet *)excludedApps excludedPIDS:(NSSet *)excludedPids;
- (uint32_t)filterType;
- (uint32_t)displayID;
- (BOOL)shareAll;
- (NSSet *)includedWindows;
- (NSSet *)includedApplications;
- (NSSet *)includedPIDS;
- (NSSet *)excludedWindows;
- (NSSet *)excludedApplications;
- (NSSet *)excludedPIDS;
@end

@protocol ProbeServerFilter
- (NSNumber *)filterPolicy;
- (NSSet *)includedWindows;
- (NSSet *)includedApplications;
- (NSSet *)includedPIDS;
- (NSSet *)excludedWindows;
- (NSSet *)excludedApplications;
- (NSSet *)excludedPIDS;
@end

@protocol ProbeContent
- (NSNumber *)displayID;
- (id<ProbeServerFilter>)filter;
@end

@protocol ProbeSession
- (uint32_t)type;
- (NSUUID *)uuid;
- (id<ProbeContent>)content;
@end

@protocol ProbeUpdate
- (int32_t)status;
- (uint64_t)displayTime;
- (IOSurfaceRef)frameSurface;
@end

@protocol ProbeStream
- (instancetype)initWithFilter:(id<ProbeFilter>)filter
    properties:(NSDictionary *)properties queue:(dispatch_queue_t)queue
    handler:(void (^)(id<ProbeUpdate>))handler error:(NSError **)error;
- (id<ProbeFilter>)filter;
- (NSDictionary *)properties;
- (id<ProbeSession>)session;
- (CGDisplayStreamRef)stream;
- (BOOL)running;
- (BOOL)start:(NSError **)error;
- (BOOL)stop:(NSError **)error;
@end

static const size_t MaxFrameBytes = 16 * 1024 * 1024;
static volatile sig_atomic_t Interrupted;
static double Started;

static double Monotonic(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}

static void HardDeadline(int signum) {
    (void)signum;
    const char message[] = "{\"event\":\"hard-deadline\",\"exit\":124,\"complete\":false,"
        "\"cleanup_confirmed\":false,\"visual_acceptance\":false}\n";
    write(STDERR_FILENO, message, sizeof(message) - 1);
    _exit(124); // Process/port death releases leases if a system call hung.
}

static void Interrupt(int signum) { Interrupted = signum; }

static void Log(NSString *event, NSDictionary *values) {
    NSMutableDictionary *record = [values mutableCopy] ?: [NSMutableDictionary dictionary];
    record[@"event"] = event;
    record[@"monotonic"] = @(Monotonic());
    record[@"wall_time"] = @([NSDate date].timeIntervalSince1970);
    NSData *json = [NSJSONSerialization dataWithJSONObject:record options:0 error:NULL];
    if (json) {
        fwrite(json.bytes, 1, json.length, stderr);
        fputc('\n', stderr);
        fflush(stderr);
    }
}

static BOOL WriteJSON(NSString *path, NSDictionary *record) {
    NSError *error = nil;
    NSData *data = [NSJSONSerialization dataWithJSONObject:record
        options:NSJSONWritingPrettyPrinted error:&error];
    BOOL ok = data && [data writeToFile:path options:NSDataWritingAtomic error:&error];
    if (!ok) Log(@"write-failed", @{@"path":path, @"error":error.description ?: @"unknown"});
    return ok;
}

static BOOL ParseUnsigned(NSString *text, uint32_t *value) {
    NSCharacterSet *digits = [NSCharacterSet characterSetWithCharactersInString:@"0123456789"];
    if (!text.length || [text rangeOfCharacterFromSet:digits.invertedSet].location != NSNotFound) return NO;
    unsigned long long number = strtoull(text.UTF8String, NULL, 10);
    if (!number || number > UINT32_MAX) return NO;
    *value = (uint32_t)number;
    return YES;
}

static NSSet<NSNumber *> *ParseWindows(NSString *csv) {
    NSArray *parts = [csv componentsSeparatedByString:@","];
    if (!parts.count || parts.count > 8) return nil;
    NSMutableSet *windows = [NSMutableSet set];
    for (NSString *part in parts) {
        uint32_t number;
        if (!ParseUnsigned(part, &number) || [windows containsObject:@(number)]) return nil;
        [windows addObject:@(number)];
    }
    return windows;
}

static BOOL EmptySet(id value) {
    return [value isKindOfClass:NSSet.class] && [(NSSet *)value count] == 0;
}

static BOOL MatchesWindows(id value, NSSet *expected) {
    return [value isKindOfClass:NSSet.class] && [(NSSet *)value isEqualToSet:expected];
}

static BOOL HasMethods(id object, NSArray<NSString *> *methods) {
    for (NSString *method in methods) {
        if (!object || ![object respondsToSelector:NSSelectorFromString(method)]) {
            Log(@"missing-method", @{@"class":object ? NSStringFromClass([object class]) : @"nil",
                @"selector":method});
            return NO;
        }
    }
    return YES;
}

static NSArray<NSString *> *FilterSetMethods(void) {
    return @[@"includedWindows", @"includedApplications", @"includedPIDS",
        @"excludedWindows", @"excludedApplications", @"excludedPIDS"];
}

static BOOL ValidateFilter(id<ProbeFilter> filter, uint32_t display, NSSet *windows) {
    if (!HasMethods(filter, [FilterSetMethods() arrayByAddingObjectsFromArray:
        @[@"filterType", @"displayID", @"shareAll"]])) return NO;
    BOOL valid = filter.filterType == 3 && filter.displayID == display && !filter.shareAll &&
        MatchesWindows(filter.includedWindows, windows) &&
        EmptySet(filter.includedApplications) && EmptySet(filter.includedPIDS) &&
        EmptySet(filter.excludedWindows) && EmptySet(filter.excludedApplications) &&
        EmptySet(filter.excludedPIDS);
    Log(@"client-filter-verified", @{@"valid":@(valid), @"type":@(filter.filterType),
        @"display_id":@(filter.displayID), @"share_all":@(filter.shareAll),
        @"included_windows":filter.includedWindows.allObjects ?: @[]});
    return valid;
}

static BOOL ValidateSession(id<ProbeSession> session, uint32_t display, NSSet *windows) {
    if (!HasMethods(session, @[@"type", @"uuid", @"content"])) return NO;
    id uuid = session.uuid;
    id<ProbeContent> content = session.content; // Actual WindowServer readback, not cached input.
    if (!HasMethods(content, @[@"displayID", @"filter"])) return NO;
    id<ProbeServerFilter> filter = content.filter;
    if (!HasMethods(filter, [FilterSetMethods() arrayByAddingObject:@"filterPolicy"])) return NO;
    NSNumber *displayNumber = content.displayID;
    NSNumber *policy = filter.filterPolicy;
    BOOL valid = session.type == 2 && [uuid isKindOfClass:NSUUID.class] &&
        [displayNumber isKindOfClass:NSNumber.class] && displayNumber.unsignedIntValue == display &&
        [policy isKindOfClass:NSNumber.class] && policy.longLongValue == 1 &&
        MatchesWindows(filter.includedWindows, windows) &&
        EmptySet(filter.includedApplications) && EmptySet(filter.includedPIDS) &&
        EmptySet(filter.excludedWindows) && EmptySet(filter.excludedApplications) &&
        EmptySet(filter.excludedPIDS);
    Log(@"server-session-readback", @{@"valid":@(valid), @"type":@(session.type),
        @"uuid":[uuid isKindOfClass:NSUUID.class] ? [uuid UUIDString] : @"invalid",
        @"display_id":displayNumber ?: @0, @"filter_policy":policy ?: @0,
        @"included_windows":filter.includedWindows.allObjects ?: @[]});
    return valid;
}

static BOOL VerifySkyLightUUID(void) {
    const uint8_t expected[16] = {0x96,0x67,0x6a,0x53,0xb1,0xe0,0x3d,0x7e,
        0xb9,0x8b,0xb7,0x38,0x73,0xcd,0x18,0x80};
    for (uint32_t i = 0; i < _dyld_image_count(); i++) {
        const char *name = _dyld_get_image_name(i);
        if (!name || !strstr(name, "/SkyLight.framework/")) continue;
        const struct mach_header_64 *header = (const void *)_dyld_get_image_header(i);
        if (header->magic != MH_MAGIC_64 || header->sizeofcmds > 1024 * 1024) return NO;
        const uint8_t *cursor = (const uint8_t *)(header + 1);
        const uint8_t *end = cursor + header->sizeofcmds;
        for (uint32_t j = 0; j < header->ncmds && cursor + sizeof(struct load_command) <= end; j++) {
            const struct load_command *command = (const void *)cursor;
            if (command->cmdsize < sizeof(*command) || command->cmdsize > (size_t)(end-cursor)) break;
            if (command->cmd == LC_UUID && command->cmdsize >= sizeof(struct uuid_command)) {
                const struct uuid_command *uuid = (const void *)cursor;
                BOOL valid = !memcmp(uuid->uuid, expected, sizeof(expected));
                Log(@"skylight-uuid", @{@"valid":@(valid), @"path":@(name),
                    @"uuid":[[NSUUID alloc] initWithUUIDBytes:uuid->uuid].UUIDString});
                return valid;
            }
            cursor += command->cmdsize;
        }
        return NO;
    }
    return NO;
}

static NSDictionary *PixelMetrics(const uint8_t *bytes, size_t width, size_t height, size_t stride) {
    size_t samples = 0, nonblack = 0, visible = 0;
    double sum = 0, square = 0;
    for (size_t y = 0; y < height; y += MAX((size_t)1, height / 128)) {
        for (size_t x = 0; x < width; x += MAX((size_t)1, width / 128)) {
            const uint8_t *pixel = bytes + y * stride + x * 4;
            double intensity = (pixel[0] + pixel[1] + pixel[2]) / 3.0;
            visible += pixel[3] > 8;
            nonblack += pixel[3] > 8 && intensity > 8;
            sum += intensity;
            square += intensity * intensity;
            samples++;
        }
    }
    double mean = samples ? sum / samples : 0;
    double deviation = samples ? sqrt(fmax(0, square / samples - mean * mean)) : 0;
    double fraction = samples ? (double)nonblack / samples : 0;
    return @{@"samples":@(samples), @"nonblack_fraction":@(fraction),
        @"visible_fraction":@(samples ? (double)visible / samples : 0),
        @"mean":@(mean), @"stddev":@(deviation),
        @"validpixels":@(fraction >= 0.001 && deviation >= 2.0)};
}

static void FreeImageBytes(void *info, const void *data, size_t length) {
    (void)info; (void)length;
    free((void *)data);
}

@interface ProbeState : NSObject
@property(nonatomic) id<ProbeStream> stream;
@property(nonatomic) NSString *directory;
@property(nonatomic) NSSet<NSNumber *> *windows;
@property(nonatomic) uint32_t display;
@property(nonatomic) NSUInteger requested, frames, validFrames, callbacks;
@property(nonatomic) size_t width, height;
@property(nonatomic) BOOL done, failed, verified;
@property(nonatomic) uint64_t previousDisplayTime;
@end
@implementation ProbeState
@end

static void Receive(ProbeState *state, id<ProbeUpdate> update) {
    @autoreleasepool {
        if (state.done || Interrupted || Monotonic() - Started > 3.5) return;
        if (!state.verified || !HasMethods(update, @[@"status", @"displayTime", @"frameSurface"])) {
            state.failed = state.done = YES;
            return;
        }
        int32_t status = update.status;
        state.callbacks++;
        if (state.callbacks <= 12) Log(@"callback", @{@"status":@(status), @"display_time":@(update.displayTime)});
        if (status == kCGDisplayStreamFrameStatusStopped) {
            state.failed = state.done = YES;
            return;
        }
        if (status != kCGDisplayStreamFrameStatusFrameComplete) return;
        state.frames++;
        state.done = state.frames >= state.requested;
        uint64_t displayTime = update.displayTime;
        IOSurfaceRef surface = update.frameSurface;
        if (!surface || !displayTime || displayTime <= state.previousDisplayTime) {
            Log(@"invalid-frame", @{@"reason":@"missing-surface-or-nonmonotonic-time"});
            state.failed = state.done = YES;
            return;
        }
        state.previousDisplayTime = displayTime;
        size_t width = IOSurfaceGetWidth(surface), height = IOSurfaceGetHeight(surface);
        size_t stride = IOSurfaceGetBytesPerRow(surface), allocation = IOSurfaceGetAllocSize(surface);
        uint32_t format = IOSurfaceGetPixelFormat(surface);
        if (width != state.width || height != state.height || format != 'BGRA' ||
            IOSurfaceGetBytesPerElement(surface) != 4 || IOSurfaceGetPlaneCount(surface) != 0 ||
            stride < width * 4 || !height || stride > MaxFrameBytes / height || allocation < stride * height) {
            Log(@"invalid-frame", @{@"reason":@"shape-format-or-budget", @"width":@(width),
                @"height":@(height), @"stride":@(stride), @"format":@(format), @"alloc_size":@(allocation)});
            state.failed = state.done = YES;
            return;
        }
        size_t length = stride * height;
        uint8_t *copy = malloc(length);
        if (!copy) { state.failed = state.done = YES; return; }
        CFRetain(surface);
        IOSurfaceIncrementUseCount(surface);
        // Do not use AvoidSync: PNG validity needs completed, CPU-coherent
        // contents, not merely a callback with a non-nil surface pointer.
        IOReturn lock = IOSurfaceLock(surface, kIOSurfaceLockReadOnly, NULL);
        void *base = lock == kIOReturnSuccess ? IOSurfaceGetBaseAddress(surface) : NULL;
        if (base) memcpy(copy, base, length);
        IOReturn unlock = lock == kIOReturnSuccess
            ? IOSurfaceUnlock(surface, kIOSurfaceLockReadOnly, NULL) : lock;
        IOSurfaceDecrementUseCount(surface);
        CFRelease(surface);
        if (!base || unlock != kIOReturnSuccess) {
            free(copy);
            Log(@"surface-lock-failed", @{@"lock":@(lock), @"unlock":@(unlock)});
            state.failed = state.done = YES;
            return;
        }
        NSMutableDictionary *receipt = [PixelMetrics(copy, width, height, stride) mutableCopy];
        receipt[@"frame"] = @(state.frames);
        receipt[@"display_time"] = @(displayTime);
        receipt[@"monotonic"] = @(Monotonic());
        receipt[@"wall_time"] = @([NSDate date].timeIntervalSince1970);
        receipt[@"width"] = @(width); receipt[@"height"] = @(height);
        receipt[@"stride"] = @(stride); receipt[@"pixel_format"] = @"BGRA";
        receipt[@"visual_acceptance"] = @NO;
        receipt[@"filter_verified_before_start"] = @YES;
        BOOL useful = [receipt[@"validpixels"] boolValue];
        BOOL pngOK = NO;
        NSString *stem = [state.directory stringByAppendingPathComponent:
            [NSString stringWithFormat:@"frame-%02lu", (unsigned long)state.frames]];
        if (useful) {
            CGDataProviderRef provider = CGDataProviderCreateWithData(NULL, copy, length, FreeImageBytes);
            if (provider) copy = NULL;
            CGColorSpaceRef color = CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
            CGImageRef image = provider && color ? CGImageCreate(width, height, 8, 32, stride,
                color, kCGBitmapByteOrder32Little | kCGImageAlphaPremultipliedFirst,
                provider, NULL, false, kCGRenderingIntentDefault) : NULL;
            NSURL *url = [NSURL fileURLWithPath:[stem stringByAppendingString:@".png"]];
            CGImageDestinationRef destination = image ? CGImageDestinationCreateWithURL(
                (__bridge CFURLRef)url, CFSTR("public.png"), 1, NULL) : NULL;
            if (destination) {
                CGImageDestinationAddImage(destination, image, NULL);
                pngOK = CGImageDestinationFinalize(destination);
                CFRelease(destination);
            }
            if (image) CGImageRelease(image);
            if (color) CGColorSpaceRelease(color);
            if (provider) CGDataProviderRelease(provider);
        }
        free(copy);
        receipt[@"png_written"] = @(pngOK);
        BOOL receiptOK = WriteJSON([stem stringByAppendingString:@".json"], receipt);
        if (useful && pngOK && receiptOK) state.validFrames++;
        else state.failed = state.done = YES;
        Log(@"frame", receipt);
    }
}

static BOOL ResolveGeometry(ProbeState *state, CGRect *source) {
    CGRect displayBounds = CGDisplayBounds(state.display);
    size_t displayWidth = CGDisplayPixelsWide(state.display);
    size_t displayHeight = CGDisplayPixelsHigh(state.display);
    if (!CGDisplayIsActive(state.display) || CGRectIsEmpty(displayBounds) || !displayWidth || !displayHeight) return NO;
    // This project's chroot CGDisplayPixelsWide/High can report logical
    // extents. NSScreen is the producer's backing-scale authority; do not
    // silently collapse Retina to 1x by dividing those CG values.
    double scale = 0;
    for (NSScreen *screen in NSScreen.screens) {
        if ([screen.deviceDescription[@"NSScreenNumber"] unsignedIntValue] == state.display) {
            scale = screen.backingScaleFactor;
            break;
        }
    }
    if (!isfinite(scale) || scale < 1 || scale > 4) return NO;
    NSArray *ids = [state.windows.allObjects sortedArrayUsingSelector:@selector(compare:)];
    NSMutableSet *seen = [NSMutableSet set];
    CGRect bounds = CGRectNull;
    for (NSNumber *requestedID in ids) {
        // Unlike presentation descriptions, the ordinary window catalog is
        // the same logical-coordinate source used for the display filter.
        NSArray *info = CFBridgingRelease(CGWindowListCopyWindowInfo(
            kCGWindowListOptionIncludingWindow, requestedID.unsignedIntValue));
        if (info.count != 1) return NO;
        NSDictionary *window = info.firstObject;
        NSNumber *number = window[(id)kCGWindowNumber];
        CGRect frame;
        if (![state.windows containsObject:number] || [seen containsObject:number] ||
            ![window[(id)kCGWindowIsOnscreen] boolValue] ||
            !CGRectMakeWithDictionaryRepresentation((__bridge CFDictionaryRef)window[(id)kCGWindowBounds], &frame) ||
            CGRectIsEmpty(frame) || !CGRectContainsRect(displayBounds, frame)) return NO;
        [seen addObject:number];
        bounds = CGRectIsNull(bounds) ? frame : CGRectUnion(bounds, frame);
        Log(@"requested-window", @{@"window_id":number, @"owner_pid":window[(id)kCGWindowOwnerPID] ?: @0,
            @"bounds":CFBridgingRelease(CGRectCreateDictionaryRepresentation(frame))});
    }
    if (![seen isEqualToSet:state.windows]) return NO;
    // CGDisplayStreamSourceRect is in display logical coordinates; destination
    // and frame size are in pixels (Apple CGDisplayStream.h, lines 151-167).
    double x = floor((CGRectGetMinX(bounds) - displayBounds.origin.x) * scale);
    double y = floor((CGRectGetMinY(bounds) - displayBounds.origin.y) * scale);
    double maxX = ceil((CGRectGetMaxX(bounds) - displayBounds.origin.x) * scale);
    double maxY = ceil((CGRectGetMaxY(bounds) - displayBounds.origin.y) * scale);
    if (!isfinite(x+y+maxX+maxY) || maxX <= x || maxY <= y) return NO;
    state.width = (size_t)(maxX-x); state.height = (size_t)(maxY-y);
    size_t stride = (state.width * 4 + 255) & ~(size_t)255;
    if (state.width > 4096 || state.height > 4096 || stride > MaxFrameBytes / state.height) return NO;
    *source = CGRectMake(x / scale, y / scale, state.width / scale, state.height / scale);
    Log(@"geometry", @{@"scale":@(scale), @"width":@(state.width), @"height":@(state.height),
        @"cg_display_pixels_wide":@(displayWidth), @"cg_display_pixels_high":@(displayHeight),
        @"estimated_frame_bytes":@(stride * state.height), @"queue_depth":@2,
        @"source_logical":CFBridgingRelease(CGRectCreateDictionaryRepresentation(*source))});
    return YES;
}

static int RunProbe(ProbeState *state) {
    int result = 70;
    BOOL started = NO, cleanupOK = YES;
    @try {
        do {
            void *sky = dlopen("/System/Library/PrivateFrameworks/SkyLight.framework/SkyLight", RTLD_NOW | RTLD_LOCAL);
            // Do not dlclose: SDK frameworks can retain queued callbacks until process exit.
            if (!sky || !VerifySkyLightUUID()) { result = 69; break; }
            if (!CGPreflightScreenCaptureAccess()) {
                Log(@"preflight-denied", @{@"prompt_requested":@NO}); result = 77; break;
            }
            CGRect source;
            if (!ResolveGeometry(state, &source)) {
                Log(@"geometry-rejected", @{@"reason":@"invalid-display-window-set-offscreen-or-budget"});
                result = 65; break;
            }
            Class filterClass = NSClassFromString(@"SLContentFilter");
            Class streamClass = NSClassFromString(@"SLContentStream");
            if (!filterClass || !streamClass || ![filterClass instancesRespondToSelector:
                @selector(initWithDisplay:shareAll:includedWindows:includedApplications:includedPIDS:excludedWindows:excludedApplications:excludedPIDS:)] ||
                ![streamClass instancesRespondToSelector:@selector(initWithFilter:properties:queue:handler:error:)]) {
                result = 69; break;
            }
            NSSet *empty = [NSSet set];
            id<ProbeFilter> filter = [(id<ProbeFilter>)[filterClass alloc]
                initWithDisplay:state.display shareAll:NO includedWindows:state.windows
                includedApplications:empty includedPIDS:empty excludedWindows:empty
                excludedApplications:empty excludedPIDS:empty];
            if (!ValidateFilter(filter, state.display, state.windows)) { result = 65; break; }
            CGRect destination = CGRectMake(0, 0, state.width, state.height);
            CGColorSpaceRef color = CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
            if (!color) { result = 71; break; }
            NSDictionary *properties = @{
                @"SLContentStreamFrameSize":CFBridgingRelease(CGSizeCreateDictionaryRepresentation(destination.size)),
                @"SLContentStreamPixelFormat":@((uint32_t)'BGRA'),
                @"SLContentStreamSourceRect":CFBridgingRelease(CGRectCreateDictionaryRepresentation(source)),
                @"SLContentStreamDestinationRect":CFBridgingRelease(CGRectCreateDictionaryRepresentation(destination)),
                @"SLContentStreamMinimumFrameTime":@0.1,
                @"SLContentStreamQueueDepth":@2,
                @"SLContentStreamShowCursor":@NO,
                @"SLContentStreamAlwaysScaleToFit":@NO,
                @"SLContentStreamPreserveAspectRatio":@YES,
                @"SLContentStreamIgnoreDeformingTransformsKey":@NO,
                @"SLContentStreamColorSpace":(__bridge id)color,
            };
            CGColorSpaceRelease(color);
            NSError *error = nil;
            state.stream = [(id<ProbeStream>)[streamClass alloc] initWithFilter:filter
                properties:properties queue:dispatch_get_main_queue()
                handler:^(id<ProbeUpdate> update) { Receive(state, update); } error:&error];
            Log(@"stream-created-unstarted", @{@"object":@(state.stream != nil),
                @"error":error.description ?: @"none", @"error_code":@(error ? error.code : 0)});
            if (!state.stream || error || !HasMethods(state.stream,
                @[@"filter", @"properties", @"session", @"stream", @"running", @"start:", @"stop:"])) break;
            if (state.stream.running || !state.stream.stream ||
                CFGetTypeID(state.stream.stream) != CGDisplayStreamGetTypeID() ||
                !ValidateFilter(state.stream.filter, state.display, state.windows) ||
                ![state.stream.properties isEqualToDictionary:properties] ||
                !ValidateSession(state.stream.session, state.display, state.windows)) {
                Log(@"start-refused", @{@"reason":@"stream-filter-session-readback-mismatch"});
                result = 65; break;
            }
            if (Interrupted || Monotonic()-Started > 2.5) { result = 75; break; }
            state.verified = YES;
            error = nil;
            started = [state.stream start:&error];
            Log(@"start", @{@"returned":@(started), @"running":@(state.stream.running),
                @"error":error.description ?: @"none", @"error_code":@(error ? error.code : 0)});
            if (!started || error || !state.stream.running) break;
            while (!state.done && !Interrupted && Monotonic()-Started < 3.5) {
                @autoreleasepool {
                    [NSRunLoop.currentRunLoop runMode:NSDefaultRunLoopMode
                        beforeDate:[NSDate dateWithTimeIntervalSinceNow:0.01]];
                }
            }
            result = !state.failed && state.frames == state.requested &&
                state.validFrames == state.requested && !Interrupted ? 0 : 75;
        } while (0);
    } @catch (NSException *exception) {
        Log(@"exception", @{@"name":exception.name, @"reason":exception.reason ?: @"unknown"});
        result = 70;
    } @finally {
        state.done = YES;
        @try {
            if (state.stream && HasMethods(state.stream, @[@"stop:", @"running", @"stream"])) {
                NSError *error = nil;
                BOOL stopped = [state.stream stop:&error];
                cleanupOK = !state.stream.running && (!started || stopped);
                Log(@"stop", @{@"returned":@(stopped), @"running":@(state.stream.running),
                    @"error":error.description ?: @"none", @"error_code":@(error ? error.code : 0)});
            }
            state.stream = nil; // dealloc releases CG stream, session and handler.
        } @catch (NSException *exception) {
            cleanupOK = NO;
            Log(@"cleanup-exception", @{@"reason":exception.reason ?: @"unknown"});
        }
        if (!cleanupOK) result = 70;
        NSDictionary *summary = @{@"complete":@(result == 0), @"exit_code":@(result),
            @"frames":@(state.frames), @"valid_frames":@(state.validFrames),
            @"requested_frames":@(state.requested), @"display_id":@(state.display),
            @"included_windows":state.windows.allObjects,
            @"filter_verified_before_start":@(state.verified), @"cleanup_confirmed":@(cleanupOK),
            @"elapsed":@(Monotonic()-Started), @"visual_acceptance":@NO};
        Log(@"summary", summary);
        if (!WriteJSON([state.directory stringByAppendingPathComponent:@"summary.json"], summary)) result = 74;
    }
    return result;
}

static int SelfTest(void) {
    const uint8_t black[16] = {0};
    const uint8_t uniform[16] = {255,255,255,255, 255,255,255,255,
        255,255,255,255, 255,255,255,255};
    const uint8_t varied[16] = {0,0,0,255, 255,255,255,255, 255,0,0,255, 0,255,0,255};
    BOOL valid = ParseWindows(@"200,214").count == 2 && !ParseWindows(@"200,200") &&
        !ParseWindows(@"0") && !ParseWindows(@"-1") && !ParseWindows(@"1,") &&
        !ParseWindows(@"1a") && !ParseWindows(@"1١") && !ParseWindows(@"") &&
        !ParseWindows(@"4294967296") && !ParseWindows(@"1,2,3,4,5,6,7,8,9") &&
        ![PixelMetrics(black,2,2,8)[@"validpixels"] boolValue] &&
        ![PixelMetrics(uniform,2,2,8)[@"validpixels"] boolValue] &&
        [PixelMetrics(varied,2,2,8)[@"validpixels"] boolValue];
    Log(@"self-test", @{@"pass":@(valid), @"capture_started":@NO, @"private_api_invoked":@NO});
    return valid ? 0 : 1;
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        Started = Monotonic();
        if (argc == 2 && !strcmp(argv[1], "--self-test")) return SelfTest();
        if (argc == 2 && !strcmp(argv[1], "--request-consent")) {
            // Explicit authorized diagnostic only. Ask the standard TCC
            // consent API; never replace its result or create a stream when
            // it denies access. A missing/unresponsive chroot service cannot
            // leave this helper running indefinitely.
            signal(SIGALRM, HardDeadline);
            alarm(5);
            BOOL before = CGPreflightScreenCaptureAccess();
            Log(@"consent-before", @{@"granted":@(before), @"capture_started":@NO});
            BOOL requested = before || CGRequestScreenCaptureAccess();
            BOOL after = CGPreflightScreenCaptureAccess();
            Log(@"consent-after", @{@"request_returned":@(requested),
                @"granted":@(after), @"capture_started":@NO});
            alarm(0);
            return after ? 0 : 77;
        }
        if (argc != 6 || strcmp(argv[1], "--capture")) {
            fprintf(stderr, "usage: %s --self-test\n"
                "       %s --request-consent (explicit standard permission prompt; no capture)\n"
                "       %s --capture DISPLAY_ID WINDOW_ID[,WINDOW_ID...] NEW_DIRECTORY FRAMES(1-3)\n"
                "Diagnostic: <=5s, explicit whitelist only, no UI actions. Run only in authorized macOS chroot.\n",
                argv[0], argv[0], argv[0]);
            return 64;
        }
        uint32_t display, frames;
        NSSet *windows = ParseWindows(@(argv[3]));
        NSString *directory = @(argv[4]);
        if (!ParseUnsigned(@(argv[2]), &display) || !ParseUnsigned(@(argv[5]), &frames) ||
            frames > 3 || !windows || !directory.isAbsolutePath || mkdir(directory.fileSystemRepresentation, 0700)) {
            fprintf(stderr, "invalid arguments or output directory already exists/uncreatable\n");
            return 64;
        }
        signal(SIGALRM, HardDeadline); signal(SIGINT, Interrupt); signal(SIGTERM, Interrupt);
        double remaining = fmax(0.001, 5.0 - (Monotonic() - Started));
        struct itimerval deadline = {0};
        deadline.it_value.tv_sec = (time_t)remaining;
        deadline.it_value.tv_usec = (suseconds_t)((remaining - floor(remaining)) * 1e6);
        if (setitimer(ITIMER_REAL, &deadline, NULL)) {
            fprintf(stderr, "cannot install hard deadline errno=%d; no capture started\n", errno);
            return 71;
        }
        ProbeState *state = [ProbeState new];
        state.display = display; state.windows = windows; state.directory = directory; state.requested = frames;
        if (!WriteJSON([directory stringByAppendingPathComponent:@"summary.json"],
            @{@"complete":@NO, @"visual_acceptance":@NO, @"cleanup_confirmed":@NO,
                @"state":@"initializing", @"requested_frames":@(frames), @"included_windows":windows.allObjects})) return 74;
        int result = RunProbe(state);
        state = nil;
        alarm(0);
        return result;
    }
}

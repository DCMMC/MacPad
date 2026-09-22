#import "MacWSCatalystDrawableCompositor.h"

#import "MacWSHostDiagnostics.h"

#import <IOSurface/IOSurfaceRef.h>
#import <simd/simd.h>
#include <string.h>

@interface NSObject (MacWSCatalystIOSurfaceAlignment)
- (NSUInteger)iosurfaceReadOnlyTextureAlignmentBytes;
@end

@interface MacWSCatalystDrawableFrame ()
- (instancetype)initWithRecord:(MacWSCatalystDrawableRecord)record
                        surface:(IOSurfaceRef)surface
                        texture:(id<MTLTexture>)texture;
@end

@implementation MacWSCatalystDrawableFrame {
    IOSurfaceRef _surface;
    BOOL _holdsTransferredUseCount;
}

- (instancetype)initWithRecord:(MacWSCatalystDrawableRecord)record
                        surface:(IOSurfaceRef)surface
                        texture:(id<MTLTexture>)texture {
    self = [super init];
    if (!self) return nil;
    _record = record;
    _surface = surface ? (IOSurfaceRef)CFRetain(surface) : NULL;
    _holdsTransferredUseCount = surface &&
        (record.flags & MacWSCatalystDrawableTransfersUseCount) != 0;
    _texture = texture;
    return self;
}

- (void)dealloc {
    if (_surface && _holdsTransferredUseCount)
        IOSurfaceDecrementUseCount(_surface);
    if (_surface) CFRelease(_surface);
}

- (IOSurfaceRef)surface {
    return _surface;
}

@end


@implementation MacWSCatalystDrawableCompositor {
    id<MTLDevice> _device;
    NSMutableDictionary<NSNumber *, MacWSCatalystDrawableFrame *> *_frames;
    BOOL _reportedGeometryRejection;
    BOOL _reportedTextureRejection;
}

- (instancetype)initWithDevice:(id<MTLDevice>)device {
    self = [super init];
    if (!self) return nil;
    _device = device;
    _frames = [NSMutableDictionary dictionary];
    return self;
}

- (MacWSCatalystDrawableFrame *)consumeDeliveryObject:(id)object
    shouldAcceptOwner:(BOOL (^)(int32_t))shouldAcceptOwner {
    NSDictionary *delivery = [object isKindOfClass:NSDictionary.class]
        ? (NSDictionary *)object : nil;
    // One producer message transfers exactly one IOSurface use count. The
    // process-global notification can have several Scene observers, so the
    // first eligible consumer owns the delivery and every later observer must
    // reject it. NotificationCenter invokes these observers synchronously on
    // the receiver's main queue, making the mutable envelope the serialization
    // boundary rather than an advisory success flag.
    if ([delivery[@"accepted"] boolValue]) return nil;
    NSData *payload = [delivery[@"record"] isKindOfClass:NSData.class]
        ? delivery[@"record"] : nil;
    IOSurfaceRef surface = delivery[@"surface"]
        ? (__bridge IOSurfaceRef)delivery[@"surface"] : NULL;
    if (payload.length != sizeof(MacWSCatalystDrawableRecord) || !_device ||
        !surface) return nil;

    MacWSCatalystDrawableRecord record = {0};
    memcpy(&record, payload.bytes, sizeof(record));
    if (!MacWSCatalystDrawableRecordIsValid(&record, sizeof(record)) ||
        (shouldAcceptOwner && !shouldAcceptOwner(record.ownerPID))) return nil;

    NSNumber *ownerKey = @(record.ownerPID);
    MacWSCatalystDrawableFrame *previous = _frames[ownerKey];
    if (previous && previous.record.sequence >= record.sequence) return nil;

    BOOL geometryMatches = IOSurfaceGetWidth(surface) == record.width &&
        IOSurfaceGetHeight(surface) == record.height &&
        IOSurfaceGetBytesPerRow(surface) == record.bytesPerRow &&
        IOSurfaceGetPixelFormat(surface) == record.ioSurfacePixelFormat;
    NSUInteger alignment = [_device respondsToSelector:
        @selector(iosurfaceReadOnlyTextureAlignmentBytes)]
        ? [(id)_device iosurfaceReadOnlyTextureAlignmentBytes] : 0;
    if (!geometryMatches ||
        (alignment && record.bytesPerRow % alignment != 0)) {
        if (!_reportedGeometryRejection) {
            _reportedGeometryRejection = YES;
            MacWSLog(@"catalyst-drawable reject-geometry pid=%d sequence=%llu "
                     "record=%ux%u/bpr%u/pf%u actual=%zux%zu/bpr%zu/pf%u "
                     "alignment=%lu remainder=%lu",
                     record.ownerPID, (unsigned long long)record.sequence,
                     record.width, record.height, record.bytesPerRow,
                     record.ioSurfacePixelFormat, IOSurfaceGetWidth(surface),
                     IOSurfaceGetHeight(surface),
                     IOSurfaceGetBytesPerRow(surface),
                     IOSurfaceGetPixelFormat(surface),
                     (unsigned long)alignment,
                     (unsigned long)(alignment
                         ? record.bytesPerRow % alignment : 0));
        }
        return nil;
    }

    MTLTextureDescriptor *descriptor =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:
            MTLPixelFormatBGRA8Unorm width:record.width height:record.height
            mipmapped:NO];
    descriptor.storageMode = MTLStorageModeShared;
    descriptor.usage = MTLTextureUsageShaderRead;
    id<MTLTexture> texture = [_device newTextureWithDescriptor:descriptor
                                                     iosurface:surface
                                                         plane:0];
    if (!texture) {
        if (!_reportedTextureRejection) {
            _reportedTextureRejection = YES;
            MacWSLog(@"catalyst-drawable reject-texture pid=%d sequence=%llu "
                     "size=%ux%u bpr=%u metal-pf=%u",
                     record.ownerPID, (unsigned long long)record.sequence,
                     record.width, record.height, record.bytesPerRow,
                     record.metalPixelFormat);
        }
        return nil;
    }

    MacWSCatalystDrawableFrame *frame =
        [[MacWSCatalystDrawableFrame alloc] initWithRecord:record
                                                   surface:surface
                                                   texture:texture];
    _frames[ownerKey] = frame;
    // Notification delivery is synchronous.  Mark the mutable envelope only
    // after the IOSurface-backed texture and frame lease both exist; the
    // receiver returns the producer-transferred use count on every rejected
    // path.
    if ([delivery isKindOfClass:NSMutableDictionary.class])
        ((NSMutableDictionary *)delivery)[@"accepted"] = @YES;
    if (!previous) {
        MacWSLog(@"runtime-confirmed catalyst-drawable imported pid=%d "
                 "surface=%u size=%ux%u bpr=%u metal-pf=%u",
                 record.ownerPID, record.surfaceID, record.width,
                 record.height, record.bytesPerRow, record.metalPixelFormat);
    }
    return frame;
}

- (MacWSCatalystDrawableFrame *)frameForOwnerPID:(int32_t)ownerPID {
    return ownerPID > 1 ? _frames[@(ownerPID)] : nil;
}

- (void)removeAllFrames {
    [_frames removeAllObjects];
}

@end


BOOL MacWSEncodeCatalystDrawable(
    id<MTLRenderCommandEncoder> encoder,
    MacWSCatalystDrawableFrame *frame,
    CGRect windowDestination,
    CGRect visiblePixels,
    CGRect contentRect,
    CGSize viewSize,
    CGFloat titlebarHeightPixels) {
    if (!encoder || !frame.texture || viewSize.width <= 0 ||
        viewSize.height <= 0 || CGRectIsEmpty(windowDestination) ||
        CGRectIsEmpty(visiblePixels) || CGRectIsEmpty(contentRect)) return NO;

    CGFloat titlebar = fmin(fmax(titlebarHeightPixels, 0.0),
                            CGRectGetHeight(windowDestination));
    CGRect clientDestination = CGRectMake(
        CGRectGetMinX(windowDestination),
        CGRectGetMinY(windowDestination) + titlebar,
        CGRectGetWidth(windowDestination),
        CGRectGetHeight(windowDestination) - titlebar);
    CGRect clipped = CGRectIntersection(clientDestination, visiblePixels);
    if (CGRectIsNull(clipped) || CGRectIsEmpty(clipped)) return NO;

    CGFloat viewLeft = CGRectGetMinX(contentRect) +
        (CGRectGetMinX(clipped) - CGRectGetMinX(visiblePixels)) /
            CGRectGetWidth(visiblePixels) * CGRectGetWidth(contentRect);
    CGFloat viewRight = CGRectGetMinX(contentRect) +
        (CGRectGetMaxX(clipped) - CGRectGetMinX(visiblePixels)) /
            CGRectGetWidth(visiblePixels) * CGRectGetWidth(contentRect);
    CGFloat viewTop = CGRectGetMinY(contentRect) +
        (CGRectGetMinY(clipped) - CGRectGetMinY(visiblePixels)) /
            CGRectGetHeight(visiblePixels) * CGRectGetHeight(contentRect);
    CGFloat viewBottom = CGRectGetMinY(contentRect) +
        (CGRectGetMaxY(clipped) - CGRectGetMinY(visiblePixels)) /
            CGRectGetHeight(visiblePixels) * CGRectGetHeight(contentRect);

    // The Catalyst drawable includes UIKit's complete layer coordinate space,
    // while SkyLight supplies AppKit's title bar independently. Preserve the
    // established source mapping by cropping the title-bar fraction from the
    // drawable instead of stretching its full height into the client rect.
    float textureLeft = (CGRectGetMinX(clipped) -
        CGRectGetMinX(windowDestination)) / CGRectGetWidth(windowDestination);
    float textureRight = (CGRectGetMaxX(clipped) -
        CGRectGetMinX(windowDestination)) / CGRectGetWidth(windowDestination);
    float textureTop = (CGRectGetMinY(clipped) -
        CGRectGetMinY(windowDestination)) / CGRectGetHeight(windowDestination);
    float textureBottom = (CGRectGetMaxY(clipped) -
        CGRectGetMinY(windowDestination)) / CGRectGetHeight(windowDestination);
    simd_float4 vertices[4] = {
        {(float)(viewLeft / viewSize.width * 2.0 - 1.0),
         (float)(1.0 - viewBottom / viewSize.height * 2.0),
         textureLeft, textureBottom},
        {(float)(viewRight / viewSize.width * 2.0 - 1.0),
         (float)(1.0 - viewBottom / viewSize.height * 2.0),
         textureRight, textureBottom},
        {(float)(viewLeft / viewSize.width * 2.0 - 1.0),
         (float)(1.0 - viewTop / viewSize.height * 2.0),
         textureLeft, textureTop},
        {(float)(viewRight / viewSize.width * 2.0 - 1.0),
         (float)(1.0 - viewTop / viewSize.height * 2.0),
         textureRight, textureTop},
    };
    [encoder setVertexBytes:vertices length:sizeof(vertices) atIndex:0];
    [encoder setFragmentTexture:frame.texture atIndex:0];
    [encoder drawPrimitives:MTLPrimitiveTypeTriangleStrip
                vertexStart:0 vertexCount:4];
    return YES;
}

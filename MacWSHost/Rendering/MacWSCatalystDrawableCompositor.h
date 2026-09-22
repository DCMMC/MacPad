#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <UIKit/UIKit.h>

#include "macws_catalyst_drawable_protocol.h"

NS_ASSUME_NONNULL_BEGIN

// Immutable ownership unit for one completed Catalyst CAMetalLayer drawable.
// The IOSurface lifetime is retained with the texture so callers never need to
// coordinate Mach-right or CF ownership with the render loop.
@interface MacWSCatalystDrawableFrame : NSObject
@property(nonatomic, readonly) MacWSCatalystDrawableRecord record;
@property(nonatomic, readonly) id<MTLTexture> texture;
@property(nonatomic, readonly) IOSurfaceRef surface;
@end

// Imports validated Catalyst IOSurface deliveries and keeps only the newest
// sequence for each producer. Transport remains in Transport/; this class is
// the rendering-side policy and lifetime boundary.
@interface MacWSCatalystDrawableCompositor : NSObject
- (instancetype)initWithDevice:(id<MTLDevice>)device
    NS_DESIGNATED_INITIALIZER;
- (instancetype)init NS_UNAVAILABLE;

- (nullable MacWSCatalystDrawableFrame *)consumeDeliveryObject:(id)object
    shouldAcceptOwner:(BOOL (^)(int32_t ownerPID))shouldAcceptOwner;
- (nullable MacWSCatalystDrawableFrame *)frameForOwnerPID:(int32_t)ownerPID;
- (void)removeAllFrames;
@end

// Draw a complete Catalyst client texture into the client portion of one
// SkyLight window. Coordinates are backing pixels in the source composite;
// contentRect/viewSize describe the already-established Host viewport.
// Returns NO when the geometry is empty or invalid.
BOOL MacWSEncodeCatalystDrawable(
    id<MTLRenderCommandEncoder> encoder,
    MacWSCatalystDrawableFrame *frame,
    CGRect windowDestination,
    CGRect visiblePixels,
    CGRect contentRect,
    CGSize viewSize,
    CGFloat titlebarHeightPixels);

NS_ASSUME_NONNULL_END

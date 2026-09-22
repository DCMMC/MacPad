#pragma once

#include <stdbool.h>
#include <stdint.h>

#import <IOSurface/IOSurfaceRef.h>
#import <Metal/Metal.h>

// Production transport for WindowServer's completed native-AGX scanout.
// Metal interception decides which surface is the coherent display target;
// this module owns admission state, the authenticated Mach handoff, bounded
// failure witnesses and service-replacement recovery.
void MacWSFinalCompositePublisherMarkContentValidated(void);
bool MacWSFinalCompositePublisherCanPublish(void);
uint64_t MacWSFinalCompositePublisherPublishedSequence(void);
bool MacWSFinalCompositePublisherPublishSurface(
    IOSurfaceRef surface, uint32_t metalPixelFormat);

// Observe the latest completed scanout without blocking WindowServer's render
// thread. Ownership selection and texture-to-IOSurface binding stay in the
// Metal adapter; the publisher owns completion coalescing and object lifetime.
void MacWSFinalCompositePublisherEnqueueCompletion(
    id<MTLCommandBuffer> commandBuffer, id<MTLTexture> sourceTexture,
    IOSurfaceRef surface,
    uint32_t metalPixelFormat);

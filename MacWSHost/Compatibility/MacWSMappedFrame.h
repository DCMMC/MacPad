#pragma once

#import <Foundation/Foundation.h>

FOUNDATION_EXPORT NSString *const MacWSFramePath;

// Explicit legacy mmap compatibility adapter. Production rendering uses
// MacWSStreamClient/IOSurface; this class is reachable only when the user has
// enabled MacWSLegacyFramebufferFallback.
@interface MacWSMappedFrame : NSObject
@property(nonatomic, readonly) const uint8_t *pixels;
@property(nonatomic, readonly) uint32_t width;
@property(nonatomic, readonly) uint32_t height;
@property(nonatomic, readonly) uint32_t stride;
@property(nonatomic, readonly) NSString *lastError;
- (BOOL)refresh;
@end

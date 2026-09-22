#import "MacWSCatalystDrawableProbe.h"

#import "MacWSCatalystDrawableCompositor.h"

#import <IOSurface/IOSurfaceRef.h>
#import <IOKit/IOReturn.h>

NSDictionary *MacWSProbeCatalystDrawable(
    MacWSCatalystDrawableFrame *frame,
    NSString *outputPath,
    NSError **error) {
    IOSurfaceRef surface = frame.surface;
    if (!surface) {
        if (error) *error = [NSError errorWithDomain:@"MacWSCatalystProbe"
            code:1 userInfo:@{NSLocalizedDescriptionKey:
                @"没有可探测的 Catalyst IOSurface"}];
        return nil;
    }
    IOReturn lockResult = IOSurfaceLock(surface, kIOSurfaceLockReadOnly, NULL);
    if (lockResult != kIOReturnSuccess) {
        if (error) *error = [NSError errorWithDomain:@"MacWSCatalystProbe"
            code:lockResult userInfo:@{NSLocalizedDescriptionKey:
                @"无法只读锁定 Catalyst IOSurface"}];
        return nil;
    }
    const uint8_t *base = IOSurfaceGetBaseAddress(surface);
    size_t size = IOSurfaceGetAllocSize(surface);
    size_t nonzero = 0;
    uint64_t digest = 1469598103934665603ULL;
    if (base && size) {
        // Bounded stratified witness: enough to distinguish a real game frame
        // from a cleared surface without adding a full 5-MB scan to testing.
        size_t sampleCount = MIN((size_t)65536, size);
        for (size_t index = 0; index < sampleCount; index++) {
            size_t offset = index * size / sampleCount;
            uint8_t byte = base[offset];
            nonzero += byte != 0;
            digest ^= byte;
            digest *= 1099511628211ULL;
        }
    }
    NSData *copy = (outputPath.length && base && size)
        ? [NSData dataWithBytes:base length:size] : nil;
    IOSurfaceUnlock(surface, kIOSurfaceLockReadOnly, NULL);
    if (copy && ![copy writeToFile:outputPath
                           options:NSDataWritingAtomic error:error]) return nil;
    MacWSCatalystDrawableRecord record = frame.record;
    return @{
        @"pid": @(record.ownerPID),
        @"surface_id": @(record.surfaceID),
        @"sequence": @(record.sequence),
        @"width": @(record.width),
        @"height": @(record.height),
        @"bytes_per_row": @(record.bytesPerRow),
        @"alloc_size": @(size),
        @"sample_count": @(MIN((size_t)65536, size)),
        @"sample_nonzero": @(nonzero),
        @"sample_digest_fnv1a64": [NSString stringWithFormat:@"%016llx",
            (unsigned long long)digest],
        @"raw_output": outputPath ?: @"",
    };
}

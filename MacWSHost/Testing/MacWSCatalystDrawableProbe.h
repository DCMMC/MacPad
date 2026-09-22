#import <Foundation/Foundation.h>

@class MacWSCatalystDrawableFrame;

NS_ASSUME_NONNULL_BEGIN

// Explicit test-only IOSurface inspection. Production never calls this and
// therefore never maps or scans a Catalyst drawable on the CPU.
NSDictionary * _Nullable MacWSProbeCatalystDrawable(
    MacWSCatalystDrawableFrame *frame,
    NSString * _Nullable outputPath,
    NSError **error);

NS_ASSUME_NONNULL_END

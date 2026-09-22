#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN

typedef void (^MacWSControlCompletion)(NSDictionary<NSString *, id> *reply);

@interface MacWSControlClient : NSObject
- (void)fetchStatus:(MacWSControlCompletion)completion;
- (void)fetchLogs:(MacWSControlCompletion)completion;
- (void)startWithExperimentalMode:(BOOL)experimental
                       completion:(MacWSControlCompletion)completion;
- (void)performOperation:(NSString *)operation
               arguments:(nullable NSDictionary<NSString *, id> *)arguments
              completion:(MacWSControlCompletion)completion;
@end

NS_ASSUME_NONNULL_END

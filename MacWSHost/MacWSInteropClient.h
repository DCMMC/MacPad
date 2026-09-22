#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN

@class MacWSInteropClient;
@class NSItemProvider;

@protocol MacWSInteropClientDelegate <NSObject>
- (void)interopClient:(MacWSInteropClient *)client
        statusChanged:(NSString *)status
            connected:(BOOL)connected;
- (void)interopClient:(MacWSInteropClient *)client
 receivedMacOSFilesAtURLs:(NSArray<NSURL *> *)urls;
@end

@interface MacWSInteropClient : NSObject
@property(nonatomic, weak, nullable) id<MacWSInteropClientDelegate> delegate;
@property(nonatomic, readonly, getter=isConnected) BOOL connected;

- (void)connect;
// Open-In is an import, not a clipboard write. Completion runs on main.
+ (void)importDocumentURL:(NSURL *)url
              completion:(void (^)(NSString * _Nullable chrootPath,
                                   NSError * _Nullable error))completion;
// Publishes every bounded representation of every ordered UIPasteboard item.
// The client also observes pasteboard changes while MacWSHost is active; this
// explicit entry point remains useful for retrying after an iPadOS privacy
// prompt or a temporarily unavailable provider.
- (void)publishGeneralPasteboard;
- (void)publishItemProviders:(NSArray<NSItemProvider *> *)providers
                  completion:(void (^)(BOOL applied,
                                       NSError * _Nullable error))completion;
- (uint64_t)macOSDragPasteboardChangeCount;
- (NSArray<NSItemProvider *> *)macOSDragItemProvidersAfterChangeCount:
    (uint64_t)changeCount waitMilliseconds:(uint64_t)waitMilliseconds;
- (NSArray<NSItemProvider *> *)macOSDragItemProvidersAfterChangeCount:
    (uint64_t)changeCount waitMilliseconds:(uint64_t)waitMilliseconds
    stagedURLs:(NSArray<NSURL *> * _Nullable * _Nullable)stagedURLs;
- (nullable NSItemProvider *)dragItemProviderForStagedURL:(NSURL *)url;
- (void)stageAndPublishFiles:(NSArray<NSURL *> *)urls
                  completion:(void (^)(NSArray<NSURL *> *stagedURLs,
                                       NSError * _Nullable error))completion;
- (void)invalidate;
@end

NS_ASSUME_NONNULL_END

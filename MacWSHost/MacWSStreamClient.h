#import <Foundation/Foundation.h>
#import <IOSurface/IOSurfaceRef.h>

#include "macws_stream_protocol.h"

NS_ASSUME_NONNULL_BEGIN

@interface MacWSStreamWindow : NSObject
@property(nonatomic, readonly) MacWSStreamWindowDescriptor descriptor;
@property(nonatomic, readonly) NSString *title;
@property(nonatomic, readonly) CGSize maximumLogicalSize;
@property(nonatomic, readonly) BOOL supportsConfigurationAcknowledgements;
@property(nonatomic, readonly) double latestConfigureTimestamp;
@property(nonatomic, readonly) uint32_t latestConfigureSequence;
@property(nonatomic, readonly) CGSize latestConfigureRequestedSize;
@property(nonatomic, readonly) CGSize latestConfigureAppliedSize;
- (instancetype)initWithDescriptor:(MacWSStreamWindowDescriptor)descriptor
                              title:(NSString *)title;
- (instancetype)initWithDescriptor:(MacWSStreamWindowDescriptor)descriptor
                              title:(NSString *)title
                             limits:(nullable const MacWSStreamWindowLimits *)limits
    NS_DESIGNATED_INITIALIZER;
- (instancetype)init NS_UNAVAILABLE;
@end

@interface MacWSSurfaceFrame : NSObject
@property(nonatomic, readonly) MacWSStreamFrameDescriptor descriptor;
@property(nonatomic, readonly) IOSurfaceRef surface;
@property(nonatomic, readonly) uint64_t receiptTime;
- (instancetype)initWithDescriptor:(MacWSStreamFrameDescriptor)descriptor
                            surface:(IOSurfaceRef)surface
                        receiptTime:(uint64_t)receiptTime NS_DESIGNATED_INITIALIZER;
- (instancetype)init NS_UNAVAILABLE;
@end

@class MacWSStreamClient;

@protocol MacWSStreamClientDelegate <NSObject>
- (void)streamClient:(MacWSStreamClient *)client
       statusChanged:(NSString *)status
           connected:(BOOL)connected;
- (void)streamClient:(MacWSStreamClient *)client
      receivedWindows:(NSArray<MacWSStreamWindow *> *)windows;
- (void)streamClient:(MacWSStreamClient *)client
        receivedFrame:(MacWSSurfaceFrame *)frame;
- (void)streamClient:(MacWSStreamClient *)client
 receivedLayerGeometryUpdates:(NSData *)updates
                   receiptTime:(uint64_t)receiptTime;
- (void)streamClient:(MacWSStreamClient *)client
 removedLayerWindowID:(uint32_t)layerWindowID;
@end

@interface MacWSStreamClient : NSObject
@property(nonatomic, weak, nullable) id<MacWSStreamClientDelegate> delegate;
@property(nonatomic, readonly, getter=isConnected) BOOL connected;
@property(nonatomic, readonly) MacWSStreamMode mode;
@property(nonatomic, readonly) uint32_t windowID;
// Main-queue snapshot, separate from the user-facing AppKit window catalog.
@property(nonatomic, readonly, copy) NSArray<MacWSStreamWindow *> *systemInputWindows;

- (void)subscribeToMode:(MacWSStreamMode)mode windowID:(uint32_t)windowID;
- (void)requestWindowList;
- (void)unsubscribe;
- (void)releaseFrame:(MacWSSurfaceFrame *)frame;
- (void)noteDirectDrawableForOwnerPID:(int32_t)ownerPID
                        layerWindowID:(uint32_t)layerWindowID
                                width:(uint32_t)width
                               height:(uint32_t)height;
- (void)clearDirectDrawableActivity;
- (void)invalidate;
@end

NS_ASSUME_NONNULL_END

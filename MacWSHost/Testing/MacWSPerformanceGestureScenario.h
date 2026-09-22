#pragma once

#import <UIKit/UIKit.h>

#include "macws_host_protocol.h"

typedef void (^MacWSPerformancePointerEmitter)(
    MacWSInputKind kind, CGPoint point, float pressure, uint16_t flags);
typedef void (^MacWSPerformanceScrollEmitter)(
    CGPoint point, CGPoint translation, uint16_t flags,
    NSTimeInterval timestamp);
typedef void (^MacWSPerformanceMagnifyEmitter)(
    CGPoint point, CGFloat amount, uint16_t flags,
    NSTimeInterval timestamp);
typedef void (^MacWSPerformanceMomentumStarter)(
    CGPoint velocity, CGPoint point);
typedef void (^MacWSPerformanceMomentumStopper)(BOOL terminalPhase);
typedef void (^MacWSPerformanceSystemGesturePreparer)(
    MacWSSystemGestureAxis axis, uint32_t contactID, int32_t dockPID,
    uint32_t frameWidth, uint32_t frameHeight, CGFloat velocity);
typedef void (^MacWSPerformanceSystemGestureEmitter)(
    MacWSSystemGestureAxis axis, CGFloat progress, CGFloat velocity,
    uint16_t flags, NSTimeInterval timestamp);
typedef void (^MacWSPerformanceSystemGestureResetter)(void);
typedef void (^MacWSPerformanceGestureCompletion)(
    BOOL success, NSString *message);

@interface MacWSPerformanceGestureScenario : NSObject

@property(nonatomic, copy) NSString *name;
@property(nonatomic) CGPoint targetPoint;
@property(nonatomic) CGPoint alternateTargetPoint;
@property(nonatomic) uint32_t frameWidth;
@property(nonatomic) uint32_t frameHeight;
@property(nonatomic) int32_t targetPID;
@property(nonatomic) int32_t dockPID;
@property(nonatomic) BOOL fullscreen;
@property(nonatomic) uint32_t contactID;
@property(nonatomic) uint16_t pointerFlags;

@property(nonatomic, copy) MacWSPerformancePointerEmitter emitPointer;
@property(nonatomic, copy) MacWSPerformanceScrollEmitter emitScroll;
@property(nonatomic, copy) MacWSPerformanceMagnifyEmitter emitMagnify;
@property(nonatomic, copy) MacWSPerformanceMomentumStarter startMomentum;
@property(nonatomic, copy) MacWSPerformanceMomentumStopper stopMomentum;
@property(nonatomic, copy) MacWSPerformanceSystemGesturePreparer
    prepareSystemGesture;
@property(nonatomic, copy) MacWSPerformanceSystemGestureEmitter
    emitSystemGesture;
@property(nonatomic, copy) MacWSPerformanceSystemGestureResetter
    resetSystemGesture;
@property(nonatomic, copy) dispatch_block_t missionControlDidCommit;

- (void)runWithCompletion:(MacWSPerformanceGestureCompletion)completion;

@end

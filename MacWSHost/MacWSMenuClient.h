#import <Foundation/Foundation.h>

#include "macws_menu_protocol.h"

NS_ASSUME_NONNULL_BEGIN

@interface MacWSMenuItem : NSObject
@property(nonatomic, readonly) uint64_t itemID;
@property(nonatomic, readonly) uint64_t parentItemID;
@property(nonatomic, readonly) uint32_t siblingIndex;
@property(nonatomic, readonly) MacWSMenuNodeFlags flags;
@property(nonatomic, readonly) NSInteger state;
@property(nonatomic, readonly) NSString *title;
@property(nonatomic, readonly) NSString *shortcut;
@end

@interface MacWSMenuSnapshot : NSObject
// Displayed scene identity and menu provider differ for multi-process apps.
// ownerPID/windowID remain the exact provider used for generation/actions.
@property(nonatomic, readonly) int32_t representedOwnerPID;
@property(nonatomic, readonly) uint32_t representedWindowID;
@property(nonatomic, readonly) int32_t ownerPID;
@property(nonatomic, readonly) uint32_t windowID;
@property(nonatomic, readonly) uint64_t generation;
@property(nonatomic, readonly) MacWSMenuAppearance appearance;
@property(nonatomic, readonly) NSArray<MacWSMenuItem *> *items;
- (NSArray<MacWSMenuItem *> *)childrenOfItemID:(uint64_t)itemID;
- (nullable MacWSMenuItem *)itemWithID:(uint64_t)itemID;
@end

typedef void (^MacWSMenuSnapshotCompletion)(
    MacWSMenuSnapshot * _Nullable snapshot, NSError * _Nullable error);
typedef void (^MacWSMenuActionCompletion)(
    MacWSMenuStatus status, NSError * _Nullable error);

@interface MacWSMenuClient : NSObject
- (void)requestSnapshotForPID:(int32_t)ownerPID
                     windowID:(uint32_t)windowID
                   completion:(MacWSMenuSnapshotCompletion)completion;
- (void)performItem:(MacWSMenuItem *)item
         inSnapshot:(MacWSMenuSnapshot *)snapshot
         completion:(MacWSMenuActionCompletion)completion;
- (void)invalidate;
@end

NS_ASSUME_NONNULL_END

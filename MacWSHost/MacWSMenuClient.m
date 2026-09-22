#import "MacWSMenuClient.h"

#include <dlfcn.h>
#include <mach/mach_time.h>
#include <xpc/xpc.h>

#include "macws_stream_protocol.h"

static NSString *const MacWSMenuErrorDomain = @"MacWSMenuError";
static char MacWSMenuClientQueueKey;

@interface MacWSMenuItem ()
@property(nonatomic, readwrite) uint64_t itemID;
@property(nonatomic, readwrite) uint64_t parentItemID;
@property(nonatomic, readwrite) uint32_t siblingIndex;
@property(nonatomic, readwrite) MacWSMenuNodeFlags flags;
@property(nonatomic, readwrite) NSInteger state;
@property(nonatomic, readwrite) NSString *title;
@property(nonatomic, readwrite) NSString *shortcut;
@end
@implementation MacWSMenuItem
@end

@interface MacWSMenuSnapshot ()
@property(nonatomic, readwrite) int32_t representedOwnerPID;
@property(nonatomic, readwrite) uint32_t representedWindowID;
@property(nonatomic, readwrite) int32_t ownerPID;
@property(nonatomic, readwrite) uint32_t windowID;
@property(nonatomic, readwrite) uint64_t generation;
@property(nonatomic, readwrite) MacWSMenuAppearance appearance;
@property(nonatomic, readwrite) NSArray<MacWSMenuItem *> *items;
@property(nonatomic) NSDictionary<NSNumber *, MacWSMenuItem *> *itemsByID;
@property(nonatomic) NSDictionary<NSNumber *, NSArray<MacWSMenuItem *> *> *childrenByID;
@end

@implementation MacWSMenuSnapshot
- (NSArray<MacWSMenuItem *> *)childrenOfItemID:(uint64_t)itemID {
    return self.childrenByID[@(itemID)] ?: @[];
}
- (MacWSMenuItem *)itemWithID:(uint64_t)itemID {
    return self.itemsByID[@(itemID)];
}
@end

typedef void (^MacWSMenuRawCompletion)(NSData * _Nullable,
                                       NSError * _Nullable);

@interface MacWSMenuClient ()
@property(nonatomic) dispatch_queue_t queue;
@property(nonatomic) xpc_connection_t connection;
@property(nonatomic) uint64_t nextNonce;
@property(nonatomic) NSMutableDictionary<NSNumber *, MacWSMenuRawCompletion> *pending;
@end

@implementation MacWSMenuClient

- (instancetype)init {
    self = [super init];
    if (self) {
        _queue = dispatch_queue_create("com.macwsguide.host.menu-client",
                                       DISPATCH_QUEUE_SERIAL);
        dispatch_queue_set_specific(_queue, &MacWSMenuClientQueueKey,
                                    &MacWSMenuClientQueueKey, NULL);
        _pending = [NSMutableDictionary dictionary];
        _nextNonce = mach_absolute_time() ?: 1;
    }
    return self;
}

- (void)dealloc { [self invalidate]; }

- (NSError *)errorWithStatus:(MacWSMenuStatus)status description:(NSString *)text {
    return [NSError errorWithDomain:MacWSMenuErrorDomain code:status
        userInfo:@{NSLocalizedDescriptionKey: text ?: @"macOS 菜单请求失败"}];
}

- (BOOL)ensureConnection {
    if (self.connection) return YES;
    xpc_connection_t (*createMach)(const char *, dispatch_queue_t, uint64_t) =
        dlsym(RTLD_DEFAULT, "xpc_connection_create_mach_service");
    if (!createMach) return NO;
    xpc_connection_t connection = createMach(MACWS_STREAM_SERVICE,
                                               self.queue, 0);
    if (!connection) return NO;
    __weak typeof(self) weakSelf = self;
    xpc_connection_set_event_handler(connection, ^(xpc_object_t event) {
        [weakSelf handleEvent:event];
    });
    xpc_connection_resume(connection);
    self.connection = connection;
    return YES;
}

- (void)failAllPending:(NSString *)message {
    NSArray<MacWSMenuRawCompletion> *blocks = self.pending.allValues;
    [self.pending removeAllObjects];
    NSError *error = [self errorWithStatus:MacWSMenuStatusTargetUnavailable
                               description:message];
    for (MacWSMenuRawCompletion block in blocks) {
        dispatch_async(dispatch_get_main_queue(), ^{ block(nil, error); });
    }
}

- (void)handleEvent:(xpc_object_t)event {
    if (event == XPC_ERROR_CONNECTION_INVALID ||
        event == XPC_ERROR_CONNECTION_INTERRUPTED) {
        xpc_connection_t connection = self.connection;
        self.connection = nil;
        if (connection) xpc_connection_cancel(connection);
        [self failAllPending:@"macOS 菜单桥已断开"];
        return;
    }
    if (!event || xpc_get_type(event) != XPC_TYPE_DICTIONARY) return;
    const char *name = xpc_dictionary_get_string(event,
                                                  MACWS_STREAM_KEY_EVENT);
    if (!name || strcmp(name, MACWS_MENU_XPC_EVENT_RESPONSE) != 0) return;
    size_t length = 0;
    const void *bytes = xpc_dictionary_get_data(
        event, MACWS_MENU_XPC_KEY_RESPONSE, &length);
    if (!bytes || length < sizeof(MacWSMenuResponseHeader)) return;
    const MacWSMenuResponseHeader *header = bytes;
    MacWSMenuRawCompletion completion = self.pending[@(header->nonce)];
    if (!completion) return;
    [self.pending removeObjectForKey:@(header->nonce)];
    NSData *data = [NSData dataWithBytes:bytes length:length];
    dispatch_async(dispatch_get_main_queue(), ^{ completion(data, nil); });
}

- (void)sendRequest:(MacWSMenuRequest)request
          operation:(const char *)operation
         completion:(MacWSMenuRawCompletion)completion {
    dispatch_async(self.queue, ^{
        if (![self ensureConnection]) {
            NSError *error = [self errorWithStatus:
                MacWSMenuStatusTargetUnavailable
                description:@"DisplayStream 菜单控制面不可用"];
            dispatch_async(dispatch_get_main_queue(), ^{
                completion(nil, error);
            });
            return;
        }
        self.pending[@(request.nonce)] = [completion copy];
        xpc_object_t message = xpc_dictionary_create(NULL, NULL, 0);
        xpc_dictionary_set_string(message, MACWS_STREAM_KEY_OP, operation);
        xpc_dictionary_set_data(message, MACWS_MENU_XPC_KEY_REQUEST,
                                &request, sizeof(request));
        xpc_connection_send_message(self.connection, message);
        uint64_t nonce = request.nonce;
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 2 * NSEC_PER_SEC),
                       self.queue, ^{
            MacWSMenuRawCompletion pending = self.pending[@(nonce)];
            if (!pending) return;
            [self.pending removeObjectForKey:@(nonce)];
            NSError *error = [self errorWithStatus:MacWSMenuStatusTimeout
                description:@"macOS 菜单响应超时"];
            dispatch_async(dispatch_get_main_queue(), ^{ pending(nil, error); });
        });
    });
}

- (MacWSMenuSnapshot *)parseSnapshot:(NSData *)data error:(NSError **)errorOut {
    const MacWSMenuResponseHeader *header = data.bytes;
    if (!MacWSMenuResponseIsValid(header, data.length)) {
        if (errorOut) *errorOut = [self errorWithStatus:
            MacWSMenuStatusInvalidRequest description:@"菜单快照格式无效"];
        return nil;
    }
    if (header->status != MacWSMenuStatusOK) {
        if (errorOut) *errorOut = [self errorWithStatus:header->status
            description:@"目标窗口暂时无法提供菜单"];
        return nil;
    }
    const MacWSMenuNode *nodes = (const void *)((const uint8_t *)header +
                                                sizeof(*header));
    const uint8_t *strings = (const uint8_t *)nodes +
        (size_t)header->nodeCount * sizeof(*nodes);
    NSMutableArray<MacWSMenuItem *> *items = [NSMutableArray array];
    NSMutableDictionary<NSNumber *, MacWSMenuItem *> *byID =
        [NSMutableDictionary dictionary];
    NSMutableDictionary<NSNumber *, NSMutableArray<MacWSMenuItem *> *> *children =
        [NSMutableDictionary dictionary];
    BOOL invalidTree = NO;
    for (uint32_t index = 0; index < header->nodeCount; index++) {
        MacWSMenuNode node;
        memcpy(&node, &nodes[index], sizeof(node));
        if (byID[@(node.itemID)]) {
            invalidTree = YES;
            break;
        }
        NSString *title = [[NSString alloc]
            initWithBytes:strings + node.titleOffset
                   length:node.titleLength encoding:NSUTF8StringEncoding];
        NSString *shortcut = [[NSString alloc]
            initWithBytes:strings + node.shortcutOffset
                   length:node.shortcutLength encoding:NSUTF8StringEncoding];
        if (!title || !shortcut) {
            invalidTree = YES;
            break;
        }
        MacWSMenuItem *item = [MacWSMenuItem new];
        item.itemID = node.itemID;
        item.parentItemID = node.parentItemID;
        item.siblingIndex = node.siblingIndex;
        item.flags = node.flags;
        item.state = node.state;
        item.title = title;
        item.shortcut = shortcut;
        [items addObject:item];
        byID[@(item.itemID)] = item;
        NSMutableArray *siblings = children[@(item.parentItemID)];
        if (!siblings) {
            siblings = [NSMutableArray array];
            children[@(item.parentItemID)] = siblings;
        }
        [siblings addObject:item];
    }
    for (MacWSMenuItem *item in invalidTree ? @[] : items) {
        uint64_t parent = item.parentItemID;
        NSUInteger depth = 0;
        while (parent != 0) {
            MacWSMenuItem *parentItem = byID[@(parent)];
            if (!parentItem || ++depth > MACWS_MENU_MAX_DEPTH) {
                invalidTree = YES;
                break;
            }
            parent = parentItem.parentItemID;
        }
        if (invalidTree) break;
    }
    if (invalidTree) {
        if (errorOut) *errorOut = [self errorWithStatus:
            MacWSMenuStatusInvalidRequest description:@"菜单快照树无效"];
        return nil;
    }
    for (NSMutableArray *siblings in children.allValues) {
        [siblings sortUsingComparator:^NSComparisonResult(
            MacWSMenuItem *lhs, MacWSMenuItem *rhs) {
            if (lhs.siblingIndex < rhs.siblingIndex) return NSOrderedAscending;
            if (lhs.siblingIndex > rhs.siblingIndex) return NSOrderedDescending;
            return NSOrderedSame;
        }];
    }
    MacWSMenuSnapshot *snapshot = [MacWSMenuSnapshot new];
    snapshot.ownerPID = header->ownerPID;
    snapshot.windowID = header->windowID;
    snapshot.generation = header->generation;
    snapshot.appearance = header->appearance;
    snapshot.items = items;
    snapshot.itemsByID = byID;
    snapshot.childrenByID = children;
    return snapshot;
}

- (void)requestSnapshotForPID:(int32_t)ownerPID
                     windowID:(uint32_t)windowID
                   completion:(MacWSMenuSnapshotCompletion)completion {
    uint64_t nonce = ++self.nextNonce;
    if (nonce == 0) nonce = ++self.nextNonce;
    MacWSMenuRequest request = {
        .magic = MACWS_MENU_MAGIC,
        .version = MACWS_MENU_VERSION,
        .size = sizeof(request),
        .operation = MacWSMenuOperationSnapshot,
        .nonce = nonce,
        .ownerPID = ownerPID,
        .windowID = windowID,
    };
    [self sendRequest:request operation:MACWS_MENU_XPC_OP_SNAPSHOT
        completion:^(NSData *data, NSError *transportError) {
            NSError *parseError = nil;
            MacWSMenuSnapshot *snapshot = data
                ? [self parseSnapshot:data error:&parseError] : nil;
            if (snapshot) {
                const MacWSMenuResponseHeader *header = data.bytes;
                if (header->nonce != nonce) {
                    snapshot = nil;
                    parseError = [self errorWithStatus:MacWSMenuStatusInvalidRequest
                        description:@"菜单快照不属于当前请求"];
                } else {
                    // The trusted display service already validates the
                    // same-UID parent chain for provider fallback. Preserve
                    // its provider identity; bind the represented identity
                    // to this request, not whatever scene is now frontmost.
                    snapshot.representedOwnerPID = ownerPID;
                    snapshot.representedWindowID = windowID;
                }
            }
            completion(snapshot, transportError ?: parseError);
        }];
}

- (void)performItem:(MacWSMenuItem *)item
         inSnapshot:(MacWSMenuSnapshot *)snapshot
         completion:(MacWSMenuActionCompletion)completion {
    uint64_t nonce = ++self.nextNonce;
    if (nonce == 0) nonce = ++self.nextNonce;
    MacWSMenuRequest request = {
        .magic = MACWS_MENU_MAGIC,
        .version = MACWS_MENU_VERSION,
        .size = sizeof(request),
        .operation = MacWSMenuOperationAction,
        .nonce = nonce,
        .ownerPID = snapshot.ownerPID,
        .windowID = snapshot.windowID,
        .generation = snapshot.generation,
        .itemID = item.itemID,
    };
    [self sendRequest:request operation:MACWS_MENU_XPC_OP_ACTION
        completion:^(NSData *data, NSError *transportError) {
            if (transportError) {
                completion(MacWSMenuStatusTargetUnavailable, transportError);
                return;
            }
            const MacWSMenuResponseHeader *header = data.bytes;
            if (!MacWSMenuResponseIsValid(header, data.length) ||
                header->nonce != nonce) {
                NSError *error = [self errorWithStatus:
                    MacWSMenuStatusInvalidRequest description:@"菜单动作回执无效"];
                completion(MacWSMenuStatusInvalidRequest, error);
                return;
            }
            NSError *error = header->status == MacWSMenuStatusOK ? nil :
                [self errorWithStatus:header->status
                    description:header->status == MacWSMenuStatusStaleGeneration
                        ? @"菜单已变化，请重新展开" : @"该菜单项当前不可执行"];
            completion(header->status, error);
        }];
}

- (void)invalidate {
    void (^invalidateOnQueue)(void) = ^{
        xpc_connection_t connection = self.connection;
        self.connection = nil;
        if (connection) xpc_connection_cancel(connection);
        [self failAllPending:@"菜单请求已取消"];
    };
    if (dispatch_get_specific(&MacWSMenuClientQueueKey)) {
        invalidateOnQueue();
    } else {
        dispatch_sync(self.queue, invalidateOnQueue);
    }
}

@end

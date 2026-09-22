#ifndef MACWS_MENU_PROTOCOL_H
#define MACWS_MENU_PROTOCOL_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define MACWS_MENU_MAGIC 0x4d4e574du /* "MWNM" */
#define MACWS_MENU_VERSION 2u

#define MACWS_MENU_XPC_OP_SNAPSHOT "menu_snapshot"
#define MACWS_MENU_XPC_OP_ACTION "menu_action"
#define MACWS_MENU_XPC_EVENT_RESPONSE "menu_response"
#define MACWS_MENU_XPC_KEY_REQUEST "menu_request"
#define MACWS_MENU_XPC_KEY_RESPONSE "menu_response"

#define MACWS_MENU_MAX_NODES 256u
#define MACWS_MENU_MAX_DEPTH 8u
#define MACWS_MENU_MAX_STRING_BYTES 20000u
#define MACWS_MENU_MAX_TOTAL_BYTES 32768u
#define MACWS_MENU_MAX_ITEM_STRING_BYTES 512u

typedef uint16_t MacWSMenuOperation;
enum {
    MacWSMenuOperationSnapshot = 1,
    MacWSMenuOperationAction = 2,
};

typedef uint16_t MacWSMenuStatus;
enum {
    MacWSMenuStatusOK = 1,
    MacWSMenuStatusInvalidRequest = 2,
    MacWSMenuStatusTargetUnavailable = 3,
    MacWSMenuStatusStaleGeneration = 4,
    MacWSMenuStatusDisabled = 5,
    MacWSMenuStatusUnsupported = 6,
    MacWSMenuStatusTimeout = 7,
    MacWSMenuStatusInternalError = 8,
};

typedef uint32_t MacWSMenuNodeFlags;
enum {
    MacWSMenuNodeSeparator = 1u << 0,
    MacWSMenuNodeEnabled = 1u << 1,
    MacWSMenuNodeHidden = 1u << 2,
    MacWSMenuNodeHasSubmenu = 1u << 3,
    MacWSMenuNodeChecked = 1u << 4,
    MacWSMenuNodeMixed = 1u << 5,
    MacWSMenuNodeAlternate = 1u << 6,
    // Custom NSMenuItem views and other semantics that cannot be represented
    // safely in UIKit remain visible but must route to the full workspace.
    MacWSMenuNodeRequiresWorkspace = 1u << 7,
    // This is the standard application-menu Command-Q item. A directly
    // exec'd Catalyst application can expose that item disabled because its
    // LaunchServices AppleEvent endpoint is absent. The Host may route only
    // that disabled semantic through PerformQuit; enabled items continue to
    // execute their real current NSMenuItem target/action.
    MacWSMenuNodeBridgedQuit = 1u << 8,
};

// The semantic iPadOS menu is rendered by Host, but its appearance belongs to
// the represented macOS window. Carry the resolved AppKit appearance in the
// snapshot instead of inheriting the unrelated iPadOS Scene appearance.
typedef uint32_t MacWSMenuAppearance;
enum {
    MacWSMenuAppearanceUnspecified = 0,
    MacWSMenuAppearanceLight = 1,
    MacWSMenuAppearanceDark = 2,
};

typedef struct __attribute__((packed)) {
    uint32_t magic;
    uint16_t version;
    uint16_t size;
    uint16_t operation;
    uint16_t reserved;
    uint64_t nonce;
    int32_t ownerPID;
    uint32_t windowID;
    uint64_t generation;
    uint64_t itemID;
} MacWSMenuRequest;

typedef struct __attribute__((packed)) {
    uint32_t magic;
    uint16_t version;
    uint16_t size;
    uint16_t status;
    uint16_t reserved;
    uint64_t nonce;
    int32_t ownerPID;
    uint32_t windowID;
    uint64_t generation;
    uint32_t appearance;
    uint32_t nodeCount;
    uint32_t stringBytes;
    uint32_t totalBytes;
} MacWSMenuResponseHeader;

typedef struct __attribute__((packed)) {
    uint64_t itemID;
    uint64_t parentItemID;
    uint32_t siblingIndex;
    uint32_t flags;
    int32_t state;
    uint32_t titleOffset;
    uint32_t titleLength;
    uint32_t shortcutOffset;
    uint32_t shortcutLength;
} MacWSMenuNode;

static inline bool MacWSMenuRequestIsValid(const MacWSMenuRequest *request,
                                            size_t byteCount) {
    if (!request || byteCount != sizeof(*request) ||
        request->magic != MACWS_MENU_MAGIC ||
        request->version != MACWS_MENU_VERSION ||
        request->size != sizeof(*request) || request->reserved != 0 ||
        request->nonce == 0 || request->ownerPID <= 1 ||
        request->windowID == 0) return false;
    if (request->operation == MacWSMenuOperationSnapshot)
        return request->generation == 0 && request->itemID == 0;
    if (request->operation == MacWSMenuOperationAction)
        return request->generation != 0 && request->itemID != 0;
    return false;
}

static inline bool MacWSMenuResponseIsValid(
    const MacWSMenuResponseHeader *header, size_t byteCount) {
    if (!header || byteCount < sizeof(*header) ||
        header->magic != MACWS_MENU_MAGIC ||
        header->version != MACWS_MENU_VERSION ||
        header->size != sizeof(*header) || header->reserved != 0 ||
        header->nonce == 0 || header->ownerPID <= 1 ||
        header->windowID == 0 || header->generation == 0 ||
        header->appearance > MacWSMenuAppearanceDark ||
        header->status < MacWSMenuStatusOK ||
        header->status > MacWSMenuStatusInternalError ||
        header->nodeCount > MACWS_MENU_MAX_NODES ||
        header->stringBytes > MACWS_MENU_MAX_STRING_BYTES ||
        header->totalBytes != byteCount ||
        header->totalBytes > MACWS_MENU_MAX_TOTAL_BYTES) return false;
    size_t nodeBytes = (size_t)header->nodeCount * sizeof(MacWSMenuNode);
    if (nodeBytes > SIZE_MAX - sizeof(*header) ||
        header->stringBytes > SIZE_MAX - sizeof(*header) - nodeBytes ||
        sizeof(*header) + nodeBytes + header->stringBytes != byteCount)
        return false;
    const MacWSMenuNode *nodes = (const void *)((const uint8_t *)header +
                                                sizeof(*header));
    for (uint32_t index = 0; index < header->nodeCount; index++) {
        const MacWSMenuNode *node = &nodes[index];
        if (node->itemID == 0 || node->itemID > header->nodeCount ||
            node->parentItemID > header->nodeCount ||
            node->titleOffset > header->stringBytes ||
            node->titleLength >
                header->stringBytes - node->titleOffset ||
            node->shortcutOffset > header->stringBytes ||
            node->shortcutLength >
                header->stringBytes - node->shortcutOffset)
            return false;
    }
    return true;
}

#if defined(__cplusplus)
static_assert(sizeof(MacWSMenuRequest) == 44, "MacWS menu request ABI");
static_assert(sizeof(MacWSMenuResponseHeader) == 52,
              "MacWS menu response ABI");
static_assert(sizeof(MacWSMenuNode) == 44, "MacWS menu node ABI");
#else
_Static_assert(sizeof(MacWSMenuRequest) == 44, "MacWS menu request ABI");
_Static_assert(sizeof(MacWSMenuResponseHeader) == 52,
               "MacWS menu response ABI");
_Static_assert(sizeof(MacWSMenuNode) == 44, "MacWS menu node ABI");
#endif

#endif

#ifndef MACWS_INTEROP_PROTOCOL_H
#define MACWS_INTEROP_PROTOCOL_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define MACWS_INTEROP_SERVICE "com.macwsguide.interop"
#define MACWS_LOCATION_REFRESH_NOTIFICATION "com.macwsguide.location.refresh-client"
#define MACWS_INTEROP_MAGIC 0x4d57494fu /* "MWIO" */
#define MACWS_INTEROP_VERSION 2u

#define MACWS_INTEROP_MAX_INLINE_BYTES (64u * 1024u * 1024u)
#define MACWS_INTEROP_MAX_ITEMS 32u
#define MACWS_INTEROP_MAX_REPRESENTATIONS 128u
#define MACWS_INTEROP_MAX_TYPE_BYTES 256u
#define MACWS_INTEROP_MAX_PATH_BYTES 4096u

#define MACWS_INTEROP_KEY_OP "op"
#define MACWS_INTEROP_KEY_EVENT "event"
#define MACWS_INTEROP_KEY_PROTOCOL_VERSION "protocol_version"
#define MACWS_INTEROP_KEY_DESCRIPTOR "descriptor"
#define MACWS_INTEROP_KEY_PAYLOAD "payload"
#define MACWS_INTEROP_KEY_TYPE "type"
#define MACWS_INTEROP_KEY_NAME "name"
#define MACWS_INTEROP_KEY_STAGED_PATH "staged_path"
#define MACWS_INTEROP_KEY_ITEMS "items"
#define MACWS_INTEROP_KEY_OK "ok"
#define MACWS_INTEROP_KEY_MESSAGE "message"
#define MACWS_INTEROP_KEY_LATITUDE "latitude"
#define MACWS_INTEROP_KEY_LONGITUDE "longitude"
#define MACWS_INTEROP_KEY_ALTITUDE "altitude"
#define MACWS_INTEROP_KEY_HORIZONTAL_ACCURACY "horizontal_accuracy"
#define MACWS_INTEROP_KEY_VERTICAL_ACCURACY "vertical_accuracy"
#define MACWS_INTEROP_KEY_COURSE "course"
#define MACWS_INTEROP_KEY_SPEED "speed"
#define MACWS_INTEROP_KEY_TIMESTAMP "timestamp"
#define MACWS_INTEROP_KEY_LOCATION_TYPE "location_type"
#define MACWS_INTEROP_KEY_REFERENCE_FRAME "reference_frame"
#define MACWS_INTEROP_KEY_RAW_REFERENCE_FRAME "raw_reference_frame"
#define MACWS_INTEROP_KEY_CHANGE_COUNT "change_count"
#define MACWS_INTEROP_KEY_AFTER_CHANGE_COUNT "after_change_count"
#define MACWS_INTEROP_KEY_WAIT_MILLISECONDS "wait_milliseconds"

#define MACWS_INTEROP_OP_HELLO "hello"
#define MACWS_INTEROP_OP_SUBSCRIBE "subscribe"
#define MACWS_INTEROP_OP_PUBLISH_CLIPBOARD "publish_clipboard"
#define MACWS_INTEROP_OP_IMPORT_FILES "import_files"
#define MACWS_INTEROP_OP_EXPORT_FILES "export_files"
#define MACWS_INTEROP_OP_PUBLISH_LOCATION "publish_location"
#define MACWS_INTEROP_OP_PUBLISH_PASTEBOARD "publish_pasteboard"
#define MACWS_INTEROP_OP_SNAPSHOT_DRAG_PASTEBOARD "snapshot_drag_pasteboard"

#define MACWS_INTEROP_EVENT_CLIPBOARD "clipboard"
#define MACWS_INTEROP_EVENT_FILES_READY "files_ready"
#define MACWS_INTEROP_EVENT_READY "ready"
#define MACWS_INTEROP_EVENT_ERROR "error"
#define MACWS_INTEROP_EVENT_PASTEBOARD "pasteboard"

typedef uint16_t MacWSInteropKind;
enum {
    MacWSInteropKindUTF8Text = 1,
    MacWSInteropKindPNG = 2,
    MacWSInteropKindJPEG = 3,
    MacWSInteropKindFile = 4,
    // A binary property-list archive containing ordered pasteboard items and
    // every bounded representation declared by each item. File URLs remain
    // staged paths in the archive instead of copying their contents inline.
    MacWSInteropKindPasteboardArchive = 5,
};

typedef uint16_t MacWSInteropFlags;
enum {
    MacWSInteropInlinePayload = 1u << 0,
    MacWSInteropStagedFile = 1u << 1,
    MacWSInteropFromIOS = 1u << 2,
    MacWSInteropFromMacOS = 1u << 3,
};

// A monotonically increasing generation plus originID prevents an iOS paste
// from bouncing back from macOS as a new paste.  digest is the first 128 bits
// of SHA-256 and is an additional content witness, not an authentication tag.
typedef struct __attribute__((packed)) {
    uint32_t magic;
    uint16_t version;
    uint16_t size;
    uint16_t kind;
    uint16_t flags;
    uint32_t itemIndex;
    uint64_t generation;
    uint64_t originID;
    uint64_t payloadLength;
    uint8_t digest[16];
} MacWSInteropItemDescriptor;

static inline bool MacWSInteropItemDescriptorIsValid(
    const MacWSInteropItemDescriptor *descriptor, size_t byteCount) {
    if (!descriptor || byteCount != sizeof(*descriptor) ||
        descriptor->magic != MACWS_INTEROP_MAGIC ||
        descriptor->version != MACWS_INTEROP_VERSION ||
        descriptor->size != sizeof(*descriptor) ||
        descriptor->kind < MacWSInteropKindUTF8Text ||
        descriptor->kind > MacWSInteropKindPasteboardArchive ||
        descriptor->itemIndex >= MACWS_INTEROP_MAX_ITEMS ||
        descriptor->generation == 0 || descriptor->originID == 0) {
        return false;
    }
    bool inlinePayload = (descriptor->flags & MacWSInteropInlinePayload) != 0;
    bool stagedFile = (descriptor->flags & MacWSInteropStagedFile) != 0;
    if (inlinePayload == stagedFile) return false;
    return !inlinePayload || descriptor->payloadLength <= MACWS_INTEROP_MAX_INLINE_BYTES;
}

#if defined(__cplusplus)
static_assert(sizeof(MacWSInteropItemDescriptor) == 56,
              "MacWS interop descriptor ABI");
#else
_Static_assert(sizeof(MacWSInteropItemDescriptor) == 56,
               "MacWS interop descriptor ABI");
#endif

#endif

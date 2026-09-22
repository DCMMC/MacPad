// Send one iOS-native HID key press through the HID event system.
//
// With no argument (or "volume-up" / "home"), this remains the original
// diagnostic/recovery helper that wakes the display without bypassing the
// lock screen or device authentication.  The w/a/s/d/return arguments use
// the Keyboard/Keypad usage page so MacWSHost's real UIKit presses callbacks
// can be checked end-to-end without short-circuiting macwsinputd.

#include <CoreFoundation/CoreFoundation.h>
#include <dlfcn.h>
#include <mach/mach_time.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

typedef CFTypeRef (*client_create_fn)(CFAllocatorRef allocator);
typedef CFTypeRef (*keyboard_event_fn)(CFAllocatorRef allocator,
                                       uint64_t timestamp,
                                       uint16_t usage_page,
                                       uint16_t usage,
                                       Boolean down,
                                       uint32_t flags);
typedef void (*dispatch_event_fn)(CFTypeRef client, CFTypeRef event);
typedef void (*set_sender_fn)(CFTypeRef event, uint64_t sender_id);

int main(int argc, char **argv) {
    const uint16_t usage_page_consumer = 0x0c;
    const uint16_t usage_page_keyboard = 0x07;
    const char *key = argc > 1 ? argv[1] : "volume-up";
    uint16_t usage_page = usage_page_consumer;
    uint16_t usage = 0x30;
    if (strcmp(key, "home") == 0) {
        usage = 0x40;
    } else if (strcmp(key, "volume-up") == 0) {
        usage = 0x30;
    } else {
        usage_page = usage_page_keyboard;
        if (strcmp(key, "w") == 0) usage = 0x1a;
        else if (strcmp(key, "a") == 0) usage = 0x04;
        else if (strcmp(key, "s") == 0) usage = 0x16;
        else if (strcmp(key, "d") == 0) usage = 0x07;
        else if (strcmp(key, "return") == 0) usage = 0x28;
        else {
            fprintf(stderr,
                    "usage: %s [volume-up|home|w|a|s|d|return]\n",
                    argv[0]);
            return 64;
        }
    }
    void *iokit = dlopen(
        "/System/Library/Frameworks/IOKit.framework/IOKit",
        RTLD_NOW | RTLD_LOCAL);
    if (!iokit) {
        fprintf(stderr, "IOKit dlopen failed: %s\n", dlerror());
        return 1;
    }
    client_create_fn create_client = (client_create_fn)dlsym(
        iokit, "IOHIDEventSystemClientCreateSimpleClient");
    keyboard_event_fn create_keyboard = (keyboard_event_fn)dlsym(
        iokit, "IOHIDEventCreateKeyboardEvent");
    dispatch_event_fn dispatch = (dispatch_event_fn)dlsym(
        iokit, "IOHIDEventSystemClientDispatchEvent");
    set_sender_fn set_sender = (set_sender_fn)dlsym(
        iokit, "IOHIDEventSetSenderID");
    if (!create_client || !create_keyboard || !dispatch) {
        fprintf(stderr, "missing HID symbol client=%p keyboard=%p dispatch=%p\n",
                create_client, create_keyboard, dispatch);
        return 2;
    }
    CFTypeRef client = create_client(kCFAllocatorDefault);
    if (!client) {
        fprintf(stderr, "IOHIDEventSystemClientCreateSimpleClient returned NULL\n");
        return 3;
    }
    for (int down = 1; down >= 0; --down) {
        CFTypeRef event = create_keyboard(kCFAllocatorDefault,
                                          mach_absolute_time(),
                                          usage_page, usage,
                                          down, 0);
        if (!event) {
            fprintf(stderr, "keyboard event creation failed down=%d\n", down);
            CFRelease(client);
            return 4;
        }
        if (set_sender) set_sender(event, 0x8000000817319372ULL);
        dispatch(client, event);
        CFRelease(event);
        usleep(80000);
    }
    printf("dispatched key=%s page=%#x usage=%#x down+up\n",
           key, usage_page, usage);
    CFRelease(client);
    return 0;
}

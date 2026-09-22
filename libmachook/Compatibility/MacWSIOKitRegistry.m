@import CoreFoundation;
@import Darwin;

#import "interpose.h"

#include <IOKit/IOKitLib.h>

// macOS GPU clients use the desktop IOKit category name while the same Apple
// Silicon AGX service is published as IOAcceleratorES by iPadOS.  Matching the
// service name is already translated in mac_hooks.m; registry walkers such as
// UE4's FMacPlatformGPUManager independently inspect IOMatchCategory and need
// the equivalent value translation as well.
//
// Runtime-confirmed on iPad13,6: the sgx AppleARMIODevice child is the real
// AGXAcceleratorG13G_B0 and reports IOMatchCategory=IOAcceleratorES.  RE-
// confirmed in Stray UUID C72D3F73-25F4-333B-9108-83432E09E687 at
// main+0x1366d88..0x1366dc8: its macOS GPU manager accepts only the literal
// IOAccelerator before copying the service's real vendor/plugin/model fields.
static CFTypeRef MacWSIORegistryEntrySearchCFProperty(
        io_registry_entry_t entry, const io_name_t plane, CFStringRef key,
        CFAllocatorRef allocator, IOOptionBits options) {
    CFTypeRef value = IORegistryEntrySearchCFProperty(
        entry, plane, key, allocator, options);
    if (!value || !key || CFGetTypeID(key) != CFStringGetTypeID() ||
        CFGetTypeID(value) != CFStringGetTypeID() ||
        !CFEqual(key, CFSTR("IOMatchCategory")) ||
        !CFEqual(value, CFSTR("IOAcceleratorES"))) {
        return value;
    }

    CFRelease(value);
    CFStringRef translated = CFSTR("IOAccelerator");
    CFRetain(translated); // Preserve IORegistryEntrySearchCFProperty ownership.
    if (getenv("MACWS_IOKIT_COMPAT_DIAGNOSTICS")) {
        fprintf(stderr,
                "[MacWSIOKitRegistry] entry=%u translated "
                "IOMatchCategory IOAcceleratorES -> IOAccelerator\n",
                entry);
        fflush(stderr);
    }
    return translated;
}

DYLD_INTERPOSE(MacWSIORegistryEntrySearchCFProperty,
               IORegistryEntrySearchCFProperty)

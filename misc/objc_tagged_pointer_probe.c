#include <dlfcn.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>

/*
 * Print the Objective-C tagged-pointer decoder globals from the current iOS
 * process.  The shared-cache addresses can then be inspected in another
 * process with LLDB; values such as the obfuscator itself remain per-process.
 */
int main(void) {
    static const char *const names[] = {
        "objc_debug_taggedpointer_mask",
        "objc_debug_taggedpointer_obfuscator",
        "objc_debug_taggedpointer_slot_shift",
        "objc_debug_taggedpointer_slot_mask",
        "objc_debug_taggedpointer_payload_lshift",
        "objc_debug_taggedpointer_payload_rshift",
        "objc_debug_taggedpointer_ext_mask",
        "objc_debug_taggedpointer_ext_slot_shift",
        "objc_debug_taggedpointer_ext_slot_mask",
        "objc_debug_taggedpointer_ext_payload_lshift",
        "objc_debug_taggedpointer_ext_payload_rshift",
        "objc_debug_taggedpointer_classes",
        "objc_debug_taggedpointer_ext_classes",
        "objc_debug_taggedpointer_permutations",
    };

    for (size_t i = 0; i < sizeof(names) / sizeof(names[0]); i++) {
        void *address = dlsym(RTLD_DEFAULT, names[i]);
        if (!address) {
            printf("%s address=(nil) error=%s\n", names[i], dlerror());
            continue;
        }
        uintptr_t value = *(const volatile uintptr_t *)address;
        printf("%s address=%p value=%#" PRIxPTR "\n",
               names[i], address, value);
    }

    uintptr_t *obfuscator_address = (uintptr_t *)dlsym(
        RTLD_DEFAULT, "objc_debug_taggedpointer_obfuscator");
    void *(*get_class)(const char *) = dlsym(RTLD_DEFAULT, "objc_getClass");
    void *(*register_selector)(const char *) =
        dlsym(RTLD_DEFAULT, "sel_registerName");
    void *message_send = dlsym(RTLD_DEFAULT, "objc_msgSend");
    if (!obfuscator_address || !get_class || !register_selector ||
        !message_send) {
        return 1;
    }

    void *number_class = get_class("NSNumber");
    void *double_selector = register_selector("numberWithDouble:");
    void *integer_selector = register_selector("numberWithLongLong:");
    void *(*send_double)(void *, void *, double) = message_send;
    void *(*send_integer)(void *, void *, long long) = message_send;
    static const double doubles[] = {
        0.0, 1.0, -1.0, 1.0 / 120.0, 1.0 / 60.0,
        0.008333, 0.016667, 1234.5, 123456.789,
    };
    static const long long integers[] = {0, 1, -1, 8, 60, 120, 1000000};
    uintptr_t obfuscator = *obfuscator_address;
    for (size_t i = 0; i < sizeof(doubles) / sizeof(doubles[0]); i++) {
        uintptr_t pointer = (uintptr_t)send_double(
            number_class, double_selector, doubles[i]);
        printf("double %.17g pointer=%#" PRIxPTR " decoded=%#" PRIxPTR "\n",
               doubles[i], pointer, pointer ^ obfuscator);
    }
    for (size_t i = 0; i < sizeof(integers) / sizeof(integers[0]); i++) {
        uintptr_t pointer = (uintptr_t)send_integer(
            number_class, integer_selector, integers[i]);
        printf("integer %lld pointer=%#" PRIxPTR " decoded=%#" PRIxPTR "\n",
               integers[i], pointer, pointer ^ obfuscator);
    }
    return 0;
}

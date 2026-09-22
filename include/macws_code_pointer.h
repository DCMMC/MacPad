#ifndef MACWS_CODE_POINTER_H
#define MACWS_CODE_POINTER_H
#include <stdint.h>
#include <ptrauth.h>

// Code symbols can be four-byte aligned. An integer PAC mask can be folded
// with clang's assumed function-pointer alignment, rounding a valid ...4
// entry down to the previous instruction. Use architectural stripping, not
// a guessed VA width. Only for code pointers, never object/data pointers.
static inline uintptr_t MacWSCodeAddress(const void *pointer) {
    return (uintptr_t)ptrauth_strip(pointer, ptrauth_key_function_pointer);
}
#endif

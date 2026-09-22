// iomfb_userspace_dump.c — read-only iOS-native IOMobileFramebuffer ABI dump.
//
// This avoids attaching LLDB to backboardd (which makes LLDB download the
// device shared cache while the display daemon is stopped).  The helper loads
// the already-trusted iOS framework in its own short-lived process, resolves
// the four public swap entry points, decodes their inline `ldr xN,[x0,#imm]`
// dispatch slot, and dumps the selected kern_* implementation bytes.  It never
// calls SwapBegin/End/Wait/Cancel and never mutates the framebuffer object.
//
// On-device build/run:
//   clang -arch arm64 -framework IOKit misc/iomfb_userspace_dump.c \
//       -o /tmp/iomfb_userspace_dump
//   ldid -S /tmp/iomfb_userspace_dump
//   jbctl trustcache add <CDHash>
//   /tmp/iomfb_userspace_dump

#include <dlfcn.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef struct __IOMobileFramebuffer *IOMobileFramebufferRef;
typedef int32_t (*get_main_display_fn)(IOMobileFramebufferRef *);

static uintptr_t strip_code_pointer(uintptr_t value) {
#if __has_feature(ptrauth_calls)
    return (uintptr_t)__builtin_ptrauth_strip((void *)value, 0);
#else
    return value;
#endif
}

static void dump_hex(const char *kind, const char *name, uintptr_t address,
                     size_t size) {
    const unsigned char *bytes = (const unsigned char *)address;
    printf("HEX %s %s addr=%#" PRIxPTR " size=%zu ",
           kind, name, address, size);
    for (size_t i = 0; i < size; i++) printf("%02x", bytes[i]);
    putchar('\n');
}

// Decode an AArch64 64-bit unsigned-offset LDR whose base register is x0.
// The public IOMFB dispatch thunks have the form:
//   cbz x0, failure; ldr xN, [x0, #slot]; cbz xN, failure; braaz xN
static int dispatch_slot(const uint32_t *code, size_t instruction_count,
                         uint32_t *slot_out) {
    for (size_t i = 0; i < instruction_count; i++) {
        uint32_t insn = code[i];
        if ((insn & 0xffc00000u) == 0xf9400000u &&
            ((insn >> 5) & 0x1fu) == 0) {
            *slot_out = ((insn >> 10) & 0xfffu) * 8u;
            return 0;
        }
    }
    return -1;
}

static void dump_entry(void *image_base, IOMobileFramebufferRef fb,
                       void *handle, const char *name) {
    void *symbol = dlsym(handle, name);
    if (!symbol) {
        printf("SYMBOL %s MISSING error=%s\n", name, dlerror());
        return;
    }
    uintptr_t public_address = strip_code_pointer((uintptr_t)symbol);
    Dl_info public_info = {0};
    dladdr((void *)public_address, &public_info);
    printf("SYMBOL %s public=%#" PRIxPTR " image=%s image_base=%p offset=%#" PRIxPTR "\n",
           name, public_address,
           public_info.dli_fname ? public_info.dli_fname : "?",
           public_info.dli_fbase,
           public_info.dli_fbase
               ? public_address - (uintptr_t)public_info.dli_fbase : 0);
    // Complex wrappers such as FrameInfo do validation before reaching their
    // object dispatch, so keep enough bytes to compare the full wrapper too.
    dump_hex("public", name, public_address, 512);

    uint32_t slot = 0;
    if (!fb || dispatch_slot((const uint32_t *)public_address, 16, &slot) != 0) {
        printf("DISPATCH %s slot=UNRESOLVED fb=%p\n", name, (void *)fb);
        return;
    }
    uintptr_t raw_target = 0;
    memcpy(&raw_target, (const char *)fb + slot, sizeof(raw_target));
    uintptr_t target = strip_code_pointer(raw_target);
    Dl_info target_info = {0};
    int target_known = dladdr((void *)target, &target_info);
    printf("DISPATCH %s fb=%p slot=%#x raw=%#" PRIxPTR
           " target=%#" PRIxPTR " image=%s image_base=%p offset=%#" PRIxPTR "\n",
           name, (void *)fb, slot, raw_target, target,
           target_known && target_info.dli_fname ? target_info.dli_fname : "?",
           target_known ? target_info.dli_fbase : NULL,
           target_known && target_info.dli_fbase
               ? target - (uintptr_t)target_info.dli_fbase : 0);
    if (target_known && target_info.dli_fbase == image_base)
        dump_hex("target", name, target, 512);
}

int main(void) {
    const char *path =
        "/System/Library/PrivateFrameworks/IOMobileFramebuffer.framework/"
        "IOMobileFramebuffer";
    void *handle = dlopen(path, RTLD_NOW | RTLD_LOCAL);
    if (!handle) {
        fprintf(stderr, "dlopen(%s): %s\n", path, dlerror());
        return 1;
    }
    get_main_display_fn get_main =
        (get_main_display_fn)dlsym(handle, "IOMobileFramebufferGetMainDisplay");
    if (!get_main) {
        fprintf(stderr, "dlsym(GetMainDisplay): %s\n", dlerror());
        return 2;
    }
    Dl_info image_info = {0};
    dladdr((void *)get_main, &image_info);
    IOMobileFramebufferRef fb = NULL;
    int32_t status = get_main(&fb);
    printf("IMAGE path=%s base=%p getMain=%p status=%#x fb=%p\n",
           image_info.dli_fname ? image_info.dli_fname : "?",
           image_info.dli_fbase, (void *)get_main, (uint32_t)status, (void *)fb);
    if (status != 0 || !fb) return 3;

    const char *names[] = {
        "IOMobileFramebufferSwapBegin",
        "IOMobileFramebufferSwapEnd",
        "IOMobileFramebufferSwapWait",
        "IOMobileFramebufferSwapCancel",
        "IOMobileFramebufferSupportedFrameInfo",
        "IOMobileFramebufferGetRunLoopSource",
        "IOMobileFramebufferFrameInfo",
        "IOMobileFramebufferChangeFrameInfo",
    };
    for (size_t i = 0; i < sizeof(names) / sizeof(names[0]); i++)
        dump_entry(image_info.dli_fbase, fb, handle, names[i]);
    return 0;
}

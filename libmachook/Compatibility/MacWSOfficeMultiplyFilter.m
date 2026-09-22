// Word's selection overlay uses a Core Image multiply filter as a CALayer
// compositingFilter.  The mixed macOS/iPadOS compositor accepts that object
// but renders its blue highlight as an opaque gray block.  Preserve the
// multiply operation through Core Animation's native filter representation.
//
// Runtime witness: MicrosoftWord.native-guarded.log, WORD-LAYER ancestor-1
// (ASInternalLayer / CIMultiplyBlendMode) and diagnostic-early-multiply.
// VNC witnesses: macws_word_probe_selected_after.png (gray) and
// macws_word_probe11_selected.png (blue with legible text).  The latter used
// this exact substitution at setCompositingFilter: time, with no color or
// timing changes.  Scope to Word and its exact layer/filter classes.

#import <Foundation/Foundation.h>
#import <objc/message.h>
#import <objc/runtime.h>
#include <string.h>

extern const char *getprogname(void);

static void (*macws_office_original_set_compositing_filter)(id, SEL, id);

static void macws_office_set_compositing_filter(id layer, SEL selector,
                                               id filter) {
    if (filter &&
        strcmp(class_getName(object_getClass(layer)), "ASInternalLayer") == 0 &&
        strcmp(class_getName(object_getClass(filter)),
               "CIMultiplyBlendMode") == 0) {
        Class ca_filter = objc_getClass("CAFilter");
        SEL factory = sel_registerName("filterWithType:");
        if (ca_filter && [ca_filter respondsToSelector:factory]) {
            id native_multiply = ((id (*)(id, SEL, id))objc_msgSend)(
                ca_filter, factory, @"multiplyBlendMode");
            if (native_multiply) filter = native_multiply;
        }
    }
    macws_office_original_set_compositing_filter(layer, selector, filter);
}

void MacWSInstallOfficeMultiplyFilterCompatibility(void) {
    const char *program = getprogname();
    if (!program || strcmp(program, "Microsoft Word") != 0) return;
    Class layer = objc_getClass("CALayer");
    SEL setter = sel_registerName("setCompositingFilter:");
    Method method = layer ? class_getInstanceMethod(layer, setter) : NULL;
    if (!method) return;
    macws_office_original_set_compositing_filter =
        (void (*)(id, SEL, id))method_setImplementation(
            method, (IMP)macws_office_set_compositing_filter);
}

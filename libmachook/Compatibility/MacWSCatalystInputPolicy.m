#import "MacWSCatalystInputPolicy.h"

#import <objc/message.h>
#import <objc/runtime.h>

typedef id (*MacWSCatalystMsgID)(id, SEL);
typedef CGPoint (*MacWSCatalystMsgPointPointID)(id, SEL, CGPoint, id);
typedef CGRect (*MacWSCatalystMsgRect)(id, SEL);

static BOOL MacWSObjectHasCatalystWindowClass(id object) {
    for (Class candidate = object_getClass(object); candidate;
         candidate = class_getSuperclass(candidate)) {
        const char *name = class_getName(candidate);
        // Runtime-confirmed on the target Ventura Catalyst build: Asphalt's
        // real window class is NSKVONotifying_UINSWindow.  Walk the hierarchy
        // instead of matching that generated KVO subclass or a bundle ID.
        if (name && strstr(name, "UINSWindow")) return YES;
    }
    return NO;
}

BOOL MacWSCatalystWindowUsesProcessLocalInputAtPoint(id window,
                                                      CGPoint windowPoint) {
    if (!window || !MacWSObjectHasCatalystWindowClass(window)) return NO;

    id contentView = ((MacWSCatalystMsgID)objc_msgSend)(
        window, sel_registerName("contentView"));
    if (!contentView) return NO;
    CGPoint contentPoint = ((MacWSCatalystMsgPointPointID)objc_msgSend)(
        contentView, sel_registerName("convertPoint:fromView:"),
        windowPoint, nil);
    CGRect bounds = ((MacWSCatalystMsgRect)objc_msgSend)(
        contentView, sel_registerName("bounds"));
    return CGRectContainsPoint(bounds, contentPoint);
}

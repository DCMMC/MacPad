#import <Foundation/Foundation.h>
#import <UIKit/UIKit.h>
#import <objc/message.h>
#import <objc/runtime.h>

static void PrintClassMetadata(Class cls) {
    if (!cls) return;
    printf("CLASS %s superclass=%s size=%zu\n", class_getName(cls),
           class_getSuperclass(cls) ? class_getName(class_getSuperclass(cls))
                                    : "(nil)",
           class_getInstanceSize(cls));
    unsigned count = 0;
    Method *methods = class_copyMethodList(cls, &count);
    for (unsigned index = 0; index < count; index++) {
        printf("METHOD - %s %s\n",
               sel_getName(method_getName(methods[index])),
               method_getTypeEncoding(methods[index]));
    }
    free(methods);
    methods = class_copyMethodList(object_getClass(cls), &count);
    for (unsigned index = 0; index < count; index++) {
        printf("METHOD + %s %s\n",
               sel_getName(method_getName(methods[index])),
               method_getTypeEncoding(methods[index]));
    }
    free(methods);
    objc_property_t *properties = class_copyPropertyList(cls, &count);
    for (unsigned index = 0; index < count; index++) {
        printf("PROPERTY %s %s\n", property_getName(properties[index]),
               property_getAttributes(properties[index]));
    }
    free(properties);
    Ivar *ivars = class_copyIvarList(cls, &count);
    for (unsigned index = 0; index < count; index++) {
        printf("IVAR %s %s offset=%td\n", ivar_getName(ivars[index]),
               ivar_getTypeEncoding(ivars[index]),
               ivar_getOffset(ivars[index]));
    }
    free(ivars);
}

int main(void) {
    @autoreleasepool {
        id options = [objc_getClass("UIWindowSceneActivationRequestOptions")
            new];
        [options setPreserveLayout:YES];
        BOOL publicPreserve = [options preserveLayout];
        BOOL windowPreserve = [options respondsToSelector:
            NSSelectorFromString(@"_preserveLayout")]
            ? ((BOOL (*)(id, SEL))objc_msgSend)(
                  options, NSSelectorFromString(@"_preserveLayout"))
            : NO;
        printf("ACTIVATION_OPTIONS after-public-set public=%s window=%s\n",
               publicPreserve ? "YES" : "NO",
               windowPreserve ? "YES" : "NO");
        if ([options respondsToSelector:
                NSSelectorFromString(@"_setPreserveLayout:")]) {
            ((void (*)(id, SEL, BOOL))objc_msgSend)(
                options, NSSelectorFromString(@"_setPreserveLayout:"), YES);
        }
        printf("ACTIVATION_OPTIONS after-window-set public=%s window=%s\n",
               [options preserveLayout] ? "YES" : "NO",
               [options respondsToSelector:
                    NSSelectorFromString(@"_preserveLayout")] &&
                   ((BOOL (*)(id, SEL))objc_msgSend)(
                       options, NSSelectorFromString(@"_preserveLayout"))
                   ? "YES" : "NO");
        PrintClassMetadata(objc_getClass("UISceneActivationRequestOptions"));
        int count = objc_getClassList(NULL, 0);
        Class *classes = calloc((size_t)MAX(count, 0), sizeof(Class));
        count = objc_getClassList(classes, count);
        for (int index = 0; index < count; index++) {
            const char *name = class_getName(classes[index]);
            if (!name) continue;
            if (strstr(name, "SceneActivation") ||
                strstr(name, "ScenePlacement") ||
                strstr(name, "SceneRequestOptions")) {
                PrintClassMetadata(classes[index]);
            }
        }
        free(classes);
    }
    return 0;
}

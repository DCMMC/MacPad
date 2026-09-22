#import <AppKit/AppKit.h>
#import <CoreLocation/CoreLocation.h>
#import <Foundation/Foundation.h>
#import <objc/runtime.h>

// Runtime catalogue for the private CLLocationManager construction surface.
// This is intentionally a standalone diagnostic: it prints selectors and
// type encodings from the exact CoreLocation image loaded by the chroot, so
// fixes do not rely on SDK declarations from a different OS release.
static void PrintMethods(Class cls, const char *prefix) {
    unsigned int count = 0;
    Method *methods = class_copyMethodList(cls, &count);
    printf("%s count=%u\n", prefix, count);
    for (unsigned int index = 0; index < count; index++) {
        SEL selector = method_getName(methods[index]);
        const char *name = sel_getName(selector);
        if (!name) continue;
        if (strstr(name, "init") || strstr(name, "Bundle") ||
            strstr(name, "bundle") || strstr(name, "Silo") ||
            strstr(name, "silo") || strstr(name, "Manager") ||
            strstr(name, "manager")) {
            printf("%s %s :: %s\n", prefix, name,
                   method_getTypeEncoding(methods[index]));
        }
    }
    free(methods);
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        Class managerClass = objc_getClass("CLLocationManager");
        if (!managerClass) {
            fprintf(stderr, "CLLocationManager unavailable\n");
            return 2;
        }
        PrintMethods(managerClass, "INSTANCE");
        PrintMethods(object_getClass(managerClass), "CLASS");

        // Optional lifecycle witness.  Keeping this separate from the method
        // catalogue lets a short run show which identity CoreLocation assigns
        // before and after AppKit creates NSApplication.
        if (argc > 1 && strcmp(argv[1], "construct") == 0) {
            fprintf(stderr, "PROBE bundle=%s path=%s\n",
                    NSBundle.mainBundle.bundleIdentifier.UTF8String ?: "(nil)",
                    NSBundle.mainBundle.bundlePath.UTF8String ?: "(nil)");
            CLLocationManager *beforeAppKit = [CLLocationManager new];
            fprintf(stderr, "PROBE before-appkit manager=%p\n", beforeAppKit);
            (void)[NSApplication sharedApplication];
            CLLocationManager *afterAppKit = [CLLocationManager new];
            fprintf(stderr, "PROBE after-appkit manager=%p\n", afterAppKit);
        }
    }
    return 0;
}

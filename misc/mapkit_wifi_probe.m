#import <Foundation/Foundation.h>
#import <objc/message.h>
#import <objc/runtime.h>
#import <dlfcn.h>

static void printMethod(Class cls, SEL selector, BOOL classMethod) {
    Method method = classMethod ? class_getClassMethod(cls, selector)
                                : class_getInstanceMethod(cls, selector);
    if (method == NULL) {
        printf("method %c[%s %s] missing\n",
               classMethod ? '+' : '-', class_getName(cls), sel_getName(selector));
        return;
    }

    IMP implementation = method_getImplementation(method);
    Dl_info info = {0};
    if (dladdr((const void *)implementation, &info) != 0 && info.dli_fbase != NULL) {
        uintptr_t offset = (uintptr_t)implementation - (uintptr_t)info.dli_fbase;
        printf("method %c[%s %s] imp=%p image=%s offset=0x%lx\n",
               classMethod ? '+' : '-', class_getName(cls), sel_getName(selector),
               implementation, info.dli_fname ?: "(unknown)", (unsigned long)offset);
    } else {
        printf("method %c[%s %s] imp=%p image=(unknown)\n",
               classMethod ? '+' : '-', class_getName(cls), sel_getName(selector),
               implementation);
    }
}

static BOOL sendBool(id object, const char *selectorName) {
    SEL selector = sel_registerName(selectorName);
    if (object == nil || ![object respondsToSelector:selector]) {
        printf("value %s unavailable\n", selectorName);
        return NO;
    }
    BOOL value = ((BOOL (*)(id, SEL))objc_msgSend)(object, selector);
    printf("value %s=%d\n", selectorName, value ? 1 : 0);
    return value;
}

static id readObjectIvar(id object, const char *name) {
    if (object == nil) {
        return nil;
    }
    Ivar ivar = class_getInstanceVariable(object_getClass(object), name);
    if (ivar == NULL) {
        printf("ivar %s missing on %s\n", name, class_getName(object_getClass(object)));
        return nil;
    }
    id value = object_getIvar(object, ivar);
    printf("ivar %s offset=0x%lx class=%s nil=%d\n", name,
           (unsigned long)ivar_getOffset(ivar), value ? class_getName(object_getClass(value)) : "(nil)",
           value == nil ? 1 : 0);
    return value;
}

int main(void) {
    @autoreleasepool {
        NSString *path = @"/System/Library/Frameworks/MapKit.framework";
        NSBundle *bundle = [NSBundle bundleWithPath:path];
        NSError *loadError = nil;
        if (bundle == nil || (![bundle isLoaded] && ![bundle loadAndReturnError:&loadError])) {
            fprintf(stderr, "MapKit load failed: %s\n",
                    loadError.localizedDescription.UTF8String ?: "unknown error");
            return 2;
        }

        Class managerClass = objc_getClass("MKLocationManager");
        if (managerClass == Nil) {
            fprintf(stderr, "MKLocationManager missing after MapKit load\n");
            return 3;
        }

        printMethod(managerClass, sel_registerName("setCanMonitorWiFiStatus:"), YES);
        printMethod(managerClass, sel_registerName("isLocationServicesPossiblyAvailable:"), NO);
        printMethod(managerClass, sel_registerName("isWiFiDisabledBlockingLocation"), NO);
        printMethod(managerClass, sel_registerName("isWiFiEnabled"), NO);

        Class observerClass = objc_getClass("_MKWiFiObserver");
        if (observerClass != Nil) {
            printMethod(observerClass, sel_registerName("init"), NO);
            printMethod(observerClass, sel_registerName("_setupInterface"), NO);
            printMethod(observerClass, sel_registerName("_updateWiFiState:"), NO);
        }

        const char *forceMonitor = getenv("MACWS_PROBE_CAN_MONITOR_WIFI");
        if (forceMonitor != NULL && strcmp(forceMonitor, "1") == 0) {
            ((void (*)(id, SEL, BOOL))objc_msgSend)(
                managerClass, sel_registerName("setCanMonitorWiFiStatus:"), YES);
            printf("probe requested setCanMonitorWiFiStatus=1 before singleton creation\n");
        }

        id manager = ((id (*)(id, SEL))objc_msgSend)(managerClass,
                                                     sel_registerName("sharedLocationManager"));
        printf("manager class=%s nil=%d\n",
               manager ? class_getName(object_getClass(manager)) : "(nil)", manager == nil ? 1 : 0);

        sendBool(manager, "isLocationServicesAuthorizationNeeded");
        sendBool(manager, "isLocationServicesEnabled");
        sendBool(manager, "isLocationServicesDenied");
        sendBool(manager, "isLocationServicesRestricted");
        sendBool(manager, "isWiFiEnabled");
        sendBool(manager, "isWiFiDisabledBlockingLocation");

        NSError *availabilityError = nil;
        SEL availableSelector = sel_registerName("isLocationServicesPossiblyAvailable:");
        BOOL possiblyAvailable = ((BOOL (*)(id, SEL, NSError **))objc_msgSend)(
            manager, availableSelector, &availabilityError);
        printf("value isLocationServicesPossiblyAvailable=%d errorDomain=%s errorCode=%ld\n",
               possiblyAvailable ? 1 : 0,
               availabilityError.domain.UTF8String ?: "(nil)", (long)availabilityError.code);

        id wifiObserver = readObjectIvar(manager, "_wifiObserver");
        sendBool(wifiObserver, "isWifiEnabled");
        id interface = readObjectIvar(wifiObserver, "_interface");
        sendBool(interface, "powerOn");

        [[NSRunLoop currentRunLoop] runUntilDate:[NSDate dateWithTimeIntervalSinceNow:1.0]];
        printf("after-wait\n");
        sendBool(manager, "isWiFiEnabled");
        sendBool(manager, "isWiFiDisabledBlockingLocation");
        sendBool(wifiObserver, "isWifiEnabled");
        sendBool(interface, "powerOn");
    }
    return 0;
}

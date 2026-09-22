// Diagnostic client for Ventura's unmodified NSOpenPanel service graph.
//
// This intentionally has no AppKit link-time dependency.  It loads the actual
// framework from the macOS rootfs and invokes only public NSApplication /
// NSOpenPanel APIs through the realized Objective-C runtime.  That keeps the
// witness independent from LocalFilePanel.m and makes a missing XPC service,
// a process crash, and a completed modal reply distinguishable.

#include <dlfcn.h>
#include <objc/message.h>
#include <objc/runtime.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <unistd.h>

typedef long MacWSInteger;

extern void *objc_autoreleasePoolPush(void);
extern void objc_autoreleasePoolPop(void *context);

static void *SendID(void *target, const char *selector) {
    return ((void *(*)(void *, SEL))objc_msgSend)(
        target, sel_registerName(selector));
}

static void SendVoidBool(void *target, const char *selector, int value) {
    ((void (*)(void *, SEL, int))objc_msgSend)(
        target, sel_registerName(selector), value);
}

static void *MakeString(const char *UTF8) {
    Class stringClass = objc_getClass("NSString");
    return stringClass && UTF8
        ? ((void *(*)(void *, SEL, const char *))objc_msgSend)(
              stringClass, sel_registerName("stringWithUTF8String:"), UTF8)
        : NULL;
}

static void UseInProcessAppKitPanel(void) {
    Class defaultsClass = objc_getClass("NSUserDefaults");
    void *key = MakeString("NSUseRemoteSavePanel");
    void *defaults = defaultsClass
        ? SendID(defaultsClass, "standardUserDefaults") : NULL;
    if (defaults && key) {
        ((void (*)(void *, SEL, int, void *))objc_msgSend)(
            defaults, sel_registerName("setBool:forKey:"), 0, key);
    }
}

static void PrintPanelRuntimeInventory(void) {
    unsigned classCount = 0;
    Class *classes = objc_copyClassList(&classCount);
    fprintf(stderr, "native-panel inventory class-count=%u\n", classCount);
    for (unsigned index = 0; index < classCount; index++) {
        const char *name = class_getName(classes[index]);
        if (!name || (!strstr(name, "OpenPanel") &&
                      !strstr(name, "SavePanel") &&
                      !strstr(name, "Nav"))) continue;
        Class superclass = class_getSuperclass(classes[index]);
        fprintf(stderr, "native-panel inventory class=%s superclass=%s",
                name, superclass ? class_getName(superclass) : "nil");
        unsigned methodCount = 0;
        Method *methods = class_copyMethodList(
            object_getClass((id)classes[index]), &methodCount);
        fprintf(stderr, " class-methods=");
        for (unsigned methodIndex = 0; methodIndex < methodCount;
             methodIndex++) {
            const char *selector = sel_getName(
                method_getName(methods[methodIndex]));
            if (selector) fprintf(stderr, "%s%s",
                methodIndex ? "," : "", selector);
        }
        free(methods);
        methods = class_copyMethodList(classes[index], &methodCount);
        fprintf(stderr, " instance-methods=");
        for (unsigned methodIndex = 0; methodIndex < methodCount;
             methodIndex++) {
            const char *selector = sel_getName(
                method_getName(methods[methodIndex]));
            if (selector) fprintf(stderr, "%s%s",
                methodIndex ? "," : "", selector);
        }
        free(methods);
        fprintf(stderr, "\n");
    }
    free(classes);
    Class local = objc_getClass("NSLocalSavePanel");
    Class remote = objc_getClass("NSSavePanel");
    const char *selectors[] = {
        "_setShowsFormats:", "setShowsFormats:",
        "_setFormatFileTypes:", "setFormatFileTypes:",
        "_setFormatTitles:", "setFormatTitles:",
        "_showsFormats", "showsFormats",
    };
    for (unsigned i = 0; i < sizeof(selectors) / sizeof(selectors[0]); i++) {
        SEL selector = sel_registerName(selectors[i]);
        Method localMethod = local ? class_getInstanceMethod(local, selector) : NULL;
        Method remoteMethod = remote ? class_getInstanceMethod(remote, selector) : NULL;
        fprintf(stderr, "native-panel selector=%s local=%s remote=%s\n",
                selectors[i], localMethod ? method_getTypeEncoding(localMethod) : "nil",
                remoteMethod ? method_getTypeEncoding(remoteMethod) : "nil");
    }
}

int main(void) {
    void *pool = objc_autoreleasePoolPush();
    void *appKit = dlopen(
        "/System/Library/Frameworks/AppKit.framework/AppKit",
        RTLD_NOW | RTLD_LOCAL);
    if (!appKit) {
        fprintf(stderr, "native-panel stage=dlopen error=%s\n", dlerror());
        return 70;
    }

    Class applicationClass = objc_getClass("NSApplication");
    Class panelClass = objc_getClass("NSOpenPanel");
    if (!applicationClass || !panelClass) {
        fprintf(stderr,
                "native-panel stage=classes app=%p panel=%p\n",
                applicationClass, panelClass);
        return 71;
    }
    if (getenv("MACWS_NATIVE_PANEL_INVENTORY"))
        PrintPanelRuntimeInventory();
    if (getenv("MACWS_NATIVE_PANEL_INVENTORY_ONLY")) {
        objc_autoreleasePoolPop(pool);
        return 0;
    }
    if (getenv("MACWS_NATIVE_PANEL_HOLD")) {
        fprintf(stderr, "native-panel stage=lldb-hold pid=%d\n", getpid());
        fflush(stderr);
        raise(SIGSTOP);
    }

    void *application = SendID(applicationClass, "sharedApplication");
    ((void (*)(void *, SEL, MacWSInteger))objc_msgSend)(
        application, sel_registerName("setActivationPolicy:"), 0);
    ((void (*)(void *, SEL))objc_msgSend)(
        application, sel_registerName("finishLaunching"));
    SendVoidBool(application, "activateIgnoringOtherApps:", 1);

    fprintf(stderr, "native-panel stage=factory begin\n");
    fflush(stderr);
    void *panel = NULL;
    if (getenv("MACWS_NATIVE_PANEL_LOCAL")) {
        UseInProcessAppKitPanel();
        Class localPanelClass = objc_getClass("NSLocalOpenPanel");
        void *allocated = localPanelClass
            ? SendID(localPanelClass, "alloc") : NULL;
        panel = allocated
            ? ((void *(*)(void *, SEL, void *))objc_msgSend)(
                  allocated, sel_registerName("initWithOptions:"), NULL)
            : NULL;
    } else {
        if (getenv("MACWS_NATIVE_PANEL_INPROCESS"))
            UseInProcessAppKitPanel();
        panel = SendID(panelClass, "openPanel");
    }
    fprintf(stderr, "native-panel stage=factory result=%p class=%s\n",
            panel, panel ? object_getClassName(panel) : "nil");
    fflush(stderr);
    if (!panel) return 72;

    SendVoidBool(panel, "setCanChooseFiles:", 1);
    SendVoidBool(panel, "setCanChooseDirectories:", 1);
    SendVoidBool(panel, "setAllowsMultipleSelection:", 0);
    const char *directoryPath = getenv("MACWS_NATIVE_PANEL_DIRECTORY");
    if (directoryPath && *directoryPath) {
        Class URLClass = objc_getClass("NSURL");
        void *path = MakeString(directoryPath);
        void *directoryURL = URLClass && path
            ? ((void *(*)(void *, SEL, void *, int))objc_msgSend)(
                  URLClass, sel_registerName("fileURLWithPath:isDirectory:"),
                  path, 1)
            : NULL;
        if (directoryURL) {
            ((void (*)(void *, SEL, void *))objc_msgSend)(
                panel, sel_registerName("setDirectoryURL:"), directoryURL);
        }
    }
    fprintf(stderr, "native-panel stage=runModal begin\n");
    fflush(stderr);
    MacWSInteger result =
        ((MacWSInteger (*)(void *, SEL))objc_msgSend)(
            panel, sel_registerName("runModal"));
    fprintf(stderr, "native-panel stage=runModal result=%ld URLs=%p\n",
            result, SendID(panel, "URLs"));
    fflush(stderr);
    objc_autoreleasePoolPop(pool);
    return 0;
}

// iOS-native diagnostic for registering the embedded Settings metadata plug-in
// with the same LaunchServices daemon used by the chroot client.

@import Foundation;

#include <dlfcn.h>
#include <objc/message.h>
#include <objc/runtime.h>
#include <stdio.h>

int main(void) {
    setbuf(stdout, NULL);
    @autoreleasepool {
        void *coreServices = dlopen(
            "/System/Library/Frameworks/CoreServices.framework/CoreServices",
            RTLD_NOW | RTLD_LOCAL);
        Class workspaceClass = objc_getClass("LSApplicationWorkspace");
        Method defaultMethod = class_getClassMethod(
            workspaceClass, sel_registerName("defaultWorkspace"));
        Method registerApplicationMethod = class_getInstanceMethod(
            workspaceClass, sel_registerName("registerApplication:"));
        Method registerPluginMethod = class_getInstanceMethod(
            workspaceClass, sel_registerName("registerPlugin:"));
        printf("RUNTIME coreServices=%p class=%p default=%s app=%s plugin=%s\n",
               coreServices, workspaceClass,
               defaultMethod ? method_getTypeEncoding(defaultMethod) : "nil",
               registerApplicationMethod
                   ? method_getTypeEncoding(registerApplicationMethod) : "nil",
               registerPluginMethod
                   ? method_getTypeEncoding(registerPluginMethod) : "nil");
        if (!workspaceClass || !defaultMethod) return 2;

        id workspace = ((id (*)(id, SEL))objc_msgSend)(
            workspaceClass, sel_registerName("defaultWorkspace"));
        NSURL *applicationURL = [NSURL fileURLWithPath:
            @"/private/var/jb/Applications/MacWSCatalystLauncher.app"];
        NSURL *pluginURL = [applicationURL URLByAppendingPathComponent:
            @"PlugIns/SettingsExtensionProxy.appex"];
        BOOL applicationResult = ((BOOL (*)(id, SEL, id))objc_msgSend)(
            workspace, sel_registerName("registerApplication:"),
            applicationURL);
        BOOL pluginResult = ((BOOL (*)(id, SEL, id))objc_msgSend)(
            workspace, sel_registerName("registerPlugin:"), pluginURL);
        Class proxyClass = objc_getClass("LSPlugInKitProxy");
        id proxyByURL = ((id (*)(id, SEL, id))objc_msgSend)(
            proxyClass, sel_registerName("pluginKitProxyForURL:"), pluginURL);
        id proxyByIdentifier = ((id (*)(id, SEL, id))objc_msgSend)(
            proxyClass, sel_registerName("pluginKitProxyForIdentifier:"),
            @"com.apple.Appearance-Settings.extension");
        printf("PROXY class=%p byURL=%s byIdentifier=%s\n", proxyClass,
               proxyByURL ? [[proxyByURL description] UTF8String] : "nil",
               proxyByIdentifier
                   ? [[proxyByIdentifier description] UTF8String] : "nil");
        id proxy = proxyByURL ?: proxyByIdentifier;
        BOOL proxyResult = proxy ? ((BOOL (*)(id, SEL, id))objc_msgSend)(
            workspace, sel_registerName("registerPlugin:"), proxy) : NO;
        printf("REGISTER application=%u pluginURL=%u pluginProxy=%u applicationURL=%s pluginURL=%s\n",
               applicationResult ? 1 : 0, pluginResult ? 1 : 0,
               proxyResult ? 1 : 0,
               applicationURL.path.UTF8String, pluginURL.path.UTF8String);
    }
    return 0;
}

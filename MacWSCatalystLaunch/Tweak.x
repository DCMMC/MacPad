@import Foundation;

#import <xpc/xpc.h>
#import <objc/runtime.h>

#include <limits.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include "../include/macws_settings_paths.h"
#include "../include/macws_settings_bridge_notify.h"

// Runtime-confirmed on iPadOS 16.3.1 (RunningBoard 803.120.4):
// RBLaunchdInterface's ABI is @48@0:8@16@24@32o^@40 and System Settings
// submits the executable as __NSCFString, the overlay as OS_xpc_dictionary,
// and the launch domain as OSLaunchdDomain.  The stock Settings overlay
// already carries the correct original RunningBoard extension identity,
// settings-extensions sandbox, ViewBridge/ExtensionKit subservices and
// _NSApplicationMain run-loop contract.  Preserve all of that data.
//
// iOS launchd cannot use a macOS Mach-O as the first image: an otherwise
// identical RootDirectory probe runs an iOS binary successfully but leaves a
// macOS binary at "spawn scheduled", last exit 78/EX_CONFIG.  Therefore only
// the launch image is translated to this registered iOS freestanding proxy.
// It enters the chroot with raw syscalls before libSystem/libxpc consumes the
// one-shot extension launch context, then execs the original macOS extension.

static const char *const MacWSAppearanceExecutable =
    "/System/Library/ExtensionKit/Extensions/Appearance.appex/Contents/"
    "MacOS/Appearance";
static const char *const MacWSAppearanceMetadataSuffix =
    "/MacWSCatalystLauncher.app/PlugIns/SettingsExtensionProxy.appex/"
    "SettingsExtensionProxy";

static BOOL MacWSStringHasSuffix(const char *string, const char *suffix) {
    if (!string || !suffix) return NO;
    size_t stringLength = strlen(string);
    size_t suffixLength = strlen(suffix);
    return suffixLength <= stringLength &&
        memcmp(string + stringLength - suffixLength, suffix, suffixLength) == 0;
}

static BOOL MacWSIsSettingsExtensionExecutable(const char *executable) {
    if (!executable || strstr(executable, "..")) return NO;
    if (strcmp(executable, MacWSAppearanceExecutable) == 0 ||
        MacWSStringHasSuffix(executable, MacWSAppearanceMetadataSuffix)) {
        return YES;
    }
    return MacWSIsStockSettingsExecutable(executable);
}

static BOOL MacWSCopyCFString(id object, char output[PATH_MAX]) {
    if (!object || !output ||
        CFGetTypeID((__bridge CFTypeRef)object) != CFStringGetTypeID()) {
        return NO;
    }
    output[0] = '\0';
    return CFStringGetCString((__bridge CFStringRef)object, output, PATH_MAX,
                              kCFStringEncodingUTF8);
}

static BOOL MacWSBuildSettingsCarrier(const char *identifier,
                                      char executable[PATH_MAX],
                                      char carrierIdentifier[PATH_MAX]) {
    if (!identifier || strncmp(identifier, "com.apple.", 10) != 0)
        return NO;
    for (const char *cursor = identifier; *cursor; cursor++) {
        char character = *cursor;
        if (!((character >= 'a' && character <= 'z') ||
              (character >= 'A' && character <= 'Z') ||
              (character >= '0' && character <= '9') ||
              character == '.' || character == '-')) return NO;
    }
    int executableLength = snprintf(
        executable, PATH_MAX,
        "/var/jb/Applications/MacWSSettingsExtension-%s.app/"
        "SettingsExtensionProxy", identifier);
    int identifierLength = snprintf(
        carrierIdentifier, PATH_MAX,
        "com.macwsguide.settings-extension-carrier.%s", identifier);
    return executableLength > 0 && executableLength < PATH_MAX &&
        identifierLength > 0 && identifierLength < PATH_MAX;
}

static BOOL MacWSPrepareSettingsExtensionOverlay(
    xpc_object_t overlay, const char *target,
    char proxyExecutable[PATH_MAX]) {
    if (!overlay || xpc_get_type(overlay) != XPC_TYPE_DICTIONARY) return NO;
    xpc_object_t service = xpc_dictionary_get_value(overlay, "XPCService");
    if (!service || xpc_get_type(service) != XPC_TYPE_DICTIONARY) return NO;
    xpc_object_t additionalProperties =
        xpc_dictionary_get_value(overlay, "_AdditionalProperties");
    xpc_object_t runningBoard = additionalProperties &&
        xpc_get_type(additionalProperties) == XPC_TYPE_DICTIONARY
            ? xpc_dictionary_get_value(additionalProperties, "RunningBoard")
            : xpc_dictionary_get_value(overlay, "RunningBoard");
    xpc_object_t launchedIdentity = runningBoard &&
        xpc_get_type(runningBoard) == XPC_TYPE_DICTIONARY
            ? xpc_dictionary_get_value(
                  runningBoard, "RunningBoardLaunchedIdentity")
            : NULL;
    const char *identifier = launchedIdentity &&
        xpc_get_type(launchedIdentity) == XPC_TYPE_DICTIONARY
            ? xpc_dictionary_get_string(launchedIdentity, "i")
            : NULL;
    char carrierIdentifier[PATH_MAX];
    if (!MacWSIsSettingsExtensionExecutable(target) ||
        !MacWSBuildSettingsCarrier(identifier, proxyExecutable,
                                   carrierIdentifier)) return NO;

    // The first image is now an iOS executable.  Platform=1 in the stock
    // overlay describes the eventual macOS extension and would make launchd
    // reject the proxy before its raw main runs; Platform=2 describes the
    // proxy actually submitted to launchd.  The original target stays in the
    // environment for the proxy's validated post-chroot exec.
    xpc_dictionary_set_int64(service, "Platform", 2);
    xpc_dictionary_set_string(service, "Program",
                              proxyExecutable);
    // `settings-extensions` is a macOS launchd builtin profile.  Runtime on
    // iPadOS 16.3.1 rejected it before proxy main with sandbox err=22/EINVAL.
    // Remove only that cross-platform override.  The freestanding proxy is a
    // hidden APPL service-cache entry rather than an appex because iPadOS
    // forces its PluginKit `container` profile onto appex launches before
    // main; that profile denies file-chroot even with no-sandbox entitlement.
    xpc_dictionary_set_value(service, "_SandboxProfile", NULL);

    xpc_object_t arguments = xpc_array_create(NULL, 0);
    xpc_array_set_string(arguments, XPC_ARRAY_APPEND,
                         proxyExecutable);
    xpc_dictionary_set_value(service, "ProgramArguments", arguments);

    xpc_object_t environment =
        xpc_dictionary_get_value(service, "EnvironmentVariables");
    if (!environment ||
        xpc_get_type(environment) != XPC_TYPE_DICTIONARY) {
        environment = xpc_dictionary_create(NULL, NULL, 0);
        xpc_dictionary_set_value(service, "EnvironmentVariables",
                                 environment);
    }
    xpc_dictionary_set_string(environment, "MACWS_EXTENSION_TARGET",
                              target);
    xpc_dictionary_set_string(environment, "MACWS_EXTENSION_IDENTIFIER",
                              identifier);
    xpc_dictionary_set_string(environment,
                              "MACWS_EXTENSION_CARRIER_IDENTIFIER",
                              carrierIdentifier);
    return YES;
}

%hook RBLaunchdInterface

- (id)submitExtension:(id)path
               overlay:(xpc_object_t)overlay
                domain:(id)domain
                 error:(NSError **)error {
    char executable[PATH_MAX];
    if (!MacWSCopyCFString(path, executable) ||
        !MacWSIsSettingsExtensionExecutable(executable)) {
        return %orig(path, overlay, domain, error);
    }
    const char *target = MacWSStringHasSuffix(
        executable, MacWSAppearanceMetadataSuffix)
            ? MacWSAppearanceExecutable : executable;
    char proxyExecutable[PATH_MAX];
    if (!MacWSPrepareSettingsExtensionOverlay(
            overlay, target, proxyExecutable))
        return %orig(path, overlay, domain, error);

    NSString *proxyPath =
        [[NSString alloc] initWithUTF8String:proxyExecutable];
    return %orig(proxyPath, overlay, domain, error);
}

%end

static void MacWSPublishRunningBoardBridgeReadiness(void) {
    int token = MacWSSettingsBridgeStateToken();
    if (token >= 0) {
        notify_set_state(token, MacWSSettingsBridgeState((uint32_t)getpid()));
        notify_post(MACWS_SETTINGS_BRIDGE_STATE_NAME);
    }
}

static void MacWSRefreshRunningBoardBridgeReadiness(
    CFNotificationCenterRef center, void *observer, CFStringRef name,
    const void *object, CFDictionaryRef userInfo) {
    (void)center; (void)observer; (void)name; (void)object; (void)userInfo;
    MacWSPublishRunningBoardBridgeReadiness();
}

%ctor {
    Class launchClass = objc_getClass("RBLaunchdInterface");
    SEL launchSelector = sel_registerName("submitExtension:overlay:domain:error:");
    Method method = launchClass
        ? class_getInstanceMethod(launchClass, launchSelector) : NULL;
    if (!method) return;
    IMP original = method_getImplementation(method);
    %init;
    if (method_getImplementation(method) == original) return;
    // runningboardd is born before Dopamine's tweak environment on a fresh
    // boot and can therefore survive without this image. Publish the current
    // process capability only after the target IMP changed. A deleted /tmp
    // file cannot make a working bridge look absent or disable production.
    // This tweak is also linked by on-device lld. Construct the notification
    // name at runtime: no static CF object or global block requires a new
    // authenticated-data fixup in the RunningBoard image.
    static CFStringRef refreshName;
    refreshName = CFStringCreateWithCString(kCFAllocatorDefault,
        MACWS_SETTINGS_BRIDGE_REFRESH_NAME, kCFStringEncodingUTF8);
    if (!refreshName) return;
    CFNotificationCenterAddObserver(CFNotificationCenterGetDarwinNotifyCenter(),
        NULL, MacWSRefreshRunningBoardBridgeReadiness, refreshName, NULL,
        CFNotificationSuspensionBehaviorDeliverImmediately);
    MacWSPublishRunningBoardBridgeReadiness();
}

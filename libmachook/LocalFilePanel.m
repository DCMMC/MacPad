// Keep Ventura's stock NSOpenPanel/NSSavePanel in the client process.
//
// RE-confirmed on macOS 13.4 AppKit from the target rootfs:
// -[NSLocalSavePanel _useRemotePanel] reads the NSUseRemoteSavePanel AppKit
// configuration key and defaults it to true.  When true, +[NSOpenPanel
// openPanel] enters the OpenAndSavePanel/ViewBridge service graph, whose
// auxiliary endpoints cannot be provisioned by iPadOS 16.3 launchservicesd.
//
// AppKit already contains the complete native implementation:
// NSLocalOpenPanel -> NSLocalSavePanel -> NSPanel.  Selecting that supported
// implementation at the factory boundary preserves the public NSOpenPanel
// API, native FinderKit UI, delegate callbacks, modal/sheet behaviour, and
// NSURL results.  There is no surrogate UI and no validation bypass here.

@import Foundation;

#import <objc/message.h>
#import <objc/runtime.h>

#include <stdio.h>
#include <stdlib.h>

static id MacWSRuntimeString(const char *UTF8) {
    Class stringClass = objc_getClass("NSString");
    return stringClass && UTF8
        ? ((id (*)(id, SEL, const char *))objc_msgSend)(
              (id)stringClass, sel_registerName("stringWithUTF8String:"),
              UTF8)
        : nil;
}

// Ventura's remote NSSavePanel and in-process NSLocalSavePanel implement the
// same format controls under different selector spellings.  Runtime inventory
// on the target AppKit (2026-09-19) confirmed matching type encodings:
//   NSSavePanel _setShowsFormats:      v20@0:8B16
//   NSLocalSavePanel setShowsFormats:  v20@0:8B16
//   NSSavePanel _setFormatFileTypes:   v24@0:8@16
//   NSLocalSavePanel setFormatFileTypes: v24@0:8@16
//   NSSavePanel _setFormatTitles:      v24@0:8@16
//   NSLocalSavePanel setFormatTitles:  v24@0:8@16
// Word's real Save action sent _setShowsFormats: to NSLocalSavePanel and
// terminated with an unrecognized-selector exception.  Forward to AppKit's
// actual local format implementation, rather than suppressing the call.
static void MacWSLocalSetShowsFormats(id panel, SEL selector, BOOL shows) {
    (void)selector;
    ((void (*)(id, SEL, BOOL))objc_msgSend)(
        panel, sel_registerName("setShowsFormats:"), shows);
}

static void MacWSLocalSetFormatFileTypes(id panel, SEL selector, id types) {
    (void)selector;
    ((void (*)(id, SEL, id))objc_msgSend)(
        panel, sel_registerName("setFormatFileTypes:"), types);
}

static void MacWSLocalSetFormatTitles(id panel, SEL selector, id titles) {
    (void)selector;
    ((void (*)(id, SEL, id))objc_msgSend)(
        panel, sel_registerName("setFormatTitles:"), titles);
}

static BOOL MacWSLocalShowsFormats(id panel, SEL selector) {
    (void)selector;
    return ((BOOL (*)(id, SEL))objc_msgSend)(
        panel, sel_registerName("showsFormats"));
}

static void MacWSInstallLocalPanelFormatCompatibility(void) {
    Class local = objc_getClass("NSLocalSavePanel");
    if (!local) return;
    struct {
        const char *remoteName;
        const char *localName;
        IMP bridge;
    } mappings[] = {
        {"_setShowsFormats:", "setShowsFormats:",
         (IMP)MacWSLocalSetShowsFormats},
        {"_setFormatFileTypes:", "setFormatFileTypes:",
         (IMP)MacWSLocalSetFormatFileTypes},
        {"_setFormatTitles:", "setFormatTitles:",
         (IMP)MacWSLocalSetFormatTitles},
        {"_showsFormats", "showsFormats",
         (IMP)MacWSLocalShowsFormats},
    };
    for (NSUInteger index = 0;
         index < sizeof(mappings) / sizeof(mappings[0]); index++) {
        SEL remote = sel_registerName(mappings[index].remoteName);
        SEL localSelector = sel_registerName(mappings[index].localName);
        if (class_getInstanceMethod(local, remote)) continue;
        Method native = class_getInstanceMethod(local, localSelector);
        if (!native) continue;
        class_addMethod(local, remote, mappings[index].bridge,
                        method_getTypeEncoding(native));
    }
}

__attribute__((constructor))
static void MacWSUseNativeInProcessFilePanels(void) {
    // This preference is meaningful only to AppKit clients.  The old global
    // constructor called +[NSUserDefaults standardUserDefaults] in every
    // injected process, including defaults(1), cfprefsd, and launchservicesd.
    // Runtime-confirmed with LLDB on the target: that constructor reached
    // _CFXPreferences::_copyDaemonConnection... and cached the iPadOS
    // com.apple.cfprefsd.daemon endpoint before libmachook's private-service
    // routing hook was installed.  The later production persistence probe
    // therefore talked to the incompatible iOS daemon and failed.
    //
    // AppKit is already mapped before an AppKit executable's constructors run.
    // Limit the preference to those clients and enqueue it after all dylib
    // constructors have installed their service routing.  This preserves the
    // stock NSLocalOpenPanel selection without initializing CFPreferences in
    // unrelated daemons.
    if (!objc_getClass("NSApplication")) return;
    MacWSInstallLocalPanelFormatCompatibility();
    dispatch_async(dispatch_get_main_queue(), ^{
        Class defaultsClass = objc_getClass("NSUserDefaults");
        id defaults = defaultsClass
            ? ((id (*)(id, SEL))objc_msgSend)(
                  (id)defaultsClass,
                  sel_registerName("standardUserDefaults"))
            : nil;
        id key = MacWSRuntimeString("NSUseRemoteSavePanel");
        if (!defaults || !key) return;
        ((void (*)(id, SEL, BOOL, id))objc_msgSend)(
            defaults, sel_registerName("setBool:forKey:"), NO, key);

        if (getenv("MACWS_FILE_PANEL_DIAG")) {
            fprintf(stderr,
                    "MACWS_FILE_PANEL native=AppKit-in-process "
                    "NSUseRemoteSavePanel=0\n");
            fflush(stderr);
        }
    });
}

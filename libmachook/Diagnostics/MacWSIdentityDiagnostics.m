@import Foundation;
@import Security;

#import "interpose.h"
#import "MacWSIdentityDiagnostics.h"

#include <objc/runtime.h>
#include <stdatomic.h>

// Diagnostic-only witnesses for application identity initialization.  These
// wrappers never change a query, returned object, OSStatus, or UUID.  Keeping
// them in Diagnostics/ and behind one launch-time variable prevents a
// third-party application's identity policy from leaking into production
// compatibility code.

static bool MacWSIdentityDiagnosticsEnabled(void) {
    return getenv("MACWS_IDENTITY_DIAGNOSTICS") != NULL;
}

static const char *MacWSCopyCFString(CFTypeRef value, char *buffer,
                                     size_t bufferSize) {
    if (!value || CFGetTypeID(value) != CFStringGetTypeID()) return "-";
    if (!CFStringGetCString((CFStringRef)value, buffer, bufferSize,
                            kCFStringEncodingUTF8)) return "<non-utf8>";
    return buffer;
}

void MacWSIdentityDiagnosticLogKeychainQuery(
        const char *api, CFDictionaryRef query, OSStatus nativeStatus,
        OSStatus finalStatus, CFTypeRef result, void *returnAddress) {
    if (!MacWSIdentityDiagnosticsEnabled()) return;
    char classBuffer[128] = {0};
    char serviceBuffer[256] = {0};
    char accountBuffer[256] = {0};
    char groupBuffer[256] = {0};
    CFTypeRef itemClass = query ?
        CFDictionaryGetValue(query, kSecClass) : NULL;
    CFTypeRef service = query ?
        CFDictionaryGetValue(query, kSecAttrService) : NULL;
    CFTypeRef account = query ?
        CFDictionaryGetValue(query, kSecAttrAccount) : NULL;
    CFTypeRef group = query ?
        CFDictionaryGetValue(query, kSecAttrAccessGroup) : NULL;
    CFTypeRef dataProtection = query ?
        CFDictionaryGetValue(query, kSecUseDataProtectionKeychain) : NULL;
    CFTypeRef synchronizable = query ?
        CFDictionaryGetValue(query, kSecAttrSynchronizable) : NULL;
    bool usesDataProtection = dataProtection == kCFBooleanTrue;
    bool usesSynchronizable = synchronizable == kCFBooleanTrue;
    CFTypeID resultType = result ? CFGetTypeID(result) : 0;
    fprintf(stderr,
            "[MacWSIdentityDiagnostics] api=%s pid=%d program=%s "
            "class=%s service=%s account=%s access-group=%s "
            "data-protection=%s synchronizable=%s native-status=%d "
            "final-status=%d "
            "result-type=%lu return=%p\n",
            api, getpid(), getprogname() ?: "(unknown)",
            MacWSCopyCFString(itemClass, classBuffer, sizeof(classBuffer)),
            MacWSCopyCFString(service, serviceBuffer, sizeof(serviceBuffer)),
            MacWSCopyCFString(account, accountBuffer, sizeof(accountBuffer)),
            MacWSCopyCFString(group, groupBuffer, sizeof(groupBuffer)),
            usesDataProtection ? "YES" : "NO",
            usesSynchronizable ? "YES" : "NO", (int)nativeStatus,
            (int)finalStatus,
            (unsigned long)resultType, returnAddress);
    fflush(stderr);
}

typedef NSUUID *(*MacWSIdentifierForVendorIMP)(id, SEL);
static MacWSIdentifierForVendorIMP gMacWSIdentifierForVendor;

static NSUUID *MacWSDiagnosticIdentifierForVendor(id object, SEL selector) {
    NSUUID *identifier = gMacWSIdentifierForVendor ?
        gMacWSIdentifierForVendor(object, selector) : nil;
    static _Atomic unsigned callCount = 0;
    unsigned call = atomic_fetch_add_explicit(
        &callCount, 1, memory_order_relaxed) + 1;
    fprintf(stderr,
            "[MacWSIdentityDiagnostics] api=identifierForVendor "
            "call=%u pid=%d program=%s present=%s return=%p\n",
            call, getpid(), getprogname() ?: "(unknown)",
            identifier ? "YES" : "NO", __builtin_return_address(0));
    fflush(stderr);
    return identifier;
}

__attribute__((constructor))
static void MacWSInstallIdentityDiagnostics(void) {
    if (!MacWSIdentityDiagnosticsEnabled()) return;
    Class deviceClass = objc_getClass("UIDevice");
    SEL selector = sel_registerName("identifierForVendor");
    Method method = deviceClass ?
        class_getInstanceMethod(deviceClass, selector) : NULL;
    if (method) {
        gMacWSIdentifierForVendor = (MacWSIdentifierForVendorIMP)
            method_setImplementation(
                method, (IMP)MacWSDiagnosticIdentifierForVendor);
    }
    fprintf(stderr,
            "[MacWSIdentityDiagnostics] installed pid=%d program=%s "
            "identifierForVendor=%s\n",
            getpid(), getprogname() ?: "(unknown)",
            gMacWSIdentifierForVendor ? "YES" : "NO");
    fflush(stderr);
}

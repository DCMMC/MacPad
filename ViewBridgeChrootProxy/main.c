// Freestanding launch stub for macOS XPC services inside the chroot.
//
// xpcproxy gives the service process a one-shot launchd receive right in
// XPC_FLAGS/XPC_*. Preserve that context through a raw chroot/exec transition;
// do not introduce an intermediate posix_spawn or XPC client initialization.
// The transition below calls no libSystem APIs, although the current build
// still links libSystem (-lSystem in the Makefile and the deployed Mach-O).
// Calling this executable dependency-free would therefore be inaccurate.

#include <stddef.h>
#include <stdint.h>
#include "../include/macws_settings_paths.h"
#include "../include/macws_chroot_environment.h"
#if defined(MACWS_WEBKIT_PROXY_EXPERIMENT)
#include "../include/macws_webkit_services.h"
#endif

#define MACWS_SYS_exit    1
#define MACWS_SYS_fork    2
#define MACWS_SYS_chdir  12
#define MACWS_SYS_getppid 39
#define MACWS_SYS_execve 59
#define MACWS_SYS_chroot 61
#define MACWS_SYS_setuid 23
#define MACWS_SYS_setgid 181
#define MACWS_SYS_poll 230

static long MacWSSyscall0(long number) {
    register long x0 __asm("x0");
    register long x16 __asm("x16") = number;
    __asm__ volatile("svc #0x80" : "=r"(x0) : "r"(x16) : "memory", "cc");
    return x0;
}

static long MacWSFork(int *isChild) {
    register long x0 __asm("x0");
    register long x1 __asm("x1");
    register long x16 __asm("x16") = MACWS_SYS_fork;
    __asm__ volatile("svc #0x80"
                     : "=r"(x0), "=r"(x1)
                     : "r"(x16)
                     : "memory", "cc");
    // Darwin's raw fork ABI returns the child PID in x0 to both tasks and
    // identifies the child with x1=1.  libc normally translates that to the
    // POSIX child return value 0; this freestanding proxy must do it itself.
    if (isChild) *isChild = x1 != 0;
    return x0;
}

#define MACWS_ROOTFS "/var/mnt/rootfs"
#define MACWS_MAX_ARGUMENTS 128
#define MACWS_MAX_ENVIRONMENT 256

static long MacWSSyscall1(long number, const void *argument0) {
    register long x0 __asm("x0") = (long)argument0;
    register long x16 __asm("x16") = number;
    __asm__ volatile("svc #0x80" : "+r"(x0) : "r"(x16) : "memory", "cc");
    return x0;
}

static long MacWSSyscall3(long number, const void *argument0,
                          const void *argument1, const void *argument2) {
    register long x0 __asm("x0") = (long)argument0;
    register long x1 __asm("x1") = (long)argument1;
    register long x2 __asm("x2") = (long)argument2;
    register long x16 __asm("x16") = number;
    __asm__ volatile("svc #0x80"
                     : "+r"(x0)
                     : "r"(x1), "r"(x2), "r"(x16)
                     : "memory", "cc");
    return x0;
}

// Unlike libc, Darwin's raw BSD syscall returns positive errno with carry
// set. Namespace setup opens a real descriptor, so preserve that distinction
// and never treat an error number as a descriptor to query/close.
static long MacWSCheckedSyscall3(long number, long a, long b, long c) {
    register long x0 __asm("x0") = a;
    register long x1 __asm("x1") = b;
    register long x2 __asm("x2") = c;
    register long x16 __asm("x16") = number;
    __asm__ volatile("svc #0x80\n\tcneg x0, x0, cs"
                     : "+r"(x0), "+r"(x1), "+r"(x2)
                     : "r"(x16) : "memory", "cc");
    return x0;
}

__attribute__((noreturn))
static void MacWSExit(long status) {
    (void)MacWSSyscall1(MACWS_SYS_exit, (const void *)status);
    __builtin_unreachable();
}

static int MacWSStringContains(const char *string, const char *needle) {
    if (!string || !needle || !*needle) return 0;
    for (const char *start = string; *start; start++) {
        const char *left = start;
        const char *right = needle;
        while (*left && *right && *left == *right) {
            left++;
            right++;
        }
        if (!*right) return 1;
    }
    return 0;
}

static int MacWSHasEnvironmentKey(const char *entry, const char *key) {
    if (!entry || !key) return 0;
    while (*key && *entry == *key) {
        entry++;
        key++;
    }
    return !*key && *entry == '=';
}

static const char *MacWSEnvironmentValue(char *envp[], const char *key) {
    for (char **entry = envp; entry && *entry; entry++) {
        if (!MacWSHasEnvironmentKey(*entry, key)) continue;
        const char *value = *entry;
        while (*value && *value != '=') value++;
        return *value == '=' ? value + 1 : (const char *)0;
    }
    return (const char *)0;
}

static int MacWSIsSettingsExtensionTarget(const char *target) {
    return MacWSIsStockSettingsExecutable(target);
}

static int MacWSIsAuthdTarget(const char *target) {
    static const char authdTarget[] =
        "/System/Library/Frameworks/Security.framework/Versions/A/"
        "XPCServices/authd.xpc/Contents/MacOS/authd";
    return target && MacWSStringContains(target, authdTarget) &&
        MacWSStringContains(authdTarget, target);
}

static const char *MacWSTargetForProxy(const char *program, char *envp[]) {
#if defined(MACWS_WEBKIT_PROXY_EXPERIMENT)
    for (unsigned i = 0; i < sizeof(MacWSWebKitServices) / sizeof(MacWSWebKitServices[0]); ++i)
        if (MacWSStringContains(program, MacWSWebKitServices[i].proxy))
            return MacWSWebKitServices[i].target;
#endif
    // RunningBoard cannot submit a macOS .appex path to the iPadOS launchd
    // service cache.  MacWSCatalystLaunch therefore submits this already
    // registered iOS proxy and carries the original executable path in the
    // extension overlay.  Validate that path narrowly before entering the
    // chroot; every launchd-provided XPC endpoint/environment entry remains
    // untouched for the real ExtensionKit process.
    const char *extensionTarget =
        MacWSEnvironmentValue(envp, "MACWS_EXTENSION_TARGET");
    if (MacWSIsSettingsExtensionTarget(extensionTarget)) {
        return extensionTarget;
    }
    // A normal login launchd discovers CarbonCore's csnameddatad through its
    // XPC bundle. MacWS has no chroot XPC bundle domain, so the root launchd
    // job reuses this freestanding chroot transition and selects exactly that
    // stock executable. Do not accept an arbitrary path from the environment.
    const char *xpcTarget = MacWSEnvironmentValue(envp, "MACWS_XPC_TARGET");
    static const char csNamedDataTarget[] =
        "/System/Library/Frameworks/CoreServices.framework/Versions/A/"
        "Frameworks/CarbonCore.framework/Versions/A/XPCServices/"
        "csnameddatad.xpc/Contents/MacOS/csnameddatad";
    if (xpcTarget && MacWSStringContains(xpcTarget, csNamedDataTarget) &&
        MacWSStringContains(csNamedDataTarget, xpcTarget)) {
        return csNamedDataTarget;
    }
    // Authorization.framework asks launchd for the system XPC service
    // `com.apple.authd`. MacWS publishes the unmodified Ventura service on a
    // private endpoint because iPadOS owns the public name with an incompatible
    // protocol. Preserve the one-shot XPC launch context through this
    // freestanding proxy just as for csnameddatad above.
    static const char authdTarget[] =
        "/System/Library/Frameworks/Security.framework/Versions/A/"
        "XPCServices/authd.xpc/Contents/MacOS/authd";
    if (xpcTarget && MacWSIsAuthdTarget(xpcTarget)) {
        return authdTarget;
    }
    if (MacWSStringContains(program, "SettingsExtensionProxy")) {
        return "/System/Library/ExtensionKit/Extensions/Appearance.appex/"
               "Contents/MacOS/Appearance";
    }
    if (MacWSStringContains(program, "HIServicesProxy")) {
        return "/System/Library/Frameworks/ApplicationServices.framework/"
               "Versions/A/Frameworks/HIServices.framework/Versions/A/"
               "XPCServices/com.apple.hiservices-xpcservice.xpc/Contents/"
               "MacOS/com.apple.hiservices-xpcservice";
    }
    if (MacWSStringContains(program, "OpenAndSavePanelProxy")) {
        return "/System/Library/Frameworks/AppKit.framework/Versions/C/"
               "XPCServices/com.apple.appkit.xpc.openAndSavePanelService.xpc/"
               "Contents/MacOS/com.apple.appkit.xpc.openAndSavePanelService";
    }
    if (MacWSStringContains(program, "QuickLookUIServiceProxy")) {
        return "/System/Library/Frameworks/QuickLookUI.framework/Versions/A/"
               "XPCServices/QuickLookUIService.xpc/Contents/MacOS/"
               "QuickLookUIService";
    }
    if (MacWSStringContains(program, "DockHelperProxy")) {
        return "/System/Library/CoreServices/Dock.app/Contents/XPCServices/"
               "DockHelper.xpc/Contents/MacOS/DockHelper";
    }
    if (MacWSStringContains(program, "ExtensionKitProxy")) {
        return "/System/Library/Frameworks/ExtensionFoundation.framework/"
               "Versions/A/XPCServices/extensionkitservice.xpc/Contents/"
               "MacOS/extensionkitservice";
    }
    if (MacWSStringContains(program, "GeodProxy")) {
        return "/System/Library/PrivateFrameworks/GeoServices.framework/"
               "Versions/A/XPCServices/com.apple.geod.xpc/Contents/MacOS/"
               "com.apple.geod";
    }
    if (MacWSStringContains(program, "WriteConfigProxy")) {
        return "/System/Library/PrivateFrameworks/"
               "SystemAdministration.framework/XPCServices/"
               "writeconfig.xpc/Contents/MacOS/writeconfig";
    }
    if (MacWSStringContains(program, "LocationdProxy")) {
        return "/usr/libexec/locationd";
    }
    return "/System/Library/PrivateFrameworks/ViewBridge.framework/Versions/A/"
           "XPCServices/ViewBridgeAuxiliary.xpc/Contents/MacOS/"
           "ViewBridgeAuxiliary";
}

static int MacWSSpawnPostExecDebugMarker(char *envp[]) {
    int isChild = 0;
    long child = MacWSFork(&isChild);
    if (isChild) {
        static char pidString[24];
        static char jbctl[] = "/var/jb/usr/bin/jbctl";
        static char operation[] = "proc_set_debugged";
        static char *arguments[] = { jbctl, operation, pidString, NULL };
        unsigned long value =
            (unsigned long)MacWSSyscall0(MACWS_SYS_getppid);
        char reversed[24];
        size_t length = 0;
        do {
            reversed[length++] = (char)('0' + value % 10);
            value /= 10;
        } while (value && length < sizeof(reversed));
        for (size_t index = 0; index < length; index++)
            pidString[index] = reversed[length - index - 1];
        pidString[length] = '\0';

        // execve replaces the proxy in the parent and clears CS_DEBUGGED.
        // Wait until that transition has completed, then ask Dopamine's
        // supported process primitive to mark the final Appearance image.
        // poll(NULL, 0, 75) is a raw-syscall sleep and does not initialize
        // libSystem/libxpc in this one-shot helper child.
        (void)MacWSSyscall3(MACWS_SYS_poll, NULL, (const void *)0,
                            (const void *)75);
        (void)MacWSSyscall3(MACWS_SYS_execve, jbctl, arguments, envp);
        MacWSExit(117);
    }
    return child > 0;
}

int main(int argc, char *argv[], char *envp[]) {
    static char *targetArguments[MACWS_MAX_ARGUMENTS];
    static char *targetEnvironment[MACWS_MAX_ENVIRONMENT];
    static char insertLibrary[] =
        "DYLD_INSERT_LIBRARIES=/usr/local/lib/libmachook.dylib";
    static char appExtension[] = "MACWS_APP_EXTENSION=1";
    static char home[] = "HOME=/Users/root";
    static char temporaryDirectory[] = "TMPDIR=/tmp";
    static char nanoZone[] = "MallocNanoZone=0";
    static char rootEnvironment[MACWS_CHROOT_ROOT_ENV_SIZE];

    // Every entry route must establish the same real chroot namespace as
    // launchdchrootexec. Without it, CoreServices sees a host root parent and
    // can traverse/cache paths that are not reachable inside this process.
    if (!MacWSBuildChrootRootEnvironment(MACWS_ROOTFS, rootEnvironment,
            sizeof(rootEnvironment), MacWSCheckedSyscall3)) MacWSExit(118);

    const char *target = MacWSTargetForProxy(
        argc > 0 && argv ? argv[0] : (const char *)0, envp);
    const int isSettingsExtension = MacWSIsSettingsExtensionTarget(target);

    // The real settings extension cannot fork after its ExtensionKit sandbox
    // is active, and exec clears a debug state acquired in this proxy.  Keep a
    // minimal root child outside the chroot just long enough to mark the final
    // post-exec process through Dopamine's supported jbctl primitive.
    if (isSettingsExtension && !MacWSSpawnPostExecDebugMarker(envp)) {
        MacWSExit(116);
    }

    size_t argumentCount = 0;
    targetArguments[argumentCount++] = (char *)target;
    for (int index = 1; index < argc &&
         argumentCount + 1 < MACWS_MAX_ARGUMENTS; index++) {
        targetArguments[argumentCount++] = argv[index];
    }
    targetArguments[argumentCount] = (char *)0;

    size_t environmentCount = 0;
    for (char **entry = envp; entry && *entry; entry++) {
        // Replace only values that must describe the macOS process.  Preserve
        // XPC_FLAGS and every launchd-provided entry byte-for-byte.
        if (MacWSHasEnvironmentKey(*entry, "DYLD_INSERT_LIBRARIES") ||
            MacWSHasEnvironmentKey(*entry, "HOME") ||
            MacWSHasEnvironmentKey(*entry, "TMPDIR") ||
            MacWSHasEnvironmentKey(*entry, "MallocNanoZone") ||
            MacWSHasEnvironmentKey(*entry, MACWS_CHROOT_ROOT_KEY))
            continue;
        // Five canonical values plus NULL remain. Reject an oversized launch
        // context instead of silently discarding trailing one-shot XPC data.
        if (environmentCount + 6 >= MACWS_MAX_ENVIRONMENT) MacWSExit(119);
        targetEnvironment[environmentCount++] = *entry;
    }
    // Settings panes are sandboxed ExtensionKit processes.  They cannot fork,
    // so libmachook's fork+ptrace JIT entitlement bootstrap is neither usable
    // nor needed by these native AppKit bundles.  Their compatibility library
    // is carried by a bundle-local LC_LOAD_DYLIB (installed by postinst), which
    // also avoids the extension sandbox's denial of the global /usr/local path.
    if (isSettingsExtension) {
        targetEnvironment[environmentCount++] = appExtension;
    } else {
        targetEnvironment[environmentCount++] = insertLibrary;
    }
    targetEnvironment[environmentCount++] = home;
    targetEnvironment[environmentCount++] = temporaryDirectory;
    targetEnvironment[environmentCount++] = nanoZone;
    targetEnvironment[environmentCount++] = rootEnvironment;
    targetEnvironment[environmentCount] = (char *)0;

    if (MacWSSyscall1(MACWS_SYS_chroot, MACWS_ROOTFS) != 0) MacWSExit(111);
    if (MacWSSyscall1(MACWS_SYS_chdir, "/") != 0) MacWSExit(112);
    if (isSettingsExtension) {
        // The proxy is setuid-root only so this first image can cross the
        // chroot boundary.  RunningBoard created the extension job for the
        // mobile host and iPadOS container setup is performed at exec time;
        // leaving euid 0 here makes the kernel reject that mobile container.
        // Drop every saved/effective credential before the real extension
        // image is admitted, while retaining the one-shot XPC environment.
        // Do not generalize this to ViewBridge/HIServices: a runtime A/B on
        // 2026-08-04 showed that mobile ViewBridge cannot look up the root
        // WindowServer session and exits 2 before publishing its listener.
        if (MacWSSyscall1(MACWS_SYS_setgid, (const void *)501) != 0)
            MacWSExit(114);
        if (MacWSSyscall1(MACWS_SYS_setuid, (const void *)501) != 0)
            MacWSExit(115);
    }
    (void)MacWSSyscall3(MACWS_SYS_execve, target, targetArguments,
                        targetEnvironment);
    MacWSExit(113);
}

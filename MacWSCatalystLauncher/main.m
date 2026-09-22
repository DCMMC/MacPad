@import Darwin;
@import UIKit;

#include "macws_macho_arch.h"
#include "macws_interop_protocol.h"
#include <notify.h>

extern int proc_pidpath(int pid, void *buffer, uint32_t buffersize);
#define MACWS_PROC_PIDPATH_MAX 4096

static const char *const kMacWSRoot = "/var/mnt/rootfs";
static const char *const kMapsExecutable =
    "/System/Applications/Maps.app/Contents/MacOS/Maps";
static const char *const kMapsHostExecutable =
    "/var/mnt/rootfs/System/Applications/Maps.app/Contents/MacOS/Maps";
static const char *const kChrootExec =
    "/var/jb/usr/macOS/bin/launchdchrootexec";
static const char *const kHostCarrierMarker =
    "/tmp/macws-maps-host-carrier.pid";
static const char *const kLocationProviderMarker =
    "/var/mnt/rootfs/private/tmp/macws_location_provider_ready";
static const char *const kLocationProviderExecutable =
    "/usr/local/libexec/MacWSInteropService.app/Contents/MacOS/"
    "macwsinteropd";
static const char *const kLocationProviderHostExecutable =
    "/private/var/mnt/rootfs/usr/local/libexec/"
    "MacWSInteropService.app/Contents/MacOS/macwsinteropd";
static const char *const kCatalystRequestPath =
    "/tmp/macws-catalyst-launch-request.plist";
static const char *const kCatalystMarkerDirectory =
    "/var/mnt/rootfs/private/tmp";
static const char *const kCatalystLoopbackProxyURL =
    "socks5h://127.0.0.1:1082";
static pid_t gMapsPID = -1;
static bool gMapsLaunchPending = false;

static void macws_clear_catalyst_debug_environment(void) {
    // MacWSHost is long-lived and can have inherited these from an earlier
    // diagnostic shell. Every child starts from the same production-clean
    // baseline; individual allowlisted applications may then opt into a
    // read-only diagnostic through a rootfs sentinel below.
    static const char *const variables[] = {
        "MACWS_APP_MOUNT_COMPAT_DIAGNOSTIC",
        "MACWS_LLDB_HOLD_AFTER_INIT",
        "MACWS_SUSPEND_AT_EXEC",
        "MACWS_SUSPEND_TARGET",
        "MACWS_RANDOM_DIAGNOSTICS",
        "MACWS_IDENTITY_DIAGNOSTICS",
        "MACWS_XPC_NAME_TRACE",
        "MACWS_XPC_NAME_TRACE_FILTER",
    };
    for (size_t index = 0;
         index < sizeof(variables) / sizeof(variables[0]); index++) {
        unsetenv(variables[index]);
    }
}

static bool macws_loopback_proxy_is_reachable(void) {
    int descriptor = socket(AF_INET, SOCK_STREAM, 0);
    if (descriptor < 0) return false;
    int flags = fcntl(descriptor, F_GETFL, 0);
    if (flags >= 0) (void)fcntl(descriptor, F_SETFL, flags | O_NONBLOCK);
    struct sockaddr_in address = {
        .sin_len = sizeof(address),
        .sin_family = AF_INET,
        .sin_port = htons(1082),
        .sin_addr = {.s_addr = htonl(INADDR_LOOPBACK)},
    };
    int result = connect(descriptor, (const struct sockaddr *)&address,
                         sizeof(address));
    bool reachable = result == 0;
    if (!reachable && errno == EINPROGRESS) {
        struct pollfd pollDescriptor = {
            .fd = descriptor,
            .events = POLLOUT,
        };
        if (poll(&pollDescriptor, 1, 150) > 0) {
            int socketError = 0;
            socklen_t errorLength = sizeof(socketError);
            reachable = getsockopt(descriptor, SOL_SOCKET, SO_ERROR,
                                   &socketError, &errorLength) == 0 &&
                socketError == 0;
        }
    }
    close(descriptor);
    return reachable;
}

static void macws_configure_asphalt_network_environment(void) {
    // Direct networking remains the default.  Some access networks block the
    // Google/Firebase endpoints required during Asphalt registration while a
    // user-owned SOCKS forward is available on the iPad loopback interface.
    // Select that already-running endpoint once at child launch.  The game
    // retains normal DNS-through-proxy and TLS peer verification; MacWS does
    // not intercept traffic, install a permissive verifier, or fabricate
    // registration state.
    static const char *const proxyVariables[] = {
        "ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy",
        "HTTP_PROXY", "http_proxy",
    };
    bool reachable = macws_loopback_proxy_is_reachable();
    for (size_t index = 0;
         index < sizeof(proxyVariables) / sizeof(proxyVariables[0]); index++) {
        if (reachable)
            setenv(proxyVariables[index], kCatalystLoopbackProxyURL, 1);
        else
            unsetenv(proxyVariables[index]);
    }
    fprintf(stderr,
            "[MacWSCatalystLauncher] Asphalt network route=%s\n",
            reachable ? kCatalystLoopbackProxyURL : "direct");
    fflush(stderr);
}

static void macws_configure_native_agx_environment(void) {
    setenv("MallocNanoZone", "0", 1);
    setenv("CA_DISABLE_SWAP_ICC", "1", 1);
    setenv("CA_VSYNC_OFF", "1", 1);
    setenv("MACWS_AGX_NATIVE", "1", 1);
    setenv("MACWS_AGX_REGISTER_CLASSES", "1", 1);
    unsetenv("MACWS_PIN_FALLBACK");
    setenv("COMMAND_MODE", "unix2003", 1);
    setenv("MACWS_CATALYST_REQUEST_INITIAL_SCENE", "1", 1);
    setenv("MACWS_CATALYST_REGISTER_APPLICATION", "1", 1);
    setenv("APPLICATION_SUPPORT_SERVICE_MACH_NAME",
           "com.apple.macosbooter.frontboard.systemappservices", 1);
}

static pid_t macws_live_host_carried_maps_pid(void) {
    int marker = open(kHostCarrierMarker, O_RDONLY | O_CLOEXEC);
    if (marker < 0) return 0;
    char value[32] = {0};
    ssize_t count = read(marker, value, sizeof(value) - 1);
    close(marker);
    int candidate = 0;
    if (count <= 0 || sscanf(value, "%d", &candidate) != 1 || candidate <= 1)
        return 0;
    char path[MACWS_PROC_PIDPATH_MAX] = {0};
    if (proc_pidpath(candidate, path, sizeof(path)) <= 0 ||
        strcmp(path, kMapsExecutable) != 0) return 0;
    return (pid_t)candidate;
}

static bool macws_live_location_provider_ready(void) {
    int marker = open(kLocationProviderMarker, O_RDONLY | O_CLOEXEC);
    if (marker < 0) return false;
    char value[32] = {0};
    ssize_t count = read(marker, value, sizeof(value) - 1);
    close(marker);
    int candidate = 0;
    char trailing = '\0';
    if (count <= 0 || sscanf(value, "%d%c", &candidate, &trailing) < 1 ||
        candidate <= 1 ||
        (trailing != '\0' && trailing != '\n')) return false;
    if (kill((pid_t)candidate, 0) != 0 && errno != EPERM) return false;
    char path[MACWS_PROC_PIDPATH_MAX] = {0};
    if (proc_pidpath(candidate, path, sizeof(path)) <= 0) return false;
    // Runtime-confirmed after the 2026-08-09 device cold boot: proc_pidpath
    // runs in the launcher's iOS mount namespace and returns the canonical
    // host path `/private/var/mnt/rootfs/...`, while the same executable sees
    // `/usr/local/...` inside its chroot. Accept only those two exact aliases
    // of the packaged interop image; a suffix or PID-only match would let an
    // unrelated process satisfy Maps' real-provider readiness invariant.
    return strcmp(path, kLocationProviderExecutable) == 0 ||
        strcmp(path, kLocationProviderHostExecutable) == 0;
}

static bool macws_wait_for_location_provider(void) {
    // The normal warm path returns immediately.  On a cold GUI generation the
    // Host remains responsive while this short-lived helper waits for the
    // first real Ventura CLLocationManager callback.  Maps must not cache a
    // hardware-capability result before that end-to-end witness exists.
    if (macws_live_location_provider_ready()) return true;
    uint32_t refreshed = notify_post(MACWS_LOCATION_REFRESH_NOTIFICATION);
    fprintf(stderr, "[MacWSCatalystLauncher] requested native location client refresh status=%u\n", refreshed);
    for (unsigned attempt = 0; attempt < 300; attempt++) {
        if (macws_live_location_provider_ready()) return true;
        usleep(100 * 1000);
    }
    fprintf(stderr,
            "[MacWSCatalystLauncher] location provider was not ready after "
            "30 seconds; Maps launch deferred\n");
    fflush(stderr);
    return false;
}

static bool macws_configure_maps_environment(void) {
    macws_macho_arch_t arch = macws_macho_arch_for_path(kMapsHostExecutable);
    const char *insert = macws_insert_dylib_for_arch(arch);
    if (!insert) return false;
    setenv("DYLD_INSERT_LIBRARIES", insert, 1);
    setenv("HOME", "/Users/root", 1);
    // The UIKit carrier inherits iPadOS's fixed preferences home
    // (/var/mobile).  Leaving that value intact makes Ventura CFPreferences
    // open a non-persistent domain even though HOME already points into the
    // chroot.  Use Maps' real Ventura container Data directory so first-run
    // state and location-related defaults survive every carrier generation.
    setenv("CFFIXED_USER_HOME",
           "/var/root/Library/Containers/com.apple.Maps/Data", 1);
    setenv("USER", "root", 1);
    setenv("TMPDIR", "/tmp", 1);
    macws_clear_catalyst_debug_environment();
    macws_configure_native_agx_environment();
    setenv("__CFBundleIdentifier", "com.apple.Maps", 1);
    return true;
}

static bool macws_valid_catalyst_root_executable(NSString *rootExecutable) {
    if (![rootExecutable hasPrefix:@"/Applications/"] &&
        ![rootExecutable hasPrefix:@"/System/Applications/"])
        return false;
    if ([rootExecutable rangeOfString:@"/../"].location != NSNotFound ||
        [rootExecutable hasSuffix:@"/.."] ||
        [rootExecutable rangeOfString:@"\n"].location != NSNotFound)
        return false;
    return [rootExecutable rangeOfString:@".app/Contents/MacOS/"].location !=
        NSNotFound;
}

static bool macws_valid_bundle_identifier(NSString *bundleIdentifier) {
    if (bundleIdentifier.length == 0 || bundleIdentifier.length > 255)
        return false;
    NSCharacterSet *allowed = [NSCharacterSet
        characterSetWithCharactersInString:
            @"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-"];
    return [bundleIdentifier rangeOfCharacterFromSet:allowed.invertedSet]
        .location == NSNotFound;
}

static bool macws_load_catalyst_request(NSString **rootExecutableOut,
                                        NSString **bundleIdentifierOut,
                                        NSString **containerHomeOut) {
    NSDictionary *request = [NSDictionary
        dictionaryWithContentsOfFile:@(kCatalystRequestPath)];
    NSString *rootExecutable = request[@"root_executable"];
    NSString *bundleIdentifier = request[@"bundle_identifier"];
    NSString *containerHome = request[@"container_home"];
    if (![rootExecutable isKindOfClass:NSString.class] ||
        ![bundleIdentifier isKindOfClass:NSString.class] ||
        !macws_valid_catalyst_root_executable(rootExecutable) ||
        !macws_valid_bundle_identifier(bundleIdentifier))
        return false;
    if (![containerHome isKindOfClass:NSString.class] ||
        ![containerHome hasPrefix:@"/Users/mobile/Library/Containers/"] ||
        [containerHome rangeOfString:@"/../"].location != NSNotFound ||
        [containerHome rangeOfString:@"\n"].location != NSNotFound)
        return false;

    NSString *hostExecutable = [@(kMacWSRoot)
        stringByAppendingString:rootExecutable];
    char resolvedRoot[PATH_MAX] = {0};
    char resolvedExecutable[PATH_MAX] = {0};
    if (!realpath(kMacWSRoot, resolvedRoot) ||
        !realpath(hostExecutable.fileSystemRepresentation,
                  resolvedExecutable))
        return false;
    size_t rootLength = strlen(resolvedRoot);
    if (strncmp(resolvedExecutable, resolvedRoot, rootLength) != 0 ||
        resolvedExecutable[rootLength] != '/')
        return false;
    struct stat executableStatus = {0};
    if (stat(resolvedExecutable, &executableStatus) != 0 ||
        !S_ISREG(executableStatus.st_mode) ||
        !(executableStatus.st_mode & 0111))
        return false;

    if (rootExecutableOut) *rootExecutableOut = rootExecutable;
    if (bundleIdentifierOut) *bundleIdentifierOut = bundleIdentifier;
    if (containerHomeOut) *containerHomeOut = containerHome;
    return true;
}

static bool macws_configure_catalyst_environment(
        NSString *rootExecutable, NSString *bundleIdentifier,
        NSString *containerHome) {
    NSString *hostExecutable = [@(kMacWSRoot)
        stringByAppendingString:rootExecutable];
    macws_macho_arch_t arch = macws_macho_arch_for_path(
        hostExecutable.fileSystemRepresentation);
    const char *insert = macws_insert_dylib_for_arch(arch);
    if (!insert) return false;
    setenv("DYLD_INSERT_LIBRARIES", insert, 1);
    setenv("HOME", "/Users/mobile", 1);
    setenv("CFFIXED_USER_HOME", containerHome.fileSystemRepresentation, 1);
    setenv("USER", "mobile", 1);
    setenv("TMPDIR", "/tmp", 1);
    macws_clear_catalyst_debug_environment();
    macws_configure_native_agx_environment();
    // Generic Catalyst applications resolve resources and container URLs
    // through the same scoped mount-namespace compatibility contract used by
    // macwshostd's production custom-path launcher.
    setenv("MACWS_APP_MOUNT_COMPAT", "1", 1);
    // The foreground Host is long-lived and can itself have been started from
    // a diagnostic shell.  A production application child must start from a
    // closed set of compatibility variables rather than inherit one-shot
    // tracing/suspend controls from that carrier.  In particular an inherited
    // LLDB hold leaves the child SIGSTOP'd before its first scene and looks to
    // the user like a slow or permanently black launch.
    // Some Catalyst Metal layers are presented by the UIKit carrier and are
    // not included in SkyLight's exact-window capture. Transfer only the
    // completed drawable's IOSurface Mach right; Host imports it on the
    // native-GPU path without a CPU readback or compression step.
    setenv("MACWS_CATALYST_DIRECT_DRAWABLE", "1", 1);
    setenv("__CFBundleIdentifier", bundleIdentifier.UTF8String, 1);
    if ([bundleIdentifier isEqualToString:@"com.gameloft.asphalt9mac"]) {
        macws_configure_asphalt_network_environment();
        // Ventura's Security client cannot speak iPadOS 16's per-user secd
        // storage protocol. Use the authenticated iOS-native Security helper;
        // it is reached only after stock SecItem reports an unavailable/MDS
        // backend and it preserves the vendor's real Keychain access groups.
        setenv("MACWS_CATALYST_LOCAL_KEYCHAIN", "1", 1);
        // Asphalt's embedded OpenSSL defaults to /usr/local/ssl/cert.pem.
        // gameoptions.gameloft.com currently serves the Sectigo leaf with an
        // unrelated Entrust intermediate, so a normal peer-verifying client
        // cannot construct the chain. postinst builds this scoped bundle from
        // Ventura's trust roots plus Sectigo's authentic OV R36 intermediate.
        // Keep verification enabled; never replace this with a permissive
        // verify callback or SSL_VERIFYPEER=0 diagnostic.
        setenv("SSL_CERT_FILE", "/usr/local/ssl/cert.pem", 1);
        unsetenv("SSL_CERT_DIR");
        // Explicit, launch-time-only random-source diagnostic. The marker
        // lives in the macOS rootfs so libmachook sees the matching /tmp path
        // after chroot. The diagnostic records the real API result and never
        // changes it; ordinary production launches do not create the marker.
        struct stat randomDiagnosticStatus = {0};
        if (lstat("/var/mnt/rootfs/private/tmp/"
                  "macws_asphalt_random_diag",
                  &randomDiagnosticStatus) == 0) {
            setenv("MACWS_RANDOM_DIAGNOSTICS", "1", 1);
        }
        // Identity diagnostics are deliberately independent of entropy/TLS
        // tracing. They record only Security API status and whether UIKit's
        // vendor identifier exists; no Keychain value or UUID is emitted.
        struct stat identityDiagnosticStatus = {0};
        if (lstat("/var/mnt/rootfs/private/tmp/"
                  "macws_asphalt_identity_diag",
                  &identityDiagnosticStatus) == 0) {
            setenv("MACWS_IDENTITY_DIAGNOSTICS", "1", 1);
            setenv("MACWS_XPC_NAME_TRACE", "1", 1);
            setenv("MACWS_XPC_NAME_TRACE_FILTER", "secur", 1);
        }
    }
    return true;
}

static bool macws_publish_catalyst_child_marker(
        NSString *rootExecutable, NSString *bundleIdentifier) {
    char markerPath[PATH_MAX] = {0};
    char temporaryPath[PATH_MAX] = {0};
    int markerLength = snprintf(
        markerPath, sizeof(markerPath), "%s/macws_catalyst_child.%d.info",
        kCatalystMarkerDirectory, getpid());
    int temporaryLength = snprintf(
        temporaryPath, sizeof(temporaryPath), "%s.tmp.%d",
        markerPath, getpid());
    if (markerLength <= 0 || (size_t)markerLength >= sizeof(markerPath) ||
        temporaryLength <= 0 ||
        (size_t)temporaryLength >= sizeof(temporaryPath))
        return false;
    int marker = open(temporaryPath,
                      O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
    if (marker < 0) return false;
    NSString *payload = [NSString stringWithFormat:@"v1\n%@\n%@\n",
                         rootExecutable, bundleIdentifier];
    NSData *data = [payload dataUsingEncoding:NSUTF8StringEncoding];
    ssize_t written = write(marker, data.bytes, data.length);
    int closeResult = close(marker);
    if (written != (ssize_t)data.length || closeResult != 0 ||
        rename(temporaryPath, markerPath) != 0) {
        unlink(temporaryPath);
        return false;
    }
    return true;
}

static int macws_exec_requested_catalyst_from_existing_host(void) {
    if (geteuid() != 0) return 77;
    NSString *rootExecutable = nil;
    NSString *bundleIdentifier = nil;
    NSString *containerHome = nil;
    if (!macws_load_catalyst_request(&rootExecutable, &bundleIdentifier,
                                     &containerHome)) {
        fprintf(stderr,
                "[MacWSCatalystLauncher] invalid generic Catalyst request\n");
        return 64;
    }
    // Consume exactly one request before exec. The PID-scoped marker below is
    // the durable identity witness used by UIKitSystem; leaving this mutable
    // control file behind could redirect a later ordinary Maps notification.
    if (unlink(kCatalystRequestPath) != 0) return 73;
    if (!macws_configure_catalyst_environment(
            rootExecutable, bundleIdentifier, containerHome))
        return 78;
    if (!macws_publish_catalyst_child_marker(
            rootExecutable, bundleIdentifier))
        return 73;
    fprintf(stderr,
            "[MacWSCatalystLauncher] foreground Host launching Catalyst "
            "bundle=%s executable=%s pid=%d\n",
            bundleIdentifier.UTF8String, rootExecutable.UTF8String,
            getpid());
    fflush(stderr);
    char *const ordinaryChildArgv[] = {
        // Catalyst applications belong to the foreground iPadOS login user.
        // Runtime-confirmed root launches receive errSecNotAvailable (-25291)
        // from every modern SecItem operation. This matching-uid launch is an
        // explicit A/B against that witness; the setuid launcher retains root
        // only long enough to chroot, then drops to MacWSHost's uid/gid.
        (char *)kChrootExec, "501", "501", (char *)kMacWSRoot,
        (char *)rootExecutable.fileSystemRepresentation, NULL,
    };
    char *const cleanStateChildArgv[] = {
        (char *)kChrootExec, "501", "501", (char *)kMacWSRoot,
        (char *)rootExecutable.fileSystemRepresentation,
        "-ApplePersistenceIgnoreState", "YES", NULL,
    };
    // Runtime-confirmed for Weather pid 90936: even with its on-disk
    // KnownSceneSessions directory empty before exec, AppKit TAL requested the
    // old CB25... persistent scene ID, opened a second UINS transaction and
    // exited status 1. ApplePersistenceIgnoreState is AppKit's native launch
    // contract for suppressing TAL restoration; it leaves Weather's saved
    // locations and preferences intact.
    BOOL cleanState = [bundleIdentifier isEqualToString:@"com.apple.weather"];
    char *const *childArgv = cleanState
        ? cleanStateChildArgv : ordinaryChildArgv;
    fprintf(stderr,
            "[MacWSCatalystLauncher] Catalyst persistence bundle=%s "
            "ignore-state=%s\n", bundleIdentifier.UTF8String,
            cleanState ? "YES" : "NO");
    fflush(stderr);
    execv(kChrootExec, childArgv);
    return errno ? errno : 78;
}

// MacWSHost is already a foreground UIApplication with a valid live
// FrontBoard scene.  In this mode the setuid helper does not create a second
// UIKit scene: it immediately replaces only itself with the chroot exec
// carrier.  The resulting Maps process remains a direct child of MacWSHost,
// while the Host keeps its fullscreen workspace and input connection alive.
static int macws_exec_maps_from_existing_host(void) {
    if (geteuid() != 0) return 77;
    if (macws_live_host_carried_maps_pid() > 1) return 0;
    if (!macws_wait_for_location_provider()) return 69;
    if (!macws_configure_maps_environment()) return 78;
    int marker = open(kHostCarrierMarker,
                      O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    if (marker >= 0) {
        char value[32];
        int length = snprintf(value, sizeof(value), "%d\n", getpid());
        if (length > 0) (void)write(marker, value, (size_t)length);
        close(marker);
    }
    char *const childArgv[] = {
        (char *)kChrootExec, "0", "0", (char *)kMacWSRoot,
        (char *)kMapsExecutable, NULL,
    };
    execv(kChrootExec, childArgv);
    return errno ? errno : 78;
}

static void macws_open_launcher_log(void) {
    int fd = open("/var/jb/var/mobile/catalyst-launcher.log",
                  O_WRONLY | O_CREAT | O_APPEND, 0644);
    if (fd < 0) return;
    (void)dup2(fd, STDOUT_FILENO);
    (void)dup2(fd, STDERR_FILENO);
    if (fd > STDERR_FILENO) close(fd);
}

static void macws_spawn_maps(void) {
    if (gMapsPID > 1 && (kill(gMapsPID, 0) == 0 || errno == EPERM)) return;

    // Keep the iOS UIApplication carrier alive after it has completed its real
    // FrontBoard scene handshake.  SETEXEC used to preserve the numeric PID,
    // but runtime evidence from SpringBoard showed that exec invalidates the
    // carrier's workspace connection and immediately marks that RBS process as
    // pending exit.  Maps could create two real AppKit windows, then iOS reaped
    // it roughly 45 seconds later.  A separate chroot child gives Maps its own
    // exec generation while the carrier continues to own the live iOS scene.
    // UIKitSystem validates the child's audit token and executable path before
    // registering that exact generation through FrontBoard's native bootstrap.
    if (seteuid(0) < 0) {
        perror("[MacWSCatalystLauncher] restore root euid");
        return;
    }
    fprintf(stderr,
            "[MacWSCatalystLauncher] UIKit lifecycle active; spawning Maps "
            "pid=%d uid=%d euid=%d\n",
            getpid(), getuid(), geteuid());
    fflush(stderr);

    if (geteuid() != 0) {
        fprintf(stderr,
                "[MacWSCatalystLauncher] root transition unavailable; "
                "refusing to fabricate a Catalyst process identity\n");
        return;
    }

    if (!macws_configure_maps_environment()) {
        fprintf(stderr,
                "[MacWSCatalystLauncher] unsupported Maps architecture\n");
        (void)seteuid(getuid());
        return;
    }
    // Complete UIKitMacHelper's native process-support handshake and initial
    // scene request through the live carrier.  UIKitSystem validates the Maps
    // child's real audit token and exact exec generation; no UIScreen or
    // application predicate is fabricated.  Verbose CATALYST_TRACE is
    // intentionally absent from the production environment.
    char *const childArgv[] = {
        (char *)kChrootExec, "0", "0", (char *)kMacWSRoot,
        (char *)kMapsExecutable, NULL,
    };
    extern char **environ;
    pid_t child = 0;
    int error = posix_spawn(&child, kChrootExec, NULL, NULL, childArgv,
                            environ);
    int restoreError = seteuid(getuid());
    if (error != 0) {
        errno = error;
        perror("[MacWSCatalystLauncher] posix_spawn Maps child");
        return;
    }
    if (restoreError < 0)
        perror("[MacWSCatalystLauncher] restore mobile euid");

    gMapsPID = child;
    fprintf(stderr,
            "[MacWSCatalystLauncher] Maps child started carrier=%d child=%d\n",
            getpid(), child);
    fflush(stderr);
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{
        int status = 0;
        pid_t waited = 0;
        do {
            waited = waitpid(child, &status, 0);
        } while (waited < 0 && errno == EINTR);
        dispatch_async(dispatch_get_main_queue(), ^{
            if (gMapsPID == child) gMapsPID = -1;
            if (waited == child) {
                if (WIFEXITED(status)) {
                    fprintf(stderr,
                            "[MacWSCatalystLauncher] Maps child=%d exited "
                            "status=%d\n",
                            child, WEXITSTATUS(status));
                } else if (WIFSIGNALED(status)) {
                    fprintf(stderr,
                            "[MacWSCatalystLauncher] Maps child=%d signaled "
                            "%d\n",
                            child, WTERMSIG(status));
                }
            } else {
                fprintf(stderr,
                        "[MacWSCatalystLauncher] Maps child=%d wait failed "
                        "errno=%d\n",
                        child, errno);
            }
            fflush(stderr);
        });
    });
}

@interface MacWSCatalystLauncherDelegate : UIResponder <UIApplicationDelegate>
@end

@interface MacWSCatalystLauncherSceneDelegate
    : UIResponder <UIWindowSceneDelegate>
@property(nonatomic, strong) UIWindow *window;
@end

static void macws_schedule_exec_maps_attempt(unsigned attempt) {
    if (macws_live_location_provider_ready()) {
        gMapsLaunchPending = false;
        macws_spawn_maps();
        return;
    }
    if (attempt == 0) {
        fprintf(stderr,
                "[MacWSCatalystLauncher] waiting for first Ventura location "
                "provider update before spawning Maps\n");
        fflush(stderr);
    }
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW,
                                 (int64_t)(250 * NSEC_PER_MSEC)),
                   dispatch_get_main_queue(), ^{
        macws_schedule_exec_maps_attempt(attempt + 1);
    });
}

static void macws_schedule_exec_maps(void) {
    dispatch_async(dispatch_get_main_queue(), ^{
        if (gMapsLaunchPending ||
            (gMapsPID > 1 && (kill(gMapsPID, 0) == 0 || errno == EPERM)))
            return;
        gMapsLaunchPending = true;
        macws_schedule_exec_maps_attempt(0);
    });
}

@implementation MacWSCatalystLauncherSceneDelegate

- (void)scene:(UIScene *)scene
    willConnectToSession:(UISceneSession *)session
                 options:(UISceneConnectionOptions *)connectionOptions {
    (void)session;
    (void)connectionOptions;
    fprintf(stderr,
            "[MacWSCatalystLauncher] UIScene connected class=%s pid=%d\n",
            object_getClassName(scene), getpid());
    fflush(stderr);
    // A connected UIWindowScene is a concrete witness that UIKit completed
    // the process-support and FrontBoard scene handshake.  Only then start the
    // chroot child, while retaining this carrier and its live workspace link.
    macws_schedule_exec_maps();
}

@end

@implementation MacWSCatalystLauncherDelegate

- (BOOL)application:(UIApplication *)application
        didFinishLaunchingWithOptions:(NSDictionary *)launchOptions {
    (void)application;
    (void)launchOptions;
    fprintf(stderr,
            "[MacWSCatalystLauncher] UIApplication didFinishLaunching "
            "pid=%d\n",
            getpid());
    fflush(stderr);
    return YES;
}

- (void)applicationDidBecomeActive:(UIApplication *)application {
    (void)application;
    // Legacy-scene fallback.  The normal iPadOS 16 path schedules from the
    // scene delegate above after receiving a real UIWindowScene.
    macws_schedule_exec_maps();
}

@end


int main(int argc, char *argv[]) {
    @autoreleasepool {
        macws_open_launcher_log();
        fprintf(stderr, "[MacWSCatalystLauncher] pid=%d uid=%d euid=%d\n",
                getpid(), getuid(), geteuid());
        fflush(stderr);
        if (geteuid() != 0) {
            fprintf(stderr,
                    "[MacWSCatalystLauncher] setuid-root installation is "
                    "required before UIApplicationMain\n");
            return 77;
        }
        if (argc == 2 && strcmp(argv[1], "--exec-maps-from-host") == 0) {
            // Keep the deployed Host's proven foreground-parent transaction
            // backward compatible: a pending generic request consumes this
            // one notification; with no request the exact Maps route is
            // unchanged.
            // access(2) checks the real uid for a setuid process.  The
            // request is deliberately root-owned 0600, so a launcher whose
            // real uid is mobile would incorrectly fall through to Maps even
            // though its effective uid is root.  Presence is enough here;
            // macws_load_catalyst_request performs the strict validation.
            struct stat requestStatus = {0};
            if (lstat(kCatalystRequestPath, &requestStatus) == 0)
                return macws_exec_requested_catalyst_from_existing_host();
            return macws_exec_maps_from_existing_host();
        }
        if (argc == 2 &&
            strcmp(argv[1], "--exec-request-from-host") == 0)
            return macws_exec_requested_catalyst_from_existing_host();
        uid_t launchUser = getuid();
        if (seteuid(launchUser) < 0) {
            perror("[MacWSCatalystLauncher] enter mobile UIKit identity");
            return 77;
        }
        fprintf(stderr,
                "[MacWSCatalystLauncher] entering UIApplicationMain "
                "uid=%d euid=%d\n",
                getuid(), geteuid());
        fflush(stderr);
        return UIApplicationMain(
            argc, argv, nil,
            NSStringFromClass([MacWSCatalystLauncherDelegate class]));
    }
}

#import "MacWSHostRuntime.h"

#import <IOKit/IOKitLib.h>

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <math.h>
#include <pthread.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

#import "MacWSHostDiagnostics.h"

@interface NSObject (MacWSMetalIOSurfaceAlignment)
- (NSUInteger)iosurfaceReadOnlyTextureAlignmentBytes;
@end

static NSString *const MacWSCaptureAckPath =
    @"/var/mnt/rootfs/private/tmp/macws_capture_done";
static const char MacWSInputSocketPath[] =
    "/var/mnt/rootfs/private/tmp/macws_host_input.sock";

// DisplayStream IOSurface transport is the production path. The historical
// full-display mmap remains available only for controlled compatibility A/Bs;
// it must never silently stand in for a native stream.
BOOL MacWSLegacyFramebufferFallbackEnabled(void) {
    static dispatch_once_t once;
    static BOOL enabled;
    dispatch_once(&once, ^{
        enabled = access("/tmp/com.macwsguide.host.legacy-framebuffer", F_OK) == 0;
    });
    return enabled;
}

BOOL MacWSAppInputEndpointReady(int32_t pid) {
    if (pid <= 1) return NO;
    // Socket path existence alone accepts an endpoint left behind by a dead
    // process.  kill(pid, 0) is a read-only existence probe; EPERM still means
    // the root-owned chroot process exists from this mobile UIKit process.
    if (kill(pid, 0) != 0 && errno != EPERM) return NO;
    char path[PATH_MAX] = {0};
    int length = snprintf(path, sizeof(path),
        "/var/mnt/rootfs/private/tmp/macws_app_input.%d.sock", pid);
    return length > 0 && (size_t)length < sizeof(path) &&
        access(path, F_OK) == 0;
}

BOOL MacWSStreamFrameGeometryEqual(
        MacWSStreamFrameDescriptor left,
        MacWSStreamFrameDescriptor right) {
    return left.streamID == right.streamID &&
        left.windowID == right.windowID &&
        left.layerWindowID == right.layerWindowID &&
        left.width == right.width && left.height == right.height &&
        left.contentX == right.contentX && left.contentY == right.contentY &&
        left.contentWidth == right.contentWidth &&
        left.contentHeight == right.contentHeight &&
        left.destinationX == right.destinationX &&
        left.destinationY == right.destinationY &&
        left.destinationWidth == right.destinationWidth &&
        left.destinationHeight == right.destinationHeight &&
        left.flags == right.flags &&
        fabsf(left.backingScale - right.backingScale) < 0.001f;
}

// Metal`_mtlValidateStrideTextureParameters in the target iOS 16.3.1 image
// calls this native-device selector for ShaderRead IOSurfaces and aborts the
// process through MTLReportFailure when bytesPerRow is not aligned. Query the
// same device-owned requirement before import so a malformed producer frame
// is rejected at the transport boundary instead of terminating every Scene.
// The producer is still responsible for allocating a conforming IOSurface.
NSUInteger MacWSIOSurfaceReadOnlyTextureAlignment(
        id<MTLDevice> device) {
    SEL selector = NSSelectorFromString(
        @"iosurfaceReadOnlyTextureAlignmentBytes");
    if (!device || ![device respondsToSelector:selector]) return 0;
    return [(NSObject *)device iosurfaceReadOnlyTextureAlignmentBytes];
}

CGFloat MacWSDensityModeFactor(MacWSHostDisplayDensity density) {
    return MacWSDisplayDensityFactor(density);
}

BOOL MacWSSendInputRecord(const MacWSInputRecord *record, int *errorOut) {
    static int socketFD = -1;
    static pthread_mutex_t socketLock = PTHREAD_MUTEX_INITIALIZER;
    BOOL sent = NO;
    int savedError = 0;

    pthread_mutex_lock(&socketLock);
    for (unsigned attempt = 0; attempt < 2 && !sent; attempt++) {
        if (socketFD < 0) {
            socketFD = socket(AF_UNIX, SOCK_DGRAM, 0);
            if (socketFD < 0) {
                savedError = errno;
                break;
            }
            int flags = fcntl(socketFD, F_GETFL, 0);
            if (flags >= 0) (void)fcntl(socketFD, F_SETFL, flags | O_NONBLOCK);
            int sendBuffer = 256 * 1024;
            (void)setsockopt(socketFD, SOL_SOCKET, SO_SNDBUF,
                             &sendBuffer, sizeof(sendBuffer));
            struct sockaddr_un address = {0};
            address.sun_family = AF_UNIX;
            _Static_assert(sizeof(MacWSInputSocketPath) <=
                           sizeof(address.sun_path),
                           "input socket path exceeds sockaddr_un.sun_path");
            memcpy(address.sun_path, MacWSInputSocketPath,
                   sizeof(MacWSInputSocketPath));
            if (connect(socketFD, (const struct sockaddr *)&address,
                        sizeof(address)) != 0) {
                savedError = errno;
                close(socketFD);
                socketFD = -1;
                continue;
            }
        }

        MacWSInputRecord wireRecord = *record;
        wireRecord.version = MacWSInputWireVersionForKind(wireRecord.kind);
        ssize_t written = send(socketFD, &wireRecord, sizeof(wireRecord),
                               MSG_DONTWAIT);
        sent = written == (ssize_t)sizeof(*record);
        if (sent) break;
        savedError = written < 0 ? errno : EMSGSIZE;

        // The chroot input daemon owns the receiving endpoint and can be
        // restarted independently of this UIKit process. A connected Unix
        // datagram retains the dead endpoint. Runtime evidence on 2026-07-31:
        // the first post-restart Host send failed with errno=39. Recreate the
        // client endpoint once; queue-pressure failures must remain visible.
        if (savedError == EAGAIN || savedError == EWOULDBLOCK ||
            savedError == ENOBUFS) break;
        close(socketFD);
        socketFD = -1;
    }
    pthread_mutex_unlock(&socketLock);

    if (errorOut) *errorOut = savedError;
    return sent;
}

BOOL MacWSReadCaptureAck(uint64_t *generationOut) {
    char value[96] = {0};
    int fd = open(MacWSCaptureAckPath.fileSystemRepresentation,
                  O_RDONLY | O_CLOEXEC);
    if (fd < 0) return NO;
    ssize_t count = read(fd, value, sizeof(value) - 1);
    close(fd);
    if (count <= 0) return NO;
    int producerPID = 0;
    unsigned long long generation = 0;
    if (sscanf(value, "%d %llu", &producerPID, &generation) != 2 ||
        producerPID <= 0 || generation == 0) {
        return NO;
    }
    if (generationOut) *generationOut = (uint64_t)generation;
    return YES;
}

// Read-only witness for Metal's RE-confirmed registration inputs.  The iOS
// 16.3.1 Metal binary's MTLRegisterDevices matches IOAcceleratorES, reads
// MetalPluginName/MetalPluginClassName, and then loads the named bundle.
void MacWSLogMetalRegistryState(void) {
    io_iterator_t iterator = IO_OBJECT_NULL;
    kern_return_t kr = IOServiceGetMatchingServices(
        kIOMasterPortDefault, IOServiceMatching("IOAcceleratorES"), &iterator);
    NSUInteger count = 0;
    io_service_t service;
    while (kr == KERN_SUCCESS && (service = IOIteratorNext(iterator))) {
        count++;
        io_name_t serviceName = {0};
        IORegistryEntryGetName(service, serviceName);
        CFTypeRef pluginValue = IORegistryEntryCreateCFProperty(
            service, CFSTR("MetalPluginName"), kCFAllocatorDefault, 0);
        CFTypeRef classValue = IORegistryEntryCreateCFProperty(
            service, CFSTR("MetalPluginClassName"), kCFAllocatorDefault, 0);
        NSString *pluginName = CFBridgingRelease(pluginValue);
        NSString *className = CFBridgingRelease(classValue);
        NSString *bundlePath = pluginName.length
            ? [@"/System/Library/Extensions" stringByAppendingPathComponent:
                [pluginName stringByAppendingString:@".bundle"]]
            : nil;
        NSBundle *bundle = bundlePath ? [NSBundle bundleWithPath:bundlePath] : nil;
        Class pluginClass = className.length ? NSClassFromString(className) : Nil;
        MacWSLog(@"metal-registry service=%s plugin=%@ class=%@ bundle=%@ loaded=%@ realized=%@",
                 serviceName, pluginName, className, bundlePath,
                 bundle.isLoaded ? @"YES" : @"NO", pluginClass);
        IOObjectRelease(service);
    }
    if (iterator) IOObjectRelease(iterator);
    MacWSLog(@"metal-registry enumeration kr=0x%x count=%lu",
             kr, (unsigned long)count);
}

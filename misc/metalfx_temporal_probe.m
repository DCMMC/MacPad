// Bounded MetalFX temporal-scaler factory witness for the macOS chroot.
//
// The scaler constructor performs MetalFX's MPSGraph/ANE preparation before
// any game content is needed.  Keeping that boundary in a small executable
// makes compiler failures reproducible without a multi-minute Steam launch.

@import Foundation;
@import Metal;
@import MetalFX;

#if __has_include(<IOSurface/IOSurface.h>)
#import <IOSurface/IOSurface.h>
#else
// The Theos iPhoneOS SDK ships the IOSurface link stub but not its private
// header.  This probe uses only the opaque CF object and IOSurfaceCreate, so
// the exact public ABI declarations are sufficient for the native-iOS control
// build without copying a macOS structure definition into the iOS target.
#import <IOSurface/IOSurfaceRef.h>
extern IOSurfaceRef IOSurfaceCreate(CFDictionaryRef properties);
#endif
#import <objc/message.h>
#import <objc/runtime.h>
#include <IOKit/IOKitLib.h>
#include <dlfcn.h>
#include <limits.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static const char *MacWSClassName(id object) {
    return object ? object_getClassName(object) : "(nil)";
}

typedef void (*MacWSCommandBufferCommitMethod)(id, SEL);
typedef void (*MacWSCommandQueueSubmitMethod)(id, SEL, id *, NSUInteger);
static MacWSCommandBufferCommitMethod MacWSNativeCommitOriginal;
static MacWSCommandQueueSubmitMethod MacWSNativeQueueSubmitOriginal;
static _Atomic unsigned MacWSNativeSubmitSequence;
static _Atomic unsigned MacWSNativeIOConnectSequence;

// Read-only witness for MetalFX V3's asynchronous pre/post-processing gate.
// RE-confirmed in Ventura MetalFX at
// -[_MFXTemporalScalingEffectV3 encodeToCommandBuffer:]+0x4d0: the framework
// registers a synchronization notification on the caller's command buffer;
// its block reads the uint32_t pointed to by the second callback argument and
// schedules the remaining work only when that value is 2.  Wrap that block
// without changing either callback argument or its return value so a bounded
// multi-frame probe can distinguish "notification did not arrive" from
// "notification arrived with a different state".
typedef BOOL (^MacWSSynchronizationNotificationBlock)(
    void *callbackObject, const uint32_t *status);
typedef void (*MacWSAddSynchronizationNotificationMethod)(id, SEL, id);
static MacWSAddSynchronizationNotificationMethod
    MacWSAddSynchronizationNotificationOriginal;
typedef void (*MacWSCommitAndWaitUntilSubmittedMethod)(id, SEL);
static MacWSCommitAndWaitUntilSubmittedMethod
    MacWSCommitAndWaitUntilSubmittedOriginal;
static _Atomic unsigned MacWSSynchronizationRegistrationSequence;
static _Atomic unsigned MacWSSynchronizationCallbackSequence;
static _Atomic unsigned MacWSCommitAndWaitSequence;
typedef void (*MacWSMPSGraphRunMethod)(id, SEL, id, id, id, id, id);
typedef id (*MacWSMPSGraphExecutableRunMethod)(id, SEL, id, id, id, id);
static MacWSMPSGraphRunMethod MacWSMPSGraphRunOriginal;
static MacWSMPSGraphExecutableRunMethod MacWSMPSGraphExecutableRunOriginal;
static _Atomic unsigned MacWSMPSGraphRunSequence;
static _Atomic unsigned MacWSMPSGraphWatchArmed;

static void MacWSMPSGraphRunDiagnostic(
    id self, SEL selector, id queue, id feeds, id targetOperations,
    id resultsDictionary, id executionDescriptor) {
    unsigned sequence = atomic_fetch_add(&MacWSMPSGraphRunSequence, 1) + 1;
    fprintf(stderr,
            "METALFX_TEMPORAL_PROBE stage=mpsgraph-run-begin sequence=%u "
            "variant=graph receiver=%p class=%s queue=%p descriptor=%p\n",
            sequence, (__bridge void *)self, MacWSClassName(self),
            (__bridge void *)queue, (__bridge void *)executionDescriptor);
    MacWSMPSGraphRunOriginal(self, selector, queue, feeds, targetOperations,
                             resultsDictionary, executionDescriptor);
    fprintf(stderr,
            "METALFX_TEMPORAL_PROBE stage=mpsgraph-run-end sequence=%u "
            "variant=graph receiver=%p\n",
            sequence, (__bridge void *)self);
}

static id MacWSMPSGraphExecutableRunDiagnostic(
    id self, SEL selector, id queue, id inputsArray, id resultsArray,
    id executionDescriptor) {
    unsigned sequence = atomic_fetch_add(&MacWSMPSGraphRunSequence, 1) + 1;
    fprintf(stderr,
            "METALFX_TEMPORAL_PROBE stage=mpsgraph-run-begin sequence=%u "
            "variant=executable receiver=%p class=%s queue=%p descriptor=%p\n",
            sequence, (__bridge void *)self, MacWSClassName(self),
            (__bridge void *)queue, (__bridge void *)executionDescriptor);
    const char *watchText = getenv(
        "MACWS_METALFX_STOP_ON_MPSGRAPH_SEQUENCE");
    unsigned watchSequence = watchText
        ? (unsigned)strtoul(watchText, NULL, 10) : 0;
    if (watchSequence == sequence) {
        atomic_store(&MacWSMPSGraphWatchArmed, sequence);
        dispatch_after(
            dispatch_time(DISPATCH_TIME_NOW, 500 * NSEC_PER_MSEC),
            dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
                if (atomic_load(&MacWSMPSGraphWatchArmed) != sequence) return;
                fprintf(stderr,
                        "METALFX_TEMPORAL_PROBE "
                        "stage=mpsgraph-watchdog-stop sequence=%u pid=%d\n",
                        sequence, getpid());
                kill(getpid(), SIGSTOP);
            });
    }
    id result = MacWSMPSGraphExecutableRunOriginal(
        self, selector, queue, inputsArray, resultsArray,
        executionDescriptor);
    if (watchSequence == sequence)
        atomic_compare_exchange_strong(
            &MacWSMPSGraphWatchArmed, &sequence, 0);
    fprintf(stderr,
            "METALFX_TEMPORAL_PROBE stage=mpsgraph-run-end sequence=%u "
            "variant=executable receiver=%p result=%p class=%s\n",
            sequence, (__bridge void *)self, (__bridge void *)result,
            MacWSClassName(result));
    return result;
}

static void MacWSInstallMPSGraphRunDiagnostic(void) {
    if (!getenv("MACWS_METALFX_MPSGRAPH_DIAG") ||
        MacWSMPSGraphRunOriginal || MacWSMPSGraphExecutableRunOriginal) return;
    Class graphClass = objc_getClass("MPSGraph");
    SEL graphSelector = sel_registerName(
        "runAsyncWithMTLCommandQueue:feeds:targetOperations:"
        "resultsDictionary:executionDescriptor:");
    Method graphMethod = graphClass
        ? class_getInstanceMethod(graphClass, graphSelector) : NULL;
    if (graphMethod) {
        MacWSMPSGraphRunOriginal =
            (MacWSMPSGraphRunMethod)method_getImplementation(graphMethod);
        method_setImplementation(graphMethod, (IMP)MacWSMPSGraphRunDiagnostic);
        fprintf(stderr,
                "METALFX_TEMPORAL_PROBE stage=mpsgraph-run-install "
                "variant=graph class=%s imp=%p types=%s\n",
                class_getName(graphClass), MacWSMPSGraphRunOriginal,
                method_getTypeEncoding(graphMethod) ?: "(nil)");
    }

    Class executableClass = objc_getClass("MPSGraphExecutable");
    SEL executableSelector = sel_registerName(
        "runAsyncWithMTLCommandQueue:inputsArray:resultsArray:"
        "executionDescriptor:");
    Method executableMethod = executableClass
        ? class_getInstanceMethod(executableClass, executableSelector) : NULL;
    if (executableMethod) {
        MacWSMPSGraphExecutableRunOriginal =
            (MacWSMPSGraphExecutableRunMethod)
                method_getImplementation(executableMethod);
        method_setImplementation(
            executableMethod, (IMP)MacWSMPSGraphExecutableRunDiagnostic);
        fprintf(stderr,
                "METALFX_TEMPORAL_PROBE stage=mpsgraph-run-install "
                "variant=executable class=%s imp=%p types=%s\n",
                class_getName(executableClass),
                MacWSMPSGraphExecutableRunOriginal,
                method_getTypeEncoding(executableMethod) ?: "(nil)");
    }
}

static void MacWSDumpMPSGraphRunMethods(void) {
    if (!getenv("MACWS_METALFX_MPSGRAPH_DIAG")) return;
    const char *names[] = {
        "runAsyncWithMTLCommandQueue:feeds:targetOperations:"
        "resultsDictionary:executionDescriptor:",
        "runAsyncWithMTLCommandQueue:inputsArray:resultsArray:"
        "executionDescriptor:",
    };
    int classCount = objc_getClassList(NULL, 0);
    if (classCount <= 0) return;
    __unsafe_unretained Class *classes =
        (__unsafe_unretained Class *)calloc(
            (size_t)classCount, sizeof(*classes));
    if (!classes) return;
    classCount = objc_getClassList(classes, classCount);
    for (size_t selectorIndex = 0;
         selectorIndex < sizeof(names) / sizeof(names[0]);
         selectorIndex++) {
        SEL selector = sel_registerName(names[selectorIndex]);
        unsigned matches = 0;
        for (int classIndex = 0; classIndex < classCount; classIndex++) {
            Method method = class_getInstanceMethod(classes[classIndex],
                                                    selector);
            if (!method) continue;
            fprintf(stderr,
                    "METALFX_TEMPORAL_PROBE stage=mpsgraph-method "
                    "selectorIndex=%zu class=%s imp=%p types=%s\n",
                    selectorIndex, class_getName(classes[classIndex]),
                    method_getImplementation(method),
                    method_getTypeEncoding(method) ?: "(nil)");
            matches++;
        }
        fprintf(stderr,
                "METALFX_TEMPORAL_PROBE stage=mpsgraph-method-summary "
                "selectorIndex=%zu selector=%s matches=%u\n",
                selectorIndex, names[selectorIndex], matches);
    }
    free(classes);
}

static void MacWSCommitAndWaitUntilSubmittedDiagnostic(
    id self, SEL selector) {
    unsigned sequence = atomic_fetch_add(&MacWSCommitAndWaitSequence, 1) + 1;
    fprintf(stderr,
            "METALFX_TEMPORAL_PROBE stage=commit-wait-begin sequence=%u "
            "commandBuffer=%p label=%s status=%lu\n",
            sequence, (__bridge void *)self,
            [[self label] UTF8String] ?: "(nil)",
            (unsigned long)[self status]);
    MacWSCommitAndWaitUntilSubmittedOriginal(self, selector);
    fprintf(stderr,
            "METALFX_TEMPORAL_PROBE stage=commit-wait-end sequence=%u "
            "commandBuffer=%p label=%s status=%lu error=%s\n",
            sequence, (__bridge void *)self,
            [[self label] UTF8String] ?: "(nil)",
            (unsigned long)[self status],
            [self error] ? [[[self error] description] UTF8String] : "(nil)");
}

static void MacWSAddSynchronizationNotificationDiagnostic(
    id self, SEL selector, id notification) {
    unsigned registration = atomic_fetch_add(
        &MacWSSynchronizationRegistrationSequence, 1) + 1;
    fprintf(stderr,
            "METALFX_TEMPORAL_PROBE stage=sync-register sequence=%u "
            "commandBuffer=%p block=%p\n",
            registration, (__bridge void *)self,
            (__bridge void *)notification);
    if (!notification || !MacWSAddSynchronizationNotificationOriginal) {
        if (MacWSAddSynchronizationNotificationOriginal) {
            MacWSAddSynchronizationNotificationOriginal(
                self, selector, notification);
        }
        return;
    }

    MacWSSynchronizationNotificationBlock original = [notification copy];
    MacWSSynchronizationNotificationBlock wrapped = [^BOOL(
            void *callbackObject, const uint32_t *status) {
        unsigned callback = atomic_fetch_add(
            &MacWSSynchronizationCallbackSequence, 1) + 1;
        uint32_t value = status ? *status : UINT32_MAX;
        fprintf(stderr,
                "METALFX_TEMPORAL_PROBE stage=sync-callback-begin "
                "registration=%u callback=%u commandBuffer=%p "
                "callbackObject=%p statusPointer=%p status=%#x\n",
                registration, callback, (__bridge void *)self,
                callbackObject, status, value);
        BOOL result = original(callbackObject, status);
        fprintf(stderr,
                "METALFX_TEMPORAL_PROBE stage=sync-callback-end "
                "registration=%u callback=%u status=%#x result=%d\n",
                registration, callback, value, result);
        return result;
    } copy];
    MacWSAddSynchronizationNotificationOriginal(self, selector, wrapped);
#if !__has_feature(objc_arc)
    [wrapped release];
    [original release];
#endif
}

static void MacWSInstallSynchronizationNotificationDiagnostic(
    id commandBuffer) {
    if (!getenv("MACWS_METALFX_SYNC_DIAG") || !commandBuffer ||
        MacWSAddSynchronizationNotificationOriginal) return;
    Class cls = object_getClass(commandBuffer);
    SEL selector = sel_registerName("addSynchronizationNotification:");
    Method method = cls ? class_getInstanceMethod(cls, selector) : NULL;
    if (!method) {
        fprintf(stderr,
                "METALFX_TEMPORAL_PROBE stage=sync-install class=%s "
                "selector=%s method=(nil)\n",
                cls ? class_getName(cls) : "(nil)",
                sel_getName(selector));
        return;
    }
    IMP original = method_getImplementation(method);
    MacWSAddSynchronizationNotificationOriginal =
        (MacWSAddSynchronizationNotificationMethod)original;
    const char *types = method_getTypeEncoding(method);
    BOOL added = class_addMethod(
        cls, selector,
        (IMP)MacWSAddSynchronizationNotificationDiagnostic, types);
    if (!added) {
        Method own = class_getInstanceMethod(cls, selector);
        method_setImplementation(
            own, (IMP)MacWSAddSynchronizationNotificationDiagnostic);
    }
    fprintf(stderr,
            "METALFX_TEMPORAL_PROBE stage=sync-install class=%s "
            "selector=%s types=%s original=%p subclassOverride=%s\n",
            class_getName(cls), sel_getName(selector),
            types ? types : "(nil)", original, added ? "YES" : "NO");

    SEL commitWaitSelector =
        sel_registerName("commitAndWaitUntilSubmitted");
    Method commitWaitMethod =
        class_getInstanceMethod(cls, commitWaitSelector);
    if (!commitWaitMethod) {
        fprintf(stderr,
                "METALFX_TEMPORAL_PROBE stage=commit-wait-install "
                "class=%s method=(nil)\n", class_getName(cls));
        return;
    }
    IMP commitWaitOriginal = method_getImplementation(commitWaitMethod);
    MacWSCommitAndWaitUntilSubmittedOriginal =
        (MacWSCommitAndWaitUntilSubmittedMethod)commitWaitOriginal;
    const char *commitWaitTypes =
        method_getTypeEncoding(commitWaitMethod);
    BOOL commitWaitAdded = class_addMethod(
        cls, commitWaitSelector,
        (IMP)MacWSCommitAndWaitUntilSubmittedDiagnostic,
        commitWaitTypes);
    if (!commitWaitAdded) {
        Method own = class_getInstanceMethod(cls, commitWaitSelector);
        method_setImplementation(
            own, (IMP)MacWSCommitAndWaitUntilSubmittedDiagnostic);
    }
    fprintf(stderr,
            "METALFX_TEMPORAL_PROBE stage=commit-wait-install class=%s "
            "types=%s original=%p subclassOverride=%s\n",
            class_getName(cls), commitWaitTypes ? commitWaitTypes : "(nil)",
            commitWaitOriginal, commitWaitAdded ? "YES" : "NO");
}

static uintptr_t MacWSStripUserPointer(uintptr_t pointer) {
    return pointer & UINT64_C(0x0000ffffffffffff);
}

static void MacWSSaveNativeSubmitBytes(const char *kind, unsigned sequence,
                                       const void *bytes, size_t length) {
    if (!kind || !bytes || !length || length > 1024U * 1024U) return;
    char path[PATH_MAX] = {0};
    snprintf(path, sizeof(path),
             "/tmp/metalfx-native-%d-s%u-%s.bin", getpid(), sequence, kind);
    FILE *file = fopen(path, "wb");
    size_t written = file ? fwrite(bytes, 1, length, file) : 0;
    if (file) fclose(file);
    fprintf(stderr,
            "METALFX_TEMPORAL_PROBE stage=native-submit-save sequence=%u "
            "kind=%s length=%#zx written=%#zx path=%s\n",
            sequence, kind, length, written, path);
}

// Capture the native control at the same protocol boundary as libmachook's
// chroot recorder: immediately before selector 0x1a enters IOKit.  The older
// -commit capture is intentionally retained as a separate producer-stage
// witness; comparing it with this one will show whether IOGPU finalization
// changes the command/list bytes before submission.
static void MacWSDumpNativeIOConnectSubmit(const void *inputStruct,
                                           size_t inputStructLength) {
    if (!getenv("MACWS_NATIVE_IOCONNECT_DIAG") || !inputStruct ||
        inputStructLength < 0x38 || inputStructLength % 0x38 != 0) return;
    const unsigned char *input = inputStruct;
    size_t entryCount = inputStructLength / 0x38;
    if (entryCount > 32) entryCount = 32;
    for (size_t entry = 0; entry < entryCount; entry++) {
        for (unsigned slot = 0; slot < 2; slot++) {
            uintptr_t descriptorRaw = 0;
            memcpy(&descriptorRaw,
                   input + entry * 0x38 + 0x10 + slot * sizeof(uintptr_t),
                   sizeof(descriptorRaw));
            uintptr_t descriptor = MacWSStripUserPointer(descriptorRaw);
            if (descriptor < 0x100000000ULL) continue;
            uintptr_t commandBufferRaw = 0;
            memcpy(&commandBufferRaw, (const void *)(descriptor + 0x20),
                   sizeof(commandBufferRaw));
            uintptr_t commandBuffer = MacWSStripUserPointer(commandBufferRaw);
            if (commandBuffer < 0x100000000ULL) continue;
            uintptr_t storageRaw = 0;
            memcpy(&storageRaw, (const void *)(commandBuffer + 0x250),
                   sizeof(storageRaw));
            uintptr_t storage = MacWSStripUserPointer(storageRaw);
            if (storage < 0x100000000ULL) continue;

            uintptr_t commandStartRaw = 0, commandCurrentRaw = 0;
            uintptr_t segmentStartRaw = 0, segmentCurrentRaw = 0;
            uintptr_t segmentLimitRaw = 0;
            memcpy(&commandStartRaw, (const void *)(storage + 0x28), 8);
            memcpy(&commandCurrentRaw, (const void *)(storage + 0x30), 8);
            memcpy(&segmentStartRaw, (const void *)(storage + 0x68), 8);
            memcpy(&segmentLimitRaw, (const void *)(storage + 0x70), 8);
            memcpy(&segmentCurrentRaw, (const void *)(storage + 0x328), 8);
            uintptr_t commandStart = MacWSStripUserPointer(commandStartRaw);
            uintptr_t commandCurrent =
                MacWSStripUserPointer(commandCurrentRaw);
            uintptr_t segmentStart = MacWSStripUserPointer(segmentStartRaw);
            uintptr_t segmentCurrent =
                MacWSStripUserPointer(segmentCurrentRaw);
            uintptr_t segmentLimit = MacWSStripUserPointer(segmentLimitRaw);
            size_t commandLength = commandStart &&
                    commandCurrent >= commandStart &&
                    commandCurrent - commandStart <= 1024U * 1024U
                ? commandCurrent - commandStart : 0;
            size_t segmentLength = segmentStart &&
                    segmentCurrent >= segmentStart &&
                    segmentCurrent <= segmentLimit &&
                    segmentCurrent - segmentStart <= 1024U * 1024U
                ? segmentCurrent - segmentStart : 0;
            unsigned sequence =
                atomic_fetch_add(&MacWSNativeIOConnectSequence, 1) + 1;
            fprintf(stderr,
                    "METALFX_TEMPORAL_PROBE stage=native-ioconnect-submit "
                    "sequence=%u entry=%zu slot=%u descriptor=%p "
                    "commandBuffer=%p storage=%p commandLength=%#zx "
                    "segmentLength=%#zx\n",
                    sequence, entry, slot, (void *)descriptor,
                    (void *)commandBuffer, (void *)storage,
                    commandLength, segmentLength);
            MacWSSaveNativeSubmitBytes("actual-kcmd", sequence,
                                       (const void *)commandStart,
                                       commandLength);
            MacWSSaveNativeSubmitBytes("actual-segments", sequence,
                                       (const void *)segmentStart,
                                       segmentLength);
        }
    }
}

typedef IOReturn (*MacWSIOConnectCallMethodFunction)(
    io_connect_t, uint32_t, const uint64_t *, uint32_t, const void *, size_t,
    uint64_t *, uint32_t *, void *, size_t *);

static IOReturn MacWSProbeIOConnectCallMethod(
    io_connect_t connection, uint32_t selector,
    const uint64_t *inputScalars, uint32_t inputScalarCount,
    const void *inputStruct, size_t inputStructLength,
    uint64_t *outputScalars, uint32_t *outputScalarCount,
    void *outputStruct, size_t *outputStructLength) {
    if (selector == 0x1a)
        MacWSDumpNativeIOConnectSubmit(inputStruct, inputStructLength);
    static MacWSIOConnectCallMethodFunction original;
    if (!original) {
        original = (MacWSIOConnectCallMethodFunction)dlsym(
            RTLD_NEXT, "IOConnectCallMethod");
    }
    if (!original) return kIOReturnError;
    return original(connection, selector, inputScalars, inputScalarCount,
                    inputStruct, inputStructLength, outputScalars,
                    outputScalarCount, outputStruct, outputStructLength);
}

#define MACWS_PROBE_INTERPOSE(replacement, replacee)                        \
    __attribute__((used)) static struct {                                  \
        const void *replacement;                                            \
        const void *replacee;                                               \
    } MacWSInterpose_##replacee                                             \
        __attribute__((section("__DATA,__interpose"))) = {                 \
            (const void *)(uintptr_t)&replacement,                          \
            (const void *)(uintptr_t)&replacee                              \
        }

MACWS_PROBE_INTERPOSE(MacWSProbeIOConnectCallMethod, IOConnectCallMethod);

static void MacWSDumpNativeSubmit(id commandBuffer) {
    unsigned sequence = atomic_fetch_add(&MacWSNativeSubmitSequence, 1) + 1;
    SEL boundsSelector = sel_registerName(
        "getCurrentKernelCommandBufferStart:current:end:");
    void *start = NULL;
    void *current = NULL;
    void *end = NULL;
    if ([commandBuffer respondsToSelector:boundsSelector]) {
        void (*bounds)(id, SEL, void **, void **, void **) =
            (void *)[commandBuffer methodForSelector:boundsSelector];
        if (bounds)
            bounds(commandBuffer, boundsSelector, &start, &current, &end);
    }
    size_t commandLength = start && current && current >= start
        ? (size_t)((uintptr_t)current - (uintptr_t)start) : 0;

    Ivar storageIvar = class_getInstanceVariable(
        [commandBuffer class], "_storage");
    uintptr_t storageRaw = 0;
    if (storageIvar) {
        ptrdiff_t offset = ivar_getOffset(storageIvar);
        memcpy(&storageRaw,
               (const char *)(__bridge void *)commandBuffer + offset,
               sizeof(storageRaw));
    }
    uintptr_t storage = MacWSStripUserPointer(storageRaw);
    uintptr_t segmentStartRaw = 0;
    uintptr_t segmentCurrentRaw = 0;
    uintptr_t segmentLimitRaw = 0;
    if (storage) {
        memcpy(&segmentStartRaw, (const void *)(storage + 0x68),
               sizeof(segmentStartRaw));
        memcpy(&segmentLimitRaw, (const void *)(storage + 0x70),
               sizeof(segmentLimitRaw));
        memcpy(&segmentCurrentRaw, (const void *)(storage + 0x328),
               sizeof(segmentCurrentRaw));
    }
    uintptr_t segmentStart = MacWSStripUserPointer(segmentStartRaw);
    uintptr_t segmentCurrent = MacWSStripUserPointer(segmentCurrentRaw);
    uintptr_t segmentLimit = MacWSStripUserPointer(segmentLimitRaw);
    size_t segmentLength = segmentStart && segmentCurrent >= segmentStart &&
            segmentCurrent <= segmentLimit
        ? (size_t)(segmentCurrent - segmentStart) : 0;

    fprintf(stderr,
            "METALFX_TEMPORAL_PROBE stage=native-submit sequence=%u "
            "commandBuffer=%p class=%s start=%p current=%p end=%p "
            "commandLength=%#zx storageIvar=%td storage=%p "
            "segmentStart=%p segmentCurrent=%p segmentLimit=%p "
            "segmentLength=%#zx\n",
            sequence, (__bridge void *)commandBuffer,
            MacWSClassName(commandBuffer), start, current, end, commandLength,
            storageIvar ? ivar_getOffset(storageIvar) : (ptrdiff_t)-1,
            (void *)storage, (void *)segmentStart, (void *)segmentCurrent,
            (void *)segmentLimit, segmentLength);
    MacWSSaveNativeSubmitBytes("kcmd", sequence, start, commandLength);
    MacWSSaveNativeSubmitBytes("segments", sequence,
                               (const void *)segmentStart, segmentLength);
}

static void MacWSNativeCommitDiagnostic(id commandBuffer, SEL selector) {
    MacWSDumpNativeSubmit(commandBuffer);
    if (MacWSNativeCommitOriginal)
        MacWSNativeCommitOriginal(commandBuffer, selector);
}

static void MacWSNativeQueueSubmitDiagnostic(id queue, SEL selector,
                                             id *commandBuffers,
                                             NSUInteger count) {
    fprintf(stderr,
            "METALFX_TEMPORAL_PROBE stage=native-queue-submit queue=%p "
            "class=%s count=%lu\n",
            (__bridge void *)queue, MacWSClassName(queue),
            (unsigned long)count);
    for (NSUInteger index = 0; commandBuffers && index < count; index++)
        MacWSDumpNativeSubmit(commandBuffers[index]);
    if (MacWSNativeQueueSubmitOriginal) {
        MacWSNativeQueueSubmitOriginal(
            queue, selector, commandBuffers, count);
    }
}

static void MacWSInstallNativeSubmitDiagnostic(id<MTLDevice> device) {
    BOOL commitDiagnostic = getenv("MACWS_NATIVE_KCMD_DIAG") != NULL;
    BOOL queueDiagnostic =
        getenv("MACWS_NATIVE_IOCONNECT_DIAG") != NULL;
    if ((!commitDiagnostic && !queueDiagnostic) || !device) return;
    id<MTLCommandQueue> queue = [device newCommandQueue];
    id<MTLCommandBuffer> commandBuffer = [queue commandBuffer];
    if (commitDiagnostic && !MacWSNativeCommitOriginal) {
        Class cls = commandBuffer ? [commandBuffer class] : Nil;
        SEL selector = @selector(commit);
        Method method = cls ? class_getInstanceMethod(cls, selector) : NULL;
        if (!method) {
            fprintf(stderr,
                    "METALFX_TEMPORAL_PROBE stage=native-submit-install "
                    "class=%s method=(nil)\n",
                    cls ? class_getName(cls) : "(nil)");
        } else {
            MacWSNativeCommitOriginal =
                (MacWSCommandBufferCommitMethod)method_getImplementation(
                    method);
            const char *types = method_getTypeEncoding(method);
            BOOL added = class_addMethod(cls, selector,
                                         (IMP)MacWSNativeCommitDiagnostic,
                                         types);
            if (!added) {
                method_setImplementation(
                    method, (IMP)MacWSNativeCommitDiagnostic);
            }
            fprintf(stderr,
                    "METALFX_TEMPORAL_PROBE stage=native-submit-install "
                    "class=%s original=%p subclassOverride=%d\n",
                    class_getName(cls), (void *)MacWSNativeCommitOriginal,
                    added);
        }
    }
    if (queueDiagnostic && !MacWSNativeQueueSubmitOriginal) {
        Class cls = queue ? [queue class] : Nil;
        SEL selector = sel_registerName("submitCommandBuffers:count:");
        Method method = cls ? class_getInstanceMethod(cls, selector) : NULL;
        if (!method) {
            fprintf(stderr,
                    "METALFX_TEMPORAL_PROBE "
                    "stage=native-queue-submit-install class=%s "
                    "method=(nil)\n",
                    cls ? class_getName(cls) : "(nil)");
        } else {
            MacWSNativeQueueSubmitOriginal =
                (MacWSCommandQueueSubmitMethod)method_getImplementation(
                    method);
            const char *types = method_getTypeEncoding(method);
            BOOL added = class_addMethod(
                cls, selector, (IMP)MacWSNativeQueueSubmitDiagnostic, types);
            if (!added) {
                method_setImplementation(
                    method, (IMP)MacWSNativeQueueSubmitDiagnostic);
            }
            fprintf(stderr,
                    "METALFX_TEMPORAL_PROBE "
                    "stage=native-queue-submit-install class=%s "
                    "original=%p subclassOverride=%d types=%s\n",
                    class_getName(cls), (void *)MacWSNativeQueueSubmitOriginal,
                    added, types ?: "(nil)");
        }
    }
}

static void MacWSDumpMPSGraphTensorDataMethods(void) {
    if (!getenv("MACWS_METALFX_DUMP_MPSGRAPH_METHODS")) return;
    Class tensorDataClass = objc_getClass("MPSGraphTensorData");
    unsigned methodCount = 0;
    Method *methods = tensorDataClass
        ? class_copyMethodList(tensorDataClass, &methodCount) : NULL;
    fprintf(stderr,
            "METALFX_TEMPORAL_PROBE stage=tensor-methods class=%p count=%u\n",
            tensorDataClass, methodCount);
    for (unsigned index = 0; index < methodCount; index++) {
        SEL selector = method_getName(methods[index]);
        const char *name = selector ? sel_getName(selector) : NULL;
        if (!name || (strncmp(name, "init", 4) != 0 &&
                      strcmp(name, "iosurface") != 0 &&
                      strcmp(name, "commonInitialize") != 0)) {
            continue;
        }
        fprintf(stderr,
                "METALFX_TEMPORAL_PROBE tensor-method selector=%s imp=%p "
                "types=%s\n",
                name, method_getImplementation(methods[index]),
                method_getTypeEncoding(methods[index]));
    }
    free(methods);
}

static void MacWSDumpBufferIOSurfaceBehavior(id<MTLDevice> device) {
    if (!getenv("MACWS_METALFX_DUMP_BUFFER_IOSURFACE")) return;
    uint8_t bytes[4096] = {0};
    id<MTLBuffer> ordinary = [device newBufferWithBytes:bytes
                                                 length:sizeof(bytes)
                                                options:MTLResourceStorageModeShared];
    SEL iosurfaceSelector = sel_registerName("iosurface");
    IOSurfaceRef ordinarySurface = nil;
    if ([ordinary respondsToSelector:iosurfaceSelector]) {
        ordinarySurface = ((IOSurfaceRef (*)(id, SEL))objc_msgSend)(
            ordinary, iosurfaceSelector);
    }
    fprintf(stderr,
            "METALFX_TEMPORAL_PROBE stage=ordinary-buffer object=%p class=%s "
            "respondsIOSurface=%d iosurface=%p\n",
            (__bridge void *)ordinary, MacWSClassName(ordinary),
            [ordinary respondsToSelector:iosurfaceSelector], ordinarySurface);

    NSDictionary *properties = @{
        @"IOSurfaceAllocSize": @(sizeof(bytes)),
        @"IOSurfaceBytesPerElement": @1,
    };
    IOSurfaceRef surface = IOSurfaceCreate((__bridge CFDictionaryRef)properties);
    SEL newBufferSelector = sel_registerName("newBufferWithIOSurface:");
    id<MTLBuffer> surfaceBuffer = nil;
    if (surface && [device respondsToSelector:newBufferSelector]) {
        surfaceBuffer = ((id (*)(id, SEL, IOSurfaceRef))objc_msgSend)(
            device, newBufferSelector, surface);
    }
    IOSurfaceRef roundTripSurface = nil;
    if ([surfaceBuffer respondsToSelector:iosurfaceSelector]) {
        roundTripSurface = ((IOSurfaceRef (*)(id, SEL))objc_msgSend)(
            surfaceBuffer, iosurfaceSelector);
    }
    fprintf(stderr,
            "METALFX_TEMPORAL_PROBE stage=surface-buffer create=%p "
            "deviceResponds=%d object=%p class=%s iosurface=%p roundTrip=%d\n",
            surface, [device respondsToSelector:newBufferSelector],
            (__bridge void *)surfaceBuffer, MacWSClassName(surfaceBuffer),
            roundTripSurface, surface && roundTripSurface == surface);
    if (surface) CFRelease(surface);
}

static NSUInteger MacWSBytesPerPixel(MTLPixelFormat format) {
    switch (format) {
        case MTLPixelFormatRGBA16Float: return 8;
        case MTLPixelFormatDepth32Float: return 4;
        case MTLPixelFormatRG16Float: return 4;
        case MTLPixelFormatR16Float: return 2;
        default: return 0;
    }
}

static void MacWSDumpCommandBufferError(NSError *error) {
    if (!error) return;
    NSDictionary *userInfo = error.userInfo;
    NSArray<id<MTLCommandBufferEncoderInfo>> *encoderInfos =
        userInfo[MTLCommandBufferEncoderInfoErrorKey];
    fprintf(stderr,
            "METALFX_TEMPORAL_PROBE stage=command-error domain=%s code=%ld "
            "userInfo=%s encoderCount=%lu\n",
            error.domain.UTF8String ?: "(nil)", (long)error.code,
            userInfo.description.UTF8String ?: "(nil)",
            (unsigned long)encoderInfos.count);
    NSUInteger index = 0;
    for (id<MTLCommandBufferEncoderInfo> info in encoderInfos) {
        fprintf(stderr,
                "METALFX_TEMPORAL_PROBE stage=encoder-info index=%lu "
                "label=%s state=%ld signposts=%s\n",
                (unsigned long)index++, info.label.UTF8String ?: "(nil)",
                (long)info.errorState,
                info.debugSignposts.description.UTF8String ?: "(nil)");
    }
}

static id<MTLTexture> MacWSNewProbeTexture(
    id<MTLDevice> device, MTLPixelFormat format, NSUInteger width,
    NSUInteger height, MTLTextureUsage usage, MTLStorageMode storageMode,
    BOOL initialize) {
    MTLTextureDescriptor *descriptor =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:format
                                                           width:width
                                                          height:height
                                                       mipmapped:NO];
    descriptor.usage = usage;
    descriptor.storageMode = storageMode;
    id<MTLTexture> texture = [device newTextureWithDescriptor:descriptor];
    NSUInteger bytesPerPixel = MacWSBytesPerPixel(format);
    if (!initialize || !texture || storageMode == MTLStorageModePrivate ||
        !bytesPerPixel) {
        return texture;
    }
    if (width > SIZE_MAX / bytesPerPixel) return nil;
    NSUInteger bytesPerRow = width * bytesPerPixel;
    if (height > SIZE_MAX / bytesPerRow) return nil;
    size_t byteCount = bytesPerRow * height;
    void *bytes = calloc(1, byteCount);
    if (!bytes) return nil;
    if (format == MTLPixelFormatR16Float && byteCount >= sizeof(uint16_t)) {
        // IEEE-754 half 1.0. The temporal scaler reads this 1x1 texture when
        // auto exposure is disabled.
        ((uint16_t *)bytes)[0] = 0x3c00;
    }
    [texture replaceRegion:MTLRegionMake2D(0, 0, width, height)
                mipmapLevel:0
                  withBytes:bytes
                bytesPerRow:bytesPerRow];
    free(bytes);
    return texture;
}

static int MacWSEncodeOneTemporalFrame(
    id<MTLDevice> device, id<MTLFXTemporalScaler> scaler,
    id<MTLCommandQueue> sharedQueue,
    NSUInteger inputWidth, NSUInteger inputHeight,
    NSUInteger contentWidth, NSUInteger contentHeight,
    NSUInteger outputWidth, NSUInteger outputHeight, BOOL strayProfile,
    BOOL resetFrame) {
    MTLStorageMode inputStorage = strayProfile
        ? MTLStorageModePrivate : MTLStorageModeShared;
    MTLTextureUsage colorUsage = strayProfile
        ? (MTLTextureUsage)0x5 : scaler.colorTextureUsage;
    MTLTextureUsage depthUsage = strayProfile
        ? (MTLTextureUsage)0x15 : scaler.depthTextureUsage;
    MTLTextureUsage motionUsage = strayProfile
        ? (MTLTextureUsage)0x13 : scaler.motionTextureUsage;
    MTLTextureUsage outputUsage = strayProfile
        ? (MTLTextureUsage)0x7 : scaler.outputTextureUsage;
    id<MTLTexture> color = MacWSNewProbeTexture(
        device, scaler.colorTextureFormat, inputWidth, inputHeight,
        colorUsage, inputStorage, !strayProfile);
    id<MTLTexture> depth = MacWSNewProbeTexture(
        device, scaler.depthTextureFormat, inputWidth, inputHeight,
        depthUsage, inputStorage, !strayProfile);
    id<MTLTexture> motion = MacWSNewProbeTexture(
        device, scaler.motionTextureFormat, inputWidth, inputHeight,
        motionUsage, inputStorage, !strayProfile);
    id<MTLTexture> output = MacWSNewProbeTexture(
        device, scaler.outputTextureFormat, outputWidth, outputHeight,
        outputUsage, MTLStorageModePrivate, NO);
    id<MTLTexture> exposure = strayProfile ? nil : MacWSNewProbeTexture(
        device, MTLPixelFormatR16Float, 1, 1, MTLTextureUsageShaderRead,
        MTLStorageModeShared, YES);
    id<MTLCommandQueue> queue = sharedQueue ?: [device newCommandQueue];
    MTLCommandBufferDescriptor *commandBufferDescriptor =
        [[MTLCommandBufferDescriptor alloc] init];
    commandBufferDescriptor.retainedReferences = YES;
    commandBufferDescriptor.errorOptions =
        MTLCommandBufferErrorOptionEncoderExecutionStatus;
    id<MTLCommandBuffer> commandBuffer =
        [queue commandBufferWithDescriptor:commandBufferDescriptor];
    MacWSInstallSynchronizationNotificationDiagnostic(commandBuffer);
    commandBuffer.label = @"MacWS MetalFX temporal probe";
    const char *resetText = getenv("MACWS_METALFX_PROBE_RESET");
    BOOL reset = resetText ? strcmp(resetText, "0") != 0 : resetFrame;
    fprintf(stderr,
            "METALFX_TEMPORAL_PROBE stage=encode-resources color=%p depth=%p "
            "motion=%p output=%p exposure=%p queue=%p commandBuffer=%p "
            "profile=%s dimensions=%lux%lu(content=%lux%lu)->%lux%lu "
            "usage=%#lx/%#lx/%#lx/%#lx storage=%lu reset=%d\n",
            (__bridge void *)color, (__bridge void *)depth,
            (__bridge void *)motion, (__bridge void *)output,
            (__bridge void *)exposure, (__bridge void *)queue,
            (__bridge void *)commandBuffer,
            strayProfile ? "stray" : "baseline",
            (unsigned long)inputWidth, (unsigned long)inputHeight,
            (unsigned long)contentWidth, (unsigned long)contentHeight,
            (unsigned long)outputWidth, (unsigned long)outputHeight,
            (unsigned long)colorUsage, (unsigned long)depthUsage,
            (unsigned long)motionUsage, (unsigned long)outputUsage,
            (unsigned long)inputStorage, reset);
    if (!color || !depth || !motion || !output ||
        (!strayProfile && !exposure) || !queue || !commandBuffer) {
        return 5;
    }

    scaler.inputContentWidth = contentWidth;
    scaler.inputContentHeight = contentHeight;
    scaler.colorTexture = color;
    scaler.depthTexture = depth;
    scaler.motionTexture = motion;
    scaler.outputTexture = output;
    scaler.exposureTexture = exposure;
    scaler.preExposure = 1.0f;
    scaler.jitterOffsetX = 0.0f;
    scaler.jitterOffsetY = 0.0f;
    scaler.motionVectorScaleX = 1.0f;
    scaler.motionVectorScaleY = 1.0f;
    scaler.reset = reset;
    scaler.depthReversed = strayProfile;

    @try {
        [scaler encodeToCommandBuffer:commandBuffer];
    } @catch (NSException *exception) {
        fprintf(stderr,
                "METALFX_TEMPORAL_PROBE stage=encode-exception name=%s "
                "reason=%s\n",
                exception.name.UTF8String, exception.reason.UTF8String);
        return 6;
    }
    dispatch_semaphore_t completion = dispatch_semaphore_create(0);
    [commandBuffer addCompletedHandler:^(id<MTLCommandBuffer> completed) {
        (void)completed;
        dispatch_semaphore_signal(completion);
    }];
    [commandBuffer commit];
    long waitResult = dispatch_semaphore_wait(
        completion, dispatch_time(DISPATCH_TIME_NOW, 30 * NSEC_PER_SEC));
    fprintf(stderr,
            "METALFX_TEMPORAL_PROBE stage=completion wait=%ld status=%lu "
            "error=%s gpuStart=%.9f gpuEnd=%.9f\n",
            waitResult, (unsigned long)commandBuffer.status,
            commandBuffer.error
                ? commandBuffer.error.description.UTF8String : "(null)",
            commandBuffer.GPUStartTime, commandBuffer.GPUEndTime);
    MacWSDumpCommandBufferError(commandBuffer.error);
    if (waitResult != 0) return 7;
    return commandBuffer.status == MTLCommandBufferStatusCompleted ? 0 : 8;
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        // libmachook intentionally installs its focused IOGPU diagnostic
        // method wrappers from the main queue so AppKit processes do not pay
        // constructor-time thread suspension costs.  This command-line probe
        // otherwise blocks its main thread waiting for GPU completion before
        // that queued installer can run.  Drain the run loop once before the
        // first Metal object so opt-in diagnostic sentinels observe this run.
        [[NSRunLoop mainRunLoop]
            runUntilDate:[NSDate dateWithTimeIntervalSinceNow:0.1]];
        fprintf(stderr,
                "METALFX_TEMPORAL_PROBE stage=main-queue-drained\n");

        const NSUInteger inputWidth = argc > 1
            ? (NSUInteger)strtoul(argv[1], NULL, 10) : 720;
        const NSUInteger inputHeight = argc > 2
            ? (NSUInteger)strtoul(argv[2], NULL, 10) : 450;
        const NSUInteger outputWidth = argc > 3
            ? (NSUInteger)strtoul(argv[3], NULL, 10) : 1440;
        const NSUInteger outputHeight = argc > 4
            ? (NSUInteger)strtoul(argv[4], NULL, 10) : 900;
        const BOOL strayProfile = getenv("MACWS_METALFX_STRAY_PROFILE") &&
            strcmp(getenv("MACWS_METALFX_STRAY_PROFILE"), "0") != 0;
        const NSUInteger contentWidth = argc > 5
            ? (NSUInteger)strtoul(argv[5], NULL, 10) : inputWidth;
        const NSUInteger contentHeight = argc > 6
            ? (NSUInteger)strtoul(argv[6], NULL, 10) : inputHeight;

        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        fprintf(stderr,
                "METALFX_TEMPORAL_PROBE stage=device object=%p class=%s "
                "name=%s\n",
                (__bridge void *)device, MacWSClassName(device),
                device ? device.name.UTF8String : "(nil)");
        if (!device) return 2;

        MacWSInstallNativeSubmitDiagnostic(device);

        BOOL supported = [MTLFXTemporalScalerDescriptor supportsDevice:device];
        fprintf(stderr,
                "METALFX_TEMPORAL_PROBE stage=support supported=%d\n",
                supported);
        if (!supported) return 3;

        MacWSDumpMPSGraphTensorDataMethods();
        MacWSDumpBufferIOSurfaceBehavior(device);

        MTLFXTemporalScalerDescriptor *descriptor =
            [MTLFXTemporalScalerDescriptor new];
        descriptor.colorTextureFormat = strayProfile
            ? (MTLPixelFormat)92 : MTLPixelFormatRGBA16Float;
        descriptor.depthTextureFormat = strayProfile
            ? (MTLPixelFormat)260 : MTLPixelFormatDepth32Float;
        descriptor.motionTextureFormat = MTLPixelFormatRG16Float;
        descriptor.outputTextureFormat = strayProfile
            ? (MTLPixelFormat)92 : MTLPixelFormatRGBA16Float;
        descriptor.inputWidth = inputWidth;
        descriptor.inputHeight = inputHeight;
        descriptor.outputWidth = outputWidth;
        descriptor.outputHeight = outputHeight;
        descriptor.autoExposureEnabled = NO;

        fprintf(stderr,
                "METALFX_TEMPORAL_PROBE stage=create-scaler input=%lux%lu "
                "output=%lux%lu color=%lu depth=%lu motion=%lu outputFormat=%lu "
                "autoExposure=%d profile=%s content=%lux%lu\n",
                (unsigned long)inputWidth, (unsigned long)inputHeight,
                (unsigned long)outputWidth, (unsigned long)outputHeight,
                (unsigned long)descriptor.colorTextureFormat,
                (unsigned long)descriptor.depthTextureFormat,
                (unsigned long)descriptor.motionTextureFormat,
                (unsigned long)descriptor.outputTextureFormat,
                descriptor.isAutoExposureEnabled,
                strayProfile ? "stray" : "baseline",
                (unsigned long)contentWidth, (unsigned long)contentHeight);

        const char *pauseText = getenv("MACWS_METALFX_PROBE_PAUSE_SECONDS");
        unsigned pauseSeconds = pauseText
            ? (unsigned)strtoul(pauseText, NULL, 10) : 0;
        if (pauseSeconds > 0) {
            fprintf(stderr,
                    "METALFX_TEMPORAL_PROBE stage=pause pid=%d seconds=%u\n",
                    getpid(), pauseSeconds);
            sleep(pauseSeconds);
        }

        id<MTLFXTemporalScaler> scaler =
            [descriptor newTemporalScalerWithDevice:device];
        fprintf(stderr,
                "METALFX_TEMPORAL_PROBE stage=scaler object=%p class=%s\n",
                (__bridge void *)scaler, MacWSClassName(scaler));
        if (!scaler) return 4;
        MacWSDumpMPSGraphRunMethods();
        MacWSInstallMPSGraphRunDiagnostic();
        if (!getenv("MACWS_METALFX_PROBE_ENCODE")) return 0;
        const char *frameCountText = getenv("MACWS_METALFX_PROBE_FRAMES");
        NSUInteger frameCount = frameCountText
            ? (NSUInteger)strtoul(frameCountText, NULL, 10) : 1;
        if (frameCount < 1) frameCount = 1;
        if (frameCount > 256) frameCount = 256;
        const char *resetIntervalText = getenv(
            "MACWS_METALFX_PROBE_RESET_INTERVAL");
        NSUInteger resetInterval = resetIntervalText
            ? (NSUInteger)strtoul(resetIntervalText, NULL, 10) : 0;
        if (resetInterval > 256) resetInterval = 256;
        BOOL newQueueEachFrame =
            getenv("MACWS_METALFX_PROBE_NEW_QUEUE_EACH_FRAME") != NULL;
        id<MTLCommandQueue> renderQueue = newQueueEachFrame
            ? nil : [device newCommandQueue];
        fprintf(stderr,
                "METALFX_TEMPORAL_PROBE stage=render-queue object=%p "
                "class=%s mode=%s\n",
                (__bridge void *)renderQueue, MacWSClassName(renderQueue),
                newQueueEachFrame ? "per-frame" : "shared");
        if (!newQueueEachFrame && !renderQueue) return 5;
        for (NSUInteger frame = 0; frame < frameCount; frame++) {
            @autoreleasepool {
                // Match an application render loop: temporary command
                // buffers, MPSGraph result arrays and descriptor objects must
                // not remain autoreleased for the entire multi-frame probe.
                // Without this boundary the probe's compatibility texture
                // pool grows monotonically and confounds event-ring failures
                // with a test-harness lifetime leak.
                fprintf(stderr,
                        "METALFX_TEMPORAL_PROBE stage=frame-begin "
                        "frame=%lu/%lu\n",
                        (unsigned long)(frame + 1),
                        (unsigned long)frameCount);
                int frameResult = MacWSEncodeOneTemporalFrame(
                    device, scaler, renderQueue, inputWidth, inputHeight,
                    contentWidth, contentHeight, outputWidth, outputHeight,
                    strayProfile,
                    frame == 0 ||
                        (resetInterval != 0 && frame % resetInterval == 0));
                fprintf(stderr,
                        "METALFX_TEMPORAL_PROBE stage=frame-end "
                        "frame=%lu/%lu result=%d\n",
                        (unsigned long)(frame + 1),
                        (unsigned long)frameCount, frameResult);
                if (frameResult != 0) return frameResult;
            }
        }
        return 0;
    }
}

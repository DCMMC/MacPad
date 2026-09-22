@import Foundation;
@import Metal;

#import <IOSurface/IOSurfaceRef.h>
#import <mach-o/dyld.h>
#import <objc/message.h>
#import <objc/runtime.h>

#include <dlfcn.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

// The iPhoneOS 16.5 Theos SDK ships IOSurfaceRef.h but omits the public
// IOSurface.h declarations.  These functions are present in the linked
// IOSurface framework and are the same minimal ABI already used by
// Metal_hooks.x in this library.
extern IOSurfaceRef IOSurfaceCreate(CFDictionaryRef properties);
extern void *IOSurfaceGetBaseAddress(IOSurfaceRef surface);
extern size_t IOSurfaceGetAllocSize(IOSurfaceRef surface);
extern int IOSurfaceLock(IOSurfaceRef surface, uint32_t options,
                         uint32_t *seed);
extern int IOSurfaceUnlock(IOSurfaceRef surface, uint32_t options,
                           uint32_t *seed);

// Ventura 13.4's AppleNeuralEngine client and iPadOS 16.3's aned expose the
// same seven _ANEDaemonProtocol selectors, but two reply blocks have different
// wire ABIs. Runtime witnesses from misc/ane_protocol_probe.m:
//
//   compile macOS: v@?B@"NSDictionary"@"NSString"@"NSError"
//   compile iOS:   v@?B@"NSDictionary"@"NSError"
//   load macOS:    v@?B@"NSDictionary"QQc@"NSString"@"NSError"
//   load iOS:      v@?B@"NSDictionary"QQc@"NSError"
//
// Unified logging then records aned rejecting the stock request as an
// "undecodable message (incompatible reply block signature ...)". This
// adapter changes only those two wire signatures and restores the omitted
// cache-URL argument as nil when calling the macOS client. Compilation,
// loading, priority validation, success values, attributes, and NSError all
// still come from the real iPadOS daemon.

static const char *MacWSANEDaemonProtocolName = "_ANEDaemonProtocol";
static NSString *const MacWSANECompileMacReplySignature =
    @"v@?B@\"NSDictionary\"@\"NSString\"@\"NSError\"";
static NSString *const MacWSANECompileIOSReplySignature =
    @"v@?B@\"NSDictionary\"@\"NSError\"";
static NSString *const MacWSANELoadMacReplySignature =
    @"v@?B@\"NSDictionary\"QQc@\"NSString\"@\"NSError\"";
static NSString *const MacWSANELoadIOSReplySignature =
    @"v@?B@\"NSDictionary\"QQc@\"NSError\"";
static const char *MacWSANEOuterMethodEncoding =
    "v52@0:8@16@24@32I40@?44";

typedef void (^MacWSANECompileMacReply)(BOOL, NSDictionary *, NSString *,
                                        NSError *);
typedef void (^MacWSANECompileIOSReply)(BOOL, NSDictionary *, NSError *);
typedef void (^MacWSANELoadMacReply)(BOOL, NSDictionary *, uint64_t, uint64_t,
                                     char, NSString *, NSError *);
typedef void (^MacWSANELoadIOSReply)(BOOL, NSDictionary *, uint64_t, uint64_t,
                                     char, NSError *);

typedef void (*MacWSANECompileMethod)(id, SEL, id, id, id, uint32_t,
                                      MacWSANECompileMacReply);
typedef void (*MacWSANELoadMethod)(id, SEL, id, id, id, uint32_t,
                                   MacWSANELoadMacReply);
typedef id (*MacWSANEInterfaceFactoryMethod)(id, SEL, Protocol *);
typedef id (*MacWSMPSGraphTensorDataBufferInitMethod)(
    id, SEL, id<MTLBuffer>, NSArray *, uint32_t, uint64_t);

@interface NSXPCInterface (MacWSANEReplySignature)
- (NSString *)replyBlockSignatureForSelector:(SEL)selector;
- (void)setReplyBlockSignature:(NSString *)signature
                    forSelector:(SEL)selector;
@end

@interface NSXPCConnection (MacWSANERemoteProxy)
- (id)synchronousRemoteObjectProxyWithErrorHandler:
    (void (^)(NSError *error))handler;
@end

@protocol MacWSANEDaemonIOSWire
- (void)compileModel:(id)model
    sandboxExtension:(id)sandboxExtension
             options:(id)options
                 qos:(uint32_t)qos
           withReply:(MacWSANECompileIOSReply)reply;
- (void)loadModel:(id)model
    sandboxExtension:(id)sandboxExtension
           options:(id)options
               qos:(uint32_t)qos
         withReply:(MacWSANELoadIOSReply)reply;
- (void)unloadModel:(id)model
            options:(id)options
                qos:(uint32_t)qos
          withReply:(void (^)(BOOL, NSError *))reply;
- (void)compiledModelExistsFor:(id)model
                      withReply:(void (^)(BOOL, NSError *))reply;
- (void)purgeCompiledModel:(id)model
                 withReply:(void (^)(BOOL, NSError *))reply;
- (void)compiledModelExistsMatchingHash:(NSString *)modelHash
                               withReply:(void (^)(BOOL, NSError *))reply;
- (void)purgeCompiledModelMatchingHash:(NSString *)modelHash
                              withReply:(void (^)(BOOL, NSError *))reply;
@end

static pthread_mutex_t MacWSANEInstallLock = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t MacWSANEMPSGraphInstallLock =
    PTHREAD_MUTEX_INITIALIZER;
static BOOL MacWSANEInterfaceHookInstalled;
static BOOL MacWSANEConnectionHooksInstalled;
static BOOL MacWSANEMPSGraphBufferHookInstalled;
static Ivar MacWSANEDaemonConnectionIvar;
static Ivar MacWSANEModelURLIvar;
static MacWSANECompileMethod MacWSANEOriginalCompile;
static MacWSANELoadMethod MacWSANEOriginalLoad;
static MacWSANEInterfaceFactoryMethod MacWSANEOriginalInterfaceFactory;
static MacWSMPSGraphTensorDataBufferInitMethod
    MacWSANEOriginalMPSGraphTensorDataBufferInit;
static char MacWSANEWireContextAssociationKey;

static NSString *const MacWSANEWireContextURLKey = @"hostModelURL";
static NSString *const MacWSANEWireContextSandboxKey = @"sandboxExtension";

static BOOL MacWSANEDiagnosticsEnabled(void) {
    const char *value = getenv("MACWS_ANE_PROTOCOL_DIAGNOSTICS");
    return value && strcmp(value, "0") != 0;
}

static void MacWSANELog(NSString *format, ...) {
    if (!MacWSANEDiagnosticsEnabled()) return;
    va_list arguments;
    va_start(arguments, format);
    NSString *message = [[NSString alloc] initWithFormat:format
                                               arguments:arguments];
    va_end(arguments);
    fprintf(stderr, "[MacWSAppleNeuralEngine] %s\n", message.UTF8String);
    fflush(stderr);
#if !__has_feature(objc_arc)
    [message release];
#endif
}

static void MacWSANEDiagnosticReplyPause(const char *stage) {
    const char *pauseText = getenv("MACWS_ANE_REPLY_PAUSE_SECONDS");
    unsigned pauseSeconds = pauseText
        ? (unsigned)strtoul(pauseText, NULL, 10) : 0;
    if (pauseSeconds > 30) pauseSeconds = 30;
    if (!pauseSeconds) return;
    MacWSANELog(@"%s reply diagnostic pause seconds=%u pid=%d",
                stage, pauseSeconds, getpid());
    sleep(pauseSeconds);
}

// Ventura MetalFX V3 constructs its ANE bootstrap tensors in
// makeMPSTensorDataWithData() using -newBufferWithBytes:length:options:, then
// passes that buffer to this MPSGraphTensorData initializer. RE-confirmed at
// MetalFX createMetalBufferRand<half>+0x118 and makeMPSTensorDataWithData+0x160.
//
// The Ventura initializer immediately asks the buffer for -iosurface and saves
// it at MPSGraphTensorData+0x78 (RE-confirmed at
// -initWithMTLBuffer:shape:dataType:rowBytes:+0x15c). iOS AGX returns nil for an
// ordinary buffer, while its real -newBufferWithIOSurface: path round-trips the
// supplied IOSurface. Translate only the short-lived MetalFX factory buffer at
// that semantic boundary. The original initializer still validates and builds
// all tensor state from a real MTLBuffer and a real IOSurface.
static BOOL MacWSANEIsMetalFXTensorFactoryCaller(void *returnAddress,
                                                  Dl_info *infoOut) {
    Dl_info info = {0};
    if (!returnAddress || !dladdr(returnAddress, &info) || !info.dli_fname) {
        return NO;
    }
    if (infoOut) *infoOut = info;
    return strstr(info.dli_fname, "/MetalFX.framework/") != NULL &&
        info.dli_sname != NULL &&
        strstr(info.dli_sname, "makeMPSTensorDataWithData") != NULL;
}

static id MacWSANEMPSGraphTensorDataBufferInit(
    id receiver, SEL selector, id<MTLBuffer> buffer, NSArray *shape,
    uint32_t dataType, uint64_t rowBytes) {
    void *returnAddress = __builtin_return_address(0);
    Dl_info caller = {0};
    BOOL isMetalFXFactory = MacWSANEIsMetalFXTensorFactoryCaller(
        returnAddress, &caller);
    SEL iosurfaceSelector = sel_registerName("iosurface");
    IOSurfaceRef existingSurface = nil;
    if ([buffer respondsToSelector:iosurfaceSelector]) {
        existingSurface = ((IOSurfaceRef (*)(id, SEL))objc_msgSend)(
            buffer, iosurfaceSelector);
    }
    MacWSANELog(@"MPSGraph tensor buffer caller=%s symbol=%s buffer=%p "
                 "length=%llu iosurface=%p metalFXFactory=%d",
                caller.dli_fname ?: "(unknown)",
                caller.dli_sname ?: "(unknown)", buffer,
                (unsigned long long)(buffer ? buffer.length : 0),
                existingSurface, isMetalFXFactory);
    if (!isMetalFXFactory || !buffer || existingSurface ||
        getenv("MACWS_DISABLE_METALFX_ANE_BUFFER_COMPAT")) {
        return MacWSANEOriginalMPSGraphTensorDataBufferInit(
            receiver, selector, buffer, shape, dataType, rowBytes);
    }

    id<MTLDevice> device = buffer.device;
    NSUInteger length = buffer.length;
    void *source = buffer.contents;
    SEL newBufferSelector = sel_registerName("newBufferWithIOSurface:");
    if (!device || !length || !source ||
        ![device respondsToSelector:newBufferSelector]) {
        return MacWSANEOriginalMPSGraphTensorDataBufferInit(
            receiver, selector, buffer, shape, dataType, rowBytes);
    }

    NSDictionary *properties = @{
        @"IOSurfaceAllocSize": @(length),
        @"IOSurfaceBytesPerElement": @1,
        @"IOSurfaceName": @"MacWS MetalFX ANE Tensor",
    };
    IOSurfaceRef surface = IOSurfaceCreate(
        (__bridge CFDictionaryRef)properties);
    id<MTLBuffer> surfaceBuffer = nil;
    BOOL copied = NO;
    if (surface && IOSurfaceLock(surface, 0, NULL) == 0) {
        void *destination = IOSurfaceGetBaseAddress(surface);
        size_t allocationSize = IOSurfaceGetAllocSize(surface);
        if (destination && allocationSize >= length) {
            memcpy(destination, source, length);
            copied = YES;
        }
        IOSurfaceUnlock(surface, 0, NULL);
    }
    if (copied) {
        surfaceBuffer = ((id (*)(id, SEL, IOSurfaceRef))objc_msgSend)(
            device, newBufferSelector, surface);
    }

    id result = nil;
    if (surfaceBuffer && surfaceBuffer.length >= length) {
        result = MacWSANEOriginalMPSGraphTensorDataBufferInit(
            receiver, selector, surfaceBuffer, shape, dataType, rowBytes);
        MacWSANELog(@"MetalFX ANE tensor translated bytes=%llu surface=%p "
                     "buffer=%p tensor=%p",
                    (unsigned long long)length, surface, surfaceBuffer, result);
    } else {
        result = MacWSANEOriginalMPSGraphTensorDataBufferInit(
            receiver, selector, buffer, shape, dataType, rowBytes);
        MacWSANELog(@"MetalFX ANE tensor translation unavailable bytes=%llu "
                     "surface=%p copied=%d buffer=%p",
                    (unsigned long long)length, surface, copied, surfaceBuffer);
    }
#if !__has_feature(objc_arc)
    [surfaceBuffer release];
#endif
    if (surface) CFRelease(surface);
    return result;
}

static BOOL MacWSANEInstallMPSGraphBufferHook(void) {
    if (getenv("MACWS_DISABLE_METALFX_ANE_BUFFER_COMPAT")) return NO;
    pthread_mutex_lock(&MacWSANEMPSGraphInstallLock);
    if (MacWSANEMPSGraphBufferHookInstalled) {
        pthread_mutex_unlock(&MacWSANEMPSGraphInstallLock);
        return YES;
    }
    Class tensorDataClass = objc_getClass("MPSGraphTensorData");
    SEL selector = sel_registerName(
        "initWithMTLBuffer:shape:dataType:rowBytes:");
    Method method = tensorDataClass
        ? class_getInstanceMethod(tensorDataClass, selector) : NULL;
    const char *types = method ? method_getTypeEncoding(method) : NULL;
    if (!method || !types || strcmp(types, "@44@0:8@16@24I32Q36") != 0) {
        pthread_mutex_unlock(&MacWSANEMPSGraphInstallLock);
        return NO;
    }
    MacWSANEOriginalMPSGraphTensorDataBufferInit =
        (MacWSMPSGraphTensorDataBufferInitMethod)method_setImplementation(
            method, (IMP)MacWSANEMPSGraphTensorDataBufferInit);
    MacWSANEMPSGraphBufferHookInstalled =
        MacWSANEOriginalMPSGraphTensorDataBufferInit != NULL;
    MacWSANELog(@"MPSGraph MetalFX ANE buffer hook installed=%d class=%s",
                MacWSANEMPSGraphBufferHookInstalled,
                class_getName(tensorDataClass));
    pthread_mutex_unlock(&MacWSANEMPSGraphInstallLock);
    return MacWSANEMPSGraphBufferHookInstalled;
}

// RE-confirmed in Ventura AppleNeuralEngine at
// -[_ANEDaemonConnection compileModel:...]+0x6c..+0x9c and
// -loadModel:...+0x6c..+0x9c: real-time QoS is rejected unless the connection
// is restricted. Let the original implementation produce that stock error;
// it does not contact aned on this branch, so no wire translation is needed.
static BOOL MacWSANEMustUseOriginalPriorityFailure(id connection,
                                                    uint32_t qos) {
    Class mapper = objc_getClass("_ANEQoSMapper");
    SEL prioritySelector = sel_registerName("programPriorityForQoS:");
    SEL realTimeSelector = sel_registerName("realTimeProgramPriority");
    SEL restrictedSelector = sel_registerName("restricted");
    if (!mapper || ![mapper respondsToSelector:prioritySelector] ||
        ![mapper respondsToSelector:realTimeSelector] ||
        ![connection respondsToSelector:restrictedSelector]) {
        // Installation checks these dependencies. Preserve stock behavior if
        // a future runtime changes them after installation.
        return YES;
    }
    uint32_t priority = ((uint32_t (*)(id, SEL, uint32_t))objc_msgSend)(
        mapper, prioritySelector, qos);
    uint32_t realTime = ((uint32_t (*)(id, SEL))objc_msgSend)(
        mapper, realTimeSelector);
    BOOL restricted = ((BOOL (*)(id, SEL))objc_msgSend)(
        connection, restrictedSelector);
    return priority == realTime && !restricted;
}

static NSXPCConnection *MacWSANEDaemonConnection(id connection) {
    if (!MacWSANEDaemonConnectionIvar) return nil;
    return object_getIvar(connection, MacWSANEDaemonConnectionIvar);
}

// A sandbox extension issued inside the chroot already contains the real
// iPadOS vnode path as its final field. AppleNeuralEngine nevertheless encodes
// _ANEModel.modelURL using the chroot-visible /var/folders path. Runtime A/B:
// aned rejected that encoding with InvalidNetworkSourceFileName, while an
// iOS-native _ANEClient compiled the exact same plist/weights successfully
// when its model URL named the token's /private/var/mnt/rootfs path. Preserve
// the original model for MPSGraph and translate only the object sent over XPC.
static NSURL *MacWSANEHostModelURL(id model, id sandboxExtension) {
    if (!model || !MacWSANEModelURLIvar ||
        ![sandboxExtension isKindOfClass:[NSString class]] ||
        ![model respondsToSelector:sel_registerName("modelURL")]) {
        return nil;
    }
    NSString *sandboxToken = sandboxExtension;
    NSRange finalSeparator = [sandboxToken rangeOfString:@";"
        options:NSBackwardsSearch];
    if (finalSeparator.location == NSNotFound ||
        NSMaxRange(finalSeparator) >= sandboxToken.length) {
        return nil;
    }
    NSString *hostModelDirectory =
        [sandboxToken substringFromIndex:NSMaxRange(finalSeparator)];
    if (![hostModelDirectory hasPrefix:@"/private/var/mnt/rootfs/"]) {
        return nil;
    }
    NSURL *sourceURL = ((id (*)(id, SEL))objc_msgSend)(
        model, sel_registerName("modelURL"));
    if (![sourceURL isKindOfClass:[NSURL class]] || !sourceURL.isFileURL ||
        ![sourceURL.lastPathComponent
            isEqualToString:hostModelDirectory.lastPathComponent]) {
        return nil;
    }

    return [NSURL fileURLWithPath:hostModelDirectory isDirectory:YES];
}

static id MacWSANECreateWireModel(id model, NSURL *hostURL) {
    SEL keySelector = sel_registerName("key");
    SEL attributesSelector = sel_registerName("modelAttributes");
    SEL factorySelector = sel_registerName("modelAtURL:key:modelAttributes:");
    Class modelClass = model ? object_getClass(model) : Nil;
    if (!model || ![hostURL isKindOfClass:[NSURL class]] ||
        ![model respondsToSelector:keySelector] ||
        ![model respondsToSelector:attributesSelector] ||
        ![modelClass respondsToSelector:factorySelector]) {
        return nil;
    }

    id key = ((id (*)(id, SEL))objc_msgSend)(model, keySelector);
    id attributes = ((id (*)(id, SEL))objc_msgSend)(model,
                                                    attributesSelector);
    id wireModel = ((id (*)(id, SEL, id, id, id))objc_msgSend)(
        modelClass, factorySelector, hostURL, key, attributes);
    if (!wireModel) return nil;
#if !__has_feature(objc_arc)
    [wireModel retain];
#endif
    MacWSANELog(@"wire model=%p hostURL=%@ key=%@ attributes=%@",
                wireModel, hostURL, key, attributes);
    return wireModel;
}

static NSDictionary *MacWSANEWireContext(id model) {
    id context = model ? objc_getAssociatedObject(
        model, &MacWSANEWireContextAssociationKey) : nil;
    return [context isKindOfClass:[NSDictionary class]] ? context : nil;
}

static void MacWSANERememberWireContext(id model, NSURL *hostURL,
                                        id sandboxExtension) {
    if (!model || !hostURL || !sandboxExtension) return;
    NSDictionary *context = @{
        MacWSANEWireContextURLKey: hostURL,
        MacWSANEWireContextSandboxKey: sandboxExtension,
    };
    objc_setAssociatedObject(model, &MacWSANEWireContextAssociationKey,
                             context, OBJC_ASSOCIATION_RETAIN_NONATOMIC);
}

static void MacWSANECompile(id connection, SEL selector, id model,
                            id sandboxExtension, id options, uint32_t qos,
                            MacWSANECompileMacReply reply) {
    if (MacWSANEMustUseOriginalPriorityFailure(connection, qos)) {
        MacWSANEOriginalCompile(connection, selector, model, sandboxExtension,
                                options, qos, reply);
        return;
    }

    NSXPCConnection *daemonConnection =
        MacWSANEDaemonConnection(connection);
    if (!daemonConnection) {
        // Do not fabricate a daemon result if the private object's invariant
        // is not satisfied on a different runtime.
        MacWSANEOriginalCompile(connection, selector, model, sandboxExtension,
                                options, qos, reply);
        return;
    }

    NSMutableDictionary *translatedOptions = nil;
    id wireOptions = options;
    if ([options isKindOfClass:[NSDictionary class]] &&
        [(NSDictionary *)options
            objectForKey:@"kANEFModelHasCacheURLIdentifierKey"]) {
        translatedOptions = [(NSDictionary *)options mutableCopy];
        [translatedOptions
            removeObjectForKey:@"kANEFModelHasCacheURLIdentifierKey"];
        wireOptions = translatedOptions;
    }
    NSURL *hostModelURL = MacWSANEHostModelURL(model, sandboxExtension);
    id wireModel = MacWSANECreateWireModel(model, hostModelURL);
    id modelForWire = wireModel ?: model;
    if (wireModel) {
        // iOS 16's native client presents the same model path sandbox extension
        // to aned for compile and load. Ventura passes nil on load because it
        // expects the newer cache-URL protocol. Retain the daemon-issued token
        // with the original model so the translated load can preserve iOS 16's
        // real protocol invariant without synthesizing sandbox authority.
        MacWSANERememberWireContext(model, hostModelURL, sandboxExtension);
    }

    id<MacWSANEDaemonIOSWire> proxy =
        [daemonConnection synchronousRemoteObjectProxyWithErrorHandler:
            ^(NSError *error) {
                MacWSANELog(@"compile transport error=%@", error);
            }];
    MacWSANELog(@"compile request model=%@ modelClass=%s sandbox=%@ "
                 "wireModel=%@ options=%@ wireOptions=%@ qos=%u "
                 "cacheOptionRemoved=%d",
                model, model ? object_getClassName(model) : "(nil)",
                sandboxExtension, modelForWire, options, wireOptions, qos,
                translatedOptions != nil);
    MacWSANECompileIOSReply translatedReply =
        ^(BOOL success, NSDictionary *attributes, NSError *error) {
            MacWSANELog(@"compile reply success=%d attributes=%@ error=%@",
                        success, attributes, error);
            MacWSANEDiagnosticReplyPause("compile");
            if (reply) reply(success, attributes, nil, error);
        };
    [proxy compileModel:modelForWire
        sandboxExtension:sandboxExtension
                 options:wireOptions
                     qos:qos
               withReply:translatedReply];
#if !__has_feature(objc_arc)
    [wireModel release];
    [translatedOptions release];
#endif
}

static void MacWSANELoad(id connection, SEL selector, id model,
                         id sandboxExtension, id options, uint32_t qos,
                         MacWSANELoadMacReply reply) {
    if (MacWSANEMustUseOriginalPriorityFailure(connection, qos)) {
        MacWSANEOriginalLoad(connection, selector, model, sandboxExtension,
                             options, qos, reply);
        return;
    }

    NSXPCConnection *daemonConnection =
        MacWSANEDaemonConnection(connection);
    if (!daemonConnection) {
        MacWSANEOriginalLoad(connection, selector, model, sandboxExtension,
                             options, qos, reply);
        return;
    }

    NSMutableDictionary *translatedOptions = nil;
    id wireOptions = options;
    if ([options isKindOfClass:[NSDictionary class]] &&
        [(NSDictionary *)options
            objectForKey:@"kANEFModelHasCacheURLIdentifierKey"]) {
        translatedOptions = [(NSDictionary *)options mutableCopy];
        [translatedOptions
            removeObjectForKey:@"kANEFModelHasCacheURLIdentifierKey"];
        wireOptions = translatedOptions;
    }
    NSDictionary *wireContext = MacWSANEWireContext(model);
    id wireSandboxExtension = sandboxExtension ?:
        [wireContext objectForKey:MacWSANEWireContextSandboxKey];
    NSURL *hostModelURL = MacWSANEHostModelURL(model, wireSandboxExtension);
    if (!hostModelURL) {
        id rememberedURL = [wireContext objectForKey:MacWSANEWireContextURLKey];
        if ([rememberedURL isKindOfClass:[NSURL class]]) {
            hostModelURL = rememberedURL;
        }
    }
    id wireModel = MacWSANECreateWireModel(model, hostModelURL);
    id modelForWire = wireModel ?: model;

    id<MacWSANEDaemonIOSWire> proxy =
        [daemonConnection synchronousRemoteObjectProxyWithErrorHandler:
            ^(NSError *error) {
                MacWSANELog(@"load transport error=%@", error);
            }];
    MacWSANELog(@"load request model=%@ modelClass=%s sandbox=%@ "
                 "wireSandbox=%@ "
                 "wireModel=%@ options=%@ wireOptions=%@ qos=%u "
                 "cacheOptionRemoved=%d",
                model, model ? object_getClassName(model) : "(nil)",
                sandboxExtension, wireSandboxExtension, modelForWire, options,
                wireOptions, qos, translatedOptions != nil);
    MacWSANELoadIOSReply translatedReply =
        ^(BOOL success, NSDictionary *attributes, uint64_t programHandle,
          uint64_t intermediateBufferHandle, char queueDepth,
          NSError *error) {
            MacWSANELog(@"load reply success=%d attributes=%@ program=%llu "
                         "intermediate=%llu queueDepth=%d error=%@",
                        success, attributes,
                        (unsigned long long)programHandle,
                        (unsigned long long)intermediateBufferHandle,
                        (int)queueDepth, error);
            MacWSANEDiagnosticReplyPause("load");
            if (reply) {
                reply(success, attributes, programHandle,
                      intermediateBufferHandle, queueDepth, nil, error);
            }
        };
    [proxy loadModel:modelForWire
        sandboxExtension:wireSandboxExtension
                 options:wireOptions
                     qos:qos
               withReply:translatedReply];
#if !__has_feature(objc_arc)
    [wireModel release];
    [translatedOptions release];
#endif
}

static BOOL MacWSANEInstallConnectionHooks(void) {
    pthread_mutex_lock(&MacWSANEInstallLock);
    if (MacWSANEConnectionHooksInstalled) {
        pthread_mutex_unlock(&MacWSANEInstallLock);
        return YES;
    }

    Class connectionClass = objc_getClass("_ANEDaemonConnection");
    Class mapperClass = objc_getClass("_ANEQoSMapper");
    SEL compileSelector = sel_registerName(
        "compileModel:sandboxExtension:options:qos:withReply:");
    SEL loadSelector = sel_registerName(
        "loadModel:sandboxExtension:options:qos:withReply:");
    Method compileMethod = connectionClass
        ? class_getInstanceMethod(connectionClass, compileSelector) : NULL;
    Method loadMethod = connectionClass
        ? class_getInstanceMethod(connectionClass, loadSelector) : NULL;
    Ivar daemonConnectionIvar = connectionClass
        ? class_getInstanceVariable(connectionClass, "_daemonConnection")
        : NULL;
    Class modelClass = objc_getClass("_ANEModel");
    Ivar modelURLIvar = modelClass
        ? class_getInstanceVariable(modelClass, "_modelURL") : NULL;
    const char *compileTypes = compileMethod
        ? method_getTypeEncoding(compileMethod) : NULL;
    const char *loadTypes = loadMethod
        ? method_getTypeEncoding(loadMethod) : NULL;
    BOOL prerequisitesMatch = connectionClass && mapperClass && modelClass &&
        compileMethod && loadMethod && daemonConnectionIvar && modelURLIvar &&
        ivar_getTypeEncoding(modelURLIvar) &&
        strcmp(ivar_getTypeEncoding(modelURLIvar), "@\"NSURL\"") == 0 &&
        compileTypes && loadTypes &&
        strcmp(compileTypes, MacWSANEOuterMethodEncoding) == 0 &&
        strcmp(loadTypes, MacWSANEOuterMethodEncoding) == 0;
    if (!prerequisitesMatch) {
        MacWSANELog(@"connection hook skipped class=%p mapper=%p "
                     "compile=%s load=%s ivar=%p",
                    connectionClass, mapperClass,
                    compileTypes ?: "(nil)", loadTypes ?: "(nil)",
                    daemonConnectionIvar);
        pthread_mutex_unlock(&MacWSANEInstallLock);
        return NO;
    }

    MacWSANEDaemonConnectionIvar = daemonConnectionIvar;
    MacWSANEModelURLIvar = modelURLIvar;
    MacWSANEOriginalCompile = (MacWSANECompileMethod)
        method_setImplementation(compileMethod, (IMP)MacWSANECompile);
    MacWSANEOriginalLoad = (MacWSANELoadMethod)
        method_setImplementation(loadMethod, (IMP)MacWSANELoad);
    MacWSANEConnectionHooksInstalled =
        MacWSANEOriginalCompile != NULL && MacWSANEOriginalLoad != NULL;
    MacWSANELog(@"connection hooks installed=%d class=%s ivarOffset=%td",
                MacWSANEConnectionHooksInstalled,
                class_getName(connectionClass),
                ivar_getOffset(daemonConnectionIvar));
    pthread_mutex_unlock(&MacWSANEInstallLock);
    return MacWSANEConnectionHooksInstalled;
}

static id MacWSANEInterfaceWithProtocol(id receiver, SEL selector,
                                        Protocol *protocol) {
    id interface = MacWSANEOriginalInterfaceFactory(
        receiver, selector, protocol);
    const char *protocolName = protocol ? protocol_getName(protocol) : NULL;
    if (!interface || !protocolName ||
        strcmp(protocolName, MacWSANEDaemonProtocolName) != 0) {
        return interface;
    }

    SEL compileSelector = sel_registerName(
        "compileModel:sandboxExtension:options:qos:withReply:");
    SEL loadSelector = sel_registerName(
        "loadModel:sandboxExtension:options:qos:withReply:");
    NSString *compileSignature =
        [interface replyBlockSignatureForSelector:compileSelector];
    NSString *loadSignature =
        [interface replyBlockSignatureForSelector:loadSelector];
    if (![compileSignature isEqualToString:MacWSANECompileMacReplySignature] ||
        ![loadSignature isEqualToString:MacWSANELoadMacReplySignature]) {
        MacWSANELog(@"wire translation skipped compile=%@ load=%@",
                    compileSignature, loadSignature);
        return interface;
    }
    if (!MacWSANEInstallConnectionHooks()) return interface;

    // NSXPCInterface's private setter is a no-op once protocol metadata has
    // populated _methodInfo (runtime-confirmed by ane_protocol_probe). Ask the
    // original factory for a clang-emitted shadow protocol instead; clang's
    // extended block type metadata describes the real iOS 16 reply ABI while
    // preserving the complete seven-selector surface.
    id wireInterface = MacWSANEOriginalInterfaceFactory(
        receiver, selector, @protocol(MacWSANEDaemonIOSWire));
    NSString *wireCompile =
        [wireInterface replyBlockSignatureForSelector:compileSelector];
    NSString *wireLoad =
        [wireInterface replyBlockSignatureForSelector:loadSelector];
    if (![wireCompile isEqualToString:MacWSANECompileIOSReplySignature] ||
        ![wireLoad isEqualToString:MacWSANELoadIOSReplySignature]) {
        MacWSANELog(@"shadow protocol rejected compile=%@ load=%@",
                    wireCompile, wireLoad);
        return interface;
    }
    MacWSANELog(@"translated protocol=%s compile=%@ load=%@",
                protocolName, wireCompile, wireLoad);
    return wireInterface;
}

static BOOL MacWSANEInstallInterfaceFactoryHook(void) {
    pthread_mutex_lock(&MacWSANEInstallLock);
    if (MacWSANEInterfaceHookInstalled) {
        pthread_mutex_unlock(&MacWSANEInstallLock);
        return YES;
    }
    Class interfaceClass = objc_getClass("NSXPCInterface");
    SEL selector = sel_registerName("interfaceWithProtocol:");
    Method method = interfaceClass
        ? class_getClassMethod(interfaceClass, selector) : NULL;
    const char *types = method ? method_getTypeEncoding(method) : NULL;
    if (!method || !types || strcmp(types, "@24@0:8@16") != 0) {
        pthread_mutex_unlock(&MacWSANEInstallLock);
        return NO;
    }
    MacWSANEOriginalInterfaceFactory = (MacWSANEInterfaceFactoryMethod)
        method_setImplementation(method, (IMP)MacWSANEInterfaceWithProtocol);
    MacWSANEInterfaceHookInstalled =
        MacWSANEOriginalInterfaceFactory != NULL;
    pthread_mutex_unlock(&MacWSANEInstallLock);
    return MacWSANEInterfaceHookInstalled;
}

static void MacWSANEImageLoaded(const struct mach_header *header,
                                intptr_t slide) {
    (void)header;
    (void)slide;
    // Foundation may be registered after an inserted dylib's constructor in a
    // small command-line process. Retry until NSXPCInterface is real; after a
    // successful installation this is only an atomic-looking boolean read
    // under an uncontended mutex.
    (void)MacWSANEInstallInterfaceFactoryHook();
    (void)MacWSANEInstallMPSGraphBufferHook();
}

__attribute__((constructor))
static void MacWSInstallAppleNeuralEngineCompatibility(void) {
    if (getenv("MACWS_DISABLE_ANE_PROTOCOL_COMPAT")) return;
    _dyld_register_func_for_add_image(MacWSANEImageLoaded);
    (void)MacWSANEInstallInterfaceFactoryHook();
    (void)MacWSANEInstallMPSGraphBufferHook();
}

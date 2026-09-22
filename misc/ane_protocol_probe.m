// Compare the AppleNeuralEngine NSXPC protocol contract in the macOS chroot
// and the native iPadOS runtime.  Build the same source for each platform and
// compare the emitted method/type descriptions; it performs no ANE operation.

@import Foundation;

#import <objc/message.h>
#import <objc/runtime.h>
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

@protocol MacWSANEDaemonIOS16ProtocolProbe
- (void)compileModel:(id)model
    sandboxExtension:(id)sandboxExtension
             options:(id)options
                 qos:(uint32_t)qos
           withReply:(void (^)(BOOL, NSDictionary *, NSError *))reply;
- (void)loadModel:(id)model
    sandboxExtension:(id)sandboxExtension
           options:(id)options
               qos:(uint32_t)qos
         withReply:(void (^)(BOOL, NSDictionary *, uint64_t, uint64_t, char,
                             NSError *))reply;
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

static void MacWSPrintMethod(Protocol *protocol, SEL selector) {
    for (int required = 0; required <= 1; required++) {
        struct objc_method_description desc = protocol_getMethodDescription(
            protocol, selector, required, YES);
        if (desc.name) {
            fprintf(stderr,
                    "ANE_PROTOCOL_PROBE protocol=%s selector=%s required=%d "
                    "types=%s\n",
                    protocol_getName(protocol), sel_getName(desc.name),
                    required, desc.types ? desc.types : "(nil)");
        }
    }
}

static void MacWSPrintIvars(Class cls) {
    unsigned count = 0;
    Ivar *ivars = class_copyIvarList(cls, &count);
    for (unsigned index = 0; index < count; index++) {
        fprintf(stderr,
                "ANE_PROTOCOL_PROBE class=%s ivar=%s offset=%td types=%s\n",
                class_getName(cls), ivar_getName(ivars[index]),
                ivar_getOffset(ivars[index]), ivar_getTypeEncoding(ivars[index]));
    }
    free(ivars);
}

static void MacWSPrintRelevantMethods(Class cls) {
    unsigned count = 0;
    Method *methods = class_copyMethodList(cls, &count);
    for (unsigned index = 0; index < count; index++) {
        const char *name = sel_getName(method_getName(methods[index]));
        if (strstr(name, "ignature") || strstr(name, "eply") ||
            strstr(name, "elector") || strstr(name, "ethod")) {
            fprintf(stderr,
                    "ANE_PROTOCOL_PROBE class=%s method=%s types=%s\n",
                    class_getName(cls), name,
                    method_getTypeEncoding(methods[index]));
        }
    }
    free(methods);
}

static void MacWSProbeShadowProtocol(Class interfaceClass) {
    const char *methods[] = {
        "compileModel:sandboxExtension:options:qos:withReply:",
        "loadModel:sandboxExtension:options:qos:withReply:",
        "unloadModel:options:qos:withReply:",
        "compiledModelExistsFor:withReply:",
        "purgeCompiledModel:withReply:",
        "compiledModelExistsMatchingHash:withReply:",
        "purgeCompiledModelMatchingHash:withReply:",
    };
    // A protocol created with objc_allocateProtocol lacks clang's extended
    // block-signature metadata and Foundation rejects it. This compile-time
    // protocol is the supported way to provide that metadata.
    Protocol *shadow = @protocol(MacWSANEDaemonIOS16ProtocolProbe);
    NSXPCInterface *shadowInterface = shadow
        ? [NSXPCInterface interfaceWithProtocol:shadow] : nil;
    SEL replySignatureSelector =
        sel_registerName("replyBlockSignatureForSelector:");
    fprintf(stderr,
            "ANE_PROTOCOL_PROBE stage=shadow protocol=%p interface=%p\n",
            shadow, (__bridge void *)shadowInterface);
    for (NSUInteger index = 0;
         shadowInterface && index < sizeof(methods) / sizeof(methods[0]);
         index++) {
        SEL selector = sel_registerName(methods[index]);
        id signature = ((id (*)(id, SEL, SEL))objc_msgSend)(
            shadowInterface, replySignatureSelector, selector);
        fprintf(stderr,
                "ANE_PROTOCOL_PROBE stage=shadow-reply selector=%s "
                "signature=%s\n",
                methods[index],
                signature ? [[signature description] UTF8String] : "(nil)");
    }
    (void)interfaceClass;
}

int main(void) {
    @autoreleasepool {
        const char *framework =
            "/System/Library/PrivateFrameworks/AppleNeuralEngine.framework/"
            "AppleNeuralEngine";
        void *handle = dlopen(framework, RTLD_NOW | RTLD_LOCAL);
        fprintf(stderr,
                "ANE_PROTOCOL_PROBE stage=load handle=%p error=%s\n",
                handle, handle ? "(nil)" : dlerror());
        if (!handle) return 2;

        SEL selector = sel_registerName(
            "compileModel:sandboxExtension:options:qos:withReply:");
        Protocol *protocol = objc_getProtocol("_ANEDaemonProtocol");
        fprintf(stderr,
                "ANE_PROTOCOL_PROBE stage=protocol object=%p name=%s\n",
                protocol, protocol ? protocol_getName(protocol) : "(nil)");
        if (!protocol) return 3;
        MacWSPrintMethod(protocol, selector);

        Class connectionClass = objc_getClass("_ANEDaemonConnection");
        Method connectionMethod = connectionClass
            ? class_getInstanceMethod(connectionClass, selector) : NULL;
        fprintf(stderr,
                "ANE_PROTOCOL_PROBE class=%s selector=%s types=%s\n",
                connectionClass ? class_getName(connectionClass) : "(nil)",
                sel_getName(selector),
                connectionMethod ? method_getTypeEncoding(connectionMethod)
                                 : "(nil)");
        if (connectionClass) {
            MacWSPrintIvars(connectionClass);
        }
        Class modelClass = objc_getClass("_ANEModel");
        if (modelClass) {
            MacWSPrintIvars(modelClass);
        }

        Class interfaceClass = objc_getClass("NSXPCInterface");
        fprintf(stderr,
                "ANE_PROTOCOL_PROBE stage=interface-class object=%p name=%s\n",
                interfaceClass,
                interfaceClass ? class_getName(interfaceClass) : "(nil)");
        if (interfaceClass) {
            MacWSPrintIvars(interfaceClass);
            MacWSPrintRelevantMethods(interfaceClass);
        }

        NSXPCInterface *interface =
            [NSXPCInterface interfaceWithProtocol:protocol];
        fprintf(stderr,
                "ANE_PROTOCOL_PROBE stage=interface object=%p class=%s "
                "description=%s\n",
                (__bridge void *)interface,
                interface ? object_getClassName(interface) : "(nil)",
                interface ? interface.description.UTF8String : "(nil)");
        if (interface && interfaceClass) {
            SEL replySignatureSelector =
                sel_registerName("replyBlockSignatureForSelector:");
            unsigned protocolMethodCount = 0;
            struct objc_method_description *protocolMethods =
                protocol_copyMethodDescriptionList(
                    protocol, YES, YES, &protocolMethodCount);
            for (unsigned index = 0; index < protocolMethodCount; index++) {
                SEL methodSelector = protocolMethods[index].name;
                id methodReplySignature =
                    ((id (*)(id, SEL, SEL))objc_msgSend)(
                        interface, replySignatureSelector, methodSelector);
                fprintf(stderr,
                        "ANE_PROTOCOL_PROBE stage=protocol-reply "
                        "selector=%s signature=%s\n",
                        sel_getName(methodSelector),
                        methodReplySignature
                            ? [[methodReplySignature description] UTF8String]
                            : "(nil)");
            }
            free(protocolMethods);
            id replySignature = ((id (*)(id, SEL, SEL))objc_msgSend)(
                interface, replySignatureSelector, selector);
            fprintf(stderr,
                    "ANE_PROTOCOL_PROBE stage=reply-signature object=%p "
                    "class=%s description=%s\n",
                    (__bridge void *)replySignature,
                    replySignature ? object_getClassName(replySignature)
                                   : "(nil)",
                    replySignature
                        ? [[replySignature description] UTF8String] : "(nil)");

            // Exercise Foundation's private setter on this disposable
            // interface so the production adapter can use the API's actual
            // contract instead of mutating NSXPCInterface storage by offset.
            SEL setReplySignatureSelector = sel_registerName(
                "setReplyBlockSignature:forSelector:");
            NSString *iOSCompileSignature =
                @"v@?B@\"NSDictionary\"@\"NSError\"";
            ((void (*)(id, SEL, id, SEL))objc_msgSend)(
                interface, setReplySignatureSelector,
                iOSCompileSignature, selector);
            id replyAfterString = ((id (*)(id, SEL, SEL))objc_msgSend)(
                interface, replySignatureSelector, selector);
            fprintf(stderr,
                    "ANE_PROTOCOL_PROBE stage=reply-setter-string "
                    "description=%s\n",
                    replyAfterString
                        ? [[replyAfterString description] UTF8String] : "(nil)");
            NSMethodSignature *iOSCompileMethod =
                [NSMethodSignature signatureWithObjCTypes:
                    iOSCompileSignature.UTF8String];
            ((void (*)(id, SEL, id, SEL))objc_msgSend)(
                interface, setReplySignatureSelector,
                iOSCompileMethod, selector);
            id replyAfterMethod = ((id (*)(id, SEL, SEL))objc_msgSend)(
                interface, replySignatureSelector, selector);
            fprintf(stderr,
                    "ANE_PROTOCOL_PROBE stage=reply-setter-method "
                    "description=%s\n",
                    replyAfterMethod
                        ? [[replyAfterMethod description] UTF8String] : "(nil)");

            SEL replyMethodSelector = sel_registerName(
                "_methodSignatureForReplyBlockOfSelector:");
            NSMethodSignature *replyMethod =
                ((id (*)(id, SEL, SEL))objc_msgSend)(
                    interface, replyMethodSelector, selector);
            fprintf(stderr,
                    "ANE_PROTOCOL_PROBE stage=reply-method object=%p "
                    "class=%s args=%lu return=%s description=%s\n",
                    (__bridge void *)replyMethod,
                    replyMethod ? object_getClassName(replyMethod) : "(nil)",
                    (unsigned long)(replyMethod
                        ? replyMethod.numberOfArguments : 0),
                    replyMethod ? replyMethod.methodReturnType : "(nil)",
                    replyMethod ? replyMethod.description.UTF8String : "(nil)");
            if (replyMethod) {
                for (NSUInteger index = 0;
                     index < replyMethod.numberOfArguments; index++) {
                    fprintf(stderr,
                            "ANE_PROTOCOL_PROBE stage=reply-argument "
                            "index=%lu types=%s\n",
                            (unsigned long)index,
                            [replyMethod getArgumentTypeAtIndex:index]);
                }
            }

            Ivar methodInfoIvar =
                class_getInstanceVariable(interfaceClass, "_methodInfo");
            if (methodInfoIvar) {
                ptrdiff_t offset = ivar_getOffset(methodInfoIvar);
                CFDictionaryRef methodInfo = *(CFDictionaryRef *)(
                    (uint8_t *)(__bridge void *)interface + offset);
                fprintf(stderr,
                        "ANE_PROTOCOL_PROBE stage=method-info object=%p "
                        "count=%ld\n",
                        methodInfo,
                        methodInfo ? (long)CFDictionaryGetCount(methodInfo) : 0L);
                if (methodInfo) CFShow(methodInfo);
            }
        }
        MacWSProbeShadowProtocol(interfaceClass);
        const char *pauseText = getenv("MACWS_ANE_PROTOCOL_PAUSE_SECONDS");
        unsigned pauseSeconds = pauseText
            ? (unsigned)strtoul(pauseText, NULL, 10) : 0;
        if (pauseSeconds > 0) {
            fprintf(stderr,
                    "ANE_PROTOCOL_PROBE stage=pause pid=%d seconds=%u\n",
                    getpid(), pauseSeconds);
            sleep(pauseSeconds);
        }
        return 0;
    }
}

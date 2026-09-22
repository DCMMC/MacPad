// iOS-native AppleNeuralEngine compiler witness for an already-generated
// MPSGraph ANECIR directory. It lets aned judge the exact files independently
// of the macOS client's XPC/path compatibility layer.

@import Foundation;

#import <objc/message.h>
#import <objc/runtime.h>

#include <dlfcn.h>
#include <stdio.h>

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc != 2) {
            fprintf(stderr, "usage: ane_compile_probe MODEL_DIRECTORY\n");
            return 64;
        }
        const char *framework =
            "/System/Library/PrivateFrameworks/AppleNeuralEngine.framework/"
            "AppleNeuralEngine";
        void *handle = dlopen(framework, RTLD_NOW | RTLD_LOCAL);
        fprintf(stderr, "ANE_COMPILE_PROBE stage=load handle=%p error=%s\n",
                handle, handle ? "(nil)" : dlerror());
        if (!handle) return 2;

        Class modelClass = objc_getClass("_ANEModel");
        Class clientClass = objc_getClass("_ANEClient");
        NSURL *modelURL = [NSURL fileURLWithPath:
            [NSString stringWithUTF8String:argv[1]] isDirectory:YES];
        SEL modelSelector = sel_registerName("modelAtURL:key:");
        id model = modelClass
            ? ((id (*)(id, SEL, id, id))objc_msgSend)(
                modelClass, modelSelector, modelURL, @"main_A14_region_0")
            : nil;
        id client = clientClass
            ? ((id (*)(id, SEL))objc_msgSend)(
                clientClass, sel_registerName("sharedConnection"))
            : nil;
        fprintf(stderr,
                "ANE_COMPILE_PROBE stage=objects model=%p/%s value=%s "
                "client=%p/%s\n",
                (__bridge void *)model,
                model ? object_getClassName(model) : "(nil)",
                model ? [[model description] UTF8String] : "(nil)",
                (__bridge void *)client,
                client ? object_getClassName(client) : "(nil)");
        if (!model || !client) return 3;

        NSDictionary *options = @{
            @"kANEFCompilerOptionsFilenameKey":
                @"MPSGraph_ANE_compilerOptions.plist",
            @"kANEFDisableIOFencesUseSharedEventsKey": @YES,
            @"kANEFEnableLateLatchKey": @YES,
            @"kANEFModelType": @"kANEFModelANECIR",
            @"kANEFNetPlistFilenameKey": @"main_A14_region_0.plist",
        };
        NSError *error = nil;
        BOOL success = ((BOOL (*)(id, SEL, id, id, uint32_t, NSError **))
            objc_msgSend)(
                client,
                sel_registerName("compileModel:options:qos:error:"),
                model, options, 33, &error);
        fprintf(stderr,
                "ANE_COMPILE_PROBE stage=compile success=%d error=%s model=%s\n",
                success, error ? error.description.UTF8String : "(nil)",
                [[model description] UTF8String]);
        return success ? 0 : 4;
    }
}

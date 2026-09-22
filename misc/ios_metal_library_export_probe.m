#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <objc/message.h>
#import <objc/runtime.h>

#include <stdio.h>

static BOOL InterestingName(const char *name) {
    if (!name) return NO;
    static const char *needles[] = {
        "data", "Data", "serial", "Serial", "archive", "Archive",
        "content", "Content", "binary", "Binary", "container",
        "Container", "cache", "Cache", "dispatch", "Dispatch",
        "library", "Library"
    };
    for (size_t index = 0; index < sizeof(needles) / sizeof(needles[0]);
         index++) {
        if (strstr(name, needles[index])) return YES;
    }
    return NO;
}

static void DumpLibraryRuntime(id library) {
    for (Class cls = object_getClass(library); cls;
         cls = class_getSuperclass(cls)) {
        unsigned int methodCount = 0;
        Method *methods = class_copyMethodList(cls, &methodCount);
        for (unsigned int index = 0; methods && index < methodCount; index++) {
            const char *name = sel_getName(method_getName(methods[index]));
            if (InterestingName(name)) {
                fprintf(stderr,
                        "IOS_METAL_EXPORT method class=%s selector=%s "
                        "types=%s imp=%p\n",
                        class_getName(cls), name,
                        method_getTypeEncoding(methods[index]),
                        method_getImplementation(methods[index]));
            }
        }
        free(methods);

        unsigned int ivarCount = 0;
        Ivar *ivars = class_copyIvarList(cls, &ivarCount);
        for (unsigned int index = 0; ivars && index < ivarCount; index++) {
            const char *name = ivar_getName(ivars[index]);
            if (InterestingName(name)) {
                fprintf(stderr,
                        "IOS_METAL_EXPORT ivar class=%s name=%s type=%s "
                        "offset=%td\n",
                        class_getName(cls), name,
                        ivar_getTypeEncoding(ivars[index]),
                        ivar_getOffset(ivars[index]));
            }
        }
        free(ivars);
    }
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc != 2 && argc != 3) {
            fprintf(stderr, "usage: %s source.metal [output.metallib]\n",
                    argv[0]);
            return 64;
        }
        NSError *readError = nil;
        NSString *source = [NSString stringWithContentsOfFile:
            [NSString stringWithUTF8String:argv[1]]
                                                   encoding:NSUTF8StringEncoding
                                                      error:&readError];
        if (!source) {
            fprintf(stderr, "IOS_METAL_EXPORT readError=%s\n",
                    readError.localizedDescription.UTF8String ?: "(nil)");
            return 65;
        }
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        fprintf(stderr,
                "IOS_METAL_EXPORT device=%p class=%s sourceLength=%lu\n",
                device, device ? object_getClassName(device) : "(nil)",
                (unsigned long)[source lengthOfBytesUsingEncoding:
                    NSUTF8StringEncoding]);
        if (!device) return 2;

        NSError *compileError = nil;
        id<MTLLibrary> library = [device newLibraryWithSource:source
                                                      options:nil
                                                        error:&compileError];
        fprintf(stderr,
                "IOS_METAL_EXPORT library=%p class=%s functions=%s "
                "errorDomain=%s errorCode=%ld description=%s userInfo=%s\n",
                library, library ? object_getClassName(library) : "(nil)",
                library ? library.functionNames.description.UTF8String : "(nil)",
                compileError ? compileError.domain.UTF8String : "(nil)",
                (long)(compileError ? compileError.code : 0),
                compileError
                    ? compileError.localizedDescription.UTF8String : "(nil)",
                compileError
                    ? compileError.userInfo.description.UTF8String : "(nil)");
        if (!library) return 3;
        DumpLibraryRuntime(library);
        if (argc == 3) {
            NSURL *outputURL = [NSURL fileURLWithPath:
                [NSString stringWithUTF8String:argv[2]]];
            NSError *serializeError = nil;
            SEL serializeSelector = sel_registerName("serializeToURL:error:");
            BOOL serialized = [library respondsToSelector:serializeSelector]
                ? ((BOOL (*)(id, SEL, NSURL *, NSError **))objc_msgSend)(
                      library, serializeSelector, outputURL, &serializeError)
                : NO;
            NSDictionary *attributes = serialized
                ? [[NSFileManager defaultManager]
                    attributesOfItemAtPath:outputURL.path error:nil] : nil;
            fprintf(stderr,
                    "IOS_METAL_EXPORT serialized=%d path=%s bytes=%llu "
                    "errorDomain=%s errorCode=%ld description=%s\n",
                    serialized, outputURL.path.UTF8String,
                    [attributes fileSize],
                    serializeError ? serializeError.domain.UTF8String : "(nil)",
                    (long)(serializeError ? serializeError.code : 0),
                    serializeError
                        ? serializeError.localizedDescription.UTF8String
                        : "(nil)");
            if (!serialized) return 4;
        }
        return 0;
    }
}

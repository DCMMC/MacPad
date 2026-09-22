// Runtime Objective-C inventory for the actual iOS private frameworks on the
// test device. This avoids relying on a different SDK's private headers while
// deriving the foreground assertion API used by the production launcher.

#import <Foundation/Foundation.h>
#import <dlfcn.h>
#import <objc/message.h>
#import <objc/runtime.h>
#import <ptrauth.h>
#include <signal.h>

static BOOL interesting(Class cls) {
    const char *name = cls ? class_getName(cls) : NULL;
    if (!name) return NO;
    NSString *value = [NSString stringWithUTF8String:name];
    NSArray<NSString *> *tokens = @[
        @"RBSAssertion", @"RBSConnection", @"RBSDomain",
        @"RBSProcessIdentity", @"RBSProcessIdentifier",
        @"RBSProcessPredicate", @"RBSProcessHandle", @"RBSProcessInstance",
        @"RBSProcessBundle", @"ApplicationIdentity",
        @"RBLaunchd", @"LaunchdJob", @"ExtensionJob",
        @"RBSLaunchRequest", @"RBSLaunchContext",
        @"RBSXPCServiceIdentity", @"RBProcess",
        @"RBSTarget", @"AuditToken",
        @"RBSCPU", @"RBSJetsam", @"RBSHereditary",
        @"BKSProcessAssertion",
    ];
    for (NSString *token in tokens) {
        if ([value containsString:token]) return YES;
    }
    // Class-cluster implementations are intentionally private and do not
    // necessarily retain "RBSProcessIdentity" in their concrete class name.
    // Include every subclass so the actual application/daemon identity
    // constructors can be derived from the running OS rather than guessed.
    for (Class superclass = class_getSuperclass(cls); superclass;
         superclass = class_getSuperclass(superclass)) {
        if (strcmp(class_getName(superclass), "RBSProcessIdentity") == 0)
            return YES;
    }
    return NO;
}

static void dump_methods(Class cls, BOOL classMethods) {
    Class owner = classMethods ? object_getClass(cls) : cls;
    unsigned count = 0;
    Method *methods = class_copyMethodList(owner, &count);
    for (unsigned index = 0; index < count; index++) {
        Method method = methods[index];
        printf("  %c %s types=%s\n", classMethods ? '+' : '-',
               sel_getName(method_getName(method)),
               method_getTypeEncoding(method) ?: "?");
    }
    free(methods);
}

static void dump_ivars(Class cls) {
    unsigned count = 0;
    Ivar *ivars = class_copyIvarList(cls, &count);
    for (unsigned index = 0; index < count; index++) {
        Ivar ivar = ivars[index];
        printf("  IVAR %s offset=%td type=%s\n",
               ivar_getName(ivar) ?: "?", ivar_getOffset(ivar),
               ivar_getTypeEncoding(ivar) ?: "?");
    }
    free(ivars);
}

static void dump_identity(NSString *label, id identity) {
    BOOL isApplication = identity
        ? ((BOOL (*)(id, SEL))objc_msgSend)(
              identity, sel_registerName("isApplication")) : NO;
    BOOL isEmbedded = identity
        ? ((BOOL (*)(id, SEL))objc_msgSend)(
              identity, sel_registerName("isEmbeddedApplication")) : NO;
    int platform = identity
        ? ((int (*)(id, SEL))objc_msgSend)(
              identity, sel_registerName("platform")) : -1;
    unsigned char flags = identity
        ? ((unsigned char (*)(id, SEL))objc_msgSend)(
              identity, sel_registerName("defaultManageFlags")) : 0;
    printf("IDENTITY label=%s class=%s application=%s embedded=%s "
           "platform=%d flags=%u value=%s\n",
           label.UTF8String,
           identity ? object_getClassName(identity) : "nil",
           isApplication ? "yes" : "no",
           isEmbedded ? "yes" : "no", platform, flags,
           identity ? [[identity description] UTF8String] : "(nil)");
}

int main(void) {
    @autoreleasepool {
        const char *frameworks[] = {
            "/System/Library/PrivateFrameworks/RunningBoard.framework/RunningBoard",
            "/System/Library/PrivateFrameworks/RunningBoardServices.framework/RunningBoardServices",
            "/System/Library/PrivateFrameworks/BackBoardServices.framework/BackBoardServices",
        };
        for (unsigned index = 0;
             index < sizeof(frameworks) / sizeof(frameworks[0]); index++) {
            void *handle = dlopen(frameworks[index], RTLD_NOW | RTLD_LOCAL);
            printf("FRAMEWORK path=%s handle=%p error=%s\n", frameworks[index],
                   handle, handle ? "none" : (dlerror() ?: "unknown"));
        }

        if (getenv("RBS_DISASM_ONLY")) {
            Method method = class_getInstanceMethod(
                objc_getClass("RBSXPCServiceProcessIdentity"),
                sel_registerName("encodeForJob"));
            IMP implementation = method ? method_getImplementation(method) : NULL;
            printf("ENCODE_FOR_JOB imp=%p stripped=%p\n", implementation,
                   ptrauth_strip(implementation,
                                 ptrauth_key_function_pointer));
            fflush(stdout);
            raise(SIGSTOP);
            return 0;
        }

        int classCount = objc_getClassList(NULL, 0);
        __unsafe_unretained Class *classes =
            (__unsafe_unretained Class *)calloc((size_t)classCount,
                                                sizeof(*classes));
        classCount = objc_getClassList(classes, classCount);
        for (int index = 0; index < classCount; index++) {
            Class cls = classes[index];
            const char *name = class_getName(cls);
            if (!interesting(cls)) continue;
            printf("CLASS %s superclass=%s size=%zu\n", name,
                   class_getSuperclass(cls)
                       ? class_getName(class_getSuperclass(cls)) : "(nil)",
                   class_getInstanceSize(cls));
            dump_ivars(cls);
            dump_methods(cls, YES);
            dump_methods(cls, NO);
        }
        free(classes);

        Class managerClass = objc_getClass("RBLaunchdJobManager");
        SEL createSelector = sel_registerName(
            "_createLaunchdJobWithIdentity:context:error:");
        SEL generateSelector = sel_registerName(
            "_generateDataWithIdentity:context:error:");
        SEL submitExtensionSelector = sel_registerName(
            "_createAndSubmitExtensionJob:UUID:error:");
        IMP createImplementation = managerClass
            ? class_getMethodImplementation(managerClass, createSelector)
            : NULL;
        IMP generateImplementation = managerClass
            ? class_getMethodImplementation(managerClass, generateSelector)
            : NULL;
        IMP submitExtensionImplementation = managerClass
            ? class_getMethodImplementation(managerClass,
                                             submitExtensionSelector)
            : NULL;
        printf("RBS_JOB_MANAGER class=%p create=%p create_stripped=%p "
               "generate=%p generate_stripped=%p submit_extension=%p "
               "submit_extension_stripped=%p\n",
               managerClass,
               createImplementation,
               ptrauth_strip(createImplementation,
                             ptrauth_key_function_pointer),
               generateImplementation,
               ptrauth_strip(generateImplementation,
                             ptrauth_key_function_pointer),
               submitExtensionImplementation,
               ptrauth_strip(submitExtensionImplementation,
                             ptrauth_key_function_pointer));

        Class identityClass = objc_getClass("RBSProcessIdentity");
        id embedded = ((id (*)(id, SEL, id))objc_msgSend)(
            (id)identityClass,
            sel_registerName("identityForEmbeddedApplicationIdentifier:"),
            @"com.apple.Maps");
        dump_identity(@"embedded-com.apple.Maps", embedded);
        NSArray<NSString *> *extensionIdentifiers = @[
            @"com.tigisoftware.Filza.Sharing",
            @"com.apple.Appearance-Settings.extension",
        ];
        for (NSString *identifier in extensionIdentifiers) {
            id plugin = ((id (*)(id, SEL, id))objc_msgSend)(
                (id)identityClass,
                sel_registerName("identityForPlugInKitIdentifier:"),
                identifier);
            dump_identity(
                [@"plugin-" stringByAppendingString:identifier], plugin);
            id xpcService = ((id (*)(id, SEL, id))objc_msgSend)(
                (id)identityClass,
                sel_registerName("identityForXPCServiceIdentifier:"),
                identifier);
            dump_identity(
                [@"xpc-" stringByAppendingString:identifier], xpcService);
        }
        for (int platform = 0; platform <= 6; platform++) {
            id application = ((id (*)(id, SEL, id, id, int))objc_msgSend)(
                (id)identityClass,
                sel_registerName(
                    "identityForApplicationJobLabel:bundleID:platform:"),
                @"UIKitApplication:com.macwsguide.catalystlauncher",
                @"com.apple.Maps", platform);
            dump_identity(
                [NSString stringWithFormat:@"application-platform-%d",
                                           platform],
                application);
        }

        Class connectionClass = objc_getClass("RBSConnection");
        id connection = ((id (*)(id, SEL))objc_msgSend)(
            (id)connectionClass, sel_registerName("sharedInstance"));
        NSError *error = nil;
        id descriptors = ((id (*)(id, SEL, BOOL, NSError **))objc_msgSend)(
            connection,
            sel_registerName("assertionDescriptorsByPidWithFlattenedAttributes:error:"),
            YES, &error);
        printf("ASSERTIONS error=%s\n%s\n",
               error ? error.description.UTF8String : "none",
               descriptors ? [[descriptors description] UTF8String] : "(nil)");
        if ([descriptors isKindOfClass:NSDictionary.class]) {
            [(NSDictionary *)descriptors enumerateKeysAndObjectsUsingBlock:
                ^(id key, id value, BOOL *stop) {
                (void)stop;
                if (![value conformsToProtocol:@protocol(NSFastEnumeration)])
                    return;
                for (id descriptor in value) {
                    id explanation = [descriptor valueForKey:@"explanation"];
                    id target = [descriptor valueForKey:@"target"];
                    id attributes = [descriptor valueForKey:@"attributes"];
                    NSMutableArray<NSString *> *attributeLines =
                        [NSMutableArray array];
                    if ([attributes conformsToProtocol:
                            @protocol(NSFastEnumeration)]) {
                        for (id attribute in attributes) {
                            NSString *line = [[attribute description]
                                stringByReplacingOccurrencesOfString:@"\n"
                                                          withString:@" "];
                            [attributeLines addObject:line];
                        }
                    }
                    printf("DESC owner=%s explanation=%s target=%s attrs=%s\n",
                           [[key description] UTF8String],
                           [[explanation description] UTF8String],
                           [[target description] UTF8String],
                           [[attributeLines componentsJoinedByString:@" | "]
                               UTF8String]);
                }
            }];
        }
    }
    return 0;
}

// Read-only Objective-C inventory for the actual macOS 13.4
// ExtensionFoundation implementation loaded inside the chroot.

@import Foundation;

#include <dlfcn.h>
#include <dispatch/dispatch.h>
#include <objc/runtime.h>
#include <objc/message.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void DumpMethods(Class cls, BOOL classMethods) {
    Class owner = classMethods ? object_getClass(cls) : cls;
    unsigned count = 0;
    Method *methods = class_copyMethodList(owner, &count);
    for (unsigned index = 0; index < count; index++) {
        Method method = methods[index];
        printf("METHOD class=%s kind=%c selector=%s types=%s imp=%p\n",
               class_getName(cls), classMethods ? '+' : '-',
               sel_getName(method_getName(method)),
               method_getTypeEncoding(method) ?: "?",
               method_getImplementation(method));
        const char *className = class_getName(cls);
        const char *selectorName = sel_getName(method_getName(method));
        if (getenv("MACWS_PROBE_DISASM_QUERY") && className && selectorName &&
            (strcmp(className, "_EXQuery") == 0 ||
             strcmp(className, "_EXQueryController") == 0 ||
             strcmp(className, "_EXDiscoveryController") == 0 ||
             strcmp(className, "_EXServiceClient") == 0 ||
             strcmp(className,
                    "_LSApplicationExtensionRecordEnumerator") == 0) &&
            (strcmp(selectorName, "executeQuery:") == 0 ||
             strcmp(selectorName, "executeQueries:") == 0 ||
             strcmp(selectorName, "extensionsMatchingQueries:") == 0 ||
             strcmp(selectorName, "extensionsMatchingQuery:") == 0 ||
             strcmp(selectorName, "extensionsWithQueries:") == 0 ||
             strcmp(selectorName, "canRunQuery:error:") == 0 ||
             strcmp(selectorName, "initWithExtensionPoint:options:") == 0 ||
             strcmp(selectorName, "matchesRecord:") == 0 ||
             strcmp(selectorName, "matches:") == 0)) {
            IMP implementation = method_getImplementation(method);
            Dl_info info = {};
            dladdr((const void *)implementation, &info);
            const uint8_t *bytes = (const uint8_t *)implementation;
            printf("QUERY_DISASM class=%s kind=%c selector=%s imp=%p "
                   "image=%s base=%p offset=%#llx bytes=",
                   className, classMethods ? '+' : '-', selectorName,
                   implementation, info.dli_fname ?: "<unknown>",
                   info.dli_fbase,
                   (unsigned long long)((uintptr_t)implementation -
                                        (uintptr_t)info.dli_fbase));
            const size_t disassemblyBytes =
                strcmp(selectorName, "extensionsMatchingQuery:") == 0
                    ? 2048
                    : (strcmp(selectorName, "canRunQuery:error:") == 0
                           ? 1024 : 512);
            for (size_t byte = 0; byte < disassemblyBytes; byte++)
                printf("%02x", bytes[byte]);
            putchar('\n');
            for (size_t byte = 0; byte < disassemblyBytes;
                 byte += sizeof(uint32_t)) {
                uint32_t instruction = 0;
                memcpy(&instruction, bytes + byte, sizeof(instruction));
                if ((instruction & 0x7c000000U) != 0x14000000U)
                    continue;
                int64_t displacement =
                    ((int64_t)(instruction & 0x03ffffffU) << 38) >> 36;
                uintptr_t instructionAddress =
                    (uintptr_t)implementation + byte;
                const void *target = (const void *)(instructionAddress +
                                                     displacement);
                Dl_info targetInfo = {};
                dladdr(target, &targetInfo);
                printf("QUERY_BRANCH class=%s kind=%c selector=%s at=%p "
                       "op=%s target=%p symbol=%s image=%s offset=%#llx\n",
                       className, classMethods ? '+' : '-', selectorName,
                       (const void *)instructionAddress,
                       (instruction & 0x80000000U) ? "bl" : "b", target,
                       targetInfo.dli_sname ?: "<unknown>",
                       targetInfo.dli_fname ?: "<unknown>",
                       (unsigned long long)((uintptr_t)target -
                           (uintptr_t)targetInfo.dli_fbase));
            }
        }
    }
    free(methods);
}

static void DumpIvars(Class cls) {
    unsigned count = 0;
    Ivar *ivars = class_copyIvarList(cls, &count);
    for (unsigned index = 0; index < count; index++) {
        printf("IVAR class=%s name=%s offset=%td type=%s\n",
               class_getName(cls), ivar_getName(ivars[index]) ?: "?",
               ivar_getOffset(ivars[index]),
               ivar_getTypeEncoding(ivars[index]) ?: "?");
    }
    free(ivars);
}

int main(void) {
    @autoreleasepool {
        const char *path =
            "/System/Library/Frameworks/ExtensionFoundation.framework/"
            "ExtensionFoundation";
        void *handle = dlopen(path, RTLD_NOW | RTLD_LOCAL);
        printf("FRAMEWORK path=%s handle=%p error=%s\n", path, handle,
               handle ? "none" : (dlerror() ?: "unknown"));
        if (!handle) return 1;

        const char *classes[] = {
            "EXExtensionPointCatalog",
            "_EXExtensionPoint",
            "EXFrameworkScanner",
            "EXEnumerator",
            "_EXQuery",
            "_EXQueryController",
            "_EXDiscoveryController",
            "_EXServiceClient",
            "_LSApplicationExtensionRecordEnumerator",
            "_EXExtensionIdentity",
            "LSApplicationExtensionRecord",
            "LSBundleRecord",
            "LSPlugInKitProxy",
            "_LSInstaller",
            "LSBundleRecordBuilder",
            "_LSDModifyClient",
            NULL,
        };
        for (const char *const *name = classes; *name; name++) {
            Class cls = objc_getClass(*name);
            printf("CLASS name=%s value=%p image=%s superclass=%s size=%zu\n",
                   *name, cls,
                   cls && class_getImageName(cls) ? class_getImageName(cls) : "nil",
                   cls && class_getSuperclass(cls)
                       ? class_getName(class_getSuperclass(cls)) : "nil",
                   cls ? class_getInstanceSize(cls) : 0);
            if (!cls) continue;
            DumpIvars(cls);
            DumpMethods(cls, YES);
            DumpMethods(cls, NO);
        }

        if (getenv("MACWS_PROBE_QUERY_OWNERS")) {
            unsigned classCount = 0;
            Class *runtimeClasses = objc_copyClassList(&classCount);
            for (unsigned classIndex = 0; classIndex < classCount;
                 classIndex++) {
                Class runtimeClass = runtimeClasses[classIndex];
                for (int classMethods = 0; classMethods <= 1;
                     classMethods++) {
                    Class owner = classMethods
                        ? object_getClass(runtimeClass) : runtimeClass;
                    unsigned methodCount = 0;
                    Method *methods = class_copyMethodList(owner,
                                                           &methodCount);
                    for (unsigned methodIndex = 0;
                         methodIndex < methodCount; methodIndex++) {
                        const char *selectorName = sel_getName(
                            method_getName(methods[methodIndex]));
                        if (!selectorName ||
                            (strcmp(selectorName, "executeQuery:") != 0 &&
                             strcmp(selectorName, "executeQueries:") != 0 &&
                             strcmp(selectorName, "executeQuery:completionHandler:") != 0 &&
                             strcmp(selectorName, "executeQueries:completionHandler:") != 0 &&
                             strcmp(selectorName, "preferInProcessDiscovery") != 0 &&
                             strcmp(selectorName, "extensionsMatchingQueries:") != 0 &&
                             strcmp(selectorName, "extensionsMatchingQuery:") != 0 &&
                             strcmp(selectorName, "extensionsWithQueries:") != 0 &&
                             strcmp(selectorName, "canRunQuery:error:") != 0 &&
                             strcmp(selectorName, "forceSandbox") != 0 &&
                             strcmp(selectorName, "allowedUnsandboxedExtensionPoints") != 0 &&
                             strcmp(selectorName, "identities") != 0)) {
                            continue;
                        }
                        IMP implementation = method_getImplementation(
                            methods[methodIndex]);
                        Dl_info info = {};
                        dladdr((const void *)implementation, &info);
                        printf("QUERY_OWNER class=%s kind=%c selector=%s "
                               "types=%s imp=%p image=%s offset=%#llx\n",
                               class_getName(runtimeClass),
                               classMethods ? '+' : '-', selectorName,
                               method_getTypeEncoding(methods[methodIndex])
                                   ?: "?",
                               implementation,
                               info.dli_fname ?: "<unknown>",
                               (unsigned long long)(
                                   (uintptr_t)implementation -
                                   (uintptr_t)info.dli_fbase));
                    }
                    free(methods);
                }
            }
            free(runtimeClasses);
        }

        // Exercise the framework's own parser against the real macOS Settings
        // framework.  This is intentionally read-only: it builds an in-memory
        // catalog and does not mutate LaunchServices or PlugInKit state.
        Class scannerClass = objc_getClass("EXFrameworkScanner");
        Class enumeratorClass = objc_getClass("EXEnumerator");
        Class catalogClass = objc_getClass("EXExtensionPointCatalog");
        NSURL *settingsURL = [NSURL fileURLWithPath:
            @"/System/Library/PrivateFrameworks/Settings.framework"];
        id defaultRootURL = ((id (*)(id, SEL))objc_msgSend)(
            scannerClass, sel_registerName("rootURL"));
        id frameworkPaths = ((id (*)(id, SEL))objc_msgSend)(
            scannerClass, sel_registerName("frameworkPaths"));
        printf("SCAN defaultRootURL=%s frameworkPaths=%s\n",
               defaultRootURL ? [[defaultRootURL description] UTF8String] : "nil",
               frameworkPaths ? [[frameworkPaths description] UTF8String] : "nil");
        id scanner = ((id (*)(id, SEL))objc_msgSend)(scannerClass,
                                                     sel_registerName("alloc"));
        scanner = ((id (*)(id, SEL, id))objc_msgSend)(
            scanner, sel_registerName("initWithSourceURL:"), settingsURL);
        ((void (*)(id, SEL))objc_msgSend)(scanner, sel_registerName("main"));
        id sdk = ((id (*)(id, SEL))objc_msgSend)(
            scanner, sel_registerName("combinedExtensionSDK"));
        id extensions = ((id (*)(id, SEL))objc_msgSend)(
            scanner, sel_registerName("_extensions"));
        printf("SCAN settings=%s\n", settingsURL.path.UTF8String);
        printf("SCAN sdkClass=%s sdk=%s\n",
               sdk ? object_getClassName(sdk) : "nil",
               sdk ? [[sdk description] UTF8String] : "nil");
        printf("SCAN extensionsClass=%s extensions=%s\n",
               extensions ? object_getClassName(extensions) : "nil",
               extensions ? [[extensions description] UTF8String] : "nil");

        id enumerator = ((id (*)(id, SEL, id))objc_msgSend)(
            enumeratorClass,
            sel_registerName("extensionPointDefinitionEnumeratorWithSDKDictionary:"),
            sdk);
        NSMutableArray *definitions = [NSMutableArray array];
        for (;;) {
            id definition = ((id (*)(id, SEL))objc_msgSend)(
                enumerator, sel_registerName("nextObject"));
            if (!definition) break;
            [definitions addObject:definition];
        }
        printf("SCAN definitionCount=%lu definitions=%s\n",
               (unsigned long)definitions.count,
               [[definitions description] UTF8String]);

        id catalog = ((id (*)(id, SEL))objc_msgSend)(catalogClass,
                                                     sel_registerName("alloc"));
        id definitionEnumerator = [definitions objectEnumerator];
        catalog = ((id (*)(id, SEL, id))objc_msgSend)(
            catalog, sel_registerName("initWithEnumerator:"),
            definitionEnumerator);
        id point = ((id (*)(id, SEL, id))objc_msgSend)(
            catalog, sel_registerName("extensionPointForIdentifier:"),
            @"com.apple.Settings.extension.ui");
        id index = ((id (*)(id, SEL))objc_msgSend)(
            catalog, sel_registerName("extensionPointByIdentifierPlatform"));
        printf("SCAN pointClass=%s point=%s\n",
               point ? object_getClassName(point) : "nil",
               point ? [[point description] UTF8String] : "nil");
        printf("SCAN catalogIndex=%s\n",
               index ? [[index description] UTF8String] : "nil");

        id directScanner = ((id (*)(id, SEL))objc_msgSend)(
            scannerClass, sel_registerName("alloc"));
        directScanner = ((id (*)(id, SEL, id))objc_msgSend)(
            directScanner, sel_registerName("initWithSourceURL:"),
            defaultRootURL);
        CFBundleRef settingsBundle = CFBundleCreate(
            kCFAllocatorDefault, (__bridge CFURLRef)settingsURL);
        ((void (*)(id, SEL, CFBundleRef))objc_msgSend)(
            directScanner, sel_registerName("processExtensionSDKFromBundle:"),
            settingsBundle);
        id directSDK = ((id (*)(id, SEL))objc_msgSend)(
            directScanner, sel_registerName("combinedExtensionSDK"));
        printf("DIRECT bundle=%p sdk=%s\n", settingsBundle,
               directSDK ? [[directSDK description] UTF8String] : "nil");

        id directEnumerator = ((id (*)(id, SEL, id))objc_msgSend)(
            enumeratorClass,
            sel_registerName("extensionPointDefinitionEnumeratorWithSDKDictionary:"),
            directSDK);
        NSMutableArray *directDefinitions = [NSMutableArray array];
        for (;;) {
            id definition = ((id (*)(id, SEL))objc_msgSend)(
                directEnumerator, sel_registerName("nextObject"));
            if (!definition) break;
            [directDefinitions addObject:definition];
        }
        id directCatalog = ((id (*)(id, SEL))objc_msgSend)(
            catalogClass, sel_registerName("alloc"));
        directCatalog = ((id (*)(id, SEL, id))objc_msgSend)(
            directCatalog, sel_registerName("initWithEnumerator:"),
            [directDefinitions objectEnumerator]);
        id directPoint = ((id (*)(id, SEL, id))objc_msgSend)(
            directCatalog, sel_registerName("extensionPointForIdentifier:"),
            @"com.apple.Settings.extension.ui");
        id directCatalogIndex = ((id (*)(id, SEL))objc_msgSend)(
            directCatalog, sel_registerName("extensionPointByIdentifierPlatform"));
        printf("DIRECT definitionCount=%lu definitions=%s\n",
               (unsigned long)directDefinitions.count,
               [[directDefinitions description] UTF8String]);
        printf("DIRECT catalogIndex=%s\n",
               directCatalogIndex ? [[directCatalogIndex description] UTF8String]
                                  : "nil");
        printf("DIRECT pointClass=%s point=%s\n",
               directPoint ? object_getClassName(directPoint) : "nil",
               directPoint ? [[directPoint description] UTF8String] : "nil");

        for (unsigned platform = 0; platform <= 16; platform++) {
            id candidate = ((id (*)(id, SEL, id, unsigned))objc_msgSend)(
                directCatalog,
                sel_registerName("extensionPointForIdentifier:platform:"),
                @"com.apple.Settings.extension.ui", platform);
            if (candidate) {
                printf("DIRECT platform=%u pointClass=%s point=%s\n",
                       platform, object_getClassName(candidate),
                       [[candidate description] UTF8String]);
                if (!directPoint) directPoint = candidate;
            }
        }

        if (directPoint) {
            Class queryClass = objc_getClass("_EXQuery");
            id directQuery = ((id (*)(id, SEL))objc_msgSend)(
                queryClass, sel_registerName("alloc"));
            directQuery = ((id (*)(id, SEL, id))objc_msgSend)(
                directQuery, sel_registerName("initWithExtensionPoint:"),
                directPoint);
            id directResults = ((id (*)(id, SEL, id))objc_msgSend)(
                queryClass, sel_registerName("executeQuery:"), directQuery);
            printf("DIRECT queryClass=%s resultClass=%s results=%s\n",
                   directQuery ? object_getClassName(directQuery) : "nil",
                   directResults ? object_getClassName(directResults) : "nil",
                   directResults ? [[directResults description] UTF8String] : "nil");
        }

        id definitionDirs = ((id (*)(id, SEL))objc_msgSend)(
            enumeratorClass,
            sel_registerName("extensionPointDefinitionDirectoryURLs"));
        id definitionCaches = ((id (*)(id, SEL))objc_msgSend)(
            enumeratorClass, sel_registerName("extensionPointCacheFileURLs"));
        printf("DIRECT definitionDirs=%s\n",
               definitionDirs ? [[definitionDirs description] UTF8String] : "nil");
        printf("DIRECT definitionCaches=%s\n",
               definitionCaches ? [[definitionCaches description] UTF8String] : "nil");

        id defaultEnumerator = ((id (*)(id, SEL))objc_msgSend)(
            enumeratorClass,
            sel_registerName("extensionPointDefinitionEnumerator"));
        id defaultCatalog = ((id (*)(id, SEL))objc_msgSend)(
            catalogClass, sel_registerName("alloc"));
        defaultCatalog = ((id (*)(id, SEL, id))objc_msgSend)(
            defaultCatalog, sel_registerName("initWithEnumerator:"),
            defaultEnumerator);
        id defaultIndex = ((id (*)(id, SEL))objc_msgSend)(
            defaultCatalog,
            sel_registerName("extensionPointByIdentifierPlatform"));
        printf("DEFAULT catalogIndexCount=%lu\n",
               (unsigned long)[defaultIndex count]);
        id defaultPoint = ((id (*)(id, SEL, id))objc_msgSend)(
            defaultCatalog, sel_registerName("extensionPointForIdentifier:"),
            @"com.apple.Settings.extension.ui");
        printf("DEFAULT unqualifiedPoint=%s\n",
               defaultPoint ? [[defaultPoint description] UTF8String] : "nil");
        for (unsigned platform = 0; platform <= 16; platform++) {
            id candidate = ((id (*)(id, SEL, id, unsigned))objc_msgSend)(
                defaultCatalog,
                sel_registerName("extensionPointForIdentifier:platform:"),
                @"com.apple.Settings.extension.ui", platform);
            if (candidate) {
                printf("DEFAULT platform=%u pointClass=%s point=%s\n",
                       platform, object_getClassName(candidate),
                       [[candidate description] UTF8String]);
                if (!defaultPoint) defaultPoint = candidate;
                Class perPlatformQueryClass = objc_getClass("_EXQuery");
                id perPlatformQuery = ((id (*)(id, SEL))objc_msgSend)(
                    perPlatformQueryClass, sel_registerName("alloc"));
                perPlatformQuery = ((id (*)(id, SEL, id))objc_msgSend)(
                    perPlatformQuery,
                    sel_registerName("initWithExtensionPoint:"), candidate);
                id perPlatformResults = ((id (*)(id, SEL, id))objc_msgSend)(
                    perPlatformQueryClass, sel_registerName("executeQuery:"),
                    perPlatformQuery);
                printf("DEFAULT platform=%u resultCount=%lu results=%s\n",
                       platform, (unsigned long)[perPlatformResults count],
                       perPlatformResults
                           ? [[perPlatformResults description] UTF8String]
                           : "nil");
            }
        }
        if (defaultPoint) {
            Class queryClass = objc_getClass("_EXQuery");
            id query = ((id (*)(id, SEL))objc_msgSend)(
                queryClass, sel_registerName("alloc"));
            query = ((id (*)(id, SEL, id))objc_msgSend)(
                query, sel_registerName("initWithExtensionPoint:"), defaultPoint);
            id results = ((id (*)(id, SEL, id))objc_msgSend)(
                queryClass, sel_registerName("executeQuery:"), query);
            printf("DEFAULT resultCount=%lu results=%s\n",
                   (unsigned long)[results count],
                   results ? [[results description] UTF8String] : "nil");
        }

        id installDirs = ((id (*)(id, SEL))objc_msgSend)(
            enumeratorClass, sel_registerName("extensionInstallDirectoryURLs"));
        printf("EXTENSIONS installDirs=%s\n",
               installDirs ? [[installDirs description] UTF8String] : "nil");
        const char *extensionSelectors[] = {
            "extensionEnumerator", "extensionURLEnumerator", NULL
        };
        for (const char *const *selectorName = extensionSelectors;
             *selectorName; selectorName++) {
            id extensionEnumerator = ((id (*)(id, SEL))objc_msgSend)(
                enumeratorClass, sel_registerName(*selectorName));
            NSUInteger extensionCount = 0;
            for (; extensionCount < 10000; extensionCount++) {
                id object = ((id (*)(id, SEL))objc_msgSend)(
                    extensionEnumerator, sel_registerName("nextObject"));
                if (!object) break;
                NSString *description = [object description];
                if (extensionCount < 3 ||
                    [description rangeOfString:@"Appearance"
                                       options:NSCaseInsensitiveSearch].location
                        != NSNotFound ||
                    [description rangeOfString:@"Settings"
                                       options:NSCaseInsensitiveSearch].location
                        != NSNotFound) {
                    printf("EXTENSIONS selector=%s index=%lu class=%s object=%s\n",
                           *selectorName, (unsigned long)extensionCount,
                           object_getClassName(object), description.UTF8String);
                }
            }
            printf("EXTENSIONS selector=%s count=%lu\n", *selectorName,
                   (unsigned long)extensionCount);
        }

        unsigned runtimeClassCount = 0;
        Class *runtimeClasses = objc_copyClassList(&runtimeClassCount);
        for (unsigned classIndex = 0; classIndex < runtimeClassCount;
             classIndex++) {
            Class runtimeClass = runtimeClasses[classIndex];
            const char *className = class_getName(runtimeClass);
            const char *imageName = class_getImageName(runtimeClass);
            if (!className || !imageName ||
                !strstr(imageName, "ExtensionFoundation.framework")) {
                continue;
            }
            unsigned methodCount = 0;
            Method *methods = class_copyMethodList(runtimeClass, &methodCount);
            for (unsigned methodIndex = 0; methodIndex < methodCount;
                 methodIndex++) {
                const char *selector = sel_getName(
                    method_getName(methods[methodIndex]));
                if (strstr(selector, "Record") || strstr(selector, "record") ||
                    strstr(selector, "Identity") || strstr(selector, "identity") ||
                    strstr(selector, "URL") || strstr(selector, "extension")) {
                    printf("INVENTORY class=%s kind=- selector=%s types=%s\n",
                           className, selector,
                           method_getTypeEncoding(methods[methodIndex]));
                }
            }
            free(methods);

            Class metaClass = object_getClass(runtimeClass);
            methods = class_copyMethodList(metaClass, &methodCount);
            for (unsigned methodIndex = 0; methodIndex < methodCount;
                 methodIndex++) {
                const char *selector = sel_getName(
                    method_getName(methods[methodIndex]));
                if (strstr(selector, "Record") || strstr(selector, "record") ||
                    strstr(selector, "Identity") || strstr(selector, "identity") ||
                    strstr(selector, "URL") || strstr(selector, "extension")) {
                    printf("INVENTORY class=%s kind=+ selector=%s types=%s\n",
                           className, selector,
                           method_getTypeEncoding(methods[methodIndex]));
                }
            }
            free(methods);
        }
        free(runtimeClasses);

        Class concreteExtensionClass = objc_getClass("EXConcreteExtension");
        dispatch_semaphore_t extensionSemaphore = dispatch_semaphore_create(0);
        __block id concreteExtension = nil;
        void (^extensionCompletion)(id) = ^(id extension) {
            concreteExtension = extension;
            dispatch_semaphore_signal(extensionSemaphore);
        };
        ((void (*)(id, SEL, id, id))objc_msgSend)(
            concreteExtensionClass, sel_registerName("extensionWithURL:completion:"),
            [NSURL fileURLWithPath:
                @"/System/Library/ExtensionKit/Extensions/Appearance.appex"],
            extensionCompletion);
        long extensionWait = dispatch_semaphore_wait(
            extensionSemaphore,
            dispatch_time(DISPATCH_TIME_NOW, 10 * NSEC_PER_SEC));
        printf("CONCRETE wait=%ld extensionClass=%s extension=%s\n",
               extensionWait,
               concreteExtension ? object_getClassName(concreteExtension) : "nil",
               concreteExtension ? [[concreteExtension description] UTF8String] : "nil");
        id identifierError = nil;
        id identifierExtension = ((id (*)(id, SEL, id, id *))objc_msgSend)(
            concreteExtensionClass,
            sel_registerName("extensionWithIdentifier:error:"),
            @"com.apple.Appearance-Settings.extension", &identifierError);
        printf("CONCRETE identifierClass=%s identifierExtension=%s error=%s\n",
               identifierExtension ? object_getClassName(identifierExtension) : "nil",
               identifierExtension
                   ? [[identifierExtension description] UTF8String] : "nil",
               identifierError ? [[identifierError description] UTF8String] : "nil");

        Class proxyClass = objc_getClass("LSPlugInKitProxy");
        id proxy = ((id (*)(id, SEL, id))objc_msgSend)(
            proxyClass, sel_registerName("pluginKitProxyForIdentifier:"),
            @"com.apple.Appearance-Settings.extension");
        id proxyPlatform = proxy ? ((id (*)(id, SEL))objc_msgSend)(
            proxy, sel_registerName("platform")) : nil;
        id proxyPoint = proxy ? ((id (*)(id, SEL))objc_msgSend)(
            proxy, sel_registerName("extensionPoint")) : nil;
        id proxyProtocol = proxy ? ((id (*)(id, SEL))objc_msgSend)(
            proxy, sel_registerName("protocol")) : nil;
        id proxyRecord = proxy ? ((id (*)(id, SEL))objc_msgSend)(
            proxy, sel_registerName("correspondingApplicationExtensionRecord"))
            : nil;
        printf("LSPROXY class=%s platform=%s point=%s protocol=%s proxy=%s\n",
               proxy ? object_getClassName(proxy) : "nil",
               proxyPlatform ? [[proxyPlatform description] UTF8String] : "nil",
               proxyPoint ? [[proxyPoint description] UTF8String] : "nil",
               proxyProtocol ? [[proxyProtocol description] UTF8String] : "nil",
               proxy ? [[proxy description] UTF8String] : "nil");
        if (proxyRecord) {
            unsigned recordPlatform = ((unsigned (*)(id, SEL))objc_msgSend)(
                proxyRecord, sel_registerName("platform"));
            id recordIdentifier = ((id (*)(id, SEL))objc_msgSend)(
                proxyRecord, sel_registerName("bundleIdentifier"));
            id recordURL = ((id (*)(id, SEL))objc_msgSend)(
                proxyRecord, sel_registerName("URL"));
            id pointRecord = ((id (*)(id, SEL))objc_msgSend)(
                proxyRecord, sel_registerName("extensionPointRecord"));
            printf("LSRECORD class=%s platform=%u identifier=%s url=%s point=%s record=%s\n",
                   object_getClassName(proxyRecord), recordPlatform,
                   recordIdentifier ? [[recordIdentifier description] UTF8String] : "nil",
                   recordURL ? [[recordURL description] UTF8String] : "nil",
                   pointRecord ? [[pointRecord description] UTF8String] : "nil",
                   [[proxyRecord description] UTF8String]);
            Class applicationExtensionRecordClass =
                objc_getClass("LSApplicationExtensionRecord");
            for (unsigned long long options = 0; options <= 8;
                 options = options ? options * 2 : 1) {
                id recordEnumerator = ((id (*)(id, SEL, id,
                                                unsigned long long))objc_msgSend)(
                    applicationExtensionRecordClass,
                    sel_registerName("enumeratorWithExtensionPointRecord:options:"),
                    pointRecord, options);
                NSUInteger recordCount = 0;
                BOOL foundAppearance = NO;
                for (; recordCount < 10000; recordCount++) {
                    id record = ((id (*)(id, SEL))objc_msgSend)(
                        recordEnumerator, sel_registerName("nextObject"));
                    if (!record) break;
                    id identifier = ((id (*)(id, SEL))objc_msgSend)(
                        record, sel_registerName("bundleIdentifier"));
                    if ([identifier isEqual:
                            @"com.apple.Appearance-Settings.extension"]) {
                        foundAppearance = YES;
                    }
                }
                printf("LSENUM options=%llu count=%lu foundAppearance=%u\n",
                       options, (unsigned long)recordCount,
                       foundAppearance ? 1 : 0);
            }
        } else {
            printf("LSRECORD nil\n");
        }
        if (settingsBundle) CFRelease(settingsBundle);
    }
    return 0;
}

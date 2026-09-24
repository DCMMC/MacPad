// Read-only Ventura LaunchServices diagnostic.  It resolves the exact
// Objective-C implementation that lsd reports in its exception backtrace and
// emits unslid coordinates plus raw instructions for offline disassembly.
//
// Build:
//   xcrun --sdk macosx clang -arch arm64 -mmacosx-version-min=13.0 -O2 \
//     -framework Foundation misc/macws_launchservices_defaults_probe.m \
//     -o /tmp/macws_launchservices_defaults_probe
#import <Foundation/Foundation.h>
#import <dlfcn.h>
#import <mach-o/dyld.h>
#import <mach-o/loader.h>
#import <objc/runtime.h>
#import <objc/message.h>
#import <stdint.h>
#import <stdio.h>
#import <errno.h>
#import <pwd.h>
#import <sys/stat.h>
#import <unistd.h>

static intptr_t ImageSlideForHeader(const struct mach_header *header) {
    for (uint32_t index = 0; index < _dyld_image_count(); ++index) {
        if (_dyld_get_image_header(index) == header)
            return _dyld_get_image_vmaddr_slide(index);
    }
    return 0;
}

static void DescribeUnslidAddress(uintptr_t unslid, intptr_t slide) {
    const void *runtime = (const void *)(unslid + slide);
    Dl_info info = {0};
    if (dladdr(runtime, &info)) {
        printf("target unslid=0x%llx runtime=%p symbol=%s+0x%llx\n",
               (unsigned long long)unslid, runtime,
               info.dli_sname ?: "?",
               (unsigned long long)(info.dli_saddr
                   ? (uintptr_t)runtime - (uintptr_t)info.dli_saddr : 0));
    } else {
        printf("target unslid=0x%llx runtime=%p unresolved\n",
               (unsigned long long)unslid, runtime);
    }
}

static void DescribeUnslidPointerSlot(uintptr_t unslid, intptr_t slide) {
    const void *slot = (const void *)(unslid + slide);
    const void *value = *(const void * const *)slot;
    Dl_info info = {0};
    if (dladdr(value, &info)) {
        printf("pointer-slot unslid=0x%llx runtime=%p value=%p "
               "symbol=%s+0x%llx\n",
               (unsigned long long)unslid, slot, value,
               info.dli_sname ?: "?",
               (unsigned long long)(info.dli_saddr
                   ? (uintptr_t)value - (uintptr_t)info.dli_saddr : 0));
    } else {
        printf("pointer-slot unslid=0x%llx runtime=%p value=%p unresolved\n",
               (unsigned long long)unslid, slot, value);
    }
}

int main(void) {
    @autoreleasepool {
        const char *path =
            "/System/Library/Frameworks/CoreServices.framework/Frameworks/"
            "LaunchServices.framework/LaunchServices";
        void *image = dlopen(path, RTLD_NOW | RTLD_LOCAL);
        if (!image) {
            fprintf(stderr, "dlopen failed: %s\n", dlerror());
            return 1;
        }

        Class defaults = NSClassFromString(@"_LSDefaults");
        SEL selector = NSSelectorFromString(
            @"systemContentDatabaseStoreFileURLWithUID:");
        Method method = defaults ? class_getInstanceMethod(defaults, selector)
                                 : NULL;
        if (!method) {
            fprintf(stderr, "method missing class=%p\n", defaults);
            return 2;
        }

        const unsigned char *implementation =
            (const unsigned char *)(uintptr_t)method_getImplementation(method);
        Dl_info info = {0};
        if (!dladdr(implementation, &info) || !info.dli_fbase) {
            fprintf(stderr, "dladdr failed imp=%p\n", implementation);
            return 3;
        }
        intptr_t slide = ImageSlideForHeader(info.dli_fbase);
        printf("class=%s selector=%s encoding=%s\n",
               class_getName(defaults), sel_getName(selector),
               method_getTypeEncoding(method));
        printf("image=%s base=%p imp=%p offset=0x%llx slide=0x%llx "
               "unslid=0x%llx\n",
               info.dli_fname, info.dli_fbase, implementation,
               (unsigned long long)((uintptr_t)implementation -
                                    (uintptr_t)info.dli_fbase),
               (unsigned long long)slide,
               (unsigned long long)((uintptr_t)implementation - slide));
        const uintptr_t branchTargets[] = {
            0x180bab880, 0x180b11768, 0x180bafd20, 0x180bb4d20,
            0x180ba7780, 0x180ba7720, 0x180b11724,
            0x180bab7a0, 0x180b111c8, 0x180bab580, 0x180bb4ee0,
            0x180ba7740, 0x180b11728,
            0x180bab660,
            0x180b109e8, 0x180b11548, 0x180badb00, 0x180b11748,
            0x180bac880, 0x180bab840, 0x180b11b08, 0x180b109a8,
            0x180a72700, 0x180b11868, 0x180b11738,
            0x180b06b68, 0x180b06bec,
        };
        for (size_t index = 0;
             index < sizeof(branchTargets) / sizeof(*branchTargets); ++index)
            DescribeUnslidAddress(branchTargets[index], slide);
        const uintptr_t pointerSlots[] = {
            0x1dff1a6f0, 0x1dff6c1f0, 0x1dff6c420, 0x1dff1e650,
            0x1dff1a520, 0x1dff1dba8, 0x1dff6c418,
            0x1dff21490, 0x1dff214c8, 0x1dff20f88,
            0x1dff21120, 0x1dff210c0, 0x1dff21608,
        };
        for (size_t index = 0;
             index < sizeof(pointerSlots) / sizeof(*pointerSlots); ++index)
            DescribeUnslidPointerSlot(pointerSlots[index], slide);
        NSString *constant = (__bridge NSString *)(void *)(
            (uintptr_t)0x1dd19b3a8 + slide);
        printf("constant unslid=0x1dd19b3a8 value=%s\n",
               constant.UTF8String ?: "(nil)");
        const char *environmentName = (const char *)(uintptr_t)(
            0x180b2d9ff + slide);
        const char *environmentFallback = (const char *)(uintptr_t)(
            0x180b2da0e + slide);
        NSString *format = (__bridge NSString *)(void *)(
            (uintptr_t)0x1dd19b388 + slide);
        void *global = *(void **)(uintptr_t)(0x1d8ac8d28 + slide);
        printf("cstring unslid=0x180b2d9ff value=%s\n", environmentName);
        printf("cstring unslid=0x180b2da0e value=%s\n", environmentFallback);
        printf("constant unslid=0x1dd19b388 value=%s\n",
               format.UTF8String ?: "(nil)");
        Dl_info globalInfo = {0};
        dladdr(global, &globalInfo);
        printf("global unslid=0x1d8ac8d28 value=%p symbol=%s\n", global,
               globalInfo.dli_sname ?: "?");
        NSString *vaultComponent = (__bridge NSString *)(void *)(
            (uintptr_t)0x1dd19b908 + slide);
        void *vaultClass = *(void **)(uintptr_t)(0x1d8ac8ce8 + slide);
        const char *vaultLog = (const char *)(uintptr_t)(0x180b2e05d + slide);
        Dl_info vaultClassInfo = {0};
        dladdr(vaultClass, &vaultClassInfo);
        printf("constant unslid=0x1dd19b908 value=%s\n",
               vaultComponent.UTF8String ?: "(nil)");
        printf("global unslid=0x1d8ac8ce8 value=%p symbol=%s\n", vaultClass,
               vaultClassInfo.dli_sname ?: "?");
        printf("cstring unslid=0x180b2e05d value=%s\n", vaultLog);
        void *(*userLocalDirname)(unsigned int, int, char *, size_t) =
            dlsym(RTLD_DEFAULT, "__user_local_dirname");
        if (userLocalDirname) {
            for (unsigned int uid = 0; uid <= 501; uid += 501) {
                char localDirectory[1024] = {};
                errno = 0;
                void *result = userLocalDirname(uid, 0, localDirectory,
                                                sizeof(localDirectory));
                int savedErrno = errno;
                struct stat status = {};
                int statResult = stat(localDirectory, &status);
                printf("user-local-dirname uid=%u result=%p value=%s "
                       "errno=%d stat=%d stat_errno=%d mode=%#o uid=%u "
                       "gid=%u writable=%d\n",
                       uid, result, localDirectory[0]
                           ? localDirectory : "(empty)",
                       savedErrno, statResult,
                       statResult ? errno : 0,
                       statResult ? 0 : status.st_mode,
                       statResult ? 0 : status.st_uid,
                       statResult ? 0 : status.st_gid,
                       localDirectory[0] ? access(localDirectory, W_OK) : -1);
            }
        } else {
            printf("user-local-dirname symbol missing\n");
        }
        for (size_t offset = 0; offset < 256; offset += 4) {
            uint32_t instruction = 0;
            memcpy(&instruction, implementation + offset, sizeof(instruction));
            printf("0x%llx %08x\n",
                   (unsigned long long)((uintptr_t)implementation - slide +
                                        offset),
                   instruction);
        }

        SEL databaseSelector = NSSelectorFromString(@"databaseStoreFileURLWithUID:");
        Method databaseMethod = class_getInstanceMethod(defaults,
                                                        databaseSelector);
        if (!databaseMethod) {
            fprintf(stderr, "database method missing\n");
            return 4;
        }
        const unsigned char *databaseImplementation =
            (const unsigned char *)(uintptr_t)method_getImplementation(
                databaseMethod);
        Dl_info databaseInfo = {0};
        if (!dladdr(databaseImplementation, &databaseInfo)) return 5;
        printf("database selector=%s encoding=%s imp=%p offset=0x%llx "
               "unslid=0x%llx\n",
               sel_getName(databaseSelector),
               method_getTypeEncoding(databaseMethod),
               databaseImplementation,
               (unsigned long long)((uintptr_t)databaseImplementation -
                                    (uintptr_t)databaseInfo.dli_fbase),
               (unsigned long long)((uintptr_t)databaseImplementation -
                                    slide));
        for (size_t offset = 0; offset < 512; offset += 4) {
            uint32_t instruction = 0;
            memcpy(&instruction, databaseImplementation + offset,
                   sizeof(instruction));
            printf("database 0x%llx %08x\n",
                   (unsigned long long)((uintptr_t)databaseImplementation -
                                        slide + offset),
                   instruction);
        }

        SEL sharedSelector = NSSelectorFromString(@"sharedInstance");
        id object = [defaults respondsToSelector:sharedSelector]
            ? ((id (*)(id, SEL))objc_msgSend)(defaults, sharedSelector) : nil;
        printf("sharedInstance=%p\n", object);
        if (object) {
            id (*databaseURL)(id, SEL, unsigned int) =
                (id (*)(id, SEL, unsigned int))method_getImplementation(
                    databaseMethod);
            for (unsigned int uid = 0; uid <= 501; uid += 501) {
                id url = databaseURL(object, databaseSelector, uid);
                printf("databaseStoreFileURLWithUID uid=%u value=%s\n",
                       uid, [[url description] UTF8String] ?: "(nil)");
            }
        }

        SEL containerSelector = NSSelectorFromString(
            @"databaseContainerURLWithUID:");
        Method containerMethod = class_getInstanceMethod(defaults,
                                                         containerSelector);
        if (!containerMethod) return 6;
        const unsigned char *containerImplementation =
            (const unsigned char *)(uintptr_t)method_getImplementation(
                containerMethod);
        Dl_info containerInfo = {0};
        if (!dladdr(containerImplementation, &containerInfo)) return 7;
        printf("container selector=%s encoding=%s imp=%p offset=0x%llx "
               "unslid=0x%llx\n",
               sel_getName(containerSelector),
               method_getTypeEncoding(containerMethod),
               containerImplementation,
               (unsigned long long)((uintptr_t)containerImplementation -
                                    (uintptr_t)containerInfo.dli_fbase),
               (unsigned long long)((uintptr_t)containerImplementation -
                                    slide));
        for (size_t offset = 0; offset < 768; offset += 4) {
            uint32_t instruction = 0;
            memcpy(&instruction, containerImplementation + offset,
                   sizeof(instruction));
            printf("container 0x%llx %08x\n",
                   (unsigned long long)((uintptr_t)containerImplementation -
                                        slide + offset),
                   instruction);
        }
        if (object) {
            id (*containerURL)(id, SEL, unsigned int) =
                (id (*)(id, SEL, unsigned int))method_getImplementation(
                    containerMethod);
            for (unsigned int uid = 0; uid <= 501; uid += 501) {
                id url = containerURL(object, containerSelector, uid);
                printf("databaseContainerURLWithUID uid=%u value=%s\n",
                       uid, [[url description] UTF8String] ?: "(nil)");
            }
        }

        SEL dataVaultSelector = NSSelectorFromString(@"dataVaultURLWithUID:");
        Method dataVaultMethod = class_getInstanceMethod(defaults,
                                                         dataVaultSelector);
        if (!dataVaultMethod) return 8;
        const unsigned char *dataVaultImplementation =
            (const unsigned char *)(uintptr_t)method_getImplementation(
                dataVaultMethod);
        Dl_info dataVaultInfo = {0};
        if (!dladdr(dataVaultImplementation, &dataVaultInfo)) return 9;
        printf("data-vault selector=%s encoding=%s imp=%p offset=0x%llx "
               "unslid=0x%llx\n",
               sel_getName(dataVaultSelector),
               method_getTypeEncoding(dataVaultMethod),
               dataVaultImplementation,
               (unsigned long long)((uintptr_t)dataVaultImplementation -
                                    (uintptr_t)dataVaultInfo.dli_fbase),
               (unsigned long long)((uintptr_t)dataVaultImplementation -
                                    slide));
        for (size_t offset = 0; offset < 1024; offset += 4) {
            uint32_t instruction = 0;
            memcpy(&instruction, dataVaultImplementation + offset,
                   sizeof(instruction));
            printf("data-vault 0x%llx %08x\n",
                   (unsigned long long)((uintptr_t)dataVaultImplementation -
                                        slide + offset),
                   instruction);
        }
        if (object) {
            id (*dataVaultURL)(id, SEL, unsigned int) =
                (id (*)(id, SEL, unsigned int))method_getImplementation(
                    dataVaultMethod);
            for (unsigned int uid = 0; uid <= 501; uid += 501) {
                errno = 0;
                id url = dataVaultURL(object, dataVaultSelector, uid);
                int savedErrno = errno;
                struct passwd *entry = getpwuid(uid);
                printf("dataVaultURLWithUID uid=%u value=%s errno=%d "
                       "pw_name=%s pw_dir=%s\n",
                       uid, [[url description] UTF8String] ?: "(nil)",
                       savedErrno, entry && entry->pw_name
                           ? entry->pw_name : "(nil)",
                       entry && entry->pw_dir ? entry->pw_dir : "(nil)");
            }
        }
    }
    return 0;
}

// Read-only runtime witness; pass an existing file inside the chroot.
// Build with Apple clang/ld64 for each target slice and admit as a native probe.
#import <Foundation/Foundation.h>
#include <sys/mount.h>
#include <sys/fsgetpath.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <fcntl.h>
#include <unistd.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <objc/runtime.h>
#include <dlfcn.h>
#include <ptrauth.h>
#include <mach/mach.h>
#include <mach-o/dyld.h>
#include <mach-o/loader.h>

static void describe_hook_identity(void) {
    printf("namespace launch metadata=%s\n", getenv("MACWS_CHROOT_HOST_ROOT") ?: "<absent>");
    for (uint32_t i = 0; i < _dyld_image_count(); i++) {
        const char *name = _dyld_get_image_name(i);
        if (!name || !strstr(name, "libmachook")) continue;
        const struct mach_header_64 *header = (const struct mach_header_64 *)_dyld_get_image_header(i);
        if (!header || header->magic != MH_MAGIC_64 || header->sizeofcmds > 65536) continue;
        const unsigned char *cursor = (const unsigned char *)(header + 1);
        const unsigned char *end = cursor + header->sizeofcmds;
        for (uint32_t n = 0; n < header->ncmds && (size_t)(end-cursor) >= sizeof(struct load_command); n++) {
            const struct load_command *command = (const struct load_command *)cursor;
            if (command->cmdsize < sizeof(*command) || command->cmdsize > (size_t)(end-cursor)) break;
            if (command->cmd == LC_UUID && command->cmdsize == sizeof(struct uuid_command)) {
                const struct uuid_command *uuid = (const struct uuid_command *)command;
                printf("namespace mapped-image=%s UUID=", name);
                for (unsigned byte = 0; byte < sizeof(uuid->uuid); byte++) printf("%02X", uuid->uuid[byte]);
                putchar('\n');
            }
            cursor += command->cmdsize;
        }
    }
}

static void describe_provider_method(id receiver, const char *selector) {
    Method method = class_getInstanceMethod(object_getClass(receiver),
        sel_registerName(selector));
    if (!method) return;
    uintptr_t entry = (uintptr_t)ptrauth_strip(method_getImplementation(method), ptrauth_key_function_pointer);
    Dl_info image = {};
    (void)dladdr((void *)entry, &image);
    uint32_t code[64] = {};
    vm_size_t received = 0;
    kern_return_t result = vm_read_overwrite(mach_task_self(), (vm_address_t)entry,
        sizeof(code), (vm_address_t)code, &received);
    printf("NSURL-provider selector=%s class=%s image=%s offset=%#lx read=%d bytes=%llu code=",
        selector, object_getClassName(receiver), image.dli_fname ?: "?",
        (unsigned long)(entry - (uintptr_t)image.dli_fbase), result,
        (unsigned long long)received);
    if (result == KERN_SUCCESS)
        for (size_t index = 0; index < received / sizeof(code[0]); index++) printf("%08x", code[index]);
    putchar('\n');
    if (result != KERN_SUCCESS) return;
    for (size_t index = 0; index < received / sizeof(code[0]); index++) {
        if ((code[index] & 0xfc000000) != 0x94000000) continue;
        int64_t displacement = (int64_t)((int32_t)(code[index] << 6) >> 4);
        uintptr_t destination = entry + index * 4 + displacement;
        Dl_info target = {};
        (void)dladdr((void *)destination, &target);
        printf("NSURL-provider bl+%zu target=%p image=%s offset=%#lx symbol=%s\n",
            index * 4, (void *)destination, target.dli_fname ?: "?",
            (unsigned long)(destination - (uintptr_t)target.dli_fbase), target.dli_sname ?: "?");
    }
}

static void describe_provider(id receiver) {
    const char *selectors[] = {"getResourceValue:forKey:error:", "resourceValuesForKeys:error:",
        "getPromisedItemResourceValue:forKey:error:", "promisedItemResourceValuesForKeys:error:"};
    for (unsigned index = 0; index < sizeof(selectors) / sizeof(selectors[0]); index++)
        describe_provider_method(receiver, selectors[index]);
}

static BOOL path_is(CFURLRef url, const char *expected) {
    char value[PATH_MAX] = {};
    return url && CFURLGetFileSystemRepresentation(
        url, true, (UInt8 *)value, sizeof(value)) && !strcmp(value, expected);
}

static BOOL verify(const char *path) {
    struct statfs root = {}, byPath = {}, byDescriptor = {};
    struct stat rootNode = {}, node = {};
    int descriptor = open(path, O_RDONLY | O_CLOEXEC);
    BOOL ok = descriptor >= 0 && statfs("/", &root) == 0 &&
        statfs(path, &byPath) == 0 && fstatfs(descriptor, &byDescriptor) == 0 &&
        stat("/", &rootNode) == 0 && fstat(descriptor, &node) == 0;
    if (descriptor >= 0) close(descriptor);
    if (!ok) { perror("namespace metadata"); return NO; }
    ok = !strcmp(root.f_mntonname, "/") && !strcmp(byPath.f_mntonname, "/") &&
        !strcmp(byDescriptor.f_mntonname, "/");
    char rootPath[PATH_MAX] = {}, filePath[PATH_MAX] = {}, canonical[PATH_MAX] = {};
    ssize_t rootLength = fsgetpath(rootPath, sizeof(rootPath), &root.f_fsid, rootNode.st_ino);
    ssize_t fileLength = fsgetpath(filePath, sizeof(filePath), &byPath.f_fsid, node.st_ino);
    // Do not short-circuit this independent witness after a statfs mismatch:
    // that previously left canonical empty and falsely reported file-ID failure.
    BOOL canonicalResolved = realpath(path, canonical) != NULL;
    BOOL fileIDMatches = fileLength > 0 && canonicalResolved && !strcmp(filePath, canonical);
    ok = ok && rootLength > 0 && !strcmp(rootPath, "/") && fileLength > 0 &&
        fileIDMatches;
    CFURLRef rootURL = CFURLCreateFromFileSystemRepresentation(NULL, (const UInt8 *)"/", 1, true);
    CFURLRef fileURL = CFURLCreateFromFileSystemRepresentation(NULL, (const UInt8 *)path, strlen(path), false);
    CFTypeRef parent = NULL, volume = NULL;
    CFErrorRef error = NULL;
    Boolean rootResult = CFURLCopyResourcePropertyForKey(rootURL,
        kCFURLParentDirectoryURLKey, &parent, &error);
    Boolean volumeResult = CFURLCopyResourcePropertyForKey(fileURL,
        kCFURLVolumeURLKey, &volume, NULL);
    BOOL rootParentNil = rootResult && !parent && !error;
    BOOL volumeRoot = volumeResult && volume && CFGetTypeID(volume) == CFURLGetTypeID() &&
        path_is((CFURLRef)volume, "/");
    ok = ok && rootParentNil && volumeRoot;
    // Independently exercise Foundation's cross-image NSURL consumer too.
    NSURL *foundationURL = [NSURL fileURLWithPath:[NSString stringWithUTF8String:path]];
    NSURL *foundationVolume = nil;
    NSError *foundationError = nil;
    BOOL foundationResult = [foundationURL getResourceValue:&foundationVolume
        forKey:NSURLVolumeURLKey error:&foundationError];
    BOOL foundationRoot = foundationResult && !foundationError &&
        [[foundationVolume path] isEqualToString:@"/"];
    if (!foundationRoot) describe_provider(foundationURL);
    NSDictionary *rootValues = [[NSURL fileURLWithPath:@"/"] resourceValuesForKeys:
        @[NSURLParentDirectoryURLKey, NSURLVolumeURLKey] error:NULL];
    BOOL nsBulk = !rootValues[NSURLParentDirectoryURLKey] &&
        [[rootValues[NSURLVolumeURLKey] path] isEqualToString:@"/"];
    id promisedValue = nil;
    NSError *promisedError = nil;
    BOOL promisedSingle = [foundationURL getPromisedItemResourceValue:&promisedValue
        forKey:NSURLVolumeURLKey error:&promisedError] && !promisedError &&
        [[promisedValue path] isEqualToString:@"/"];
    NSDictionary *promisedValues = [[NSURL fileURLWithPath:@"/"]
        promisedItemResourceValuesForKeys:@[NSURLParentDirectoryURLKey, NSURLVolumeURLKey]
        error:&promisedError];
    BOOL promisedBulk = !promisedError && !promisedValues[NSURLParentDirectoryURLKey] &&
        [[promisedValues[NSURLVolumeURLKey] path] isEqualToString:@"/"];
    CFDictionaryRef cfValues = CFURLCopyResourcePropertiesForKeys(rootURL,
        (CFArrayRef)@[NSURLParentDirectoryURLKey, NSURLVolumeURLKey], NULL);
    BOOL cfBulk = cfValues && !CFDictionaryContainsKey(cfValues, kCFURLParentDirectoryURLKey) &&
        path_is(CFDictionaryGetValue(cfValues, kCFURLVolumeURLKey), "/");
    id bridgedValue = nil;
    BOOL bridgedURL = [(NSURL *)fileURL getResourceValue:&bridgedValue
        forKey:NSURLVolumeURLKey error:NULL] && [[bridgedValue path] isEqualToString:@"/"];
    if (cfValues) CFRelease(cfValues);
    printf("foundation-root-values parent=%s volume=%s\n",
        [[rootValues[NSURLParentDirectoryURLKey] description] UTF8String] ?: "<nil>",
        [[rootValues[NSURLVolumeURLKey] description] UTF8String] ?: "<nil>");
    printf("foundation result=%d volume=%s error-domain=%s error-code=%ld\n",
           foundationResult, [[foundationVolume path] UTF8String] ?: "<nil>",
           [[foundationError domain] UTF8String] ?: "<none>",
           (long)[foundationError code]);
    printf("resource-protocols ns-bulk=%d cf-bulk=%d promised-single=%d promised-bulk=%d bridged-url=%d\n",
        nsBulk, cfBulk, promisedSingle, promisedBulk, bridgedURL);
    ok = ok && foundationRoot && nsBulk && cfBulk && promisedSingle && promisedBulk && bridgedURL;
    printf("namespace pid=%d statfs=%s fstatfs=%s root-fileid=%s fileid-matches=%d "
           "root-parent-nil=%d cf-volume-root=%d ns-volume-root=%d result=%s\n",
           getpid(), byPath.f_mntonname, byDescriptor.f_mntonname, rootPath,
           fileIDMatches, rootParentNil, volumeRoot,
           foundationRoot, ok ? "PASS" : "FAIL");
    if (parent) CFRelease(parent);
    if (volume) CFRelease(volume);
    if (error) CFRelease(error);
    CFRelease(rootURL); CFRelease(fileURL);
    return ok;
}

int main(int argc, const char **argv) {
    if (argc != 2 || argv[1][0] != '/') {
        fprintf(stderr, "usage: macws_mount_namespace_probe /absolute/existing/file\n");
        return 64;
    }
    alarm(8);
    describe_hook_identity();
    @autoreleasepool {
        if (!verify(argv[1])) return 1;
        fflush(stdout);
        pid_t child = fork();
        if (child == 0) {
            // Do not require Objective-C/CoreFoundation to be fork-safe in a
            // multithreaded child. Verify that native atfork returns, using
            // only async-signal-safe calls before the ordinary child exit.
            const char reached[] = "namespace fork-child-reached-main\n";
            (void)write(STDOUT_FILENO, reached, sizeof(reached) - 1);
            _exit(0);
        }
        if (child < 0) { perror("fork"); return 1; }
        int status = 0;
        BOOL ok = waitpid(child, &status, 0) == child && WIFEXITED(status) &&
            WEXITSTATUS(status) == 0;
        if (ok) ok = verify(argv[1]);
        printf("namespace-after-fork child=%d status=%d result=%s\n", child, status,
               ok ? "PASS" : "FAIL");
        return ok ? 0 : 1;
    }
}

// Read-only runtime inventory for AppServerSupport's private launchd wrapper.
//
// This deliberately does not create or submit a job.  It exists so we can
// correlate the method implementations in the iPad's dyld shared cache with
// an exact selector before disassembling them through LLDB.

#import <Foundation/Foundation.h>
#import <dlfcn.h>
#import <objc/message.h>
#import <objc/runtime.h>
#import <ptrauth.h>
#import <xpc/xpc.h>
#include <signal.h>
#include <stdlib.h>
#include <unistd.h>

static void dump_methods(Class cls) {
    for (Class owner = cls; owner; owner = class_getSuperclass(owner)) {
        unsigned count = 0;
        Method *methods = class_copyMethodList(owner, &count);
        for (unsigned index = 0; index < count; index++) {
            Method method = methods[index];
            IMP implementation = method_getImplementation(method);
            printf("METHOD class=%s selector=%s imp=%p stripped=%p types=%s\n",
                   class_getName(owner),
                   sel_getName(method_getName(method)), implementation,
                   ptrauth_strip(implementation,
                                 ptrauth_key_function_pointer),
                   method_getTypeEncoding(method) ?: "?");
        }
        free(methods);
    }
}

static void dump_ivars(Class cls) {
    unsigned count = 0;
    Ivar *ivars = class_copyIvarList(cls, &count);
    for (unsigned index = 0; index < count; index++) {
        Ivar ivar = ivars[index];
        printf("IVAR class=%s name=%s offset=%td type=%s\n",
               class_getName(cls), ivar_getName(ivar) ?: "?",
               ivar_getOffset(ivar), ivar_getTypeEncoding(ivar) ?: "?");
    }
    free(ivars);
}

static void dump_class(const char *name) {
    Class cls = objc_getClass(name);
    printf("CLASS name=%s value=%p\n", name, cls);
    if (cls) {
        dump_ivars(cls);
        dump_methods(cls);
    }
}

static void run_submit_probe(id interface, id domain, BOOL rootDirectory) {
    if (!interface || !domain) return;

    char label[128];
    snprintf(label, sizeof(label), "com.macwsguide.launchd-root-probe.%d.%s",
             getpid(), rootDirectory ? "root" : "plain");
    xpc_object_t plist = xpc_dictionary_create(NULL, NULL, 0);
    xpc_dictionary_set_string(plist, "Label", label);
    xpc_dictionary_set_string(plist, "Program", "/usr/bin/true");
    xpc_object_t arguments = xpc_array_create(NULL, 0);
    xpc_array_set_string(arguments, XPC_ARRAY_APPEND, "/usr/bin/true");
    xpc_dictionary_set_value(plist, "ProgramArguments", arguments);
    if (rootDirectory) {
        xpc_dictionary_set_string(plist, "RootDirectory",
                                  "/var/mnt/rootfs");
    }

    char *plistDescription = xpc_copy_description(plist);
    printf("SUBMIT begin root=%s domain=%s plist=%s\n",
           rootDirectory ? "yes" : "no",
           [[domain description] UTF8String],
           plistDescription ?: "(null)");
    free(plistDescription);

    id job = ((id (*)(id, SEL, xpc_object_t, id))objc_msgSend)(
        interface, sel_registerName("jobWithPlist:domain:"), plist, domain);
    __autoreleasing NSError *error = nil;
    id result = job
        ? ((id (*)(id, SEL, NSError **))objc_msgSend)(
              job, sel_registerName("submitAndStart:"), &error)
        : nil;
    printf("SUBMIT result root=%s job=%s result=%s error=%s\n",
           rootDirectory ? "yes" : "no",
           job ? [[job description] UTF8String] : "nil",
           result ? [[result description] UTF8String] : "nil",
           error ? [[error description] UTF8String] : "nil");

    if (job) {
        __autoreleasing NSError *removeError = nil;
        BOOL removed = ((BOOL (*)(id, SEL, NSError **))objc_msgSend)(
            job, sel_registerName("remove:"), &removeError);
        printf("SUBMIT remove root=%s removed=%s error=%s\n",
               rootDirectory ? "yes" : "no", removed ? "yes" : "no",
               removeError ? [[removeError description] UTF8String] : "nil");
    }
}

int main(void) {
    @autoreleasepool {
        const char *path =
            "/System/Library/PrivateFrameworks/"
            "AppServerSupport.framework/AppServerSupport";
        void *handle = dlopen(path, RTLD_NOW | RTLD_LOCAL);
        printf("FRAMEWORK path=%s handle=%p error=%s\n", path, handle,
               handle ? "none" : (dlerror() ?: "unknown"));
        if (!handle) return 1;

        const char *runningBoardPath =
            "/System/Library/PrivateFrameworks/"
            "RunningBoard.framework/RunningBoard";
        void *runningBoard =
            dlopen(runningBoardPath, RTLD_NOW | RTLD_LOCAL);
        printf("FRAMEWORK path=%s handle=%p error=%s\n", runningBoardPath,
               runningBoard,
               runningBoard ? "none" : (dlerror() ?: "unknown"));

        dump_class("OSLaunchdJob");
        dump_class("OSLaunchdDomain");
        dump_class("RBLaunchdInterface");
        dump_class("RBSXPCServiceProcessIdentity");

        Class interfaceClass = objc_getClass("RBLaunchdInterface");
        id interface = interfaceClass && getenv("MACWS_PROBE_INTERFACE")
            ? ((id (*)(id, SEL))objc_msgSend)(
                  ((id (*)(id, SEL))objc_msgSend)(
                      (id)interfaceClass, sel_registerName("alloc")),
                  sel_registerName("init"))
            : nil;
        id currentDomain = interface
            ? ((id (*)(id, SEL))objc_msgSend)(
                  interface, sel_registerName("currentDomain"))
            : nil;
        id userDomain = interface
            ? ((id (*)(id, SEL, unsigned))objc_msgSend)(
                  interface, sel_registerName("domainForUser:"), 501)
            : nil;
        id pidDomain = interface
            ? ((id (*)(id, SEL, int))objc_msgSend)(
                  interface, sel_registerName("domainForPid:"), getpid())
            : nil;
        printf("INTERFACE value=%p class=%s current=%s user501=%s pid=%s\n",
               interface, interface ? object_getClassName(interface) : "nil",
               currentDomain
                   ? [[currentDomain description] UTF8String] : "nil",
               userDomain ? [[userDomain description] UTF8String] : "nil",
               pidDomain ? [[pidDomain description] UTF8String] : "nil");

        if (getenv("MACWS_PROBE_SUBMIT")) {
            run_submit_probe(interface, userDomain, NO);
            run_submit_probe(interface, userDomain, YES);
        }
        fflush(stdout);
        if (getenv("MACWS_PROBE_HOLD")) raise(SIGSTOP);
    }
    return 0;
}

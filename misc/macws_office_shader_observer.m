/* Explicitly injected, process-local DIAGNOSTIC. Never production-linked.
 * Observe the device the app actually requests, then its actual library class.
 * No constructor, extra device creation, guessed class, source/constant edits,
 * fallback, code-page patch, or GPU command. Compile MRC, Foundation/Metal.
 */
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <objc/runtime.h>
#include <errno.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

enum { SHADER_OBS_LIMIT = 8, SHADER_CLASS_LIMIT = 8 };
typedef id (*LibraryIMP)(id, SEL, dispatch_data_t, NSError **);
typedef id (*FunctionIMP)(id, SEL, NSString *, MTLFunctionConstantValues *, NSError **);
typedef struct { Class cls; IMP original; } Hook;
static Hook devices[SHADER_CLASS_LIMIT], libraries[SHADER_CLASS_LIMIT];
static pthread_mutex_t hooks_lock = PTHREAD_MUTEX_INITIALIZER;
static _Atomic unsigned library_samples, function_samples;
static _Thread_local int shader_observing;
static id shader_function_at(unsigned, id, SEL, NSString *, MTLFunctionConstantValues *, NSError **);
static id shader_library_at(unsigned, id, SEL, dispatch_data_t, NSError **);
// A distinct typed entry per concrete class also preserves an override that
// calls [super ...]. Looking up only object_getClass(self) inside one universal
// replacement would redispatch the subclass original and recurse in that case.
#define SHADER_SLOT(N) \
static id shader_function_##N(id self, SEL cmd, NSString *name, MTLFunctionConstantValues *values, NSError **error) { \
    return shader_function_at(N, self, cmd, name, values, error); } \
static id shader_library_##N(id self, SEL cmd, dispatch_data_t data, NSError **error) { \
    return shader_library_at(N, self, cmd, data, error); }
SHADER_SLOT(0) SHADER_SLOT(1) SHADER_SLOT(2) SHADER_SLOT(3)
SHADER_SLOT(4) SHADER_SLOT(5) SHADER_SLOT(6) SHADER_SLOT(7)
#undef SHADER_SLOT
static const IMP function_wrappers[SHADER_CLASS_LIMIT] = {
    (IMP)shader_function_0, (IMP)shader_function_1, (IMP)shader_function_2, (IMP)shader_function_3,
    (IMP)shader_function_4, (IMP)shader_function_5, (IMP)shader_function_6, (IMP)shader_function_7
};
static const IMP library_wrappers[SHADER_CLASS_LIMIT] = {
    (IMP)shader_library_0, (IMP)shader_library_1, (IMP)shader_library_2, (IMP)shader_library_3,
    (IMP)shader_library_4, (IMP)shader_library_5, (IMP)shader_library_6, (IMP)shader_library_7
};

static void shader_emit(const char *line, int count) {
    if (count <= 0 || count >= 1024) return;
    size_t length = (size_t)count;
    while (length) {
        ssize_t n = write(STDERR_FILENO, line, length);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) break;
        line += n; length -= (size_t)n;
    }
}
static unsigned shader_claim(_Atomic unsigned *counter) {
    unsigned value = atomic_load_explicit(counter, memory_order_relaxed);
    do { if (value >= SHADER_OBS_LIMIT) return 0; }
    while (!atomic_compare_exchange_weak_explicit(counter, &value, value + 1,
            memory_order_relaxed, memory_order_relaxed));
    return value + 1;
}
static BOOL shader_type(Method method, unsigned index, const char *wanted) {
    char *type = index == UINT_MAX ? method_copyReturnType(method) :
                                   method_copyArgumentType(method, index);
    const char *base = type;
    while (base && *base && strchr("rnNoORV", *base)) ++base;
    BOOL valid = base && !strcmp(base, wanted);
    free(type); return valid;
}
static IMP shader_original(Hook *table, unsigned slot) {
    pthread_mutex_lock(&hooks_lock);
    IMP result = table[slot].original;
    pthread_mutex_unlock(&hooks_lock);
    return result;
}
static BOOL shader_install(Hook *table, id object, SEL selector, BOOL function) {
    if (!object) return NO;
    Class cls = object_getClass(object);
    pthread_mutex_lock(&hooks_lock);
    for (unsigned i = 0; i < SHADER_CLASS_LIMIT; ++i)
        if (table[i].cls == cls) { pthread_mutex_unlock(&hooks_lock); return YES; }
    unsigned slot = 0;
    while (slot < SHADER_CLASS_LIMIT && table[slot].cls) ++slot;
    Method method = class_getInstanceMethod(cls, selector);
    BOOL valid = slot < SHADER_CLASS_LIMIT && method &&
        method_getNumberOfArguments(method) == (function ? 5u : 4u) &&
        shader_type(method, UINT_MAX, "@") && shader_type(method, 0, "@") &&
        shader_type(method, 1, ":") && shader_type(method, 2, "@") &&
        shader_type(method, function ? 4 : 3, "^@") &&
        (!function || shader_type(method, 3, "@"));
    if (valid) {
        const IMP *wrappers = function ? function_wrappers : library_wrappers;
        IMP replacement = wrappers[slot];
        IMP original = method_getImplementation(method);
        for (unsigned i = 0; i < SHADER_CLASS_LIMIT; ++i)
            if (original == wrappers[i]) { original = table[i].original; break; }
        valid = original && original != replacement;
        if (valid) {
            // Publish original before installing. Add an own method if this is
            // inherited; never alter the unobserved superclass or its siblings.
            table[slot] = (Hook){cls, original};
            if (!class_addMethod(cls, selector, replacement, method_getTypeEncoding(method)))
                method_setImplementation(class_getInstanceMethod(cls, selector), replacement);
        }
    }
    pthread_mutex_unlock(&hooks_lock);
    if (valid) {
        char line[1024];
        shader_emit(line, snprintf(line, sizeof(line),
            "OFFICE-SHADER ready=%s pid=%d class=%.160s selector=%.160s\n",
            function ? "library" : "device", getpid(), class_getName(cls), sel_getName(selector)));
    }
    return valid;
}
static void shader_observe_function(id self, NSString *name,
        MTLFunctionConstantValues *constants, NSError **error, id result,
        NSException *exception, unsigned ordinal) {
    if (!ordinal) return;
    // Never inspect an NSError slot after success: callers may leave it
    // untouched/uninitialized when the original operation succeeds.
    NSError *failure = !result && !exception && error ? *error : nil;
    char line[1024];
    shader_emit(line, snprintf(line, sizeof(line),
        "OFFICE-SHADER function pid=%d sample=%u/8 library=%p class=%.120s "
        "name=%.120s constants=%p error-slot=%p result=%p domain=%.100s code=%ld "
        "description=%.240s exception=%.100s\n", getpid(), ordinal,
        (void *)self, class_getName(object_getClass(self)), name.UTF8String,
        (void *)constants, (void *)error, (void *)result,
        failure ? failure.domain.UTF8String : "", failure ? (long)failure.code : 0,
        failure ? failure.localizedDescription.UTF8String : "",
        exception ? exception.name.UTF8String : ""));
}
static id shader_function_at(unsigned slot, id self, SEL cmd, NSString *name,
                           MTLFunctionConstantValues *constants, NSError **error) {
    int entry_errno = errno;
    FunctionIMP original = (FunctionIMP)shader_original(libraries, slot);
    unsigned ordinal = 0;
    if (!shader_observing && atomic_load_explicit(&function_samples, memory_order_relaxed) < SHADER_OBS_LIMIT) {
        shader_observing = 1;
        @try { if ([name hasPrefix:@"bitmap"]) ordinal = shader_claim(&function_samples); }
        @catch (NSException *exception) { (void)exception; }
        @finally { shader_observing = 0; }
    }
    errno = entry_errno;
    id result;
    @try { result = original(self, cmd, name, constants, error); }
    @catch (NSException *exception) {
        int saved_errno = errno;
        if (ordinal) {
            shader_observing = 1;
            @try { shader_observe_function(self, name, constants, error, nil, exception, ordinal); }
            @catch (NSException *diagnostic) { (void)diagnostic; }
            @finally { shader_observing = 0; errno = saved_errno; }
        }
        @throw;
    }
    int saved_errno = errno;
    if (ordinal) {
        shader_observing = 1;
        @try { shader_observe_function(self, name, constants, error, result, nil, ordinal); }
        @catch (NSException *exception) { (void)exception; }
        @finally { shader_observing = 0; }
    }
    errno = saved_errno; return result;
}
static id shader_library_at(unsigned slot, id self, SEL cmd, dispatch_data_t data, NSError **error) {
    int entry_errno = errno;
    LibraryIMP original = (LibraryIMP)shader_original(devices, slot);
    errno = entry_errno;
    id result = original(self, cmd, data, error);
    int saved_errno = errno;
    if (!shader_observing) {
        shader_observing = 1;
        @try {
            shader_install(libraries, result,
                @selector(newFunctionWithName:constantValues:error:), YES);
            unsigned ordinal = shader_claim(&library_samples);
            if (ordinal) {
                NSError *failure = !result && error ? *error : nil;
                char line[1024];
                shader_emit(line, snprintf(line, sizeof(line),
                    "OFFICE-SHADER load pid=%d sample=%u/8 device=%p data=%p "
                    "error-slot=%p library=%p class=%.120s domain=%.100s code=%ld "
                    "description=%.240s\n", getpid(), ordinal, (void *)self,
                    (void *)data, (void *)error, (void *)result,
                    result ? class_getName(object_getClass(result)) : "",
                    failure ? failure.domain.UTF8String : "", failure ? (long)failure.code : 0,
                    failure ? failure.localizedDescription.UTF8String : ""));
            }
        } @catch (NSException *exception) { (void)exception; }
        @finally { shader_observing = 0; }
    }
    errno = saved_errno; return result;
}
static void shader_device(id device) {
    int saved_errno = errno;
    if (!shader_observing) {
        shader_observing = 1;
        @try {
            shader_install(devices, device, @selector(newLibraryWithData:error:),
                           NO);
        } @catch (NSException *exception) { (void)exception; }
        @finally { shader_observing = 0; }
    }
    errno = saved_errno;
}

#ifdef MACWS_OFFICE_SHADER_TEST
static id shader_test_create(void);
static NSArray *shader_test_copy(void);
#define SHADER_CREATE shader_test_create
#define SHADER_COPY shader_test_copy
#else
#define SHADER_CREATE MTLCreateSystemDefaultDevice
#define SHADER_COPY MTLCopyAllDevices
#endif
static id shader_create_device(void) {
    id result = SHADER_CREATE();
    shader_device(result);
    return result;
}
static NSArray *shader_copy_devices(void) {
    NSArray *result = SHADER_COPY();
    int saved_errno = errno;
    @try { for (id device in result) shader_device(device); }
    @catch (NSException *exception) { (void)exception; }
    errno = saved_errno;
    return result;
}
#ifndef MACWS_OFFICE_SHADER_TEST
__attribute__((used, section("__DATA,__interpose")))
static const struct { const void *replacement; const void *replacee; } shader_interpose[] = {
    {(const void *)&shader_create_device, (const void *)&MTLCreateSystemDefaultDevice},
    {(const void *)&shader_copy_devices, (const void *)&MTLCopyAllDevices}
};
#endif

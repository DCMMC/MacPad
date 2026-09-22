// iOS-native foreground assertion supervisor for chroot GUI clients.
//
// The child stops before exec so RunningBoard can attach a focal CPU grant to
// its stable PID. The supervisor keeps the assertion alive until the child
// exits. This is an upstream process-policy adapter; it does not patch timing,
// V8, or graphics APIs.

#import <Foundation/Foundation.h>
#import <dlfcn.h>
#import <errno.h>
#import <objc/message.h>
#import <objc/runtime.h>
#import <signal.h>
#import <stdio.h>
#import <sys/wait.h>
#import <unistd.h>

static id send_id(id receiver, const char *selector) {
    return ((id (*)(id, SEL))objc_msgSend)(receiver,
                                           sel_registerName(selector));
}

static id send_id_pid(id receiver, const char *selector, pid_t pid) {
    return ((id (*)(id, SEL, pid_t))objc_msgSend)(receiver,
                                                  sel_registerName(selector),
                                                  pid);
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s command [args...]\n", argv[0]);
        return 64;
    }

    pid_t child = fork();
    if (child < 0) {
        perror("fork");
        return 71;
    }
    if (child == 0) {
        raise(SIGSTOP);
        execv(argv[1], &argv[1]);
        perror("execv");
        _exit(126);
    }

    int status = 0;
    if (waitpid(child, &status, WUNTRACED) != child || !WIFSTOPPED(status)) {
        fprintf(stderr, "rbs-foreground: child %d did not stop status=%#x\n",
                child, status);
        kill(child, SIGKILL);
        (void)waitpid(child, &status, 0);
        return 71;
    }

    @autoreleasepool {
        const char *path =
            "/System/Library/PrivateFrameworks/RunningBoardServices.framework/RunningBoardServices";
        void *framework = dlopen(path, RTLD_NOW | RTLD_LOCAL);
        if (!framework) {
            fprintf(stderr, "rbs-foreground: dlopen failed: %s\n",
                    dlerror() ?: "unknown");
            kill(child, SIGKILL);
            (void)waitpid(child, &status, 0);
            return 78;
        }
        const char *backboardPath =
            "/System/Library/PrivateFrameworks/BackBoardServices.framework/BackBoardServices";
        void *backboard = dlopen(backboardPath, RTLD_NOW | RTLD_LOCAL);
        if (!backboard) {
            fprintf(stderr, "rbs-foreground: BackBoardServices dlopen failed: %s\n",
                    dlerror() ?: "unknown");
        }

        Class targetClass = objc_getClass("RBSTarget");
        Class cpuGrantClass = objc_getClass("RBSCPUAccessGrant");
        Class assertionClass = objc_getClass("RBSAssertion");
        id target = send_id_pid((id)targetClass, "targetWithPid:", child);
        id cpuGrant = send_id((id)cpuGrantClass,
                              "grantWithUserInteractivityAndFocus");
        NSArray *attributes = cpuGrant ? @[cpuGrant] : @[];
        id allocated = send_id((id)assertionClass, "alloc");
        id assertion = ((id (*)(id, SEL, id, id, id))objc_msgSend)(
            allocated,
            sel_registerName("initWithExplanation:target:attributes:"),
            @"MacWS chroot foreground CPU", target, attributes);
        NSError *error = nil;
        BOOL acquired = ((BOOL (*)(id, SEL, NSError **))objc_msgSend)(
            assertion, sel_registerName("acquireWithError:"), &error);
        fprintf(stderr,
                "rbs-foreground: child=%d target=%s cpu=%s acquired=%d error=%s\n",
                child, [[target description] UTF8String],
                [[cpuGrant description] UTF8String], acquired,
                error ? [[error description] UTF8String] : "none");
        id bksAssertion = nil;
        if (!acquired) {
            // BackBoardServices' supported legacy adapter translates these
            // flags into server-validated RunningBoard attributes. 0xb is the
            // narrow combination of prevent-suspend (bit 0), prevent-throttle
            // (bit 1), and foreground-resource-priority (bit 3). Reason 7 is
            // the legacy BackgroundUI reason. Runtime logs and the scalar A/B
            // determine whether iOS 16 still honors this mapping.
            const unsigned flags = 0xb;
            const unsigned reason = 7;
            Class bksClass = objc_getClass("BKSProcessAssertion");
            id bksAllocated = send_id((id)bksClass, "alloc");
            bksAssertion =
                ((id (*)(id, SEL, pid_t, unsigned, unsigned, id, id, BOOL))
                    objc_msgSend)(
                    bksAllocated,
                    sel_registerName(
                        "initWithPID:flags:reason:name:withHandler:acquire:"),
                    child, flags, reason, @"MacWS chroot foreground", nil,
                    NO);
            BOOL bksAcquired = bksAssertion &&
                ((BOOL (*)(id, SEL))objc_msgSend)(
                    bksAssertion, sel_registerName("acquire"));
            fprintf(stderr,
                    "rbs-foreground: bks=%s flags=%#x reason=%u acquired=%d\n",
                    bksAssertion ? [[bksAssertion description] UTF8String]
                                 : "(nil)",
                    flags, reason, bksAcquired);
            if (!bksAcquired) {
                kill(child, SIGKILL);
                (void)waitpid(child, &status, 0);
                return 77;
            }
        }

        if (kill(child, SIGCONT) != 0) {
            perror("SIGCONT");
            ((void (*)(id, SEL))objc_msgSend)(assertion,
                                              sel_registerName("invalidate"));
            kill(child, SIGKILL);
            (void)waitpid(child, &status, 0);
            return 71;
        }
        do {
            if (waitpid(child, &status, 0) == child) break;
        } while (errno == EINTR);
        ((void (*)(id, SEL))objc_msgSend)(assertion,
                                          sel_registerName("invalidate"));
        if (bksAssertion) {
            ((void (*)(id, SEL))objc_msgSend)(
                bksAssertion, sel_registerName("invalidate"));
        }
    }

    if (WIFEXITED(status)) return WEXITSTATUS(status);
    if (WIFSIGNALED(status)) return 128 + WTERMSIG(status);
    return 1;
}

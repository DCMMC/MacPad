#import "MacWSCatalystLaunchCoordinator.h"

#import "MacWSHostDiagnostics.h"

#include <errno.h>
#include <fcntl.h>
#include <spawn.h>
#include <sys/wait.h>
#include <unistd.h>

CFStringRef const MacWSLaunchMapsFromHostNotification =
    CFSTR("com.macwsguide.host.launch-maps");
CFStringRef const MacWSLaunchCatalystFromHostNotification =
    CFSTR("com.macwsguide.host.launch-catalyst");

static const char MacWSCatalystLauncherPath[] =
    "/var/jb/Applications/MacWSCatalystLauncher.app/"
    "MacWSCatalystLauncher";
static const char MacWSCatalystLauncherLogPath[] =
    "/var/mobile/Library/Logs/MacWSCatalystLauncher.host.log";

static BOOL MacWSSpawnCatalystLauncher(const char *mode,
                                       const char *logPrefix,
                                       int *errorOut) {
    char *const arguments[] = {
        (char *)MacWSCatalystLauncherPath,
        (char *)mode,
        NULL,
    };
    extern char **environ;
    posix_spawn_file_actions_t actions;
    int actionsError = posix_spawn_file_actions_init(&actions);
    int logDescriptor = -1;
    if (actionsError == 0) {
        logDescriptor = open(MacWSCatalystLauncherLogPath,
                             O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC,
                             0644);
        if (logDescriptor >= 0) {
            (void)posix_spawn_file_actions_adddup2(
                &actions, logDescriptor, STDOUT_FILENO);
            (void)posix_spawn_file_actions_adddup2(
                &actions, logDescriptor, STDERR_FILENO);
            (void)posix_spawn_file_actions_addclose(&actions, logDescriptor);
        }
    }
    pid_t child = 0;
    int error = posix_spawn(&child, MacWSCatalystLauncherPath,
                            actionsError == 0 ? &actions : NULL,
                            NULL, arguments, environ);
    if (actionsError == 0) posix_spawn_file_actions_destroy(&actions);
    if (logDescriptor >= 0) close(logDescriptor);
    if (errorOut) *errorOut = error;
    MacWSLog(@"%s spawn result=%d child=%d parent=%d",
             logPrefix, error, child, getpid());
    if (error != 0 || child <= 1) return NO;

    NSString *prefix = @(logPrefix);
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{
        int status = 0;
        pid_t waited = 0;
        do {
            waited = waitpid(child, &status, 0);
        } while (waited < 0 && errno == EINTR);
        if (waited == child) {
            MacWSLog(@"%@ child-exit pid=%d exited=%@ code=%d "
                     "signaled=%@ signal=%d", prefix, child,
                     WIFEXITED(status) ? @"YES" : @"NO",
                     WIFEXITED(status) ? WEXITSTATUS(status) : -1,
                     WIFSIGNALED(status) ? @"YES" : @"NO",
                     WIFSIGNALED(status) ? WTERMSIG(status) : -1);
        } else {
            MacWSLog(@"%@ wait-failed pid=%d errno=%d",
                     prefix, child, errno);
        }
    });
    return YES;
}

static void MacWSCatalystLaunchNotificationCallback(
    __unused CFNotificationCenterRef center,
    __unused void *observer,
    CFStringRef name,
    __unused const void *object,
    __unused CFDictionaryRef userInfo) {
    BOOL maps = CFEqual(name, MacWSLaunchMapsFromHostNotification);
    const char *mode = maps ? "--exec-maps-from-host"
                            : "--exec-request-from-host";
    const char *prefix = maps ? "maps-host-carrier"
                              : "catalyst-host-carrier";
    dispatch_async(dispatch_get_main_queue(), ^{
        int error = 0;
        (void)MacWSSpawnCatalystLauncher(mode, prefix, &error);
    });
}

void MacWSInstallCatalystLaunchCoordinator(void) {
    static dispatch_once_t onceToken;
    dispatch_once(&onceToken, ^{
        CFNotificationCenterRef center =
            CFNotificationCenterGetDarwinNotifyCenter();
        CFNotificationCenterAddObserver(
            center, NULL, MacWSCatalystLaunchNotificationCallback,
            MacWSLaunchMapsFromHostNotification, NULL,
            CFNotificationSuspensionBehaviorDeliverImmediately);
        CFNotificationCenterAddObserver(
            center, NULL, MacWSCatalystLaunchNotificationCallback,
            MacWSLaunchCatalystFromHostNotification, NULL,
            CFNotificationSuspensionBehaviorDeliverImmediately);
    });
}

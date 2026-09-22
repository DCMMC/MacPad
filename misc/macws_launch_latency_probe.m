// Explicit one-shot device test, never packaged or started in production.
// EMBEDDED launcher experiment, not a request to the running hostd. Rebuild
// this binary whenever hostd source changes. Deployment acceptance MUST use
// macws_control_probe launch-path (the real service) or the Host UI instead.
// It may open ONE app selected by the operator, without publishing a service.
#define main MacWSUnusedDaemonMain
#include "../macwshostd/main.m"
#undef main

int main(int argc, const char *argv[]) {
    if (argc != 2 || getuid() != 0) {
        fprintf(stderr, "usage (root): macws_launch_latency_probe APP_ID|/absolute/App.app\n");
        return 64;
    }
    @autoreleasepool {
        setenv("PATH", "/var/jb/usr/bin:/var/jb/usr/sbin:/usr/bin:/bin:/usr/sbin:/sbin", 1);
        setenv("CA_DISABLE_SWAP_ICC", "1", 1);
        setenv("CA_VSYNC_OFF", "1", 1);
        gLogQueue = dispatch_queue_create("com.macwsguide.launch-probe.log", DISPATCH_QUEUE_SERIAL);
        gApplicationSessions = [NSMutableDictionary dictionary];
        NSString *message = nil;
        CFAbsoluteTime began = CFAbsoluteTimeGetCurrent();
        BOOL ready = argv[1][0] == '/'
            ? LaunchRequestedPath(argv[1], NO, &message)
            : LaunchAllowedApp(argv[1], &message);
        double elapsed = CFAbsoluteTimeGetCurrent() - began;
        dispatch_sync(gLogQueue, ^{});
        printf("LAUNCH-PROBE engine=embedded-not-live-hostd app=%s ready=%s pid=%d seconds=%.3f message=%s\n",
               argv[1], ready ? "yes" : "no", gActiveAppPID,
               elapsed, message.UTF8String ?: "");
        return ready ? 0 : 1;
    }
}

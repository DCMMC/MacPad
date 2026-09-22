#import <Cocoa/Cocoa.h>
#import <ApplicationServices/ApplicationServices.h>

int main(int argc, const char * argv[]) {
    @autoreleasepool {
        if (argc < 2) {
            NSLog(@"Usage: %s <PID>", argv[0]);
            return 1;
        }
        
        pid_t pid = (pid_t)atoi(argv[1]);
        NSRunningApplication *app = [NSRunningApplication runningApplicationWithProcessIdentifier:pid];
        
        if (app && [app activateWithOptions:
                (NSApplicationActivateAllWindows |
                 NSApplicationActivateIgnoringOtherApps)]) {
            NSLog(@"Activated pid %d through NSRunningApplication", pid);
            return 0;
        }

        // A chroot-launched AppKit process may have valid SkyLight windows but
        // no LaunchServices registration, so NSWorkspace cannot manufacture
        // an NSRunningApplication for it.  HIServices resolves the existing
        // process directly by PID and asks WindowServer to move its real
        // front window; it does not launch or synthesize another application.
        ProcessSerialNumber psn = {0, 0};
        OSStatus lookup = GetProcessForPID(pid, &psn);
        if (lookup != noErr) {
            NSLog(@"GetProcessForPID(%d) failed: %d", pid, (int)lookup);
            return 2;
        }
        OSStatus activate = SetFrontProcessWithOptions(
            &psn, kSetFrontProcessFrontWindowOnly |
                  kSetFrontProcessCausedByUser);
        if (activate != noErr) {
            NSLog(@"SetFrontProcessWithOptions(%d) failed: %d",
                  pid, (int)activate);
            return 3;
        }
        NSLog(@"Activated pid %d through HIServices", pid);
    }
    return 0;
}

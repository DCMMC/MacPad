// One-shot diagnosis of real NSBundle loading. No app activation or nib
// instantiation; never packaged. The loaded bundle must remain in scope.
#import <AppKit/AppKit.h>
#include <signal.h>
#include <unistd.h>
static void Deadline(int n) { (void)n; _exit(124); }
int main(int argc, const char **argv) {
    if (argc != 2) return 64;
    signal(SIGALRM, Deadline); alarm(15);
    @autoreleasepool {
        NSBundle *bundle = [NSBundle bundleWithPath:@(argv[1])];
        NSError *error = nil;
        BOOL loaded = [bundle loadAndReturnError:&error];
        printf("BUNDLE-PROBE path=%s exists=%d loaded=%d class=%s error=%s\n",
            argv[1], bundle != nil, loaded,
            loaded ? NSStringFromClass(bundle.principalClass).UTF8String : "none",
            error.description.UTF8String ?: "none");
        return loaded ? 0 : 1;
    }
}

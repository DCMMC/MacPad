#import <CoreFoundation/CoreFoundation.h>
#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN

extern CFStringRef const MacWSLaunchMapsFromHostNotification;
extern CFStringRef const MacWSLaunchCatalystFromHostNotification;

// Installs the two Darwin-notification entry points used by hostd. The
// foreground Host remains the responsible process, while request validation
// and the eventual chroot exec stay inside MacWSCatalystLauncher/hostd.
void MacWSInstallCatalystLaunchCoordinator(void);

NS_ASSUME_NONNULL_END

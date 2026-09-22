@import Darwin;
@import Foundation;

%hook NSProgress
+ (NSObject *)_addSubscriberForCategory:(id)category usingPublishingHandler:(id)handler {
    // For some reason the retain count is 1, need to retain one more before returning
    // ControlCenter: (AppKit) [com.apple.AppKit:General] NSProgress: you invoked +[NSProgress addSubscriber...] but then didn't pass the result to +[NSProgress removeSubscriber:] before it was released. Not allowed.
    // ControlCenter: (AppKit) [com.apple.AppKit:General] (
    //    0   CoreFoundation                      0x00000001a1cf3154 __exceptionPreprocess + 176
    //    1   libobjc.A.dylib                     0x00000001a18124d4 objc_exception_throw + 60
    //    2   Foundation                          0x00000001a2caae00 -[_NSProgressSubscriber dealloc] + 188
    //    3   ControlCenter                       0x0000000104ab7074 ControlCenter + 2682996
    id orig = %orig;
    if([orig retainCount] == 1) {
        [orig retain];
    }
    return orig;
}
%end

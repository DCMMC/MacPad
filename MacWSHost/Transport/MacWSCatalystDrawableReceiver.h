#pragma once

#import <Foundation/Foundation.h>

FOUNDATION_EXPORT NSNotificationName const
    MacWSCatalystDrawableDidPresentNotification;

// Starts the authenticated Catalyst IOSurface transport exactly once.
void MacWSStartCatalystDrawableReceiver(void);

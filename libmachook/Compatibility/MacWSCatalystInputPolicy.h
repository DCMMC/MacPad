#pragma once

#import <CoreGraphics/CoreGraphics.h>
#import <Foundation/Foundation.h>

// Returns YES only for the client-area portion of a real Mac Catalyst
// _UINSWindow.  Native title-bar/traffic-light chrome remains owned by
// WindowServer and must keep using the exact global pointer path.
BOOL MacWSCatalystWindowUsesProcessLocalInputAtPoint(id window,
                                                      CGPoint windowPoint);

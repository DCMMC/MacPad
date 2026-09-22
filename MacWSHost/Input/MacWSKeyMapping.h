#pragma once

#import <UIKit/UIKit.h>

// Pure keyboard mapping policy shared by hardware and software keyboard
// adapters.  This module performs no transport, responder or UI work.
uint16_t MacWSMacKeyCodeForHIDUsage(NSInteger usage);
uint32_t MacWSKeySymForHIDUsage(NSInteger usage, NSString *characters,
                                UIKeyModifierFlags modifiers);
NSInteger MacWSHIDUsageForASCII(uint32_t scalar);

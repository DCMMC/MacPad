#pragma once

#import <CoreFoundation/CoreFoundation.h>
#import <Security/Security.h>

void MacWSIdentityDiagnosticLogKeychainQuery(const char *api,
                                             CFDictionaryRef query,
                                             OSStatus nativeStatus,
                                             OSStatus finalStatus,
                                             CFTypeRef result,
                                             void *returnAddress);

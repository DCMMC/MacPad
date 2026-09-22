@import Foundation;
@import Security;

#include <string.h>
#include <unistd.h>

static void RunProbe(NSString *suffix, BOOL dataProtection,
                     BOOL preserveForNextProcess) {
    NSData *payload = [@"macws-keychain-probe" dataUsingEncoding:NSUTF8StringEncoding];
    NSMutableDictionary *query = [@{
        (__bridge id)kSecClass: (__bridge id)kSecClassGenericPassword,
        (__bridge id)kSecAttrService: @"com.macwsguide.keychain-probe",
        (__bridge id)kSecAttrAccount: suffix,
    } mutableCopy];
    if (dataProtection)
        query[(__bridge id)kSecUseDataProtectionKeychain] = @YES;
    if (!preserveForNextProcess)
        (void)SecItemDelete((__bridge CFDictionaryRef)query);
    NSMutableDictionary *attributes = [query mutableCopy];
    attributes[(__bridge id)kSecValueData] = payload;
    OSStatus add = SecItemAdd((__bridge CFDictionaryRef)attributes, NULL);

    NSMutableDictionary *lookup = [query mutableCopy];
    lookup[(__bridge id)kSecReturnData] = @YES;
    CFTypeRef result = NULL;
    OSStatus copy = SecItemCopyMatching(
        (__bridge CFDictionaryRef)lookup, &result);
    BOOL payloadMatches = result && CFGetTypeID(result) == CFDataGetTypeID() &&
        [(__bridge NSData *)result isEqualToData:payload];
    if (result) CFRelease(result);
    OSStatus remove = preserveForNextProcess ? errSecSuccess :
        SecItemDelete((__bridge CFDictionaryRef)query);
    printf("backend=%s add=%d copy=%d payload-match=%s delete=%d preserve=%s\n",
           suffix.UTF8String, (int)add, (int)copy,
           payloadMatches ? "yes" : "no", (int)remove,
           preserveForNextProcess ? "yes" : "no");
    fflush(stdout);
}

int main(void) {
    const char marker[] = "probe: entered-main\n";
    write(STDERR_FILENO, marker, sizeof(marker) - 1);
    @autoreleasepool {
        BOOL preserve = getenv("MACWS_KEYCHAIN_PROBE_PRESERVE") != NULL;
        RunProbe(@"default", NO, preserve);
        RunProbe(@"data-protection", YES, preserve);
    }
    return 0;
}

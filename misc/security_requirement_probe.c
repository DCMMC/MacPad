#include <CoreFoundation/CoreFoundation.h>
#include <Security/Security.h>
#include <arpa/inet.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/*
 * Verify the macOS code-requirement operations used by CoreLocationAgent.
 *
 * iPadOS 16's Security.framework accepts compiled requirement data but its
 * SecRequirementCreateWithString implementation returns errSecParam for the
 * macOS textual requirement received over the desktop registration protocol.
 * This probe exercises both paths against the same executable without
 * bypassing SecStaticCodeCheckValidity.
 */

enum {
    kMacWSRequirementMagic = 0xfade0c00,
    kMacWSRequirementKindExplicit = 1,
    kMacWSRequirementOpIdentifier = 2,
};

static void put_be32(uint8_t *where, uint32_t value) {
    value = htonl(value);
    memcpy(where, &value, sizeof(value));
}

static CFDataRef copy_identifier_requirement_data(const char *identifier) {
    size_t identifier_length = strlen(identifier);
    size_t padded_length = (identifier_length + 3u) & ~3u;
    size_t blob_length = 20u + padded_length;
    if (identifier_length == 0 || identifier_length > UINT32_MAX ||
        blob_length > UINT32_MAX) {
        return NULL;
    }

    uint8_t *blob = calloc(1, blob_length);
    if (!blob) return NULL;
    put_be32(blob + 0, kMacWSRequirementMagic);
    put_be32(blob + 4, (uint32_t)blob_length);
    put_be32(blob + 8, kMacWSRequirementKindExplicit);
    put_be32(blob + 12, kMacWSRequirementOpIdentifier);
    put_be32(blob + 16, (uint32_t)identifier_length);
    memcpy(blob + 20, identifier, identifier_length);

    CFDataRef data = CFDataCreate(kCFAllocatorDefault, blob,
                                  (CFIndex)blob_length);
    free(blob);
    return data;
}

int main(int argc, char **argv) {
    if (argc != 3 && argc != 4) {
        fprintf(stderr,
                "usage: %s /path/to/executable bundle.identifier [pid]\n",
                argv[0]);
        return 64;
    }

    CFStringRef path_string = CFStringCreateWithCString(
        kCFAllocatorDefault, argv[1], kCFStringEncodingUTF8);
    CFURLRef url = path_string ? CFURLCreateWithFileSystemPath(
        kCFAllocatorDefault, path_string, kCFURLPOSIXPathStyle, false) : NULL;
    SecStaticCodeRef code = NULL;
    OSStatus status = url ? SecStaticCodeCreateWithPath(url, 0, &code)
                          : errSecParam;
    printf("SecStaticCodeCreateWithPath=%d code=%p\n", (int)status, code);

    char text[1024];
    snprintf(text, sizeof(text), "identifier \"%s\"", argv[2]);
    CFStringRef requirement_string = CFStringCreateWithCString(
        kCFAllocatorDefault, text, kCFStringEncodingUTF8);
    SecRequirementRef text_requirement = NULL;
    status = SecRequirementCreateWithString(requirement_string, 0,
                                             &text_requirement);
    printf("SecRequirementCreateWithString=%d requirement=%p\n",
           (int)status, text_requirement);
    if (code && text_requirement) {
        status = SecStaticCodeCheckValidity(code, 0, text_requirement);
        printf("SecStaticCodeCheckValidity(text)=%d\n", (int)status);
    }

    CFDataRef requirement_data = copy_identifier_requirement_data(argv[2]);
    SecRequirementRef data_requirement = NULL;
    status = requirement_data
        ? SecRequirementCreateWithData(requirement_data, 0, &data_requirement)
        : errSecAllocate;
    printf("SecRequirementCreateWithData=%d requirement=%p\n",
           (int)status, data_requirement);
    if (code && data_requirement) {
        status = SecStaticCodeCheckValidity(code, 0, data_requirement);
        printf("SecStaticCodeCheckValidity(data)=%d\n", (int)status);
    }

    SecCodeRef live_code = NULL;
    CFNumberRef pid_number = NULL;
    CFDictionaryRef guest_attributes = NULL;
    if (argc == 4) {
        int pid = atoi(argv[3]);
        const void *keys[] = { kSecGuestAttributePid };
        pid_number = CFNumberCreate(kCFAllocatorDefault, kCFNumberIntType,
                                    &pid);
        const void *values[] = { pid_number };
        guest_attributes = pid_number ? CFDictionaryCreate(
            kCFAllocatorDefault, keys, values, 1,
            &kCFTypeDictionaryKeyCallBacks,
            &kCFTypeDictionaryValueCallBacks) : NULL;
        status = guest_attributes ? SecCodeCopyGuestWithAttributes(
            NULL, guest_attributes, 0, &live_code) : errSecParam;
        printf("SecCodeCopyGuestWithAttributes(pid=%d)=%d code=%p\n",
               pid, (int)status, live_code);
        if (live_code && data_requirement) {
            status = SecCodeCheckValidity(live_code, 0, data_requirement);
            printf("SecCodeCheckValidity(data)=%d\n", (int)status);
        }
    }

    if (data_requirement) CFRelease(data_requirement);
    if (requirement_data) CFRelease(requirement_data);
    if (text_requirement) CFRelease(text_requirement);
    if (requirement_string) CFRelease(requirement_string);
    if (code) CFRelease(code);
    if (url) CFRelease(url);
    if (path_string) CFRelease(path_string);
    if (live_code) CFRelease(live_code);
    if (guest_attributes) CFRelease(guest_attributes);
    if (pid_number) CFRelease(pid_number);
    return 0;
}

#import <CoreFoundation/CoreFoundation.h>
#import <dlfcn.h>
#import <stdio.h>
#import <stdlib.h>
#import <string.h>
#import <unistd.h>

/*
 * Rootless, read-only Apple-silicon GPU throttling witness for performance
 * tests.  The API is loaded dynamically because libIOReport is present in
 * the dyld shared cache but has no public SDK header.
 */
typedef CFDictionaryRef (*copy_channels_fn)(CFStringRef, CFStringRef,
                                             uint64_t, uint64_t, uint64_t);
typedef CFTypeRef (*create_subscription_fn)(CFTypeRef,
                                             CFMutableDictionaryRef,
                                             CFMutableDictionaryRef *,
                                             uint64_t, CFTypeRef);
typedef CFDictionaryRef (*create_samples_fn)(CFTypeRef,
                                              CFDictionaryRef,
                                              CFTypeRef);
typedef CFDictionaryRef (*create_delta_fn)(CFDictionaryRef, CFDictionaryRef,
                                            CFTypeRef);
typedef void (*iterate_fn)(CFDictionaryRef,
                           void (^)(CFDictionaryRef));
typedef CFStringRef (*channel_name_fn)(CFDictionaryRef);
typedef int64_t (*simple_value_fn)(CFDictionaryRef, int32_t);

static void *required_symbol(void *handle, const char *name) {
    void *symbol = dlsym(handle, name);
    if (symbol == NULL) {
        fprintf(stderr, "macos_gpu_throttle_probe: missing %s: %s\n",
                name, dlerror());
        exit(3);
    }
    return symbol;
}

static void print_json_string(CFStringRef value) {
    char buffer[256];
    if (value != NULL && CFStringGetCString(value, buffer, sizeof(buffer),
                                             kCFStringEncodingUTF8)) {
        putchar('"');
        for (const unsigned char *p = (const unsigned char *)buffer; *p; p++) {
            if (*p == '"' || *p == '\\') putchar('\\');
            putchar(*p);
        }
        putchar('"');
    } else {
        fputs("null", stdout);
    }
}

int main(int argc, char **argv) {
    double interval = 1.0;
    if (argc == 2) {
        char *end = NULL;
        interval = strtod(argv[1], &end);
        if (end == argv[1] || *end != '\0' || interval <= 0.0 ||
            interval > 300.0) {
            fprintf(stderr, "usage: %s [interval-seconds]\n", argv[0]);
            return 2;
        }
    } else if (argc != 1) {
        fprintf(stderr, "usage: %s [interval-seconds]\n", argv[0]);
        return 2;
    }

    void *handle = dlopen("/usr/lib/libIOReport.dylib", RTLD_NOW | RTLD_LOCAL);
    if (handle == NULL) {
        fprintf(stderr, "macos_gpu_throttle_probe: dlopen: %s\n", dlerror());
        return 3;
    }
    copy_channels_fn copy_channels = (copy_channels_fn)required_symbol(
        handle, "IOReportCopyChannelsInGroup");
    create_subscription_fn create_subscription =
        (create_subscription_fn)required_symbol(
            handle, "IOReportCreateSubscription");
    create_samples_fn create_samples = (create_samples_fn)required_symbol(
        handle, "IOReportCreateSamples");
    create_delta_fn create_delta = (create_delta_fn)required_symbol(
        handle, "IOReportCreateSamplesDelta");
    iterate_fn iterate = (iterate_fn)required_symbol(handle, "IOReportIterate");
    channel_name_fn channel_name = (channel_name_fn)required_symbol(
        handle, "IOReportChannelGetChannelName");
    simple_value_fn simple_value = (simple_value_fn)required_symbol(
        handle, "IOReportSimpleGetIntegerValue");

    CFDictionaryRef channels = copy_channels(
        CFSTR("GPU Stats"), CFSTR("GPU Throttler Counters"), 0, 0, 0);
    if (channels == NULL || CFDictionaryGetCount(channels) == 0) {
        fprintf(stderr, "macos_gpu_throttle_probe: no throttler channels\n");
        if (channels != NULL) CFRelease(channels);
        dlclose(handle);
        return 4;
    }
    CFMutableDictionaryRef subscribed_channels = NULL;
    CFTypeRef subscription = create_subscription(
        NULL, (CFMutableDictionaryRef)channels, &subscribed_channels,
        0, NULL);
    if (subscription == NULL || subscribed_channels == NULL) {
        fprintf(stderr, "macos_gpu_throttle_probe: subscription failed\n");
        if (subscribed_channels != NULL) CFRelease(subscribed_channels);
        if (subscription != NULL) CFRelease(subscription);
        CFRelease(channels);
        dlclose(handle);
        return 5;
    }
    CFDictionaryRef before = create_samples(
        subscription, subscribed_channels, NULL);
    if (before == NULL) {
        fprintf(stderr, "macos_gpu_throttle_probe: first sample failed\n");
        CFRelease(subscribed_channels);
        CFRelease(subscription);
        CFRelease(channels);
        dlclose(handle);
        return 5;
    }
    usleep((useconds_t)(interval * 1000000.0));
    CFDictionaryRef after = create_samples(
        subscription, subscribed_channels, NULL);
    CFDictionaryRef delta = after == NULL ? NULL :
        create_delta(before, after, NULL);
    if (delta == NULL) {
        fprintf(stderr, "macos_gpu_throttle_probe: delta sample failed\n");
        if (after != NULL) CFRelease(after);
        CFRelease(before);
        CFRelease(subscribed_channels);
        CFRelease(subscription);
        CFRelease(channels);
        dlclose(handle);
        return 5;
    }

    __block int count = 0;
    fputs("{\"interval_seconds\":", stdout);
    printf("%.3f,\"counters\":{", interval);
    iterate(delta, ^(CFDictionaryRef sample) {
        CFStringRef name = channel_name(sample);
        if (name == NULL) return;
        if (count++) putchar(',');
        print_json_string(name);
        printf(":%lld", (long long)simple_value(sample, 0));
    });
    fputs("}}\n", stdout);

    CFRelease(delta);
    CFRelease(after);
    CFRelease(before);
    CFRelease(subscribed_channels);
    CFRelease(subscription);
    CFRelease(channels);
    dlclose(handle);
    return count == 0 ? 6 : 0;
}

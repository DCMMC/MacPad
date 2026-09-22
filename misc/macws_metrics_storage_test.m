#import <Foundation/Foundation.h>
#include <assert.h>
#include <string.h>
#include "macws_stream_protocol.h"

int main(void) {
    @autoreleasepool {
        NSUInteger objcSize = 0, alignment = 0;
        NSGetSizeAndAlignment(@encode(MacWSWindowMetricsEntry), &objcSize, &alignment);
        printf("metrics-storage wire=%zu objc-encoded=%lu alignment=%lu encoding=%s\n",
            sizeof(MacWSWindowMetricsEntry), (unsigned long)objcSize,
            (unsigned long)alignment, @encode(MacWSWindowMetricsEntry));
        // ObjC type encodings do not describe __attribute__((packed)). Store
        // the wire record with an explicit byte length, not NSValue's inferred
        // natural ABI size. Guard both sides of the destination for regression.
        MacWSWindowMetricsEntry input = {0};
        input.windowID = 826;
        input.flags = MacWSStreamWindowVisible;
        input.configureAck.timestamp = 110871.685612;
        input.configureAck.sequence = 3028;
        input.configureAck.appliedWidth = 1077;
        input.configureAck.appliedHeight = 734;
        NSData *stored = [NSData dataWithBytes:&input length:sizeof(input)];
        struct { uint64_t before; MacWSWindowMetricsEntry entry; uint64_t after; } output;
        memset(&output, 0xA5, sizeof(output));
        assert(stored.length == sizeof(output.entry));
        [stored getBytes:&output.entry length:sizeof(output.entry)];
        assert(memcmp(&input, &output.entry, sizeof(input)) == 0);
        assert(output.before == UINT64_C(0xA5A5A5A5A5A5A5A5));
        assert(output.after == UINT64_C(0xA5A5A5A5A5A5A5A5));
        puts("metrics-storage byte-exact roundtrip and canaries: PASS");
    }
    return 0;
}

#include "macws_nocopy_abi.h"
#include <assert.h>
#include <pthread.h>
#include <stdio.h>

__thread struct MacWSNoCopyScope *g_macws_nocopy_scope;
static const uintptr_t address = UINT64_C(0x1042e4000);
static const size_t length = 16384;

static void put64(uint8_t *request, size_t offset, uint64_t value) {
    memcpy(request + offset, &value, sizeof(value));
}
static void fixture(uint8_t request[104]) {
    memset(request, 0, 104);
    put64(request, 0, 0x80);
    put64(request, 8, UINT64_C(0x0000000100010001));
    put64(request, 0x10, UINT64_C(0x0000047001000101));
    put64(request, 0x18, UINT64_C(0x0a0b0c0d01020304));
    put64(request, 0x30, 1);
    put64(request, 0x38, address);
    put64(request, 0x40, address);
    put64(request, 0x48, length);
    put64(request, 0x50, UINT64_C(0x1020304050607080));
    put64(request, 0x58, UINT64_C(0x8877665544332211));
    put64(request, 0x60, UINT64_C(0xfedcba9876543210));
}
static void rejects(const struct MacWSNoCopyScope *scope, uint8_t *source,
                    size_t size, size_t capacity) {
    uint8_t before[104], output[128], saved[128];
    memcpy(before, source, sizeof(before));
    memset(output, 0xa5, sizeof(output));
    memcpy(saved, output, sizeof(saved));
    assert(!MacWSNoCopyTranslateRequest(scope, source, size, output + 16, capacity));
    assert(!memcmp(before, source, sizeof(before)));
    assert(!memcmp(saved, output, sizeof(output)));
}

static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t entered = PTHREAD_COND_INITIALIZER;
static unsigned waiting;
static void *thread_scope(void *argument) {
    uintptr_t marker = (uintptr_t)argument;
    assert(g_macws_nocopy_scope == NULL);
    struct MacWSNoCopyScope outer = {marker, 16384, 16384, g_macws_nocopy_scope};
    g_macws_nocopy_scope = &outer;
    struct MacWSNoCopyScope inner = {marker + 16384, 32768, 16384, g_macws_nocopy_scope};
    g_macws_nocopy_scope = &inner;
    pthread_mutex_lock(&lock);
    ++waiting;
    pthread_cond_broadcast(&entered);
    while (waiting != 2) pthread_cond_wait(&entered, &lock);
    pthread_mutex_unlock(&lock);
    assert(g_macws_nocopy_scope == &inner && inner.previous == &outer);
    assert(g_macws_nocopy_scope->bytes == marker + 16384);
    g_macws_nocopy_scope = inner.previous;
    assert(g_macws_nocopy_scope == &outer);
    g_macws_nocopy_scope = outer.previous;
    assert(g_macws_nocopy_scope == NULL);
    return NULL;
}

int main(void) {
    struct MacWSNoCopyScope scope = {address, length, 16384, NULL};
    uint8_t raw[106], output[128];
    uint8_t *source = raw + 1; // Unaligned wire must be read through memcpy.
    fixture(source);
    uint8_t original[104];
    memcpy(original, source, sizeof(original));
    memset(output, 0xa5, sizeof(output));
    assert(MacWSNoCopyTranslateRequest(&scope, source, 104, output + 16, 96));
    assert(!memcmp(original, source, 104));
    assert(!memcmp(output + 16, original, 0x30));
    assert(!memcmp(output + 16 + 0x30, original + 0x38, 0x30));
    assert(MacWSNoCopyRead64(output + 16 + 0x30) == address);
    assert(MacWSNoCopyRead64(output + 16 + 0x38) == address);
    assert(MacWSNoCopyRead64(output + 16 + 0x40) == length);
    assert(MacWSNoCopyRead64(output + 16 + 0x58) == UINT64_C(0xfedcba9876543210));
    for (size_t i = 0; i < 16; ++i) assert(output[i] == 0xa5 && output[112 + i] == 0xa5);
    for (size_t n = 0; n < 104; ++n) rejects(&scope, source, n, 96);
    rejects(&scope, source, 105, 96); rejects(&scope, source, SIZE_MAX, 96);
    for (size_t n = 0; n < 96; ++n) rejects(&scope, source, 104, n);
    rejects(NULL, source, 104, 96);
    assert(!MacWSNoCopyTranslateRequest(&scope, NULL, 104, output, 96));
    assert(!MacWSNoCopyTranslateRequest(&scope, source, 104, NULL, 96));
    const struct { size_t offset; uint64_t value; } wrong[] = {
        {0,0}, {0,0x82}, {0,0x10080}, {0x20,1}, {0x28,1},
        {0x30,0}, {0x30,2}, {0x30,UINT64_C(0x100000001)},
        {0x38,address + 16384}, {0x40,address + 16384}, {0x48,length + 16384},
    };
    for (size_t i = 0; i < sizeof(wrong) / sizeof(wrong[0]); ++i) {
        fixture(source); put64(source, wrong[i].offset, wrong[i].value);
        rejects(&scope, source, 104, 96);
    }
    fixture(source); source[0x15] |= 8; rejects(&scope, source, 104, 96);
    fixture(source);
    struct MacWSNoCopyScope bad = scope;
    bad.bytes = 0; rejects(&bad, source, 104, 96);
    bad = scope; bad.length = 0; rejects(&bad, source, 104, 96);
    bad = scope; ++bad.bytes; rejects(&bad, source, 104, 96);
    bad = scope; ++bad.length; rejects(&bad, source, 104, 96);
    bad = scope; bad.page_size = 0; rejects(&bad, source, 104, 96);
    bad = scope; bad.page_size = 12288; rejects(&bad, source, 104, 96);
    bad = scope; bad.bytes = UINTPTR_MAX - 16383; rejects(&bad, source, 104, 96);
    assert(MacWSNoCopySpanValid(address, 16384, 4096));
    assert(MacWSNoCopySpanValid(address, 16384, 16384));

    uint8_t overlaps[400], saved[400];
    memset(overlaps, 0xa5, sizeof(overlaps)); fixture(overlaps + 150);
    memcpy(saved, overlaps, sizeof(saved));
    for (size_t dest = 55; dest < 254; ++dest) {
        assert(!MacWSNoCopyTranslateRequest(&scope, overlaps + 150, 104,
                                           overlaps + dest, 96));
        assert(!memcmp(overlaps, saved, sizeof(saved)));
    }
    assert(MacWSNoCopyTranslateRequest(&scope, overlaps + 150, 104, overlaps + 54, 96));
    assert(!memcmp(overlaps + 150, saved + 150, 104));
    memcpy(overlaps, saved, sizeof(saved));
    assert(MacWSNoCopyTranslateRequest(&scope, overlaps + 150, 104, overlaps + 254, 96));
    assert(!memcmp(overlaps + 150, saved + 150, 104));

    g_macws_nocopy_scope = &scope;
    pthread_t threads[2];
    assert(!pthread_create(&threads[0], NULL, thread_scope, (void *)(uintptr_t)0x100000));
    assert(!pthread_create(&threads[1], NULL, thread_scope, (void *)(uintptr_t)0x200000));
    for (size_t i = 0; i < 2; ++i) assert(!pthread_join(threads[i], NULL));
    assert(g_macws_nocopy_scope == &scope);
    g_macws_nocopy_scope = NULL;
    puts("NoCopy exact wire, immutable source, guards and TLS isolation PASS");
    return 0;
}

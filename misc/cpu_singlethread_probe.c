// Fixed-work single-thread CPU probe used to compare the same arm64 Mach-O on
// the M1 MacBook Air reference and in the iPad macOS chroot.  The arithmetic
// dependency chain prevents vectorization and exposes scheduler/core-class or
// gross execution-rate differences without involving Metal, WindowServer, or
// JavaScript.  The checksum is part of the witness: a timing number without
// the same completed work is not comparable.

#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <time.h>

#define MACWS_CPU_PROBE_ITERATIONS UINT64_C(1000000000)

static uint64_t monotonic_ns(void) {
    struct timespec value = {0};
    clock_gettime(CLOCK_MONOTONIC_RAW, &value);
    return (uint64_t)value.tv_sec * UINT64_C(1000000000) +
        (uint64_t)value.tv_nsec;
}

int main(void) {
    uint64_t value = UINT64_C(0x9e3779b97f4a7c15);
    uint64_t start = monotonic_ns();
    for (uint64_t i = 0; i < MACWS_CPU_PROBE_ITERATIONS; i++) {
        value ^= value >> 12;
        value ^= value << 25;
        value ^= value >> 27;
        value *= UINT64_C(0x2545f4914f6cdd1d);
    }
    uint64_t end = monotonic_ns();
    double seconds = (double)(end - start) / 1000000000.0;
    printf("iterations=%" PRIu64 " elapsed_seconds=%.9f "
           "million_iterations_per_second=%.3f checksum=%#" PRIx64 "\n",
           MACWS_CPU_PROBE_ITERATIONS, seconds,
           (double)MACWS_CPU_PROBE_ITERATIONS / seconds / 1000000.0, value);
    return 0;
}

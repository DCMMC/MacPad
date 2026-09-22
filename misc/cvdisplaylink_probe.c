#include <CoreVideo/CoreVideo.h>
#include <CoreGraphics/CoreGraphics.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

static _Atomic uint64_t callback_count;
static _Atomic uint64_t first_host_time;
static _Atomic uint64_t last_host_time;
static _Atomic uint64_t minimum_delta = UINT64_MAX;
static _Atomic uint64_t maximum_delta;

static CVReturn display_link_callback(CVDisplayLinkRef display_link,
                                      const CVTimeStamp *now,
                                      const CVTimeStamp *output,
                                      CVOptionFlags flags_in,
                                      CVOptionFlags *flags_out,
                                      void *context) {
    (void)display_link;
    (void)flags_in;
    (void)flags_out;
    (void)context;
    uint64_t current_host_time = CVGetCurrentHostTime();
    uint64_t now_host_time = now ? now->hostTime : 0;
    uint64_t host_time = output ? output->hostTime : current_host_time;
    uint64_t previous = atomic_exchange(&last_host_time, host_time);
    uint64_t sequence = atomic_fetch_add(&callback_count, 1) + 1;
    if (sequence == 1) atomic_store(&first_host_time, host_time);
    if (previous != 0 && host_time > previous) {
        uint64_t delta = host_time - previous;
        uint64_t old_min = atomic_load(&minimum_delta);
        while (delta < old_min &&
               !atomic_compare_exchange_weak(&minimum_delta, &old_min, delta)) {
        }
        uint64_t old_max = atomic_load(&maximum_delta);
        while (delta > old_max &&
               !atomic_compare_exchange_weak(&maximum_delta, &old_max, delta)) {
        }
        if (sequence <= 16) {
            double frequency = CVGetHostClockFrequency();
            fprintf(stderr,
                    "CVDL callback=%llu now=%llu output=%llu current=%llu "
                    "output_minus_now_ms=%.3f current_minus_now_ms=%.3f "
                    "output_minus_current_ms=%.3f delta_ms=%.3f "
                    "nowVideoTime=%lld outputVideoTime=%lld scale=%d "
                    "refreshPeriod=%lld nowFlags=%#llx outputFlags=%#llx\n",
                    (unsigned long long)sequence,
                    (unsigned long long)now_host_time,
                    (unsigned long long)host_time,
                    (unsigned long long)current_host_time,
                    (double)((int64_t)host_time - (int64_t)now_host_time) *
                        1000.0 / frequency,
                    (double)((int64_t)current_host_time -
                             (int64_t)now_host_time) * 1000.0 / frequency,
                    (double)((int64_t)host_time -
                             (int64_t)current_host_time) * 1000.0 / frequency,
                    (double)delta * 1000.0 / frequency,
                    now ? now->videoTime : 0,
                    output ? output->videoTime : 0,
                    output ? output->videoTimeScale : 0,
                    output ? output->videoRefreshPeriod : 0,
                    (unsigned long long)(now ? now->flags : 0),
                    (unsigned long long)(output ? output->flags : 0));
        }
    }
    return kCVReturnSuccess;
}

int main(int argc, char **argv) {
    CGDirectDisplayID displays[16] = {0};
    uint32_t display_count = 0;
    CGError display_result = CGGetActiveDisplayList(
        (uint32_t)(sizeof(displays) / sizeof(displays[0])), displays,
        &display_count);
    fprintf(stderr, "CVDL displays result=%d count=%u main=%u ids=",
            display_result, display_count, CGMainDisplayID());
    for (uint32_t index = 0; index < display_count; index++)
        fprintf(stderr, "%s%u", index ? "," : "", displays[index]);
    fputc('\n', stderr);

    CVDisplayLinkRef link = NULL;
    CVReturn result;
    if (argc > 1) {
        char *end = NULL;
        unsigned long parsed = strtoul(argv[1], &end, 0);
        if (!end || end == argv[1] || *end != '\0' || parsed > UINT32_MAX) {
            fprintf(stderr, "CVDL invalid display id: %s\n", argv[1]);
            return 3;
        }
        fprintf(stderr, "CVDL create mode=explicit display=%lu\n", parsed);
        result = CVDisplayLinkCreateWithCGDisplay(
            (CGDirectDisplayID)parsed, &link);
    } else {
        fprintf(stderr, "CVDL create mode=active-displays\n");
        result = CVDisplayLinkCreateWithActiveCGDisplays(&link);
    }
    if (result != kCVReturnSuccess || !link) {
        fprintf(stderr, "CVDL create failed result=%d link=%p\n", result, link);
        return 1;
    }

    CVTime nominal = CVDisplayLinkGetNominalOutputVideoRefreshPeriod(link);
    double nominal_hz = nominal.timeValue > 0
        ? (double)nominal.timeScale / (double)nominal.timeValue : 0.0;
    CVTime latency = CVDisplayLinkGetOutputVideoLatency(link);
    double latency_seconds = latency.timeScale > 0
        ? (double)latency.timeValue / (double)latency.timeScale : 0.0;
    fprintf(stderr,
            "CVDL nominal value=%lld scale=%d flags=%#x hz=%.6f "
            "latency_s=%.9f running=%d\n",
            nominal.timeValue, nominal.timeScale, nominal.flags, nominal_hz,
            latency_seconds,
            CVDisplayLinkIsRunning(link));

    result = CVDisplayLinkSetOutputCallback(link, display_link_callback, NULL);
    if (result == kCVReturnSuccess) result = CVDisplayLinkStart(link);
    if (result != kCVReturnSuccess) {
        fprintf(stderr, "CVDL start failed result=%d\n", result);
        CVDisplayLinkRelease(link);
        return 2;
    }

    sleep(5);
    CVDisplayLinkStop(link);
    uint64_t count = atomic_load(&callback_count);
    uint64_t first = atomic_load(&first_host_time);
    uint64_t last = atomic_load(&last_host_time);
    uint64_t min_delta = atomic_load(&minimum_delta);
    uint64_t max_delta = atomic_load(&maximum_delta);
    double frequency = CVGetHostClockFrequency();
    double elapsed = count > 1 && last > first
        ? (double)(last - first) / frequency : 0.0;
    fprintf(stderr,
            "CVDL summary callbacks=%llu elapsed_s=%.6f measured_hz=%.6f "
            "min_ms=%.3f max_ms=%.3f\n",
            (unsigned long long)count, elapsed,
            elapsed > 0.0 ? (double)(count - 1) / elapsed : 0.0,
            min_delta != UINT64_MAX ? (double)min_delta * 1000.0 / frequency : 0.0,
            (double)max_delta * 1000.0 / frequency);
    CVDisplayLinkRelease(link);
    return 0;
}

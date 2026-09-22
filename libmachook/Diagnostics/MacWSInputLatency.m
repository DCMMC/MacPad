@import Foundation;
@import Darwin;

#import <math.h>

#import "MacWSInputLatency.h"

typedef struct {
    MacWSInputKind kind;
    uint32_t count;
    uint32_t firstSequence;
    uint32_t lastSequence;
    double transportTotalUS;
    double transportMaximumUS;
    double queueTotalUS;
    double queueMaximumUS;
    double dispatchTotalUS;
    double dispatchMaximumUS;
} MacWSInputLatencyAggregate;

static MacWSInputLatencyAggregate MacWSInputLatency;

#define MACWS_SYSTEM_INPUT_LATENCY_MAGIC UINT32_C(0x4d574c54)

double MacWSInputUptimeSeconds(void) {
#ifdef CLOCK_UPTIME_RAW
    struct timespec now = {0};
    if (clock_gettime(CLOCK_UPTIME_RAW, &now) == 0)
        return (double)now.tv_sec + (double)now.tv_nsec / 1.0e9;
#endif
    return [[NSProcessInfo processInfo] systemUptime];
}
static void MacWSSystemInputLatencyPath(uint32_t windowNumber,
                                        char path[PATH_MAX]) {
    snprintf(path, PATH_MAX,
             "/private/tmp/macws_input_latency_pending.%u.bin",
             windowNumber);
}

BOOL MacWSWriteSystemInputLatencyMarker(MacWSInputRecord record,
                                        uint32_t windowNumber) {
    if (!(record.flags & MacWSInputFlagLatencyDiagnostic) ||
        windowNumber == 0 ||
        (record.kind != MacWSInputKindTap &&
         record.kind != MacWSInputKindSecondaryTap)) return NO;
    MacWSSystemInputLatencyMarker marker = {
        .magic = MACWS_SYSTEM_INPUT_LATENCY_MAGIC,
        .version = 1,
        .kind = record.kind,
        .windowNumber = windowNumber,
        .sampleSequence = record.sampleSequence,
        .producerTimestamp = record.timestamp,
        .posterReceiptTimestamp = MacWSInputUptimeSeconds(),
    };
    char path[PATH_MAX] = {0};
    MacWSSystemInputLatencyPath(windowNumber, path);
    int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    if (fd < 0) return NO;
    ssize_t count = write(fd, &marker, sizeof(marker));
    close(fd);
    if (count != (ssize_t)sizeof(marker)) {
        unlink(path);
        return NO;
    }
    return YES;
}

BOOL MacWSConsumeSystemInputLatencyMarker(
        uint32_t windowNumber, MacWSSystemInputLatencyMarker *marker) {
    if (!marker || windowNumber == 0) return NO;
    char path[PATH_MAX] = {0};
    MacWSSystemInputLatencyPath(windowNumber, path);
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return NO;
    MacWSSystemInputLatencyMarker candidate = {0};
    ssize_t count = read(fd, &candidate, sizeof(candidate));
    close(fd);
    unlink(path);
    double now = MacWSInputUptimeSeconds();
    BOOL valid = count == (ssize_t)sizeof(candidate) &&
        candidate.magic == MACWS_SYSTEM_INPUT_LATENCY_MAGIC &&
        candidate.version == 1 &&
        candidate.windowNumber == windowNumber &&
        candidate.producerTimestamp > 0.0 &&
        candidate.posterReceiptTimestamp >= candidate.producerTimestamp &&
        now >= candidate.posterReceiptTimestamp &&
        now - candidate.posterReceiptTimestamp <= 2.0;
    if (!valid) return NO;
    *marker = candidate;
    return YES;
}

void MacWSRemoveSystemInputLatencyMarker(uint32_t windowNumber) {
    if (windowNumber == 0) return;
    char path[PATH_MAX] = {0};
    MacWSSystemInputLatencyPath(windowNumber, path);
    unlink(path);
}

void MacWSAppendOneShotInputLatency(MacWSInputRecord record,
                                    double totalUS,
                                    double transportUS,
                                    double queueUS,
                                    double dispatchUS) {
    char path[PATH_MAX] = {0};
    snprintf(path, sizeof(path),
             "/private/tmp/macws_input_latency.%d.jsonl", getpid());
    int fd = open(path, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0644);
    if (fd < 0) return;
    char line[320] = {0};
    int length = snprintf(line, sizeof(line),
        "{\"sample\":%u,\"kind\":%u,\"total_us\":%.1f,"
        "\"transport_us\":%.1f,\"queue_us\":%.1f,"
        "\"dispatch_us\":%.1f}\n",
        record.sampleSequence, record.kind, totalUS, transportUS,
        queueUS, dispatchUS);
    if (length > 0 && (size_t)length < sizeof(line))
        (void)write(fd, line, (size_t)length);
    close(fd);
}

void MacWSRecordInputLatency(MacWSInputRecord record,
                             double mainStart,
                             double dispatchEnd) {
    if (!(record.flags & MacWSInputFlagLatencyDiagnostic)) return;
    BOOL oneShot = record.kind == MacWSInputKindTap ||
                   record.kind == MacWSInputKindSecondaryTap;
    BOOL began = oneShot ||
        (record.flags & MacWSInputFlagGestureBegan) != 0;
    BOOL terminal = (record.flags & (MacWSInputFlagGestureEnded |
                                     MacWSInputFlagGestureCancelled)) != 0 ||
                    oneShot;
    if (began || MacWSInputLatency.kind != record.kind) {
        MacWSInputLatency = (MacWSInputLatencyAggregate){
            .kind = (MacWSInputKind)record.kind,
            .firstSequence = record.sampleSequence,
        };
    }
    double totalUS = fmax(0.0, (mainStart - record.timestamp) * 1.0e6);
    double transportUS = record.reserved;
    double queueUS = fmax(0.0, totalUS - transportUS);
    double dispatchUS = fmax(0.0, (dispatchEnd - mainStart) * 1.0e6);
    if (oneShot) {
        MacWSAppendOneShotInputLatency(
            record, totalUS, transportUS, queueUS, dispatchUS);
    }
    MacWSInputLatency.count++;
    MacWSInputLatency.lastSequence = record.sampleSequence;
    MacWSInputLatency.transportTotalUS += transportUS;
    MacWSInputLatency.transportMaximumUS = fmax(
        MacWSInputLatency.transportMaximumUS, transportUS);
    MacWSInputLatency.queueTotalUS += queueUS;
    MacWSInputLatency.queueMaximumUS = fmax(
        MacWSInputLatency.queueMaximumUS, queueUS);
    MacWSInputLatency.dispatchTotalUS += dispatchUS;
    MacWSInputLatency.dispatchMaximumUS = fmax(
        MacWSInputLatency.dispatchMaximumUS, dispatchUS);
    if (terminal && MacWSInputLatency.count != 0 && !oneShot) {
        double count = MacWSInputLatency.count;
        fprintf(stderr,
            "#### APP-INPUT LATENCY pid=%d kind=%u samples=%u seq=%u..%u "
            "transport-us(avg/max)=%.1f/%.1f queue-us(avg/max)=%.1f/%.1f "
            "dispatch-us(avg/max)=%.1f/%.1f\n",
            getpid(), MacWSInputLatency.kind, MacWSInputLatency.count,
            MacWSInputLatency.firstSequence,
            MacWSInputLatency.lastSequence,
            MacWSInputLatency.transportTotalUS / count,
            MacWSInputLatency.transportMaximumUS,
            MacWSInputLatency.queueTotalUS / count,
            MacWSInputLatency.queueMaximumUS,
            MacWSInputLatency.dispatchTotalUS / count,
            MacWSInputLatency.dispatchMaximumUS);
        fflush(stderr);
    }
}

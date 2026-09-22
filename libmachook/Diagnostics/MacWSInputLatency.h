#pragma once

@import Foundation;

#import "macws_host_protocol.h"

typedef struct {
    uint32_t magic;
    uint16_t version;
    uint16_t kind;
    uint32_t windowNumber;
    uint32_t sampleSequence;
    double producerTimestamp;
    double posterReceiptTimestamp;
} MacWSSystemInputLatencyMarker;

// Opt-in measurement boundary. None of these functions changes production
// event routing; callers invoke them only for LatencyDiagnostic records.
double MacWSInputUptimeSeconds(void);
BOOL MacWSWriteSystemInputLatencyMarker(MacWSInputRecord record,
                                        uint32_t windowNumber);
BOOL MacWSConsumeSystemInputLatencyMarker(
    uint32_t windowNumber, MacWSSystemInputLatencyMarker *marker);
void MacWSRemoveSystemInputLatencyMarker(uint32_t windowNumber);
void MacWSAppendOneShotInputLatency(MacWSInputRecord record,
                                    double totalUS,
                                    double transportUS,
                                    double queueUS,
                                    double dispatchUS);
void MacWSRecordInputLatency(MacWSInputRecord record,
                             double mainStart,
                             double dispatchEnd);

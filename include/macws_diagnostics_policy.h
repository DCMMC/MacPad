#pragma once

#include <stdbool.h>
#include <string.h>
#include <strings.h>

// Diagnostics require an affirmative value, not mere environment presence.
// In particular, NAME=0 must never enable expensive instrumentation.
static inline bool MacWSDiagnosticSwitchEnabled(const char *value) {
    return value && (strcmp(value, "1") == 0 ||
        strcasecmp(value, "true") == 0 ||
        strcasecmp(value, "yes") == 0 ||
        strcasecmp(value, "on") == 0);
}

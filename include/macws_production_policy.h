#ifndef MACWS_PRODUCTION_POLICY_H
#define MACWS_PRODUCTION_POLICY_H

#include <stdbool.h>
#include <string.h>

// Production features survive a clean launch environment. An explicit zero
// is reserved for a controlled diagnostic opt-out; absence is never off.
static inline bool MacWSProductionDefaultEnabled(const char *value) {
    return !value || strcmp(value, "0") != 0;
}

// Defined once by mac_hooks.m so native driver selection stays consistent
// across allocation, texture, compiler and library-routing translation units.
bool macws_agx_native_enabled(void);

#endif

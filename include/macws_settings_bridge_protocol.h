#ifndef MACWS_SETTINGS_BRIDGE_PROTOCOL_H
#define MACWS_SETTINGS_BRIDGE_PROTOCOL_H

#include <stdbool.h>
#include <stdint.h>

#define MACWS_SETTINGS_BRIDGE_STATE_NAME "com.macwsguide.settings-bridge.capabilities.v1"
#define MACWS_SETTINGS_BRIDGE_REFRESH_NAME "com.macwsguide.settings-bridge.query-capabilities"
#define MACWS_SETTINGS_BRIDGE_ABI 1u
#define MACWS_SETTINGS_BRIDGE_MAGIC UINT64_C(0x4d53)
#define MACWS_SETTINGS_BRIDGE_LAUNCH_PROXY 1u

static inline uint64_t MacWSSettingsBridgeState(uint32_t pid) {
    return (MACWS_SETTINGS_BRIDGE_MAGIC << 48) |
        ((uint64_t)MACWS_SETTINGS_BRIDGE_ABI << 40) |
        ((uint64_t)MACWS_SETTINGS_BRIDGE_LAUNCH_PROXY << 32) | pid;
}

static inline uint32_t MacWSSettingsBridgePublisher(uint64_t state) {
    return (uint32_t)state;
}

static inline bool MacWSSettingsBridgeStateSupports(uint64_t state) {
    return (state >> 48) == MACWS_SETTINGS_BRIDGE_MAGIC &&
        ((state >> 40) & 0xffu) == MACWS_SETTINGS_BRIDGE_ABI &&
        ((state >> 32) & MACWS_SETTINGS_BRIDGE_LAUNCH_PROXY) != 0 &&
        MacWSSettingsBridgePublisher(state) > 1;
}

#endif

#ifndef MACWS_SETTINGS_PATHS_H
#define MACWS_SETTINGS_PATHS_H

// Shared by the RunningBoard bridge, raw-syscall launch proxy and macOS
// identity provider. GeneralSettings ships inside System Settings.app, not
// the system ExtensionKit directory. Keep all three launch boundaries aligned.
// Freestanding: no libc imports may consume the proxy's one-shot XPC context.
static inline const char *MacWSSettingsPathSuffix(const char *path) {
    static const char *const roots[] = {
        "/System/Library/ExtensionKit/Extensions/",
        "/System/Applications/System Settings.app/Contents/PlugIns/",
    };
    if (!path) return (const char *)0;
    for (unsigned i = 0; i < sizeof(roots) / sizeof(roots[0]); ++i) {
        const char *p = path, *r = roots[i];
        while (*r && *p == *r) { ++p; ++r; }
        if (!*r) return p;
    }
    return (const char *)0;
}

static inline const char *MacWSSettingsBundleEnd(const char *path) {
    const char *name = MacWSSettingsPathSuffix(path);
    if (!name || !*name || *name == '.') return (const char *)0;
    const char *end = name;
    while (*end && *end != '/') ++end;
    static const char suffix[] = ".appex";
    if (end - name <= 6) return (const char *)0;
    for (unsigned i = 0; i < 6; ++i)
        if ((end - 6)[i] != suffix[i]) return (const char *)0;
    return end;
}

static inline int MacWSIsStockSettingsBundle(const char *path) {
    const char *end = MacWSSettingsBundleEnd(path);
    return end && !*end;
}

static inline int MacWSIsStockSettingsExecutable(const char *path) {
    const char *end = MacWSSettingsBundleEnd(path);
    static const char relative[] = "/Contents/MacOS/";
    if (!end) return 0;
    const char *r = relative;
    while (*r && *end == *r) { ++end; ++r; }
    if (*r || !*end || *end == '.') return 0;
    while (*end) if (*end++ == '/') return 0;
    return 1;
}
#endif

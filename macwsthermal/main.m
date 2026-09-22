// iOS-native thermal snapshot and read-only process probe.
// This runs outside the chroot so NSProcessInfo exposes the real iPad thermal
// state. Thermal data is observational: this tool never suspends Steam or
// terminates Stray when iPadOS changes thermal-pressure state.

#import <Foundation/Foundation.h>
#import <IOKit/IOKitLib.h>

#include <errno.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

typedef struct {
    pid_t pid;
    char path[512];
    char name[64];
} MacWSProcess;

static MacWSProcess MacWSProcesses[1024];
static int MacWSPIDs[2048];

extern int proc_listpids(uint32_t type, uint32_t typeinfo, void *buffer,
                         int buffersize);
extern int proc_pidpath(int pid, void *buffer, uint32_t buffersize);
extern int proc_name(int pid, void *buffer, uint32_t buffersize);

static int64_t MacWSReadIntegerProperty(io_registry_entry_t service,
                                        CFStringRef key) {
    if (service == IO_OBJECT_NULL) return -1;
    CFTypeRef value = IORegistryEntryCreateCFProperty(
        service, key, kCFAllocatorDefault, 0);
    if (!value) return -1;
    int64_t result = -1;
    if (CFGetTypeID(value) == CFNumberGetTypeID()) {
        CFNumberGetValue((CFNumberRef)value, kCFNumberSInt64Type, &result);
    }
    CFRelease(value);
    return result;
}

static const char *MacWSThermalStateName(NSProcessInfoThermalState state) {
    switch (state) {
        case NSProcessInfoThermalStateNominal: return "nominal";
        case NSProcessInfoThermalStateFair: return "fair";
        case NSProcessInfoThermalStateSerious: return "serious";
        case NSProcessInfoThermalStateCritical: return "critical";
    }
    return "unknown";
}

static int MacWSThermalStateExitCode(NSProcessInfoThermalState state) {
    switch (state) {
        case NSProcessInfoThermalStateNominal: return 0;
        case NSProcessInfoThermalStateFair: return 2;
        case NSProcessInfoThermalStateSerious: return 3;
        case NSProcessInfoThermalStateCritical: return 4;
    }
    return 5;
}

static size_t MacWSReadProcesses(void) {
    int bytes = proc_listpids(1 /* PROC_ALL_PIDS */, 0, MacWSPIDs,
                              sizeof(MacWSPIDs));
    if (bytes <= 0) return 0;
    size_t count = 0;
    int pidCount = bytes / (int)sizeof(MacWSPIDs[0]);
    for (int index = 0; index < pidCount &&
            count < sizeof(MacWSProcesses) / sizeof(MacWSProcesses[0]);
            index++) {
        pid_t pid = MacWSPIDs[index];
        if (pid <= 1) continue;
        MacWSProcess *process = &MacWSProcesses[count];
        memset(process, 0, sizeof(*process));
        proc_name(pid, process->name, sizeof(process->name));
        int pathLength = proc_pidpath(pid, process->path,
                                     sizeof(process->path));
        // A process in the macOS chroot can be visible in the global PID
        // table while proc_pidpath cannot resolve its vnode from the native
        // launchd context.  Preserve that PID and its kernel process name so
        // safety policy does not become blind precisely during memory
        // pressure.  Exact path remains the preferred identity witness.
        if (pathLength <= 0 && process->name[0] == '\0') continue;
        process->pid = pid;
        count++;
    }
    return count;
}

static int MacWSPrintThermalSnapshot(void) {
    @autoreleasepool {
        NSProcessInfo *processInfo = [NSProcessInfo processInfo];
        NSProcessInfoThermalState thermalState = processInfo.thermalState;

        CFMutableDictionaryRef matching = IOServiceMatching(
            "AppleSmartBattery");
        io_service_t battery = matching
            ? IOServiceGetMatchingService(kIOMasterPortDefault, matching)
            : IO_OBJECT_NULL;
        int64_t temperature = MacWSReadIntegerProperty(
            battery, CFSTR("Temperature"));
        int64_t virtualTemperature = MacWSReadIntegerProperty(
            battery, CFSTR("VirtualTemperature"));
        if (battery != IO_OBJECT_NULL) IOObjectRelease(battery);

        int64_t effectiveTemperature = temperature;
        if (virtualTemperature > effectiveTemperature)
            effectiveTemperature = virtualTemperature;

        // AppleSmartBattery reports these two properties in centi-degrees C
        // on the target iPad (runtime witnesses: 2959 and 2989). Keep the raw
        // integers in the machine-readable output so the watchdog never has
        // to parse locale-dependent floating point.
        printf("thermal-state=%s raw=%ld low-power=%s "
               "battery-temp-centic=%" PRId64 " "
               "virtual-temp-centic=%" PRId64 " "
               "effective-temp-centic=%" PRId64 " uptime=%.3f\n",
               MacWSThermalStateName(thermalState), (long)thermalState,
               processInfo.lowPowerModeEnabled ? "yes" : "no",
               temperature, virtualTemperature, effectiveTemperature,
               processInfo.systemUptime);
        fflush(stdout);

        // Preserve a distinct exit status when numeric battery telemetry is
        // absent. The shell still parses thermal-state first: only an observed
        // critical state intervenes, while missing numeric data is log-only.
        if (effectiveTemperature < 0) return 6;
        return MacWSThermalStateExitCode(thermalState);
    }
}

int main(int argc, char **argv) {
    @autoreleasepool {
        if (argc >= 2 && strcmp(argv[1], "scan") == 0) {
            const char *filter = argc >= 3 ? argv[2] : NULL;
            size_t count = MacWSReadProcesses();
            printf("process-scan readable=%zu filter=%s\n", count,
                   filter ? filter : "<all>");
            for (size_t index = 0; index < count; index++) {
                const MacWSProcess *process = &MacWSProcesses[index];
                if (filter && !strstr(process->path, filter) &&
                    !strstr(process->name, filter)) continue;
                printf("pid=%d name=%s path=%s\n", process->pid,
                       process->name[0] ? process->name : "<unavailable>",
                       process->path[0] ? process->path : "<unavailable>");
            }
            return 0;
        }
        if (argc >= 2 && strcmp(argv[1], "stream") == 0) {
            long intervalMilliseconds = 2000;
            if (argc >= 3) {
                char *end = NULL;
                errno = 0;
                long requested = strtol(argv[2], &end, 10);
                if (errno || !end || end == argv[2] || *end != '\0' ||
                    requested < 250 || requested > 60000) {
                    fprintf(stderr,
                            "usage: macwsthermal stream [250..60000-ms]\n");
                    return 64;
                }
                intervalMilliseconds = requested;
            }
            // Read-only hot loop for a controller that pre-arms telemetry
            // before a game's allocation peak.  It intentionally performs
            // no process scan, signal, suspension or termination.
            for (;;) {
                (void)MacWSPrintThermalSnapshot();
                if (usleep((useconds_t)intervalMilliseconds * 1000) != 0 &&
                    errno != EINTR) return 1;
            }
        }
        if (argc >= 2 && (strcmp(argv[1], "watch") == 0 ||
                          strcmp(argv[1], "supervise") == 0)) {
            fprintf(stderr,
                    "macwsthermal: automatic Stray process control was "
                    "removed; use 'stream' for read-only telemetry\n");
            return 64;
        }
    }
    return MacWSPrintThermalSnapshot();
}

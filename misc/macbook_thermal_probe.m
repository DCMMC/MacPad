// One-shot, machine-readable thermal snapshot for the M1 MacBook reference.
// This is intentionally separate from the iOS helper: AppleSmartBattery's
// VirtualTemperature is not the physical pack temperature on macOS, so the
// effective numeric field uses Temperature and retains VirtualTemperature as
// separate evidence. Intervention policy is based only on thermalState.

#import <Foundation/Foundation.h>
#import <IOKit/IOKitLib.h>

#include <inttypes.h>
#include <stdio.h>

static int64_t ReadIntegerProperty(io_registry_entry_t service,
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

static const char *ThermalStateName(NSProcessInfoThermalState state) {
    switch (state) {
        case NSProcessInfoThermalStateNominal: return "nominal";
        case NSProcessInfoThermalStateFair: return "fair";
        case NSProcessInfoThermalStateSerious: return "serious";
        case NSProcessInfoThermalStateCritical: return "critical";
    }
    return "unknown";
}

static int ThermalStateExitCode(NSProcessInfoThermalState state) {
    switch (state) {
        case NSProcessInfoThermalStateNominal: return 0;
        case NSProcessInfoThermalStateFair: return 2;
        case NSProcessInfoThermalStateSerious: return 3;
        case NSProcessInfoThermalStateCritical: return 4;
    }
    return 5;
}

int main(void) {
    @autoreleasepool {
        NSProcessInfo *processInfo = [NSProcessInfo processInfo];
        NSProcessInfoThermalState state = processInfo.thermalState;
        CFMutableDictionaryRef matching = IOServiceMatching("AppleSmartBattery");
        io_service_t battery = matching
            ? IOServiceGetMatchingService(kIOMainPortDefault, matching)
            : IO_OBJECT_NULL;
        int64_t temperature = ReadIntegerProperty(
            battery, CFSTR("Temperature"));
        int64_t virtualTemperature = ReadIntegerProperty(
            battery, CFSTR("VirtualTemperature"));
        if (battery != IO_OBJECT_NULL) IOObjectRelease(battery);

        printf("thermal-state=%s raw=%ld low-power=%s "
               "battery-temp-centic=%" PRId64 " "
               "virtual-temp-centic=%" PRId64 " "
               "effective-temp-centic=%" PRId64 " uptime=%.3f\n",
               ThermalStateName(state), (long)state,
               processInfo.lowPowerModeEnabled ? "yes" : "no",
               temperature, virtualTemperature, temperature,
               processInfo.systemUptime);
        fflush(stdout);

        if (temperature < 0) return 6;
        return ThermalStateExitCode(state);
    }
}

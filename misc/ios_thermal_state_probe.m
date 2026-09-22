// One-shot iOS-side thermal gate for reproducible performance measurements.
// Build outside the chroot with the iPhoneOS SDK and run before/after a test.
// Exit 0 means nominal; fair/serious/critical deliberately return non-zero so
// automation cannot silently publish a thermally-throttled result.

@import Foundation;

int main(void) {
    @autoreleasepool {
        NSProcessInfo *processInfo = [NSProcessInfo processInfo];
        NSProcessInfoThermalState state = processInfo.thermalState;
        const char *name = "unknown";
        switch (state) {
            case NSProcessInfoThermalStateNominal: name = "nominal"; break;
            case NSProcessInfoThermalStateFair: name = "fair"; break;
            case NSProcessInfoThermalStateSerious: name = "serious"; break;
            case NSProcessInfoThermalStateCritical: name = "critical"; break;
        }
        printf("thermal-state=%s raw=%ld low-power=%s uptime=%.3f\n",
               name, (long)state,
               processInfo.lowPowerModeEnabled ? "yes" : "no",
               processInfo.systemUptime);
        return state == NSProcessInfoThermalStateNominal ? 0 : 2;
    }
}

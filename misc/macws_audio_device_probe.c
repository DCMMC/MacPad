#include <CoreAudio/CoreAudio.h>
#include <CoreFoundation/CoreFoundation.h>

#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static bool CopyStringProperty(AudioObjectID object,
                               AudioObjectPropertySelector selector,
                               char *destination, size_t capacity) {
    AudioObjectPropertyAddress address = {
        selector,
        kAudioObjectPropertyScopeGlobal,
        kAudioObjectPropertyElementMain,
    };
    CFStringRef value = NULL;
    UInt32 size = sizeof(value);
    OSStatus status = AudioObjectGetPropertyData(
        object, &address, 0, NULL, &size, &value);
    if (status != noErr || !value) return false;
    Boolean copied = CFStringGetCString(
        value, destination, capacity, kCFStringEncodingUTF8);
    CFRelease(value);
    return copied;
}

static OSStatus ReadUInt32Property(AudioObjectID object,
                                   AudioObjectPropertySelector selector,
                                   UInt32 *value) {
    AudioObjectPropertyAddress address = {
        selector,
        kAudioObjectPropertyScopeGlobal,
        kAudioObjectPropertyElementMain,
    };
    UInt32 size = sizeof(*value);
    return AudioObjectGetPropertyData(
        object, &address, 0, NULL, &size, value);
}

static OSStatus ReadDefaultOutput(AudioObjectID *device) {
    AudioObjectPropertyAddress address = {
        kAudioHardwarePropertyDefaultOutputDevice,
        kAudioObjectPropertyScopeGlobal,
        kAudioObjectPropertyElementMain,
    };
    UInt32 size = sizeof(*device);
    return AudioObjectGetPropertyData(
        kAudioObjectSystemObject, &address, 0, NULL, &size, device);
}

static OSStatus WriteDefaultOutput(AudioObjectID device) {
    AudioObjectPropertyAddress address = {
        kAudioHardwarePropertyDefaultOutputDevice,
        kAudioObjectPropertyScopeGlobal,
        kAudioObjectPropertyElementMain,
    };
    return AudioObjectSetPropertyData(
        kAudioObjectSystemObject, &address, 0, NULL, sizeof(device), &device);
}

int main(int argc, char **argv) {
    AudioObjectPropertyAddress devicesAddress = {
        kAudioHardwarePropertyDevices,
        kAudioObjectPropertyScopeGlobal,
        kAudioObjectPropertyElementMain,
    };
    UInt32 bytes = 0;
    OSStatus status = AudioObjectGetPropertyDataSize(
        kAudioObjectSystemObject, &devicesAddress, 0, NULL, &bytes);
    if (status != noErr || bytes == 0 || bytes % sizeof(AudioObjectID) != 0) {
        fprintf(stderr, "device-list-size status=%d bytes=%u\n",
                (int)status, (unsigned)bytes);
        return 2;
    }
    AudioObjectID *devices = calloc(1, bytes);
    if (!devices) return 3;
    status = AudioObjectGetPropertyData(
        kAudioObjectSystemObject, &devicesAddress, 0, NULL, &bytes, devices);
    if (status != noErr) {
        fprintf(stderr, "device-list status=%d\n", (int)status);
        free(devices);
        return 4;
    }

    AudioObjectID initialDefault = kAudioObjectUnknown;
    OSStatus defaultStatus = ReadDefaultOutput(&initialDefault);
    const char *requestedUID = argc == 3 && strcmp(argv[1], "--set") == 0
        ? argv[2] : NULL;
    AudioObjectID requestedDevice = kAudioObjectUnknown;
    size_t count = bytes / sizeof(*devices);
    printf("default-status=%d default-id=%u devices=%zu\n",
           (int)defaultStatus, (unsigned)initialDefault, count);
    for (size_t index = 0; index < count; index++) {
        char name[256] = "<unknown>";
        char uid[256] = "<unknown>";
        UInt32 running = 0;
        UInt32 transport = 0;
        (void)CopyStringProperty(
            devices[index], kAudioObjectPropertyName, name, sizeof(name));
        (void)CopyStringProperty(
            devices[index], kAudioDevicePropertyDeviceUID, uid, sizeof(uid));
        OSStatus runningStatus = ReadUInt32Property(
            devices[index], kAudioDevicePropertyDeviceIsRunningSomewhere,
            &running);
        OSStatus transportStatus = ReadUInt32Property(
            devices[index], kAudioDevicePropertyTransportType, &transport);
        printf("device id=%u default=%s name=%s uid=%s "
               "running=%u running-status=%d transport=%#x "
               "transport-status=%d\n",
               (unsigned)devices[index],
               devices[index] == initialDefault ? "yes" : "no",
               name, uid, (unsigned)running, (int)runningStatus,
               (unsigned)transport, (int)transportStatus);
        if (requestedUID && strcmp(uid, requestedUID) == 0)
            requestedDevice = devices[index];
    }

    int result = 0;
    if (requestedUID) {
        if (requestedDevice == kAudioObjectUnknown) {
            fprintf(stderr, "requested UID not found: %s\n", requestedUID);
            result = 5;
        } else {
            status = WriteDefaultOutput(requestedDevice);
            AudioObjectID observed = kAudioObjectUnknown;
            OSStatus observedStatus = ReadDefaultOutput(&observed);
            printf("set-default uid=%s id=%u status=%d "
                   "observed-status=%d observed-id=%u\n",
                   requestedUID, (unsigned)requestedDevice, (int)status,
                   (int)observedStatus, (unsigned)observed);
            if (status != noErr || observedStatus != noErr ||
                observed != requestedDevice)
                result = 6;
        }
    }
    free(devices);
    return result;
}

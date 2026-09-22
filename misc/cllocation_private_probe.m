#import <CoreLocation/CoreLocation.h>
#import <Foundation/Foundation.h>
#import <objc/message.h>
#import <objc/runtime.h>

#include <stdio.h>
#include <string.h>

static BOOL SelectorLooksRelevant(const char *name) {
    static const char *const needles[] = {
        "client", "Client", "type", "Type", "source", "Source",
        "simulation", "Simulation", "technology", "Technology",
        "coordinate", "Coordinate", "location", "Location",
        "reference", "Reference", "frame", "Frame",
    };
    for (size_t index = 0; index < sizeof(needles) / sizeof(needles[0]);
         index++) {
        if (strstr(name, needles[index])) return YES;
    }
    return NO;
}

static void PrintMethods(Class cls, BOOL classMethods) {
    Class target = classMethods ? object_getClass(cls) : cls;
    unsigned int count = 0;
    Method *methods = class_copyMethodList(target, &count);
    printf("class=%s kind=%s method-count=%u\n", class_getName(cls),
           classMethods ? "class" : "instance", count);
    for (unsigned int index = 0; index < count; index++) {
        const char *name = sel_getName(method_getName(methods[index]));
        if (!SelectorLooksRelevant(name)) continue;
        printf("  %c[%s %s] encoding=%s imp=%p\n",
               classMethods ? '+' : '-', class_getName(cls), name,
               method_getTypeEncoding(methods[index]),
               method_getImplementation(methods[index]));
    }
    free(methods);
}

static void ProbeClientLocationABI(void) {
    CLLocation *publicLocation = [[CLLocation alloc]
        initWithCoordinate:CLLocationCoordinate2DMake(1.0, 1.0)
                  altitude:2.0
        horizontalAccuracy:3.0
          verticalAccuracy:4.0
                    course:5.0
                     speed:6.0
                 timestamp:[NSDate dateWithTimeIntervalSince1970:1700000000]];
    SEL clientSelector = NSSelectorFromString(@"clientLocation");
    SEL initSelector = NSSelectorFromString(@"initWithClientLocation:");
    NSMethodSignature *clientSignature =
        [publicLocation methodSignatureForSelector:clientSelector];
    NSMethodSignature *initSignature =
        [CLLocation instanceMethodSignatureForSelector:initSelector];
    if (!clientSignature || !initSignature) {
        printf("client-location-abi unavailable\n");
        return;
    }
    NSUInteger argumentSize = 0;
    NSUInteger argumentAlignment = 0;
    NSGetSizeAndAlignment([initSignature getArgumentTypeAtIndex:2],
                          &argumentSize, &argumentAlignment);
    printf("client-location-abi return-size=%lu argument-size=%lu "
           "argument-alignment=%lu\n",
           (unsigned long)clientSignature.methodReturnLength,
           (unsigned long)argumentSize, (unsigned long)argumentAlignment);
    if (clientSignature.methodReturnLength != argumentSize ||
        argumentSize < sizeof(int)) {
        printf("client-location-abi incompatible\n");
        return;
    }

    NSMutableData *bytes = [NSMutableData dataWithLength:argumentSize];
    NSInvocation *getter =
        [NSInvocation invocationWithMethodSignature:clientSignature];
    getter.target = publicLocation;
    getter.selector = clientSelector;
    [getter invoke];
    [getter getReturnValue:bytes.mutableBytes];
    // RE-confirmed in Ventura CoreLocation: -[CLLocation type] loads the
    // internal object at self+0x8, then its int at +0x68.  The
    // -clientLocation copier maps internal +0x68 to result +0x60.
    static const NSUInteger kClientLocationTypeOffset = 0x60;
    if (argumentSize < kClientLocationTypeOffset + sizeof(int)) {
        printf("client-location-abi missing-type-field\n");
        return;
    }
    int originalType = 0;
    memcpy(&originalType,
           (const uint8_t *)bytes.bytes + kClientLocationTypeOffset,
           sizeof(originalType));
    int accessorType = ((int (*)(id, SEL))objc_msgSend)(
        publicLocation, NSSelectorFromString(@"type"));
    printf("client-location-public accessor-type=%d struct-type=%d\n",
           accessorType, originalType);

    int probeType = 1;
    memcpy((uint8_t *)bytes.mutableBytes + kClientLocationTypeOffset,
           &probeType, sizeof(probeType));
    CLLocation *allocated = [CLLocation alloc];
    NSInvocation *initializer =
        [NSInvocation invocationWithMethodSignature:initSignature];
    initializer.target = allocated;
    initializer.selector = initSelector;
    [initializer setArgument:bytes.mutableBytes atIndex:2];
    [initializer invoke];
    __unsafe_unretained CLLocation *unretainedResult = nil;
    [initializer getReturnValue:&unretainedResult];
    CLLocation *rebuilt = unretainedResult;
    int rebuiltType = ((int (*)(id, SEL))objc_msgSend)(
        rebuilt, NSSelectorFromString(@"type"));
    printf("client-location-rebuilt requested-type=%d accessor-type=%d\n",
           probeType, rebuiltType);
}

int main(void) {
    @autoreleasepool {
        const char *const classNames[] = {
            "CLLocation",
            "CLLocationInternal",
            "CLDaemonLocation",
            "CLClientLocation",
        };
        for (size_t index = 0;
             index < sizeof(classNames) / sizeof(classNames[0]); index++) {
            Class cls = objc_getClass(classNames[index]);
            if (!cls) {
                printf("class=%s unavailable\n", classNames[index]);
                continue;
            }
            PrintMethods(cls, NO);
            PrintMethods(cls, YES);
        }
        ProbeClientLocationABI();
    }
    return 0;
}

// Isolated 16x16 native-Metal CoreImage producer witness. No application UI,
// injection, service restart, software fallback or validation bypass.
// The optional --fresh-dag changes lexer whitespace only in this probe's
// own request, avoiding an existing compiler-cache entry for this experiment.
#import <Foundation/Foundation.h>
#import <CoreImage/CoreImage.h>
#import <Metal/Metal.h>
#import <objc/runtime.h>
#include <dlfcn.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

typedef id (*DAGFn)(id, SEL, NSString *, NSArray *, NSError **);
static DAGFn OriginalDAG;
static unsigned DAGCount;
static BOOL FreshDAG;

static id ObserveDAG(id device, SEL command, NSString *dag, NSArray *functions,
                     NSError **error) {
    unsigned sequence = ++DAGCount;
    if (FreshDAG && sequence == 1) dag = [dag stringByAppendingString:@"\n \t\n \t\n \t"];
    fprintf(stderr, "CI-PROBE DAG sequence=%u functions=%lu chars=%lu fresh=%d\n",
        sequence, (unsigned long)functions.count, (unsigned long)dag.length, FreshDAG);
    if (sequence <= 4 && dag.length <= 8192)
        fprintf(stderr, "CI-PROBE DAG text=%s\n", dag.UTF8String);
    id result = OriginalDAG(device, command, dag, functions, error);
    fprintf(stderr, "CI-PROBE DAG result=%p error=%s\n", (__bridge void *)result,
        error && *error ? (*error).description.UTF8String : "nil");
    return result;
}

int main(int argc, const char **argv) {
    BOOL validateReply = argc == 3 && !strcmp(argv[1], "--compiled-reply") && argv[2][0] == '/';
    BOOL gamma = argc == 2 && !strcmp(argv[1], "--gamma");
    BOOL coreUI = argc == 2 && !strcmp(argv[1], "--coreui");
    if (!validateReply && !gamma && !coreUI && argc != 1 && (argc != 2 || strcmp(argv[1], "--fresh-dag"))) return 64;
    alarm(15);
    FreshDAG = argc == 2 && !strcmp(argv[1], "--fresh-dag");
    @autoreleasepool {
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        if (!device) return 2;
        fprintf(stderr, "CI-PROBE device=%s class=%s\n", device.name.UTF8String,
                object_getClassName(device));
        if (validateReply) {
            NSData *reply = [NSData dataWithContentsOfFile:[NSString stringWithUTF8String:argv[2]]];
            if (reply.length < 104 || reply.length > 4 * 1048576) return 65;
            uint32_t offset, length;
            memcpy(&offset, (const char *)reply.bytes + 40, sizeof(offset));
            memcpy(&length, (const char *)reply.bytes + 44, sizeof(length));
            if (offset != 104 || length < 88 || length != reply.length - offset ||
                memcmp((const char *)reply.bytes + offset, "MTLB", 4)) return 65;
            dispatch_data_t data = dispatch_data_create((const char *)reply.bytes + offset,
                length, NULL, DISPATCH_DATA_DESTRUCTOR_DEFAULT);
            NSError *error = nil;
            id<MTLLibrary> library = [device newLibraryWithData:data error:&error];
            fprintf(stderr, "CI-PROBE library=%p error=%s\n", (__bridge void *)library,
                error ? error.description.UTF8String : "nil");
            if (!library) return 6;
            id<MTLFunction> function = [library newFunctionWithName:@"ciKernelMain"];
            id<MTLComputePipelineState> pipeline = function
                ? [device newComputePipelineStateWithFunction:function error:&error] : nil;
            fprintf(stderr, "CI-PROBE function=%p pipeline=%p error=%s\n",
                (__bridge void *)function, (__bridge void *)pipeline,
                error ? error.description.UTF8String : "nil");
            return pipeline ? 0 : 7;
        }
        SEL command = sel_registerName("newLibraryWithDAG:functions:error:");
        Method method = class_getInstanceMethod(object_getClass(device), command);
        if (method) OriginalDAG = (DAGFn)method_setImplementation(method, (IMP)ObserveDAG);
        CIContext *context = [CIContext contextWithMTLDevice:device options:@{
            kCIContextCacheIntermediates:@NO, kCIContextName:@"MacWS bounded CI probe"
        }];
        if (!context) return 3;
        uint8_t source[16 * 16 * 4], output[sizeof(source)];
        for (unsigned y = 0; y < 16; ++y) {
            for (unsigned x = 0; x < 16; ++x) {
                unsigned index = (y * 16 + x) * 4;
                source[index] = x * 16;
                source[index + 1] = y * 16;
                source[index + 2] = 80;
                source[index + 3] = 255;
            }
        }
        memset(output, 0, sizeof(output));
        CGColorSpaceRef colorSpace = CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
        CIImage *input = [CIImage imageWithBitmapData:[NSData dataWithBytes:source length:sizeof(source)]
            bytesPerRow:64 size:CGSizeMake(16, 16) format:kCIFormatRGBA8 colorSpace:colorSpace];
        CIImage *filtered = nil;
        if (coreUI) {
            // Runtime-confirmed on 2026-09-20: the real CoreUI tint filter
            // reproduces Word's transparent Save button in a 16x16 image.
            // It submits compiler request kind 5, not kind 14 as the ordinary
            // CoreImage graph below does. Use its actual class/defaults, no
            // replacement kernel, software context or altered return value.
            void *framework = dlopen(
                "/System/Library/PrivateFrameworks/CoreUI.framework/CoreUI",
                RTLD_NOW | RTLD_LOCAL);
            Class filterClass = framework ? NSClassFromString(
                @"CUIHueSaturationFilterLocal") : Nil;
            if (!filterClass || ![filterClass isSubclassOfClass:[CIFilter class]]) {
                CGColorSpaceRelease(colorSpace);
                return 8;
            }
            CIFilter *filter = [[filterClass alloc] init];
            [filter setDefaults];
            if (![filter.inputKeys containsObject:kCIInputImageKey]) {
                CGColorSpaceRelease(colorSpace);
                return 8;
            }
            [filter setValue:input forKey:kCIInputImageKey];
            filtered = filter.outputImage;
            fprintf(stderr, "CI-PROBE filter=%s compiler-contract=image-filter\n",
                    object_getClassName(filter));
        } else {
            filtered = [input imageByApplyingFilter:@"CIColorControls"
                withInputParameters:@{kCIInputSaturationKey:@0.37, kCIInputContrastKey:@1.12}];
        }
        if (!filtered) {
            CGColorSpaceRelease(colorSpace);
            return 8;
        }
        if (gamma) filtered = [filtered imageByApplyingFilter:@"CIGammaAdjust"
            withInputParameters:@{@"inputPower":@2.13}];
        [context render:filtered toBitmap:output rowBytes:64
                 bounds:CGRectMake(0, 0, 16, 16) format:kCIFormatRGBA8 colorSpace:colorSpace];
        CGColorSpaceRelease(colorSpace);
        unsigned visible = 0, changed = 0, varied = 0;
        uint64_t hash = UINT64_C(1469598103934665603);
        for (unsigned index = 0; index < sizeof(output); ++index) {
            hash = (hash ^ output[index]) * UINT64_C(1099511628211);
            changed += source[index] != output[index];
            if (index % 4 == 3) visible += output[index] != 0;
            if (index >= 4) varied += output[index] != output[index % 4];
        }
        printf("CI-PROBE pixels=256 visible=%u changed-bytes=%u varied-bytes=%u dags=%u hash=%016llx first=%u,%u,%u,%u last=%u,%u,%u,%u\n",
            visible, changed, varied, DAGCount, (unsigned long long)hash,
            output[0], output[1], output[2], output[3],
            output[1020], output[1021], output[1022], output[1023]);
        return visible == 256 && changed && varied ? 0 : 4;
    }
}

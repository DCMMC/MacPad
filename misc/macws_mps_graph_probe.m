// Bounded MPSGraph compatibility witness, not a benchmark or production hook.
// Runs a real GPU-backed graph and reads back sixteen floats. No app attach,
// compositor restart, exception bypass, or invented success on API failure.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <MetalPerformanceShadersGraph/MetalPerformanceShadersGraph.h>
#import <objc/runtime.h>
#include <dlfcn.h>
#include <ptrauth.h>
#include <mach/mach.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <stdatomic.h>
#include "../include/macws_agx_compute_abi.h"

// Same read-only queue boundary as misc/metalfx_temporal_probe.m. Capture
// only this probe's first 16 submissions, never an ambient application's.
static void (*originalSubmit)(id, SEL, id *, NSUInteger);
static _Atomic unsigned submitSequence;
static BOOL ReadMemory(uintptr_t address, void *bytes, size_t length) {
    vm_size_t copied = 0;
    address &= UINT64_C(0x0000ffffffffffff);
    return address >= UINT64_C(0x100000000) &&
        vm_read_overwrite(mach_task_self(), address, length,
            (vm_address_t)bytes, &copied) == KERN_SUCCESS && copied == length;
}

static void SaveSubmit(unsigned sequence, const char *kind,
                       uintptr_t start, uintptr_t end) {
    start &= UINT64_C(0x0000ffffffffffff);
    end &= UINT64_C(0x0000ffffffffffff);
    if (!start || end <= start || end - start > 1024 * 1024) return;
    size_t size = end - start;
    void *bytes = malloc(size);
    if (!bytes || !ReadMemory(start, bytes, size)) { free(bytes); return; }
    char path[256];
    snprintf(path, sizeof(path), "/tmp/mps-submit-%d-s%u-%s.bin",
        getpid(), sequence, kind);
    int fd = open(path, O_WRONLY | O_CREAT | O_EXCL, 0600);
    size_t done = 0;
    while (fd >= 0 && done < size) {
        ssize_t n = write(fd, (char *)bytes + done, size - done);
        if (n <= 0) break;
        done += n;
    }
    if (fd >= 0) close(fd);
    fprintf(stderr, "MPS-SUBMIT sequence=%u kind=%s bytes=%#zx written=%#zx path=%s\n",
        sequence, kind, size, done, path);
    free(bytes);
}

static void TraceSubmit(id queue, SEL selector, id *buffers, NSUInteger count) {
    for (NSUInteger i = 0; buffers && i < count && i < 32; i++) {
        unsigned sequence = atomic_fetch_add(&submitSequence, 1) + 1;
        if (sequence > 16) break;
        id buffer = buffers[i];
        Ivar ivar = class_getInstanceVariable(object_getClass(buffer), "_storage");
        uintptr_t storage = 0, start = 0, current = 0, segments = 0, segmentEnd = 0;
        if (!ivar || !ReadMemory((uintptr_t)(__bridge void *)buffer +
                ivar_getOffset(ivar), &storage, sizeof(storage))) continue;
        storage &= UINT64_C(0x0000ffffffffffff);
        if (ReadMemory(storage + 0x28, &start, 8) &&
            ReadMemory(storage + 0x30, &current, 8))
            SaveSubmit(sequence, "kcmd", start, current);
        if (ReadMemory(storage + 0x68, &segments, 8) &&
            ReadMemory(storage + 0x328, &segmentEnd, 8))
            SaveSubmit(sequence, "segments", segments, segmentEnd);
        // Isolated protocol A/B only; never enabled in a native control.
        if (getenv("MACWS_MPS_PROBE_COMPACT_COMPUTE")) {
            unsigned char command[0x1f0], list[0xb0];
            if (current - start != sizeof(command) ||
                segmentEnd - segments != sizeof(list) ||
                !ReadMemory(start, command, sizeof(command)) ||
                !ReadMemory(segments, list, sizeof(list)) ||
                !MacWSAGXIsTrailerlessCompute(command, sizeof(command)) ||
                MacWSAGXRead32(list, 8) != 1 ||
                MacWSAGXRead32(list, 0xc) != 0x800000b0 ||
                MacWSAGXRead32(list, 0x18) != 0 ||
                MacWSAGXRead32(list, 0x1c) != sizeof(command) ||
                MacWSAGXRead32(list, 0x28) != 11 ||
                MacWSAGXRead32(list, 0x2c) != 2) exit(68);
            unsigned char *record = (void *)(start & UINT64_C(0x0000ffffffffffff));
            memmove(record + 0x1d0, record + 0x1e0, 0x10);
            memset(record + 0x1e0, 0, 0x10);
            *(uint32_t *)(record + 4) = 0x1e0;
            *(uint32_t *)(record + 0x28) = 0x1d8;
            *(uint32_t *)(record + 0x2c) = 0x1a8;
            *(uint32_t *)((segments & UINT64_C(0x0000ffffffffffff)) + 0x1c) = 0x1e0;
            *(uintptr_t *)(storage + 0x30) = current - 0x10;
            fprintf(stderr, "MPS-SUBMIT diagnostic trailerless-compute 0x1f0->0x1e0\n");
        }
    }
    originalSubmit(queue, selector, buffers, count);
}

static void InstallSubmitTrace(id queue) {
    if (!getenv("MACWS_MPS_PROBE_TRACE_SUBMIT")) return;
    Class cls = object_getClass(queue);
    SEL selector = sel_registerName("submitCommandBuffers:count:");
    Method method = class_getInstanceMethod(cls, selector);
    if (!method) exit(67);
    originalSubmit = (void *)method_getImplementation(method);
    if (!class_addMethod(cls, selector, (IMP)TraceSubmit, method_getTypeEncoding(method)))
        method_setImplementation(method, (IMP)TraceSubmit);
    fprintf(stderr, "MPS-SUBMIT observer class=%s\n", class_getName(cls));
}

static void DumpPostSubmit(void) {
    if (!getenv("MACWS_SUBMIT_FAST_RING")) return;
    void (*dump)(const char *, const void *, uint64_t) =
        dlsym(RTLD_DEFAULT, "macws_dump_fast_agx_submit_serial");
    if (dump) dump("mps-probe-readback", NULL, 0);
}

// Probe-local, read-only boundary tracing. Never installed in an application
// or daemon. The original descriptor, returned object and NSError are intact.
typedef id (*StitchFn)(id, SEL, id, NSError **);
static StitchFn originalStitch;
static StitchFn originalStitchSPI;
typedef id (*StitchArchiveFn)(id, SEL, id, id, NSError **);
static StitchArchiveFn originalStitchArchive;
static StitchArchiveFn originalDAG;
static id TraceStitch(id device, SEL selector, MTLStitchedLibraryDescriptor *descriptor,
                      NSError **error) {
    fprintf(stderr, "MPS-STITCH begin functions=%lu graphs=%lu\n",
            (unsigned long)descriptor.functions.count,
            (unsigned long)descriptor.functionGraphs.count);
    for (id<MTLFunction> function in descriptor.functions)
        fprintf(stderr, "MPS-STITCH input name=%s type=%lu device-match=%d\n",
                function.name.UTF8String, (unsigned long)function.functionType,
                function.device == device);
    id result = originalStitch(device, selector, descriptor, error);
    fprintf(stderr, "MPS-STITCH result=%p error=%s\n", result,
            error && *error ? (*error).description.UTF8String : "nil");
    return result;
}

static id TraceStitchSPI(id device, SEL selector, id descriptor, NSError **error) {
    fprintf(stderr, "MPS-STITCH SPI begin descriptor-class=%s\n", object_getClassName(descriptor));
    id result = originalStitchSPI(device, selector, descriptor, error);
    fprintf(stderr, "MPS-STITCH SPI result=%p error=%s\n", result,
            error && *error ? (*error).description.UTF8String : "nil");
    return result;
}

static id TraceStitchArchive(id device, SEL selector, id descriptor, id archive, NSError **error) {
    fprintf(stderr, "MPS-STITCH archive begin descriptor-class=%s archive=%p\n",
            object_getClassName(descriptor), archive);
    id result = originalStitchArchive(device, selector, descriptor, archive, error);
    fprintf(stderr, "MPS-STITCH archive result=%p error=%s\n", result,
            error && *error ? (*error).description.UTF8String : "nil");
    return result;
}

static id TraceDAG(id device, SEL selector, NSString *dag, NSArray *functions, NSError **error) {
    // Opt-in cache-isolation experiment: trailing lexer whitespace changes no
    // graph node, operation, data dependency or function implementation.
    const char *fresh = getenv("MACWS_MPS_PROBE_FRESH_DAG");
    if (fresh) {
        unsigned count = (unsigned)strtoul(fresh, NULL, 10);
        if (!count || count > 64) exit(64);
        NSMutableString *isolated = [NSMutableString stringWithString:dag];
        for (unsigned i = 0; i < count; i++) [isolated appendString:@"\n \t"];
        dag = isolated;
    }
    fprintf(stderr, "MPS-STITCH DAG begin functions=%lu dag=%s\n",
            (unsigned long)functions.count, dag.UTF8String);
    id result = originalDAG(device, selector, dag, functions, error);
    fprintf(stderr, "MPS-STITCH DAG result=%p error=%s\n", result,
            error && *error ? (*error).description.UTF8String : "nil");
    return result;
}

static BOOL DumpMetalRange(id device) {
    const char *range = getenv("MACWS_MPS_PROBE_METAL_RANGE");
    if (!range) return NO;
    unsigned long offset = 0, length = 0;
    if (sscanf(range, "%lx:%lx", &offset, &length) != 2 || !length || length > 32768)
        exit(64);
    Method method = class_getInstanceMethod(object_getClass(device),
        sel_registerName("newLibraryWithDAG:functions:error:"));
    Dl_info base = {0};
    dladdr(ptrauth_strip(method_getImplementation(method), ptrauth_key_function_pointer), &base);
    unsigned char *address = (unsigned char *)base.dli_fbase + offset;
    Dl_info info = {0};
    if (!dladdr(address, &info) || info.dli_fbase != base.dli_fbase) exit(65);
    unsigned char bytes[32768];
    vm_size_t copied = 0;
    if (vm_read_overwrite(mach_task_self(), (vm_address_t)address,
        length, (vm_address_t)bytes, &copied) || copied != length) exit(66);
    fprintf(stderr, "MPS-RANGE address=%p image=%s base=%p symbol=%s\n", address,
        info.dli_fname, info.dli_fbase, info.dli_sname ?: "nil");
    for (unsigned i = 0; i + 16 <= length; i += 16) {
        fprintf(stderr, "MPS-RANGE code +%04x:", i);
        for (unsigned j = 0; j < 16; j++) fprintf(stderr, " %02x", bytes[i+j]);
        fputc('\n', stderr);
    }
    for (unsigned i = 0; i + 4 <= length; i += 4) {
        uint32_t insn;
        memcpy(&insn, bytes + i, 4);
        if ((insn & 0xfc000000) != 0x94000000) continue;
        int32_t displacement = ((int32_t)(insn << 6)) >> 4;
        const void *target = address + i + displacement;
        Dl_info call = {0};
        dladdr(target, &call);
        fprintf(stderr, "MPS-RANGE call +%04x target=%p symbol=%s\n", i,
            target, call.dli_sname ?: "nil");
    }
    return YES;
}

static void TraceDevice(id device) {
    if (!getenv("MACWS_MPS_PROBE_TRACE_STITCH")) return;
    for (Class cls = object_getClass(device); cls; cls = class_getSuperclass(cls)) {
        unsigned count = 0;
        Method *methods = class_copyMethodList(cls, &count);
        for (unsigned i = 0; i < count; i++) {
            const char *name = sel_getName(method_getName(methods[i]));
            if (!strstr(name, "titch") && !strstr(name, "DAG")) continue;
            const unsigned char *imp = (const unsigned char *)ptrauth_strip(
                method_getImplementation(methods[i]), ptrauth_key_function_pointer);
            Dl_info info = {0};
            dladdr(imp, &info);
            fprintf(stderr, "MPS-STITCH method class=%s sel=%s types=%s imp=%p image=%s base=%p offset=%#lx\n",
                    class_getName(cls), name, method_getTypeEncoding(methods[i]), imp,
                    info.dli_fname ?: "nil", info.dli_fbase,
                    (unsigned long)(imp - (const unsigned char *)info.dli_fbase));
            if (!strcmp(name, "newLibraryWithStitchedDescriptor:destinationBinaryArchive:error:")) {
                for (unsigned offset = 0; offset < 768; offset += 16) {
                    fprintf(stderr, "MPS-STITCH code +%03x:", offset);
                    for (unsigned j = 0; j < 16; j++) fprintf(stderr, " %02x", imp[offset+j]);
                    fputc('\n', stderr);
                }
                for (unsigned offset = 0; offset < 768; offset += 4) {
                    uint32_t insn;
                    memcpy(&insn, imp + offset, 4);
                    if ((insn & 0xfc000000) != 0x94000000) continue;
                    int32_t displacement = ((int32_t)(insn << 6)) >> 4;
                    const void *target = imp + offset + displacement;
                    Dl_info call = {0};
                    dladdr(target, &call);
                    fprintf(stderr, "MPS-STITCH call +%03x target=%p symbol=%s\n",
                            offset, target, call.dli_sname ?: "nil");
                }
            }
        }
        free(methods);
    }
    Method method = class_getInstanceMethod(object_getClass(device),
        @selector(newLibraryWithStitchedDescriptor:error:));
    if (method) originalStitch = (StitchFn)method_setImplementation(method, (IMP)TraceStitch);
    method = class_getInstanceMethod(object_getClass(device),
        sel_registerName("newLibraryWithStitchedDescriptorSPI:error:"));
    if (method) originalStitchSPI = (StitchFn)method_setImplementation(method, (IMP)TraceStitchSPI);
    method = class_getInstanceMethod(object_getClass(device),
        sel_registerName("newLibraryWithStitchedDescriptor:destinationBinaryArchive:error:"));
    if (method) originalStitchArchive = (StitchArchiveFn)method_setImplementation(method, (IMP)TraceStitchArchive);
    method = class_getInstanceMethod(object_getClass(device),
        sel_registerName("newLibraryWithDAG:functions:error:"));
    if (method) originalDAG = (StitchArchiveFn)method_setImplementation(method, (IMP)TraceDAG);
}

int main(int argc, char **argv) {
    if (argc != 1 && (argc != 4 || strcmp(argv[1], "--library"))) return 64;
    alarm(30);
    @autoreleasepool {
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        if (DumpMetalRange(device)) return 0;
        TraceDevice(device);
        if (argc == 4) {
            // Replay the public visible-function compilation boundary against
            // an explicit library. No route installation or result replacement.
            NSString *path = [NSString stringWithUTF8String:argv[2]];
            NSString *name = [NSString stringWithUTF8String:argv[3]];
            NSError *error = nil;
            id<MTLLibrary> library = [device newLibraryWithURL:
                [NSURL fileURLWithPath:path] error:&error];
            fprintf(stderr, "MPS-LIB path=%s library=%p error=%s\n",
                    argv[2], (__bridge void *)library,
                    error.description.UTF8String ?: "nil");
            if (!library) return 6;
            id<MTLFunction> base = [library newFunctionWithName:name];
            fprintf(stderr, "MPS-LIB base name=%s function=%p type=%lu constants=%s\n",
                    argv[3], (__bridge void *)base, (unsigned long)base.functionType,
                    base.functionConstantsDictionary.description.UTF8String ?: "nil");
            if (!base) return 7;
            MTLFunctionDescriptor *descriptor = [MTLFunctionDescriptor functionDescriptor];
            descriptor.name = name;
            descriptor.options = MTLFunctionOptionCompileToBinary;
            error = nil;
            id<MTLFunction> compiled = [library newFunctionWithDescriptor:descriptor error:&error];
            fprintf(stderr, "MPS-LIB binary name=%s function=%p error=%s userInfo=%s\n",
                    argv[3], (__bridge void *)compiled,
                    error.description.UTF8String ?: "nil",
                    error.userInfo.description.UTF8String ?: "nil");
            return compiled ? 0 : 8;
        }
        id<MTLCommandQueue> queue = [device newCommandQueue];
        InstallSubmitTrace(queue);
        fprintf(stderr, "MPS-GRAPH device=%s queue=%p\n",
                device.name.UTF8String ?: "nil", (__bridge void *)queue);
        if (!device || !queue) return 2;
        float input[16], output[16] = {0};
        for (unsigned i = 0; i < 16; i++) input[i] = i * .25f;
        id<MTLBuffer> buffer = [device newBufferWithBytes:input
            length:sizeof(input) options:MTLResourceStorageModeShared];
        if (!buffer) return 3;
        MPSGraph *graph = [MPSGraph new];
        MPSGraphTensor *x = [graph placeholderWithShape:@[@16]
            dataType:MPSDataTypeFloat32 name:@"input"];
        MPSGraphTensor *one = [graph constantWithScalar:1.0 shape:@[@16]
            dataType:MPSDataTypeFloat32];
        MPSGraphTensor *y = [graph additionWithPrimaryTensor:x
            secondaryTensor:one name:@"result"];
        if (getenv("MACWS_MPS_PROBE_MATRIX")) {
            float identity[16] = {0};
            for (unsigned i = 0; i < 4; i++) identity[i * 4 + i] = 1.f;
            MPSGraphTensor *matrix = [graph reshapeTensor:y withShape:@[@4, @4] name:nil];
            MPSGraphTensor *identityTensor = [graph constantWithData:
                [NSData dataWithBytes:identity length:sizeof(identity)]
                shape:@[@4, @4] dataType:MPSDataTypeFloat32];
            y = [graph matrixMultiplicationWithPrimaryTensor:matrix
                secondaryTensor:identityTensor name:@"identity-product"];
        }
        MPSGraphTensorData *feed = [[MPSGraphTensorData alloc]
            initWithMTLBuffer:buffer shape:@[@16] dataType:MPSDataTypeFloat32];
        fprintf(stderr, "MPS-GRAPH run-begin\n");
        NSDictionary *results = [graph runWithMTLCommandQueue:queue
            feeds:@{x: feed} targetTensors:@[y] targetOperations:nil];
        MPSGraphTensorData *result = results[y];
        if (!result) return 4;
        [result.mpsndarray readBytes:output strideBytes:NULL];
        DumpPostSubmit();
        for (unsigned i = 0; i < 16; i++) {
            if (!isfinite(output[i]) || fabsf(output[i] - input[i] - 1.f) > 1e-6f) {
                fprintf(stderr, "MPS-GRAPH mismatch index=%u actual=%g expected=%g\n",
                        i, output[i], input[i] + 1.f);
                return 5;
            }
        }
        fprintf(stderr, "MPS-GRAPH readback=16/16 first=%g last=%g\n",
                output[0], output[15]);
    }
    return 0;
}

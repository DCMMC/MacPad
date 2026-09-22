// Standalone diagnostic, never injected or linked into production.
// Replays the IconRef -> ISIcon/ISImageDescriptor -> NSImage -> layer-contents
// boundaries RE-confirmed in Finder 10D552E1-7ECE-31C9-94A2-6363AA269C11.
// No UI, cache deletion, renderer overrides, or GPU command submission.
#import <AppKit/AppKit.h>
#import <CoreServices/CoreServices.h>
#import <QuartzCore/QuartzCore.h>
#include <objc/message.h>
#include <objc/runtime.h>
#include <dlfcn.h>
#include <mach-o/dyld.h>
#include <mach-o/loader.h>
#include <sys/stat.h>
#include <unistd.h>

// Typed init-family declarations are intentional: ARC must consume the +1
// receiver and own a possibly substituted class-cluster result correctly.
// A cast objc_msgSend function pointer does not carry that init convention.
@interface NSObject (MacWSIconPrivateInitializers)
- (instancetype)initWithBinding:(CFTypeRef)binding;
- (instancetype)initWithCGImage:(CGImageRef)image;
@end

static BOOL MethodMatches(Class cls, const char *name, const char *result,
                          NSArray<NSString *> *arguments) {
    Method method = class_getInstanceMethod(cls, sel_registerName(name));
    const char *types = method ? method_getTypeEncoding(method) : NULL;
    printf("METHOD class=%s selector=%s types=%s\n", class_getName(cls), name,
           types ?: "missing");
    if (!types) return NO;
    NSMethodSignature *signature = [NSMethodSignature signatureWithObjCTypes:types];
    if (signature.numberOfArguments != arguments.count + 2 ||
        strcmp(signature.methodReturnType, result)) return NO;
    for (NSUInteger index = 0; index < arguments.count; index++)
        if (strcmp([signature getArgumentTypeAtIndex:index + 2],
                   arguments[index].UTF8String)) return NO;
    return YES;
}

static void PrintImages(void) {
    for (uint32_t i = 0; i < _dyld_image_count(); i++) {
        const char *path = _dyld_get_image_name(i);
        if (!strstr(path, "libmachook") && !strstr(path, "IconServices.framework/")) continue;
        const struct mach_header_64 *header = (const void *)_dyld_get_image_header(i);
        const struct load_command *command = (const void *)(header + 1);
        for (uint32_t j = 0; j < header->ncmds; j++) {
            if (command->cmd == LC_UUID) {
                const struct uuid_command *uuid = (const void *)command;
                printf("IMAGE path=%s uuid=", path);
                for (unsigned k = 0; k < 16; k++) printf("%02x", uuid->uuid[k]);
                puts("");
            }
            command = (const void *)((const char *)command + command->cmdsize);
        }
    }
}

static BOOL PrintPixels(const char *stage, CGImageRef image) {
    unsigned visible = 0, colored = 0;
    uint64_t hash = 14695981039346656037ULL;
    uint8_t pixels[64 * 64 * 4] = {0};
    CGColorSpaceRef color = CGColorSpaceCreateDeviceRGB();
    CGContextRef context = CGBitmapContextCreate(pixels, 64, 64, 8, 256, color,
        kCGImageAlphaPremultipliedLast | kCGBitmapByteOrder32Big);
    if (!context) { puts("ERROR bitmap-allocation"); exit(70); }
    if (image) CGContextDrawImage(context, CGRectMake(0, 0, 64, 64), image);
    for (size_t i = 0; i < sizeof(pixels); i++) { hash ^= pixels[i]; hash *= 1099511628211ULL; }
    for (unsigned i = 0; i < 4096; i++) {
        const uint8_t *pixel = pixels + i * 4;
        visible += pixel[3] != 0;
        colored += pixel[3] && (pixel[0] != pixel[1] || pixel[1] != pixel[2]);
    }
    printf("PIXELS stage=%s present=%d size=%zux%zu visible=%u colored=%u fnv=%016llx\n",
        stage, image != NULL, image ? CGImageGetWidth(image) : 0,
        image ? CGImageGetHeight(image) : 0, visible, colored, (unsigned long long)hash);
    CGContextRelease(context);
    CGColorSpaceRelease(color);
    return image != NULL && visible != 0;
}

static BOOL CheckLayer(NSImage *image, double scale, const char *stage) {
    if (!image || image.size.width <= 0 || image.size.height <= 0) return NO;
    CGFloat recommended = [image recommendedLayerContentsScale:scale];
    CGFloat selected = MIN(recommended, scale);
    if (!(selected > 0)) return NO;
    id contents = [image layerContentsForContentsScale:selected];
    // AppKit may return an opaque Objective-C _NSImageLayerContents. It is
    // not guaranteed to be a CF object, so even CFGetTypeID is not a valid
    // discriminator. The actual CALayer render below is the pixel witness.
    printf("LAYER requested=%.3f recommended=%.3f selected=%.3f class=%s\n",
        scale, (double)recommended, (double)selected,
        contents ? object_getClassName(contents) : "nil");
    // AppKit legitimately returns _NSImageLayerContents, not CGImage. Consume
    // it through the public CALayer CPU rendering API instead of calling it
    // empty or guessing its private representation/layout.
    CALayer *layer = [CALayer layer];
    layer.bounds = CGRectMake(0, 0, image.size.width, image.size.height);
    layer.contentsScale = selected;
    layer.contents = contents;
    layer.contentsGravity = kCAGravityResizeAspect;
    uint8_t layerPixels[64 * 256] = {0};
    CGColorSpaceRef color = CGColorSpaceCreateDeviceRGB();
    CGContextRef context = CGBitmapContextCreate(layerPixels, 64, 64, 8, 256, color,
        kCGImageAlphaPremultipliedLast | kCGBitmapByteOrder32Big);
    if (!context) { puts("ERROR layer-bitmap-allocation"); exit(70); }
    CGContextScaleCTM(context, 64 / image.size.width, 64 / image.size.height);
    [layer renderInContext:context];
    CGImageRef rendered = CGBitmapContextCreateImage(context);
    BOOL visible = PrintPixels(stage, rendered);
    if (rendered) CGImageRelease(rendered);
    CGContextRelease(context);
    CGColorSpaceRelease(color);
    return contents != nil && visible;
}

static BOOL CheckNSImage(NSImage *image, double scale) {
    // Finder updateLayer does not drawInRect first. Consume the cold contents
    // before isValid/CPU rasterization can populate any NSImage backing cache.
    BOOL coldVisible = CheckLayer(image, scale, "CALayer-CPU-cold");
    printf("NSIMAGE class=%s size=%.0fx%.0f reps=%lu valid=%d\n",
        image ? object_getClassName(image) : "nil", image.size.width, image.size.height,
        (unsigned long)image.representations.count, image.isValid);
    NSBitmapImageRep *bitmap = [[NSBitmapImageRep alloc]
        initWithBitmapDataPlanes:NULL pixelsWide:64 pixelsHigh:64 bitsPerSample:8
        samplesPerPixel:4 hasAlpha:YES isPlanar:NO colorSpaceName:NSDeviceRGBColorSpace
        bytesPerRow:256 bitsPerPixel:32];
    if (!bitmap) { puts("ERROR NSBitmap-allocation"); exit(70); }
    memset(bitmap.bitmapData, 0, 64 * 256);
    [NSGraphicsContext saveGraphicsState];
    [NSGraphicsContext setCurrentContext:[NSGraphicsContext graphicsContextWithBitmapImageRep:bitmap]];
    [image drawInRect:NSMakeRect(0, 0, 64, 64) fromRect:NSZeroRect
           operation:NSCompositingOperationCopy fraction:1 respectFlipped:NO hints:nil];
    [NSGraphicsContext restoreGraphicsState];
    BOOL bitmapVisible = PrintPixels("NSImage-bitmap", bitmap.CGImage);
    BOOL warmVisible = CheckLayer(image, scale, "CALayer-CPU-warm");
    return coldVisible && bitmapVisible && warmVisible && image.isValid &&
        image.representations.count != 0;
}

int main(int argc, const char *argv[]) {
    if (argc == 2 && !strcmp(argv[1], "--self-test-empty")) {
        @autoreleasepool {
            BOOL absentVisible = PrintPixels("absent-negative-control", NULL);
            NSImage *empty = [[NSImage alloc] initWithSize:NSMakeSize(64, 64)];
            BOOL emptyVisible = CheckNSImage(empty, 1);
            printf("NEGATIVE_CONTROL absent-visible=%d empty-visible=%d\n",
                   absentVisible, emptyVisible);
            // A blank result is deliberately a nonzero probe exit status.
            return absentVisible || emptyVisible ? 70 : 74;
        }
    }
    if (argc != 2 || argv[1][0] != '/') return 64;
    setvbuf(stdout, NULL, _IONBF, 0);
    alarm(15);
    @autoreleasepool {
        struct stat file;
        if (lstat(argv[1], &file) || !S_ISREG(file.st_mode) || file.st_size > 2 * 1024 * 1024) return 65;
        printf("FIXTURE path=%s inode=%llu bytes=%lld\n", argv[1], file.st_ino, file.st_size);
        void *framework = dlopen("/System/Library/PrivateFrameworks/IconServices.framework/IconServices", RTLD_LAZY);
        if (!framework) { puts("ERROR IconServices-unavailable"); return 66; }
        PrintImages();
        Class iconClass = objc_getClass("ISIcon"), descriptorClass = objc_getClass("ISImageDescriptor");
        if (!iconClass || !descriptorClass) return 67;
        // Inspect and verify each exact method before any private call. The CF
        // binding pointer has an opaque pointer encoding; no private layout read.
        id allocatedIcon = [iconClass alloc];
        Class concreteIconClass = object_getClass(allocatedIcon);
        printf("ICON_ALLOCATION class=%s\n", class_getName(concreteIconClass));
        Method bindingInit = class_getInstanceMethod(concreteIconClass, sel_registerName("initWithBinding:"));
        const char *bindingTypes = bindingInit ? method_getTypeEncoding(bindingInit) : NULL;
        printf("METHOD class=ISIcon selector=initWithBinding: types=%s\n", bindingTypes ?: "missing");
        NSMethodSignature *bindingSignature = bindingTypes ?
            [NSMethodSignature signatureWithObjCTypes:bindingTypes] : nil;
        BOOL ok = bindingSignature && bindingSignature.numberOfArguments == 3 &&
            !strcmp(bindingSignature.methodReturnType, "@") &&
            [bindingSignature getArgumentTypeAtIndex:2][0] == '^';
        ok &= MethodMatches(iconClass, "prepareImageForDescriptor:", "@", @[@"@"]);
        Method cgMethod = class_getInstanceMethod(iconClass, sel_registerName("CGImageForDescriptor:"));
        const char *cgTypes = cgMethod ? method_getTypeEncoding(cgMethod) : NULL;
        printf("METHOD class=ISIcon selector=CGImageForDescriptor: types=%s\n", cgTypes ?: "missing");
        NSMethodSignature *cgSignature = cgTypes ? [NSMethodSignature signatureWithObjCTypes:cgTypes] : nil;
        ok &= cgSignature && cgSignature.numberOfArguments == 3 &&
            !strcmp(cgSignature.methodReturnType, "^{CGImage=}") &&
            !strcmp([cgSignature getArgumentTypeAtIndex:2], "@");
        ok &= MethodMatches(descriptorClass, "setSize:", "v", @[@"{CGSize=dd}"]);
        ok &= MethodMatches(descriptorClass, "setScale:", "v", @[@"d"]);
        ok &= MethodMatches(descriptorClass, "setVariantOptions:", "v", @[@"Q"]);
        ok &= MethodMatches(descriptorClass, "setBadgeOptions:", "v", @[@"Q"]);
        ok &= MethodMatches(descriptorClass, "setSelectedVariant:", "v", @[@"B"]);
        ok &= MethodMatches(descriptorClass, "setBackgroundStyle:", "v", @[@"Q"]);
        ok &= MethodMatches(descriptorClass, "setTemplateVariant:", "v", @[@"B"]);
        Class imageRepClass = objc_getClass("NSCGImageRep");
        ok &= imageRepClass && MethodMatches(imageRepClass, "initWithCGImage:", "@", @[@"^{CGImage=}"]);
        if (!ok) { puts("ERROR unsupported-method-signature; no-private-call-made"); return 68; }
        CFTypeRef (*bindingCreate)(CFAllocatorRef, IconRef) = dlsym(RTLD_DEFAULT, "_LSBindingCreateWithIconRef");
        if (!bindingCreate) { puts("ERROR binding-factory-missing"); return 69; }
        FSRef ref;
        OSStatus status = FSPathMakeRef((const UInt8 *)argv[1], &ref, NULL);
        IconRef iconRef = NULL;
        SInt16 label = 0;
        if (!status) status = GetIconRefFromFileInfo(&ref, 0, NULL, kFSCatInfoNone,
            NULL, kIconServicesNormalUsageFlag, &iconRef, &label);
        printf("ICONREF status=%d present=%d label=%d\n", (int)status, iconRef != NULL, label);
        if (status || !iconRef) return 71;
        CFTypeRef binding = bindingCreate(kCFAllocatorDefault, iconRef);
        printf("BINDING factory=_LSBindingCreateWithIconRef present=%d\n", binding != NULL);
        if (!binding) { ReleaseIconRef(iconRef); return 72; }
        id icon = [allocatedIcon initWithBinding:binding];
        CFRelease(binding);
        ReleaseIconRef(iconRef);
        printf("ICON class=%s\n", icon ? object_getClassName(icon) : "nil");
        if (!icon) return 73;
        unsigned failedCases = 0;
        for (unsigned sizeIndex = 0; sizeIndex < 2; sizeIndex++) {
            double size = sizeIndex ? 64 : 16;
            for (unsigned scale = 1; scale <= 2; scale++) {
                for (unsigned selected = 0; selected < 2; selected++) {
                    @autoreleasepool {
                        id descriptor = [[descriptorClass alloc] init];
                        ((void (*)(id, SEL, CGSize))objc_msgSend)(descriptor, sel_registerName("setSize:"), CGSizeMake(size, size));
                        ((void (*)(id, SEL, double))objc_msgSend)(descriptor, sel_registerName("setScale:"), (double)scale);
                        ((void (*)(id, SEL, NSUInteger))objc_msgSend)(descriptor, sel_registerName("setVariantOptions:"), 0);
                        ((void (*)(id, SEL, NSUInteger))objc_msgSend)(descriptor, sel_registerName("setBadgeOptions:"), 0);
                        ((void (*)(id, SEL, BOOL))objc_msgSend)(descriptor, sel_registerName("setSelectedVariant:"), selected != 0);
                        ((void (*)(id, SEL, NSUInteger))objc_msgSend)(descriptor, sel_registerName("setBackgroundStyle:"), 0);
                        ((void (*)(id, SEL, BOOL))objc_msgSend)(descriptor, sel_registerName("setTemplateVariant:"), NO);
                        printf("CASE size=%.0f scale=%u selected=%u descriptor-class=%s\n",
                            size, scale, selected, object_getClassName(descriptor));
                        id prepared = ((id (*)(id, SEL, id))objc_msgSend)(icon,
                            sel_registerName("prepareImageForDescriptor:"), descriptor);
                        printf("PREPARED class=%s\n", prepared ? object_getClassName(prepared) : "nil");
                        CGImageRef image = ((CGImageRef (*)(id, SEL, id))objc_msgSend)(icon,
                            sel_registerName("CGImageForDescriptor:"), descriptor);
                        BOOL visibleCG = PrintPixels("ISIcon-CGImage", image);
                        NSImage *wrapped = [[NSImage alloc] initWithSize:NSMakeSize(size, size)];
                        if (image) {
                            NSImageRep *representation = [[imageRepClass alloc] initWithCGImage:image];
                            representation.size = NSMakeSize(CGImageGetWidth(image) / scale,
                                                             CGImageGetHeight(image) / scale);
                            if (representation) [wrapped addRepresentation:representation];
                        }
                        BOOL visibleWrapped = CheckNSImage(wrapped, scale);
                        BOOL caseOK = visibleCG && visibleWrapped && prepared != nil;
                        failedCases += !caseOK;
                        printf("CASE_RESULT passed=%d\n", caseOK);
                    }
                }
            }
        }
        printf("COMPLETE cases=8 failed=%u\n", failedCases);
        return failedCases ? 74 : 0;
    }
}

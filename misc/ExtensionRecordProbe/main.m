// Read-only probe for the macOS 13.4 LaunchServices record used by
// ExtensionFoundation's Settings extension discovery path.

@import Foundation;
@import AppKit;

#include <dlfcn.h>
#include <mach-o/dyld.h>
#include <mach-o/getsect.h>
#include <mach-o/loader.h>
#include <objc/message.h>
#include <objc/runtime.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static id SendObject(id object, const char *selectorName) {
    return object ? ((id (*)(id, SEL))objc_msgSend)(
        object, sel_registerName(selectorName)) : nil;
}

static id SendClassObject(const char *className, const char *selectorName) {
    Class cls = objc_getClass(className);
    return cls ? ((id (*)(id, SEL))objc_msgSend)(
        cls, sel_registerName(selectorName)) : nil;
}

static NSInteger SendInteger(id object, const char *selectorName) {
    return object ? ((NSInteger (*)(id, SEL))objc_msgSend)(
        object, sel_registerName(selectorName)) : 0;
}

static double SendDouble(id object, const char *selectorName) {
    return object ? ((double (*)(id, SEL))objc_msgSend)(
        object, sel_registerName(selectorName)) : 0.0;
}

static void DumpObjectIvars(id object, const char *label) {
    if (!object) {
        printf("OBJECT_IVARS label=%s object=nil\n", label);
        return;
    }
    printf("OBJECT_IVARS label=%s class=%s value=%s\n", label,
           object_getClassName(object), [[object description] UTF8String]);
    for (Class owner = object_getClass(object); owner;
         owner = class_getSuperclass(owner)) {
        unsigned count = 0;
        Ivar *ivars = class_copyIvarList(owner, &count);
        for (unsigned index = 0; index < count; index++) {
            const char *name = ivar_getName(ivars[index]);
            const char *type = ivar_getTypeEncoding(ivars[index]);
            ptrdiff_t offset = ivar_getOffset(ivars[index]);
            if (type && type[0] == '@') {
                id value = object_getIvar(object, ivars[index]);
                printf("OBJECT_IVAR label=%s owner=%s name=%s offset=%td "
                       "type=%s class=%s value=%s\n", label,
                       class_getName(owner), name ?: "?", offset, type,
                       value ? object_getClassName(value) : "nil",
                       value ? [[value description] UTF8String] : "nil");
            } else {
                printf("OBJECT_IVAR label=%s owner=%s name=%s offset=%td "
                       "type=%s\n", label, class_getName(owner), name ?: "?",
                       offset, type ?: "?");
            }
        }
        free(ivars);
    }
}

static id ObjectIvarNamed(id object, const char *name) {
    if (!object || !name) return nil;
    for (Class owner = object_getClass(object); owner;
         owner = class_getSuperclass(owner)) {
        Ivar ivar = class_getInstanceVariable(owner, name);
        if (ivar) return object_getIvar(object, ivar);
    }
    return nil;
}

static void WriteProbeCGImage(id image, NSString *path) {
    if (!image || !path) return;
    SEL cgImageSelector = sel_registerName("CGImage");
    CGImageRef cgImage = [image respondsToSelector:cgImageSelector]
        ? ((CGImageRef (*)(id, SEL))objc_msgSend)(image, cgImageSelector)
        : NULL;
    if (!cgImage && CFGetTypeID((__bridge CFTypeRef)image) ==
                        CGImageGetTypeID()) {
        cgImage = (__bridge CGImageRef)image;
    }
    NSBitmapImageRep *representation = cgImage
        ? [[NSBitmapImageRep alloc] initWithCGImage:cgImage] : nil;
    NSData *png = [representation representationUsingType:NSBitmapImageFileTypePNG
                                                properties:@{}];
    BOOL wrote = png.length && [png writeToFile:path atomically:YES];
    printf("PROBE_IMAGE_WRITE path=%s bytes=%lu result=%u\n",
           path.UTF8String, (unsigned long)png.length, wrote ? 1u : 0u);
}

static void DumpProbeMethods(Class cls, const char *label) {
    for (Class owner = cls; owner; owner = class_getSuperclass(owner)) {
        unsigned count = 0;
        Method *methods = class_copyMethodList(owner, &count);
        for (unsigned index = 0; index < count; index++) {
            printf("PROBE_METHOD label=%s owner=%s selector=%s types=%s\n",
                   label, class_getName(owner),
                   sel_getName(method_getName(methods[index])),
                   method_getTypeEncoding(methods[index]));
        }
        free(methods);
    }
}

static IMP gOriginalGraphicVariantWithOptions;

static id ProbeGraphicVariantWithOptions(id self, SEL selector, id options) {
    DumpObjectIvars(options, "graphic-variant-options");
    printf("GRAPHIC_VARIANT_OPTION_VALUES shape=%ld fill=%ld content=%ld "
           "shapeEffect=%ld centering=%ld scaling=%ld alignment=%ld "
           "corner=%.3f preferred=%ld\n",
           (long)SendInteger(options, "shape"),
           (long)SendInteger(options, "fill"),
           (long)SendInteger(options, "contentEffect"),
           (long)SendInteger(options, "shapeEffect"),
           (long)SendInteger(options, "imageCentering"),
           (long)SendInteger(options, "imageScaling"),
           (long)SendInteger(options, "imageAlignment"),
           SendDouble(options, "roundedRectCornerRadius"),
           (long)SendInteger(self, "preferredRenderingMode"));
    id result = ((id (*)(id, SEL, id))gOriginalGraphicVariantWithOptions)(
        self, selector, options);
    printf("GRAPHIC_VARIANT glyph-class=%s options-class=%s result-class=%s "
           "result=%s\n",
           self ? object_getClassName(self) : "nil",
           options ? object_getClassName(options) : "nil",
           result ? object_getClassName(result) : "nil",
           result ? [[result description] UTF8String] : "nil");
    DumpObjectIvars(result, "graphic-variant-result");
    SEL safeAreaSelector = sel_registerName(
        "_safeContentAreaBoundsForBackgroundSize:");
    SEL scaledRectSelector = sel_registerName(
        "_scaledContentRectForBackgroundSize:safeContentArea:");
    if (result && [result respondsToSelector:safeAreaSelector] &&
        [result respondsToSelector:scaledRectSelector]) {
        CGSize backgroundSize = CGSizeMake(32.0, 32.0);
        CGRect safeArea = ((CGRect (*)(id, SEL, CGSize))objc_msgSend)(
            result, safeAreaSelector, backgroundSize);
        CGRect contentRect = ((CGRect (*)(id, SEL, CGSize, CGRect))objc_msgSend)(
            result, scaledRectSelector, backgroundSize, safeArea);
        printf("GRAPHIC_VARIANT_GEOMETRY background=%.3fx%.3f "
               "safe=(%.3f,%.3f %.3fx%.3f) "
               "content=(%.3f,%.3f %.3fx%.3f)\n",
               backgroundSize.width, backgroundSize.height,
               safeArea.origin.x, safeArea.origin.y,
               safeArea.size.width, safeArea.size.height,
               contentRect.origin.x, contentRect.origin.y,
               contentRect.size.width, contentRect.size.height);
    }
    if (result && [result respondsToSelector:
            sel_registerName("rasterizeImageUsingScaleFactor:forTargetSize:")]) {
        id raster = ((id (*)(id, SEL, double, CGSize))objc_msgSend)(
            result,
            sel_registerName("rasterizeImageUsingScaleFactor:forTargetSize:"),
            2.0, CGSizeMake(32.0, 32.0));
        printf("GRAPHIC_VARIANT_RASTER class=%s value=%s cgimage=%p\n",
               raster ? object_getClassName(raster) : "nil",
               raster ? [[raster description] UTF8String] : "nil",
               raster && [raster respondsToSelector:sel_registerName("CGImage")]
                   ? ((void *(*)(id, SEL))objc_msgSend)(
                         raster, sel_registerName("CGImage")) : NULL);
        NSArray *palette = SendObject(options, "fillColors");
        SEL paletteSelector = sel_registerName(
            "rasterizeImageUsingScaleFactor:forTargetSize:withPaletteColors:");
        id paletteRaster = [result respondsToSelector:paletteSelector]
            ? ((id (*)(id, SEL, double, CGSize, id))objc_msgSend)(
                  result, paletteSelector, 2.0, CGSizeMake(32.0, 32.0),
                  palette) : nil;
        CGColorRef firstColor = palette.count
            ? (__bridge CGColorRef)palette.firstObject
            : CGColorGetConstantColor(kCGColorWhite);
        CGColorRef (^resolver)(NSUInteger, NSUInteger) =
            ^CGColorRef(NSUInteger layer, NSUInteger style) {
                (void)layer;
                (void)style;
                return firstColor;
            };
        SEL colorSelector = sel_registerName(
            "rasterizeImageUsingScaleFactor:forTargetSize:withColorResolver:");
        id colorRaster = [result respondsToSelector:colorSelector]
            ? ((id (*)(id, SEL, double, CGSize, id))objc_msgSend)(
                  result, colorSelector, 2.0, CGSizeMake(32.0, 32.0),
                  resolver) : nil;
        printf("GRAPHIC_VARIANT_ALT_RASTER palette=%s color=%s\n",
               paletteRaster ? object_getClassName(paletteRaster) : "nil",
               colorRaster ? object_getClassName(colorRaster) : "nil");
    }
    Class optionsClass = objc_getClass("CUIVectorGlyphGraphicVariantOptions");
    if (optionsClass && !getenv("MACWS_PROBE_NO_VARIANT_MATRIX")) {
        const char *const getters[] = {
            "shape", "fill", "contentEffect", "shapeEffect",
            "imageCentering", "imageScaling", "imageAlignment", NULL,
        };
        const char *const setters[] = {
            "setShape:", "setFill:", "setContentEffect:", "setShapeEffect:",
            "setImageCentering:", "setImageScaling:", "setImageAlignment:",
            NULL,
        };
        for (NSUInteger stage = 0; stage < 8; stage++) {
            id matrixOptions = ((id (*)(id, SEL))objc_msgSend)(
                optionsClass, sel_registerName("new"));
            for (NSUInteger optionIndex = 0; optionIndex < stage;
                 optionIndex++) {
                NSInteger value = SendInteger(options, getters[optionIndex]);
                ((void (*)(id, SEL, NSInteger))objc_msgSend)(
                    matrixOptions, sel_registerName(setters[optionIndex]),
                    value);
            }
            id matrixVariant = ((id (*)(id, SEL, id))
                gOriginalGraphicVariantWithOptions)(self, selector,
                                                     matrixOptions);
            id matrixRaster = matrixVariant
                ? ((id (*)(id, SEL, double, CGSize))objc_msgSend)(
                      matrixVariant,
                      sel_registerName(
                          "rasterizeImageUsingScaleFactor:forTargetSize:"),
                      2.0, CGSizeMake(32.0, 32.0)) : nil;
            printf("GRAPHIC_VARIANT_MATRIX stage=%lu last=%s "
                   "variant=%s raster=%s\n",
                   (unsigned long)stage,
                   stage ? getters[stage - 1] : "defaults",
                   matrixVariant ? object_getClassName(matrixVariant) : "nil",
                   matrixRaster ? object_getClassName(matrixRaster) : "nil");
        }
    }
    return result;
}

static void InstallGraphicVariantProbe(void) {
    Class glyphClass = objc_getClass("CUINamedVectorGlyph");
    SEL selector = sel_registerName("graphicVariantWithOptions:");
    Method method = glyphClass ? class_getInstanceMethod(glyphClass, selector)
                               : NULL;
    if (!method) {
        printf("GRAPHIC_VARIANT_PROBE installed=0\n");
        return;
    }
    gOriginalGraphicVariantWithOptions = method_getImplementation(method);
    method_setImplementation(method, (IMP)ProbeGraphicVariantWithOptions);
    Class variantClass = objc_getClass("_CUIGraphicVariantVectorGlyph");
    SEL rasterSelector = sel_registerName(
        "rasterizeImageUsingScaleFactor:forTargetSize:");
    Method rasterMethod = variantClass
        ? class_getInstanceMethod(variantClass, rasterSelector) : NULL;
    printf("GRAPHIC_VARIANT_PROBE installed=1 class=%s original=%p\n",
           class_getName(glyphClass), gOriginalGraphicVariantWithOptions);
    printf("GRAPHIC_VARIANT_RASTER_IMP class=%s imp=%p\n",
           variantClass ? class_getName(variantClass) : "nil",
           rasterMethod ? method_getImplementation(rasterMethod) : NULL);
}

static void DumpLoadedImageBuild(const char *needle) {
    uint32_t imageCount = _dyld_image_count();
    for (uint32_t imageIndex = 0; imageIndex < imageCount; imageIndex++) {
        const char *name = _dyld_get_image_name(imageIndex);
        if (!name || !strstr(name, needle)) continue;
        const struct mach_header_64 *header =
            (const struct mach_header_64 *)_dyld_get_image_header(imageIndex);
        uint32_t platform = 0;
        uint32_t minOS = 0;
        uint32_t sdk = 0;
        if (header && header->magic == MH_MAGIC_64) {
            const struct load_command *command =
                (const struct load_command *)((const uint8_t *)header +
                                               sizeof(*header));
            for (uint32_t index = 0; index < header->ncmds; index++) {
                if (command->cmd == LC_BUILD_VERSION &&
                    command->cmdsize >= sizeof(struct build_version_command)) {
                    const struct build_version_command *build =
                        (const struct build_version_command *)command;
                    platform = build->platform;
                    minOS = build->minos;
                    sdk = build->sdk;
                    break;
                }
                command = (const struct load_command *)
                    ((const uint8_t *)command + command->cmdsize);
            }
        }
        printf("LOADED_IMAGE needle=%s path=%s header=%p platform=%u "
               "minos=%u.%u.%u sdk=%u.%u.%u\n",
               needle, name, header, platform,
               (minOS >> 16) & 0xffff, (minOS >> 8) & 0xff, minOS & 0xff,
               (sdk >> 16) & 0xffff, (sdk >> 8) & 0xff, sdk & 0xff);
    }
}

static int DumpIconRecord(void) {
    if (!getenv("MACWS_PROBE_NO_APPKIT_APP")) [NSApplication sharedApplication];
    void *iconServices = dlopen(
        "/System/Library/PrivateFrameworks/IconServices.framework/"
        "IconServices", RTLD_NOW | RTLD_LOCAL);
    printf("ICON_FRAMEWORK handle=%p error=%s\n", iconServices,
           iconServices ? "none" : (dlerror() ?: "unknown"));
    void *settingsFramework = dlopen(
        "/System/Library/PrivateFrameworks/Settings.framework/Settings",
        RTLD_NOW | RTLD_LOCAL);
    printf("SETTINGS_FRAMEWORK handle=%p error=%s\n", settingsFramework,
           settingsFramework ? "none" : (dlerror() ?: "unknown"));
    void *sfSymbolsFramework = dlopen(
        "/System/Library/PrivateFrameworks/SFSymbols.framework/SFSymbols",
        RTLD_NOW | RTLD_LOCAL);
    printf("SFSYMBOLS_FRAMEWORK handle=%p error=%s\n", sfSymbolsFramework,
           sfSymbolsFramework ? "none" : (dlerror() ?: "unknown"));
    NSBundle *privateGlyphsBundle = [NSBundle bundleWithPath:
        @"/System/Library/PrivateFrameworks/SFSymbols.framework/Versions/A/"
         "Resources/CoreGlyphsPrivate.bundle"];
    NSError *privateGlyphsError = nil;
    BOOL privateGlyphsLoaded = [privateGlyphsBundle
        loadAndReturnError:&privateGlyphsError];
    NSBundle *bundleByIdentifier =
        [NSBundle bundleWithIdentifier:@"com.apple.CoreGlyphsPrivate"];
    printf("PRIVATE_GLYPHS_BUNDLE loaded=%u executable=%s error=%s\n",
           privateGlyphsLoaded ? 1u : 0u,
           privateGlyphsBundle.executablePath
               ? [privateGlyphsBundle.executablePath UTF8String] : "nil",
           privateGlyphsError
               ? [[privateGlyphsError description] UTF8String] : "nil");
    printf("PRIVATE_GLYPHS_IDENTIFIER bundle=%s same=%u loaded=%u\n",
           bundleByIdentifier
               ? [[bundleByIdentifier description] UTF8String] : "nil",
           bundleByIdentifier == privateGlyphsBundle ? 1u : 0u,
           bundleByIdentifier.loaded ? 1u : 0u);
    NSImage *publicSymbol = [NSImage imageWithSystemSymbolName:@"gearshape"
                                     accessibilityDescription:nil];
    NSImage *privateSymbol = [NSImage imageWithSystemSymbolName:@"appearance"
                                      accessibilityDescription:nil];
    printf("NSIMAGE_SYMBOL public=%s private=%s\n",
           publicSymbol ? [[publicSymbol description] UTF8String] : "nil",
           privateSymbol ? [[privateSymbol description] UTF8String] : "nil");
    id corePrivateBundle = SendClassObject("IFBundle",
                                            "coreGlyphsPrivateBundle");
    id corePublicBundle = SendClassObject("IFBundle",
                                           "coreGlyphsBundle");
    id corePublicCatalog = SendClassObject("IFSymbol",
                                            "coreGlyphsCatalog");
    id corePrivateCatalog = SendClassObject("IFSymbol",
                                             "coreGlyphsPrivateCatalog");
    printf("CORE_PUBLIC bundle-class=%s bundle=%s catalog-class=%s "
           "catalog=%s asset-url=%s\n",
           corePublicBundle ? object_getClassName(corePublicBundle) : "nil",
           corePublicBundle
               ? [[corePublicBundle description] UTF8String] : "nil",
           corePublicCatalog ? object_getClassName(corePublicCatalog) : "nil",
           corePublicCatalog
               ? [[corePublicCatalog description] UTF8String] : "nil",
           SendObject(corePublicBundle, "assetCatalogURL")
               ? [[SendObject(corePublicBundle, "assetCatalogURL") description]
                     UTF8String] : "nil");
    printf("CORE_PRIVATE bundle-class=%s bundle=%s catalog-class=%s "
           "catalog=%s asset-url=%s\n",
           corePrivateBundle ? object_getClassName(corePrivateBundle) : "nil",
           corePrivateBundle
               ? [[corePrivateBundle description] UTF8String] : "nil",
           corePrivateCatalog ? object_getClassName(corePrivateCatalog) : "nil",
           corePrivateCatalog
               ? [[corePrivateCatalog description] UTF8String] : "nil",
           SendObject(corePrivateBundle, "assetCatalogURL")
               ? [[SendObject(corePrivateBundle, "assetCatalogURL") description]
                     UTF8String] : "nil");
    DumpObjectIvars(corePrivateBundle, "core-private-bundle");
    DumpObjectIvars(corePrivateCatalog, "core-private-catalog");
    SEL namedGlyphSelector = sel_registerName(
        "namedVectorGlyphWithName:scaleFactor:deviceIdiom:layoutDirection:"
        "glyphSize:glyphWeight:glyphPointSize:appearanceName:");
    Method namedGlyphMethod = corePrivateCatalog
        ? class_getInstanceMethod(object_getClass(corePrivateCatalog),
                                  namedGlyphSelector)
        : NULL;
    printf("CORE_PRIVATE_GLYPH_API responds=%u types=%s\n",
           corePrivateCatalog &&
               [corePrivateCatalog respondsToSelector:namedGlyphSelector]
                   ? 1u : 0u,
           namedGlyphMethod ? method_getTypeEncoding(namedGlyphMethod) : "nil");
    if (corePublicCatalog &&
        [corePublicCatalog respondsToSelector:namedGlyphSelector]) {
        typedef id (*NamedGlyphMessage)(id, SEL, id, double, NSUInteger,
                                        NSUInteger, NSUInteger, NSInteger,
                                        double, id);
        const NSString *const names[] = {@"wifi", @"speaker.wave.3.fill",
                                         @"gearshape", @"network"};
        for (NSUInteger index = 0; index < 4; index++) {
            id glyph = ((NamedGlyphMessage)objc_msgSend)(
                corePublicCatalog, namedGlyphSelector, names[index], 2.0,
                0, 0, 0, 0, 16.0, nil);
            printf("CORE_PUBLIC_GLYPH name=%s class=%s value=%s\n",
                   [names[index] UTF8String],
                   glyph ? object_getClassName(glyph) : "nil",
                   glyph ? [[glyph description] UTF8String] : "nil");
        }
    }
    if (corePrivateCatalog && namedGlyphMethod) {
        typedef id (*NamedGlyphMessage)(id, SEL, id, double, NSUInteger,
                                        NSUInteger, NSUInteger, NSInteger,
                                        double, id);
        const double scales[] = {1.0, 2.0};
        const NSUInteger sizes[] = {0, 1, 2, 3};
        const NSInteger weights[] = {-1, 0, 3, 6};
        for (NSUInteger scaleIndex = 0; scaleIndex < 2; scaleIndex++) {
            for (NSUInteger sizeIndex = 0; sizeIndex < 4; sizeIndex++) {
                for (NSUInteger weightIndex = 0; weightIndex < 4;
                     weightIndex++) {
                    id glyph = ((NamedGlyphMessage)objc_msgSend)(
                        corePrivateCatalog, namedGlyphSelector, @"appearance",
                        scales[scaleIndex], 0, 0, sizes[sizeIndex],
                        weights[weightIndex], 16.0, nil);
                    if (glyph) {
                        printf("CORE_PRIVATE_GLYPH_HIT scale=%.0f size=%lu "
                               "weight=%ld class=%s value=%s\n",
                               scales[scaleIndex],
                               (unsigned long)sizes[sizeIndex],
                               (long)weights[weightIndex],
                               object_getClassName(glyph),
                               [[glyph description] UTF8String]);
                    }
                }
            }
        }
    }
    DumpLoadedImageBuild("/Settings.framework/");
    DumpLoadedImageBuild("/IconServices.framework/");
    DumpLoadedImageBuild("/SFSymbols.framework/");
    Class recordClass = objc_getClass("LSApplicationExtensionRecord");
    NSURL *url = [NSURL fileURLWithPath:
        @"/System/Library/ExtensionKit/Extensions/Appearance.appex"];
    NSError *error = nil;
    id record = ((id (*)(id, SEL))objc_msgSend)(
        recordClass, sel_registerName("alloc"));
    record = record ? ((id (*)(id, SEL, id, id *))objc_msgSend)(
        record, sel_registerName("initWithURL:error:"), url, &error) : nil;
    printf("ICON_RECORD class=%s value=%s error=%s\n",
           record ? object_getClassName(record) : "nil",
           record ? [[record description] UTF8String] : "nil",
           error ? [[error description] UTF8String] : "nil");
    if (!record) return 2;
    for (Class owner = object_getClass(record); owner;
         owner = class_getSuperclass(owner)) {
        unsigned methodCount = 0;
        Method *methods = class_copyMethodList(owner, &methodCount);
        for (unsigned methodIndex = 0; methodIndex < methodCount;
             methodIndex++) {
            const char *selector = sel_getName(
                method_getName(methods[methodIndex]));
            if (!strcasestr(selector, "icon")) continue;
            printf("ICON_METHOD class=%s selector=%s types=%s\n",
                   class_getName(owner), selector,
                   method_getTypeEncoding(methods[methodIndex]));
        }
        free(methods);
    }
    const char *const iconSelectors[] = {
        "iconsDictionary", "iconDictionary", "iconResourceBundleURL",
        "iconData", "iconUTTypeIdentifier", "iconFileNames", NULL,
    };
    for (const char *const *selector = iconSelectors; *selector; selector++) {
        SEL sel = sel_registerName(*selector);
        BOOL responds = [record respondsToSelector:sel];
        id value = responds ? ((id (*)(id, SEL))objc_msgSend)(record, sel)
                            : nil;
        printf("ICON_VALUE selector=%s responds=%u class=%s value=%s\n",
               *selector, responds ? 1u : 0u,
               value ? object_getClassName(value) : "nil",
               value ? [[value description] UTF8String] : "nil");
    }
    NSBundle *bundle = [NSBundle bundleWithURL:url];
    printf("ICON_BUNDLE icons=%s file=%s name=%s\n",
           [[bundle.infoDictionary[@"CFBundleIcons"] description]
               UTF8String] ?: "nil",
           [[bundle.infoDictionary[@"CFBundleIconFile"] description]
               UTF8String] ?: "nil",
           [[bundle.infoDictionary[@"CFBundleIconName"] description]
               UTF8String] ?: "nil");
    Class providerClass = objc_getClass("ISBundleResourceProvider");
    id provider = providerClass
        ? ((id (*)(id, SEL))objc_msgSend)(providerClass,
                                           sel_registerName("alloc"))
        : nil;
    SEL providerInit =
        sel_registerName("initWithBundleURL:iconDictionary:options:");
    if (provider && [provider respondsToSelector:providerInit]) {
        provider = ((id (*)(id, SEL, id, id, NSUInteger))objc_msgSend)(
            provider, providerInit, url,
            bundle.infoDictionary[@"CFBundleIcons"], 0);
    }
    id iconResourceBeforeResolve = SendObject(provider, "iconResource");
    DumpObjectIvars(provider, "provider-before-resolve");
    DumpObjectIvars(iconResourceBeforeResolve, "resource-before-resolve");
    if (provider && [provider respondsToSelector:
            sel_registerName("resolveResources")]) {
        ((void (*)(id, SEL))objc_msgSend)(provider,
                                          sel_registerName("resolveResources"));
    }
    id iconResource = SendObject(provider, "iconResource");
    id templateResource = SendObject(provider, "templateIconResource");
    DumpObjectIvars(provider, "provider-after-resolve");
    DumpObjectIvars(iconResource, "resource-after-resolve");
    id resourceDescriptor = ObjectIvarNamed(iconResource, "_descriptor");
    DumpObjectIvars(resourceDescriptor, "resource-descriptor-after-resolve");
    DumpProbeMethods(object_getClass(resourceDescriptor),
                     "resource-descriptor-after-resolve");
    const char *const descriptorSelectors[] = {
        "enclosureColors", "symbolColors", "resolvedEnclosureColors",
        "resolvedSymbolColors", "_defaultEnclosureColor",
        "_defaultSymbolColor", "resolvedName", NULL,
    };
    for (const char *const *name = descriptorSelectors; *name; name++) {
        id value = SendObject(resourceDescriptor, *name);
        printf("DESCRIPTOR_VALUE selector=%s class=%s value=%s\n", *name,
               value ? object_getClassName(value) : "nil",
               value ? [[value description] UTF8String] : "nil");
    }
    NSArray *resolvedEnclosure = SendObject(resourceDescriptor,
                                             "resolvedEnclosureColors");
    NSArray *resolvedSymbol = SendObject(resourceDescriptor,
                                          "resolvedSymbolColors");
    DumpObjectIvars(resolvedEnclosure.firstObject,
                    "resolved-enclosure-color");
    DumpObjectIvars(resolvedSymbol.firstObject, "resolved-symbol-color");
    DumpProbeMethods(objc_getClass("IFColor"), "if-color");
    printf("DESCRIPTOR_NUMERIC resolvedShape=%ld shape=%ld resolvedFill=%ld "
           "fill=%ld symbolOffset=%.3f,%.3f\n",
           (long)SendInteger(resourceDescriptor, "resolvedShape"),
           (long)SendInteger(resourceDescriptor, "shape"),
           (long)SendInteger(resourceDescriptor, "resolvedFill"),
           (long)SendInteger(resourceDescriptor, "fill"),
           ((CGSize (*)(id, SEL))objc_msgSend)(
               resourceDescriptor, sel_registerName("symbolOffset")).width,
           ((CGSize (*)(id, SEL))objc_msgSend)(
               resourceDescriptor, sel_registerName("symbolOffset")).height);
    SEL shapeNameSelector = sel_registerName("_overridesShapeStringForShape:");
    for (NSInteger shape = 0; shape < 8; shape++) {
        id value = [resourceDescriptor respondsToSelector:shapeNameSelector]
            ? ((id (*)(id, SEL, NSInteger))objc_msgSend)(
                  resourceDescriptor, shapeNameSelector, shape) : nil;
        printf("DESCRIPTOR_SHAPE value=%ld name=%s\n", (long)shape,
               value ? [[value description] UTF8String] : "nil");
    }
    DumpObjectIvars(SendObject(resourceDescriptor, "_resourceProvider"),
                    "resource-descriptor-provider-after-resolve");
    Class ifSymbolClass = objc_getClass("IFSymbol");
    SEL symbolInit = sel_registerName("initWithSymbolName:bundleURL:");
    id appSymbol = ifSymbolClass
        ? ((id (*)(id, SEL))objc_msgSend)(ifSymbolClass,
                                          sel_registerName("alloc")) : nil;
    appSymbol = appSymbol
        ? ((id (*)(id, SEL, id, id))objc_msgSend)(
              appSymbol, symbolInit, @"appearance", url) : nil;
    id privateBundleURL = [NSURL fileURLWithPath:
        @"/System/Library/CoreServices/CoreGlyphsPrivate.bundle"];
    id privateSymbolObject = ifSymbolClass
        ? ((id (*)(id, SEL))objc_msgSend)(ifSymbolClass,
                                          sel_registerName("alloc")) : nil;
    privateSymbolObject = privateSymbolObject
        ? ((id (*)(id, SEL, id, id))objc_msgSend)(
              privateSymbolObject, symbolInit, @"appearance",
              privateBundleURL) : nil;
    InstallGraphicVariantProbe();
    if (getenv("MACWS_PROBE_STOP_BEFORE_GRAPHIC_RENDER") ||
        access("/tmp/macws_probe_stop_before_graphic", F_OK) == 0) {
        printf("GRAPHIC_VARIANT_PROBE stopping pid=%d\n", getpid());
        fflush(stdout);
        raise(SIGSTOP);
    }
    const char *const renderSelectors[] = {
        "imageForDescriptor:", "imageForGraphicSymbolDescriptor:", NULL,
    };
    for (const char *const *renderSelector = renderSelectors;
         *renderSelector; renderSelector++) {
        SEL selector = sel_registerName(*renderSelector);
        id appImage = appSymbol && [appSymbol respondsToSelector:selector]
            ? ((id (*)(id, SEL, id))objc_msgSend)(appSymbol, selector,
                                                  resourceDescriptor) : nil;
        id privateImage = privateSymbolObject &&
                [privateSymbolObject respondsToSelector:selector]
            ? ((id (*)(id, SEL, id))objc_msgSend)(privateSymbolObject,
                                                  selector,
                                                  resourceDescriptor) : nil;
        printf("IFSYMBOL_RENDER selector=%s app-class=%s app=%s "
               "private-class=%s private=%s private-url=%s\n",
               *renderSelector,
               appImage ? object_getClassName(appImage) : "nil",
               appImage ? [[appImage description] UTF8String] : "nil",
               privateImage ? object_getClassName(privateImage) : "nil",
               privateImage ? [[privateImage description] UTF8String] : "nil",
               privateBundleURL
                   ? [[privateBundleURL description] UTF8String] : "nil");
    }
    SEL imageForDescriptorSelector = sel_registerName("imageForDescriptor:");
    id symbolImage = appSymbol &&
            [appSymbol respondsToSelector:imageForDescriptorSelector]
        ? ((id (*)(id, SEL, id))objc_msgSend)(appSymbol,
                                              imageForDescriptorSelector,
                                              resourceDescriptor) : nil;
    id vectorGlyph = SendObject(symbolImage, "vectorGlyph");
    DumpObjectIvars(symbolImage, "if-symbol-image");
    DumpObjectIvars(vectorGlyph, "if-vector-glyph");
    if (vectorGlyph && [vectorGlyph respondsToSelector:
            sel_registerName("rasterizeImageUsingScaleFactor:forTargetSize:")]) {
        id raster = ((id (*)(id, SEL, double, CGSize))objc_msgSend)(
            vectorGlyph,
            sel_registerName("rasterizeImageUsingScaleFactor:forTargetSize:"),
            2.0, CGSizeMake(32.0, 32.0));
        printf("VECTOR_GLYPH_RASTER class=%s value=%s cgimage=%p\n",
               raster ? object_getClassName(raster) : "nil",
               raster ? [[raster description] UTF8String] : "nil",
               raster && [raster respondsToSelector:sel_registerName("CGImage")]
                   ? ((void *(*)(id, SEL))objc_msgSend)(
                         raster, sel_registerName("CGImage")) : NULL);
        WriteProbeCGImage(raster,
                          @"/tmp/macws-settings-appearance-raw-symbol.png");
    }
    printf("ICON_PROVIDER class=%s value=%s supports=%u only=%u "
           "resource-class=%s resource=%s template-class=%s template=%s\n",
           provider ? object_getClassName(provider) : "nil",
           provider ? [[provider description] UTF8String] : "nil",
           provider && [provider respondsToSelector:
               sel_registerName("supportsGraphicIcons")]
               ? ((BOOL (*)(id, SEL))objc_msgSend)(
                     provider, sel_registerName("supportsGraphicIcons")) : 0,
           provider && [provider respondsToSelector:
               sel_registerName("onlySupportsGraphicIcons")]
               ? ((BOOL (*)(id, SEL))objc_msgSend)(
                     provider, sel_registerName("onlySupportsGraphicIcons")) : 0,
           iconResource ? object_getClassName(iconResource) : "nil",
           iconResource ? [[iconResource description] UTF8String] : "nil",
           templateResource ? object_getClassName(templateResource) : "nil",
           templateResource ? [[templateResource description] UTF8String]
                            : "nil");
    if (iconResource && [iconResource respondsToSelector:
            sel_registerName("imageForSize:scale:")]) {
        id image = ((id (*)(id, SEL, CGSize, double))objc_msgSend)(
            iconResource, sel_registerName("imageForSize:scale:"),
            CGSizeMake(32.0, 32.0), 2.0);
        printf("ICON_PROVIDER_IMAGE class=%s value=%s\n",
               image ? object_getClassName(image) : "nil",
               image ? [[image description] UTF8String] : "nil");
        WriteProbeCGImage(image, @"/tmp/macws-settings-appearance-icon.png");
        if (image && [image respondsToSelector:sel_registerName("TIFFRepresentation")]) {
            NSData *tiff = SendObject(image, "TIFFRepresentation");
            NSString *outputPath = @"/tmp/macws-settings-appearance-icon.tiff";
            BOOL wrote = [tiff writeToFile:outputPath atomically:YES];
            printf("ICON_PROVIDER_IMAGE_WRITE path=%s bytes=%lu result=%u\n",
                   [outputPath UTF8String], (unsigned long)tiff.length,
                   wrote ? 1u : 0u);
        }
    }
    if (templateResource && [templateResource respondsToSelector:
            sel_registerName("imageForSize:scale:")]) {
        id image = ((id (*)(id, SEL, CGSize, double))objc_msgSend)(
            templateResource, sel_registerName("imageForSize:scale:"),
            CGSizeMake(32.0, 32.0), 2.0);
        printf("TEMPLATE_PROVIDER_IMAGE class=%s value=%s\n",
               image ? object_getClassName(image) : "nil",
               image ? [[image description] UTF8String] : "nil");
        WriteProbeCGImage(image,
                          @"/tmp/macws-settings-appearance-template.png");
    }
    NSDictionary *iconsDictionary = bundle.infoDictionary[@"CFBundleIcons"];
    id graphicConfiguration = iconsDictionary[@"ISGraphicIconConfiguration"];
    printf("GRAPHIC_CONFIGURATION class=%s value=%s\n",
           graphicConfiguration ? object_getClassName(graphicConfiguration)
                                : "nil",
           graphicConfiguration
               ? [[graphicConfiguration description] UTF8String] : "nil");
    id namedResource = provider && [provider respondsToSelector:
            sel_registerName("resourceNamed:")]
        ? ((id (*)(id, SEL, id))objc_msgSend)(
              provider, sel_registerName("resourceNamed:"), @"appearance")
        : nil;
    DumpObjectIvars(namedResource, "provider-resource-named-appearance");
    Class settingsExtensionClass =
        objc_getClass("_TtC8Settings17SettingsExtension");
    printf("SETTINGS_EXTENSION_CLASS value=%p\n", settingsExtensionClass);
    if (settingsExtensionClass) {
        id settingsExtension = ((id (*)(id, SEL))objc_msgSend)(
            settingsExtensionClass, sel_registerName("alloc"));
        SEL initSelector =
            sel_registerName("initWithApplicationExtensionRecord:");
        printf("SETTINGS_EXTENSION_INIT responds=%u\n",
               [settingsExtension respondsToSelector:initSelector] ? 1u : 0u);
        if ([settingsExtension respondsToSelector:initSelector]) {
            settingsExtension = ((id (*)(id, SEL, id))objc_msgSend)(
                settingsExtension, initSelector, record);
            id icon = SendObject(settingsExtension, "icon");
            printf("SETTINGS_EXTENSION value=%s icon-class=%s icon=%s\n",
                   settingsExtension
                       ? [[settingsExtension description] UTF8String] : "nil",
                   icon ? object_getClassName(icon) : "nil",
                   icon ? [[icon description] UTF8String] : "nil");
            if (icon && [icon respondsToSelector:
                    sel_registerName("imageForSize:scale:")]) {
                CGSize size = CGSizeMake(32.0, 32.0);
                id image = ((id (*)(id, SEL, CGSize, double))objc_msgSend)(
                    icon, sel_registerName("imageForSize:scale:"), size, 2.0);
                printf("SETTINGS_EXTENSION_IMAGE class=%s value=%s\n",
                       image ? object_getClassName(image) : "nil",
                       image ? [[image description] UTF8String] : "nil");
            }
        }
    }
    const char *const classNames[] = {
        "ISIcon", "ISGraphicIcon", "ISImage", "ISImageDescriptor",
        "IFImage", "IFSymbolImage",
        "ISBundleResourceProvider", "ISGraphicSymbolResource",
        "ISGraphicSymbolDescriptor", "ISIconFactory", "ISConcreteIcon",
        "ISSymbolIcon", NULL,
    };
    for (const char *const *name = classNames; *name; name++) {
        Class iconClass = objc_getClass(*name);
        printf("ICON_CLASS name=%s value=%p\n", *name, iconClass);
        if (!iconClass) continue;
        Class owners[2] = {iconClass, object_getClass(iconClass)};
        const char kinds[2] = {'-', '+'};
        for (unsigned ownerIndex = 0; ownerIndex < 2; ownerIndex++) {
            unsigned methodCount = 0;
            Method *methods = class_copyMethodList(owners[ownerIndex],
                                                   &methodCount);
            for (unsigned methodIndex = 0; methodIndex < methodCount;
                 methodIndex++) {
                const char *selector = sel_getName(
                    method_getName(methods[methodIndex]));
                if (!strcasestr(selector, "init") &&
                    !strcasestr(selector, "image") &&
                    !strcasestr(selector, "graphic") &&
                    !strcasestr(selector, "dictionary") &&
                    !strcasestr(selector, "resource") &&
                    !strcasestr(selector, "configuration") &&
                    !strcasestr(selector, "icon")) continue;
                printf("ICON_API class=%s kind=%c selector=%s types=%s\n",
                       *name, kinds[ownerIndex], selector,
                       method_getTypeEncoding(methods[methodIndex]));
            }
            free(methods);
        }
    }
    unsigned runtimeClassCount = 0;
    Class *runtimeClasses = objc_copyClassList(&runtimeClassCount);
    for (unsigned index = 0; index < runtimeClassCount; index++) {
        Class candidate = runtimeClasses[index];
        const char *image = class_getImageName(candidate);
        if (!image || (!strstr(image, "/Settings.framework/") &&
                       !strstr(image, "/IconServices.framework/") &&
                       !strstr(image, "/IconFoundation.framework/"))) continue;
        Class owners[2] = {candidate, object_getClass(candidate)};
        const char kinds[2] = {'-', '+'};
        for (unsigned ownerIndex = 0; ownerIndex < 2; ownerIndex++) {
            unsigned methodCount = 0;
            Method *methods = class_copyMethodList(owners[ownerIndex],
                                                   &methodCount);
            for (unsigned methodIndex = 0; methodIndex < methodCount;
                 methodIndex++) {
                const char *selector = sel_getName(
                    method_getName(methods[methodIndex]));
                if (!strcasestr(selector, "icon") &&
                    !strcasestr(selector, "graphic") &&
                    !strcasestr(selector, "dictionary") &&
                    !strcasestr(selector, "configuration") &&
                    !strcasestr(selector, "applicationExtensionRecord"))
                    continue;
                printf("FRAMEWORK_ICON_API image=%s class=%s kind=%c "
                       "selector=%s types=%s\n", image,
                       class_getName(candidate), kinds[ownerIndex], selector,
                       method_getTypeEncoding(methods[methodIndex]));
            }
            free(methods);
        }
    }
    free(runtimeClasses);
    return 0;
}

static int RegisterPendingObjectiveCClasses(void) {
    typedef struct { uint32_t version; uint32_t flags; } ObjCImageInfo;
    typedef Class (*ReadClassPair)(Class, const void *);
    ReadClassPair readClassPair =
        (ReadClassPair)dlsym(RTLD_DEFAULT, "objc_readClassPair");
    if (!readClassPair) return -1;

    int realized = 0;
    uint32_t imageCount = _dyld_image_count();
    for (uint32_t imageIndex = 0; imageIndex < imageCount; imageIndex++) {
        const struct mach_header_64 *header =
            (const struct mach_header_64 *)_dyld_get_image_header(imageIndex);
        if (!header) continue;
        unsigned long classListSize = 0;
        uint64_t *classList = (uint64_t *)getsectiondata(
            header, "__DATA_CONST", "__objc_classlist", &classListSize);
        if (!classList) classList = (uint64_t *)getsectiondata(
            header, "__DATA", "__objc_classlist", &classListSize);
        if (!classList || !classListSize) continue;

        unsigned long imageInfoSize = 0;
        ObjCImageInfo *imageInfo = (ObjCImageInfo *)getsectiondata(
            header, "__DATA_CONST", "__objc_imageinfo", &imageInfoSize);
        if (!imageInfo) imageInfo = (ObjCImageInfo *)getsectiondata(
            header, "__DATA", "__objc_imageinfo", &imageInfoSize);
        if (!imageInfo) imageInfo = (ObjCImageInfo *)getsectiondata(
            header, "__OBJC", "__image_info", &imageInfoSize);
        if (!imageInfo) continue;

        size_t classCount = classListSize / sizeof(uint64_t);
        for (int pass = 0; pass < 8; pass++) {
            int passRealized = 0;
            for (size_t classIndex = 0; classIndex < classCount;
                 classIndex++) {
                Class candidate = (Class)classList[classIndex];
                if (!candidate) continue;
                const char *name = class_getName(candidate);
                if (!name || !name[0] || objc_getClass(name)) continue;
                Class result = readClassPair(candidate, imageInfo);
                if (result && objc_getClass(name)) {
                    realized++;
                    passRealized++;
                }
            }
            if (!passRealized) break;
        }
    }
    return realized;
}

int main(void) {
    setbuf(stdout, NULL);
    setbuf(stderr, NULL);
    @autoreleasepool {
        void *extensionFoundation = dlopen(
            "/System/Library/Frameworks/ExtensionFoundation.framework/"
            "ExtensionFoundation", RTLD_NOW | RTLD_LOCAL);
        printf("FRAMEWORK handle=%p error=%s\n", extensionFoundation,
               extensionFoundation ? "none" : (dlerror() ?: "unknown"));
        if (!extensionFoundation) return 1;
        if (getenv("MACWS_PROBE_ICON_RECORD_EARLY")) {
            return DumpIconRecord();
        }
        if (getenv("MACWS_PROBE_EXTENSIONKIT_CONSTANTS")) {
            // Runtime addresses come directly from the current-boot
            // ExtensionFoundation 13.4 disassembly of
            // +[_EXDiscoveryController canRunQuery:error:] and
            // -extensionsMatchingQuery:.  This is diagnostic-only evidence,
            // not a production patch or an ABI dependency.
            const uintptr_t constantAddresses[] = {
                0x2322222b8ULL,
                0x2322222d8ULL,
                0x2322222f8ULL,
            };
            for (size_t index = 0;
                 index < sizeof(constantAddresses) /
                             sizeof(constantAddresses[0]); index++) {
                id value = (__bridge id)(const void *)constantAddresses[index];
                printf("EXTENSIONKIT_CONSTANT address=%p class=%s value=%s\n",
                       (const void *)constantAddresses[index],
                       value ? object_getClassName(value) : "nil",
                       value ? [[value description] UTF8String] : "nil");
            }
        }
        void *launchServices = dlopen(
            "/System/Library/Frameworks/CoreServices.framework/Versions/A/"
            "Frameworks/LaunchServices.framework/Versions/A/LaunchServices",
            RTLD_NOW | RTLD_LOCAL);
        printf("LAUNCHSERVICES handle=%p error=%s\n", launchServices,
               launchServices ? "none" : (dlerror() ?: "unknown"));

        // lsregister itself uses this one-argument entry point when it imports
        // ExtensionKit plug-ins discovered by EXEnumerator. Keep the mutation
        // opt-in: the default probe remains read-mostly, while the diagnostic
        // mode exercises the same Ventura client/server path as database seed.
        if (getenv("MACWS_PROBE_REGISTER_ORIGINAL")) {
            if (getenv("MACWS_PROBE_STOP_BEFORE_REGISTER")) {
                printf("PRIVATE_REGISTER waiting-for-lldb pid=%d\n",
                       getpid());
                raise(SIGSTOP);
            }
            typedef int32_t (*LSRegisterPluginURL)(CFURLRef);
            LSRegisterPluginURL registerPluginURL =
                (LSRegisterPluginURL)dlsym(launchServices,
                                           "_LSRegisterPluginURL");
            NSURL *appearanceURL = [NSURL fileURLWithPath:
                @"/System/Library/ExtensionKit/Extensions/Appearance.appex"];
            BOOL wantsRegistrationDetail =
                getenv("MACWS_PROBE_REGISTER_DETAIL") != NULL;
            int32_t status = !wantsRegistrationDetail && registerPluginURL
                ? registerPluginURL((__bridge CFURLRef)appearanceURL)
                : INT32_MIN;
            printf("PRIVATE_REGISTER symbol=%p status=%d url=%s\n",
                   registerPluginURL, status,
                   appearanceURL.path.UTF8String);
            if (wantsRegistrationDetail) {
                typedef int32_t (*LSContextInit)(void *);
                typedef void (*LSContextDestroy)(void *);
                typedef BOOL (*LSRegisterPluginNode)(
                    void *, id, id, uint32_t, uint32_t, NSError **);
                LSContextInit contextInit = (LSContextInit)dlsym(
                    launchServices, "_LSContextInit");
                LSContextDestroy contextDestroy = (LSContextDestroy)dlsym(
                    launchServices, "_LSContextDestroy");
                LSRegisterPluginNode registerPluginNode =
                    (LSRegisterPluginNode)dlsym(
                        launchServices, "_LSRegisterPluginNode");
                // These three symbols are local in Ventura's LaunchServices
                // image, so dlsym normally cannot return them. libmachook has
                // already loaded Substrate; use its image-symbol resolver for
                // this opt-in diagnostic, matching the project's LLDB symbol
                // names exactly.
                typedef void *(*MSFindSymbolFn)(void *, const char *);
                MSFindSymbolFn findSymbol = (MSFindSymbolFn)dlsym(
                    RTLD_DEFAULT, "MSFindSymbol");
                Dl_info launchServicesInfo = {};
                if (findSymbol && registerPluginURL &&
                    dladdr((void *)registerPluginURL,
                           &launchServicesInfo) &&
                    launchServicesInfo.dli_fbase) {
                    void *image = (void *)launchServicesInfo.dli_fbase;
                    if (!contextInit) contextInit = (LSContextInit)findSymbol(
                        image, "__LSContextInit");
                    if (!contextDestroy)
                        contextDestroy = (LSContextDestroy)findSymbol(
                            image, "__LSContextDestroy");
                    if (!registerPluginNode)
                        registerPluginNode = (LSRegisterPluginNode)findSymbol(
                            image, "__LSRegisterPluginNode");
                }
                // RE-confirmed at _LSRegisterPluginURL+0x30 in Ventura 13.4:
                // the classref loaded immediately before objc_alloc is FSNode.
                Class builderClass = objc_getClass("FSNode");
                NSError *builderError = nil;
                id builder = ((id (*)(id, SEL))objc_msgSend)(
                    builderClass, sel_registerName("alloc"));
                builder = ((id (*)(id, SEL, id, NSUInteger, NSError **))
                    objc_msgSend)(
                        builder, sel_registerName("initWithURL:flags:error:"),
                        appearanceURL, 0, &builderError);
                uintptr_t context = 0;
                int32_t contextStatus = contextInit
                    ? contextInit(&context) : INT32_MIN;
                NSError *registrationError = nil;
                BOOL registrationResult = builder && !contextStatus &&
                    registerPluginNode
                    ? registerPluginNode(&context, builder, nil, 0, 0,
                                         &registrationError)
                    : NO;
                printf("PRIVATE_REGISTER_DETAIL builderClass=%p builder=%s "
                       "builderError=%s contextInit=%p contextStatus=%d "
                       "context=%p registerNode=%p result=%u error=%s\n",
                       builderClass,
                       builder ? [[builder description] UTF8String] : "nil",
                       builderError
                           ? [[builderError description] UTF8String] : "nil",
                       contextInit, contextStatus, (void *)context,
                       registerPluginNode, registrationResult ? 1 : 0,
                       registrationError
                           ? [[registrationError description] UTF8String]
                           : "nil");
                if (contextDestroy && !contextStatus)
                    contextDestroy(&context);
            }
        }
        int realizedClasses = RegisterPendingObjectiveCClasses();
        printf("OBJC_PREREGISTER realized=%d LSPlugInKitProxy=%p\n",
               realizedClasses, objc_getClass("LSPlugInKitProxy"));
        if (getenv("MACWS_PROBE_ICON_RECORD") ||
            access("/tmp/macws_probe_stop_before_graphic", F_OK) == 0) {
            return DumpIconRecord();
        }

        // Capture the exact Objective-C ABI of the late ExtensionFoundation
        // entry point before installing a boundary hook in libmachook.  This
        // keeps the production wrapper tied to runtime metadata from the
        // actual Ventura framework rather than a guessed private signature.
        Class runningExtensionClass = objc_getClass("_EXRunningExtension");
        Method startMethod = class_getInstanceMethod(
            runningExtensionClass,
            sel_registerName("_startWithArguments:count:"));
        printf("RUNNING_EXTENSION class=%p method=%p types=%s imp=%p\n",
               runningExtensionClass, startMethod,
               startMethod ? method_getTypeEncoding(startMethod) : "nil",
               startMethod ? method_getImplementation(startMethod) : NULL);

        NSString *identifier = @"com.apple.Appearance-Settings.extension";
        NSURL *embeddedProxyURL = [NSURL fileURLWithPath:
            @"/private/var/jb/Applications/MacWSCatalystLauncher.app/"
             "PlugIns/SettingsExtensionProxy.appex"];
        Class workspaceClass = objc_getClass("LSApplicationWorkspace");
        Method defaultWorkspaceMethod = class_getClassMethod(
            workspaceClass, sel_registerName("defaultWorkspace"));
        Method registerPluginMethod = class_getInstanceMethod(
            workspaceClass, sel_registerName("registerPlugin:"));
        printf("WORKSPACE class=%p defaultTypes=%s registerTypes=%s\n",
               workspaceClass,
               defaultWorkspaceMethod
                   ? method_getTypeEncoding(defaultWorkspaceMethod) : "nil",
               registerPluginMethod
                   ? method_getTypeEncoding(registerPluginMethod) : "nil");
        id workspace = ((id (*)(id, SEL))objc_msgSend)(
            workspaceClass, sel_registerName("defaultWorkspace"));
        NSURL *carrierURL = [NSURL fileURLWithPath:
            @"/private/var/jb/Applications/MacWSCatalystLauncher.app"];
        BOOL applicationRegistered = ((BOOL (*)(id, SEL, id))objc_msgSend)(
            workspace, sel_registerName("registerApplication:"), carrierURL);
        printf("WORKSPACE registerApplication=%u url=%s\n",
               applicationRegistered ? 1 : 0, carrierURL.path.UTF8String);
        BOOL registered = ((BOOL (*)(id, SEL, id))objc_msgSend)(
            workspace, sel_registerName("registerPlugin:"), embeddedProxyURL);
        printf("WORKSPACE registerPlugin=%u url=%s\n", registered ? 1 : 0,
               embeddedProxyURL.path.UTF8String);
        NSURL *originalAppearanceURL = [NSURL fileURLWithPath:
            @"/System/Library/ExtensionKit/Extensions/Appearance.appex"];
        BOOL originalRegistered = ((BOOL (*)(id, SEL, id))objc_msgSend)(
            workspace, sel_registerName("registerPlugin:"),
            originalAppearanceURL);
        printf("WORKSPACE registerOriginalPlugin=%u url=%s\n",
               originalRegistered ? 1 : 0,
               originalAppearanceURL.path.UTF8String);
        Class concreteExtensionClass = objc_getClass("EXConcreteExtension");
        id concreteError = nil;
        id concreteExtension = ((id (*)(id, SEL, id, id *))objc_msgSend)(
            concreteExtensionClass,
            sel_registerName("extensionWithIdentifier:error:"), identifier,
            &concreteError);
        printf("CONCRETE class=%s value=%s error=%s\n",
               concreteExtension ? object_getClassName(concreteExtension) : "nil",
               concreteExtension ? [[concreteExtension description] UTF8String] : "nil",
               concreteError ? [[concreteError description] UTF8String] : "nil");
        Class queryClass = objc_getClass("_EXQuery");
        id query = ((id (*)(id, SEL, id))objc_msgSend)(
            queryClass, sel_registerName("extensionPointIdentifierQuery:"),
            @"com.apple.Settings.extension.ui");
        id queryPointRecords = SendObject(query, "extensionPointRecords");
        unsigned long long queryResultType =
            ((unsigned long long (*)(id, SEL))objc_msgSend)(
                query, sel_registerName("resultType"));
        BOOL queryPostprocessing = ((BOOL (*)(id, SEL))objc_msgSend)(
            query, sel_registerName("includePostprocessing"));
        printf("QUERY_STATE class=%s pointRecordCount=%lu resultType=%llu "
               "postprocessing=%u pointRecords=%s\n",
               query ? object_getClassName(query) : "nil",
               (unsigned long)[queryPointRecords count], queryResultType,
               queryPostprocessing ? 1 : 0,
               queryPointRecords
                   ? [[queryPointRecords description] UTF8String] : "nil");
        Class discoveryControllerClass =
            objc_getClass("_EXDiscoveryController");
        NSError *queryAdmissionError = nil;
        BOOL queryCanRun =
            ((BOOL (*)(id, SEL, id, NSError **))objc_msgSend)(
                discoveryControllerClass,
                sel_registerName("canRunQuery:error:"), query,
                &queryAdmissionError);
        Class defaultsClassForAdmission = objc_getClass("_EXDefaults");
        id defaultsForAdmission = ((id (*)(id, SEL))objc_msgSend)(
            defaultsClassForAdmission, sel_registerName("sharedInstance"));
        BOOL forceSandbox = ((BOOL (*)(id, SEL))objc_msgSend)(
            defaultsForAdmission, sel_registerName("forceSandbox"));
        id allowedUnsandboxed = SendObject(
            defaultsForAdmission, "allowedUnsandboxedExtensionPoints");
        printf("QUERY_ADMISSION canRun=%u error=%s forceSandbox=%u "
               "allowedUnsandboxed=%s\n",
               queryCanRun ? 1 : 0,
               queryAdmissionError
                   ? [[queryAdmissionError description] UTF8String] : "nil",
               forceSandbox ? 1 : 0,
               allowedUnsandboxed
                   ? [[allowedUnsandboxed description] UTF8String] : "nil");
        if (getenv("MACWS_PROBE_QUERY_POINT_ENUMERATOR")) {
            id queryPointRecord = [queryPointRecords firstObject];
            Class recordClassForQuery =
                objc_getClass("LSApplicationExtensionRecord");
            id recordEnumerator = ((id (*)(id, SEL, id,
                                            unsigned long long))objc_msgSend)(
                recordClassForQuery,
                sel_registerName("enumeratorWithExtensionPointRecord:options:"),
                queryPointRecord, 0);
            NSUInteger recordCount = 0;
            NSUInteger matchCount = 0;
            for (; recordCount < 10000; recordCount++) {
                id candidate = SendObject(recordEnumerator, "nextObject");
                if (!candidate) break;
                BOOL matches = ((BOOL (*)(id, SEL, id))objc_msgSend)(
                    query, sel_registerName("matchesRecord:"), candidate);
                if (matches) matchCount++;
                if (recordCount < 5 || matches) {
                    printf("QUERY_ENUM_RECORD index=%lu class=%s id=%s "
                           "platform=%u matches=%u url=%s\n",
                           (unsigned long)recordCount,
                           object_getClassName(candidate),
                           [[SendObject(candidate, "bundleIdentifier")
                               description] UTF8String],
                           ((unsigned (*)(id, SEL))objc_msgSend)(
                               candidate, sel_registerName("platform")),
                           matches ? 1 : 0,
                           [[SendObject(candidate, "URL") description]
                               UTF8String]);
                }
            }
            printf("QUERY_ENUM_SUMMARY pointRecord=%s enumeratorClass=%s "
                   "recordCount=%lu matchCount=%lu\n",
                   queryPointRecord
                       ? [[queryPointRecord description] UTF8String] : "nil",
                   recordEnumerator
                       ? object_getClassName(recordEnumerator) : "nil",
                   (unsigned long)recordCount, (unsigned long)matchCount);
        }
        id queryResults = ((id (*)(id, SEL, id))objc_msgSend)(
            queryClass, sel_registerName("executeQuery:"), query);
        printf("QUERY resultCount=%lu results=%s\n",
               (unsigned long)[queryResults count],
               queryResults ? [[queryResults description] UTF8String] : "nil");
        if (getenv("MACWS_PROBE_QUERY_ROUTES")) {
            Class defaultsClass = objc_getClass("_EXDefaults");
            id defaults = ((id (*)(id, SEL))objc_msgSend)(
                defaultsClass, sel_registerName("sharedInstance"));
            BOOL preferInProcess = ((BOOL (*)(id, SEL))objc_msgSend)(
                defaults, sel_registerName("preferInProcessDiscovery"));
            NSArray *queries = query ? @[query] : @[];
            Class discoveryClass = objc_getClass("_EXDiscoveryController");
            id discovery = ((id (*)(id, SEL))objc_msgSend)(
                discoveryClass, sel_registerName("sharedInstance"));
            id discoveryResult = ((id (*)(id, SEL, id))objc_msgSend)(
                discovery, sel_registerName("extensionsMatchingQueries:"),
                queries);
            id discoveryIdentities = SendObject(discoveryResult,
                                                 "identities");
            id discoverySingleResult =
                ((id (*)(id, SEL, id))objc_msgSend)(
                    discovery,
                    sel_registerName("extensionsMatchingQuery:"), query);
            id discoverySingleIdentities = SendObject(
                discoverySingleResult, "identities");
            printf("QUERY_ROUTE defaults=%s preferInProcess=%u "
                   "discovery=%s resultClass=%s identitiesCount=%lu "
                   "identities=%s singleResultClass=%s "
                   "singleIdentitiesCount=%lu singleIdentities=%s\n",
                   defaults ? object_getClassName(defaults) : "nil",
                   preferInProcess ? 1 : 0,
                   discovery ? object_getClassName(discovery) : "nil",
                   discoveryResult ? object_getClassName(discoveryResult)
                                   : "nil",
                   (unsigned long)[discoveryIdentities count],
                   discoveryIdentities
                       ? [[discoveryIdentities description] UTF8String]
                       : "nil",
                   discoverySingleResult
                       ? object_getClassName(discoverySingleResult) : "nil",
                   (unsigned long)[discoverySingleIdentities count],
                   discoverySingleIdentities
                       ? [[discoverySingleIdentities description] UTF8String]
                       : "nil");

            Class serviceClass = objc_getClass("_EXServiceClient");
            id service = ((id (*)(id, SEL))objc_msgSend)(
                serviceClass, sel_registerName("sharedInstance"));
            id serviceResult = ((id (*)(id, SEL, id))objc_msgSend)(
                service, sel_registerName("extensionsWithQueries:"), queries);
            id serviceIdentities = SendObject(serviceResult, "identities");
            printf("QUERY_ROUTE service=%s resultClass=%s "
                   "identitiesCount=%lu identities=%s\n",
                   service ? object_getClassName(service) : "nil",
                   serviceResult ? object_getClassName(serviceResult) : "nil",
                   (unsigned long)[serviceIdentities count],
                   serviceIdentities
                       ? [[serviceIdentities description] UTF8String]
                       : "nil");
        }
        Class enumeratorClass = objc_getClass("EXEnumerator");
        Class catalogClass = objc_getClass("EXExtensionPointCatalog");
        id pointEnumerator = ((id (*)(id, SEL))objc_msgSend)(
            enumeratorClass,
            sel_registerName("extensionPointDefinitionEnumerator"));
        id catalog = ((id (*)(id, SEL))objc_msgSend)(
            catalogClass, sel_registerName("alloc"));
        catalog = ((id (*)(id, SEL, id))objc_msgSend)(
            catalog, sel_registerName("initWithEnumerator:"), pointEnumerator);
        id point = ((id (*)(id, SEL, id))objc_msgSend)(
            catalog, sel_registerName("extensionPointForIdentifier:"),
            @"com.apple.Settings.extension.ui");
        id pointQuery = ((id (*)(id, SEL))objc_msgSend)(
            queryClass, sel_registerName("alloc"));
        pointQuery = ((id (*)(id, SEL, id))objc_msgSend)(
            pointQuery, sel_registerName("initWithExtensionPoint:"), point);
        id pointQueryRecords = SendObject(pointQuery,
                                          "extensionPointRecords");
        printf("POINT_QUERY_STATE pointRecordCount=%lu pointRecords=%s\n",
               (unsigned long)[pointQueryRecords count],
               pointQueryRecords
                   ? [[pointQueryRecords description] UTF8String] : "nil");
        id pointQueryResults = ((id (*)(id, SEL, id))objc_msgSend)(
            queryClass, sel_registerName("executeQuery:"), pointQuery);
        printf("POINT_QUERY point=%s resultCount=%lu\n",
               point ? [[point description] UTF8String] : "nil",
               (unsigned long)[pointQueryResults count]);
        Class recordClass = objc_getClass("LSApplicationExtensionRecord");
        id embeddedError = nil;
        id embeddedRecord = ((id (*)(id, SEL))objc_msgSend)(
            recordClass, sel_registerName("alloc"));
        embeddedRecord = ((id (*)(id, SEL, id, id *))objc_msgSend)(
            embeddedRecord, sel_registerName("initWithURL:error:"),
            embeddedProxyURL, &embeddedError);
        printf("EMBEDDED_RECORD class=%s value=%s error=%s\n",
               embeddedRecord ? object_getClassName(embeddedRecord) : "nil",
               embeddedRecord ? [[embeddedRecord description] UTF8String] : "nil",
               embeddedError ? [[embeddedError description] UTF8String] : "nil");
        if (embeddedRecord) {
            unsigned embeddedPlatform = ((unsigned (*)(id, SEL))objc_msgSend)(
                embeddedRecord, sel_registerName("platform"));
            id embeddedPoint = SendObject(embeddedRecord,
                                          "extensionPointRecord");
            printf("EMBEDDED_RECORD platform=%u point=%s url=%s\n",
                   embeddedPlatform,
                   embeddedPoint ? [[embeddedPoint description] UTF8String] : "nil",
                   [[SendObject(embeddedRecord, "URL") description] UTF8String]);
        }
        Class proxyClass = objc_getClass("LSPlugInKitProxy");
        id proxy = ((id (*)(id, SEL, id))objc_msgSend)(
            proxyClass, sel_registerName("pluginKitProxyForIdentifier:"),
            identifier);
        id proxyPlatform = SendObject(proxy, "platform");
        id proxyPoint = SendObject(proxy, "extensionPoint");
        id record = SendObject(proxy,
                               "correspondingApplicationExtensionRecord");
        printf("PROXY class=%s platform=%s point=%s\n",
               proxy ? object_getClassName(proxy) : "nil",
               proxyPlatform ? [[proxyPlatform description] UTF8String] : "nil",
               proxyPoint ? [[proxyPoint description] UTF8String] : "nil");
        if (!record) {
            printf("RECORD nil\n");
            return 2;
        }

        unsigned platform = ((unsigned (*)(id, SEL))objc_msgSend)(
            record, sel_registerName("platform"));
        id recordIdentifier = SendObject(record, "bundleIdentifier");
        id recordURL = SendObject(record, "URL");
        id pointRecord = SendObject(record, "extensionPointRecord");
        printf("RECORD class=%s platform=%u identifier=%s url=%s\n",
               object_getClassName(record), platform,
               [[recordIdentifier description] UTF8String],
               [[recordURL description] UTF8String]);
        if (getenv("MACWS_PROBE_ICON_RECORD")) {
            Class owners[2] = {object_getClass(record),
                               object_getClass(object_getClass(record))};
            const char kinds[2] = {'-', '+'};
            for (unsigned ownerIndex = 0; ownerIndex < 2; ownerIndex++) {
                unsigned methodCount = 0;
                Method *methods = class_copyMethodList(owners[ownerIndex],
                                                       &methodCount);
                for (unsigned methodIndex = 0; methodIndex < methodCount;
                     methodIndex++) {
                    const char *selector = sel_getName(
                        method_getName(methods[methodIndex]));
                    if (!strcasestr(selector, "icon")) continue;
                    printf("ICON_METHOD class=%s kind=%c selector=%s types=%s\n",
                           class_getName(object_getClass(record)),
                           kinds[ownerIndex], selector,
                           method_getTypeEncoding(methods[methodIndex]));
                }
                free(methods);
            }
            const char *const iconSelectors[] = {
                "iconsDictionary", "iconDictionary", "iconResourceBundleURL",
                "iconData", "iconUTTypeIdentifier", "iconFileNames", NULL,
            };
            for (const char *const *selector = iconSelectors; *selector;
                 selector++) {
                SEL sel = sel_registerName(*selector);
                BOOL responds = [record respondsToSelector:sel];
                id value = responds ? ((id (*)(id, SEL))objc_msgSend)(record, sel)
                                    : nil;
                printf("ICON_VALUE selector=%s responds=%u class=%s value=%s\n",
                       *selector, responds ? 1u : 0u,
                       value ? object_getClassName(value) : "nil",
                       value ? [[value description] UTF8String] : "nil");
            }
            NSBundle *recordBundle = [NSBundle bundleWithURL:recordURL];
            printf("ICON_BUNDLE icons=%s file=%s name=%s\n",
                   [[recordBundle.infoDictionary[@"CFBundleIcons"] description]
                       UTF8String] ?: "nil",
                   [[recordBundle.infoDictionary[@"CFBundleIconFile"] description]
                       UTF8String] ?: "nil",
                   [[recordBundle.infoDictionary[@"CFBundleIconName"] description]
                       UTF8String] ?: "nil");
        }
        printf("POINT_RECORD class=%s value=%s\n",
               pointRecord ? object_getClassName(pointRecord) : "nil",
               pointRecord ? [[pointRecord description] UTF8String] : "nil");

        if (getenv("MACWS_PROBE_DETACHED_ENUMERATOR")) {
            id enumerator = ((id (*)(id, SEL, id,
                                      unsigned long long))objc_msgSend)(
                recordClass,
                sel_registerName("enumeratorWithExtensionPointRecord:options:"),
                pointRecord, 0);
            NSUInteger count = 0;
            BOOL foundAppearance = NO;
            for (; count < 10000; count++) {
                id candidate = SendObject(enumerator, "nextObject");
                if (!candidate) break;
                id candidateIdentifier = SendObject(candidate, "bundleIdentifier");
                if ([candidateIdentifier isEqual:identifier]) {
                    foundAppearance = YES;
                }
            }
            printf("ENUMERATOR class=%s count=%lu foundAppearance=%u\n",
                   enumerator ? object_getClassName(enumerator) : "nil",
                   (unsigned long)count, foundAppearance ? 1 : 0);
        }

        id urlError = nil;
        id urlRecord = ((id (*)(id, SEL))objc_msgSend)(
            recordClass, sel_registerName("alloc"));
        urlRecord = ((id (*)(id, SEL, id, id *))objc_msgSend)(
            urlRecord, sel_registerName("initWithURL:error:"), recordURL,
            &urlError);
        printf("URL_RECORD class=%s value=%s error=%s\n",
               urlRecord ? object_getClassName(urlRecord) : "nil",
               urlRecord ? [[urlRecord description] UTF8String] : "nil",
               urlError ? [[urlError description] UTF8String] : "nil");

        if (getenv("MACWS_PROBE_DETACHED_IDENTITY")) {
            Class identityClass = objc_getClass("_EXExtensionRecordIdentity");
            id identity = ((id (*)(id, SEL))objc_msgSend)(
                identityClass, sel_registerName("alloc"));
            identity = ((id (*)(id, SEL, id))objc_msgSend)(
                identity,
                sel_registerName("initWithApplicationExtensionRecord:"), record);
            printf("IDENTITY class=%s value=%s\n",
                   identity ? object_getClassName(identity) : "nil",
                   identity ? [[identity description] UTF8String] : "nil");
        }

        unsigned classCount = 0;
        Class *classes = objc_copyClassList(&classCount);
        for (unsigned classIndex = 0; classIndex < classCount; classIndex++) {
            Class candidateClass = classes[classIndex];
            const char *image = class_getImageName(candidateClass);
            if (!image || !strstr(image, "LaunchServices.framework")) continue;
            Class owners[2] = {candidateClass, object_getClass(candidateClass)};
            const char kinds[2] = {'-', '+'};
            for (unsigned ownerIndex = 0; ownerIndex < 2; ownerIndex++) {
                unsigned methodCount = 0;
                Method *methods = class_copyMethodList(owners[ownerIndex],
                                                       &methodCount);
                for (unsigned methodIndex = 0; methodIndex < methodCount;
                     methodIndex++) {
                    const char *selector = sel_getName(
                        method_getName(methods[methodIndex]));
                    if (!strstr(selector, "register") &&
                        !strstr(selector, "Register") &&
                        !strstr(selector, "plugin") &&
                        !strstr(selector, "Plugin") &&
                        !strstr(selector, "applicationExtension")) {
                        continue;
                    }
                    printf("LSINVENTORY class=%s kind=%c selector=%s types=%s\n",
                           class_getName(candidateClass), kinds[ownerIndex],
                           selector,
                           method_getTypeEncoding(methods[methodIndex]));
                }
                free(methods);
            }
        }
        free(classes);
        const char *targetClassNames[] = {
            "_LSInstaller", "LSBundleRecordBuilder", "_LSDModifyClient", NULL
        };
        for (const char *const *name = targetClassNames; *name; name++) {
            Class targetClass = objc_getClass(*name);
            printf("LSTARGET class=%s value=%p\n", *name, targetClass);
            if (!targetClass) continue;
            Class owners[2] = {targetClass, object_getClass(targetClass)};
            const char kinds[2] = {'-', '+'};
            for (unsigned ownerIndex = 0; ownerIndex < 2; ownerIndex++) {
                unsigned methodCount = 0;
                Method *methods = class_copyMethodList(owners[ownerIndex],
                                                       &methodCount);
                for (unsigned methodIndex = 0; methodIndex < methodCount;
                     methodIndex++) {
                    printf("LSTARGET class=%s kind=%c selector=%s types=%s\n",
                           *name, kinds[ownerIndex],
                           sel_getName(method_getName(methods[methodIndex])),
                           method_getTypeEncoding(methods[methodIndex]));
                }
                free(methods);
            }
        }
    }
    return 0;
}

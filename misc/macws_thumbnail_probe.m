// Bounded, non-activating diagnostic for one operator-selected fixture.
// Compares real QuickLook representations with the separate NSWorkspace
// icon probe. No cache deletion, renderer override or app injection.
#import <AppKit/AppKit.h>
#import <QuickLook/QuickLook.h>
#import <QuickLookThumbnailing/QuickLookThumbnailing.h>
#include <objc/runtime.h>
#include <xpc/xpc.h>
#include <errno.h>
#include <stdatomic.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <unistd.h>

// Optional observation of this diagnostic client's own reply construction.
// Does not inject into/restart the shared QuickLook services or alter replies.
static id (*OriginalMessageInit)(id, SEL, id);
static unsigned MessageCount;
static id TraceMessageInit(id self, SEL command, id message) {
    if (MessageCount++ < 8 && xpc_get_type(message) == XPC_TYPE_DICTIONARY) {
        char *description = xpc_copy_description(message);
        printf("QLMESSAGE incoming=%.*s\n", 8192, description ?: "nil");
        free(description);
        fflush(stdout);
    }
    return OriginalMessageInit(self, command, message);
}

static BOOL ObserveOwnReplies(void) {
    Class cl = NSClassFromString(@"QLSatelliteMessage");
    Method method = cl ? class_getInstanceMethod(cl,
        sel_registerName("initWithXPCMessage:")) : NULL;
    if (!method || strcmp(method_getTypeEncoding(method), "@24@0:8@16")) return NO;
    OriginalMessageInit = (void *)method_getImplementation(method);
    method_setImplementation(method, (IMP)TraceMessageInit);
    printf("QLMESSAGE observer=self-only installed=1\n"); fflush(stdout);
    return YES;
}

static unsigned ImagePixels(CGImageRef image, unsigned *colored) {
    unsigned visible = 0;
    *colored = 0;
    uint8_t pixels[64 * 64 * 4] = {0};
    CGColorSpaceRef colorSpace = CGColorSpaceCreateDeviceRGB();
    CGContextRef context = CGBitmapContextCreate(pixels, 64, 64, 8,
        64 * 4, colorSpace,
        kCGImageAlphaPremultipliedLast | kCGBitmapByteOrder32Big);
    if (image && context) CGContextDrawImage(context, CGRectMake(0, 0, 64, 64), image);
    for (unsigned index = 0; index < 4096; index++) {
        uint8_t *pixel = pixels + index * 4;
        visible += pixel[3] != 0;
        *colored += pixel[3] && (pixel[0] != pixel[1] || pixel[1] != pixel[2]);
    }
    if (context) CGContextRelease(context);
    CGColorSpaceRelease(colorSpace);
    return visible;
}

static void PrintVolume(NSURL *url) {
    struct statfs info = {0};
    int result = statfs(url.fileSystemRepresentation, &info), savedError = errno;
    printf("VOLUME statfs=%d errno=%d type=%s mount=%s from=%s bsize=%u blocks=%llu fsid=%x:%x flags=%#x\n",
        result, result ? savedError : 0, info.f_fstypename, info.f_mntonname,
        info.f_mntfromname, info.f_bsize, info.f_blocks,
        info.f_fsid.val[0], info.f_fsid.val[1], info.f_flags);
    struct stat file = {0};
    result = stat(url.fileSystemRepresentation, &file); savedError = errno;
    printf("VOLUME stat=%d errno=%d dev=%d inode=%llu size=%lld\n",
        result, result ? savedError : 0, file.st_dev, file.st_ino, file.st_size);
    for (NSString *key in @[NSURLCanonicalPathKey, NSURLVolumeURLKey,
            NSURLVolumeIdentifierKey, NSURLVolumeLocalizedFormatDescriptionKey,
            NSURLVolumeTotalCapacityKey, NSURLVolumeSupportsPersistentIDsKey,
            NSURLVolumeSupportsJournalingKey, NSURLVolumeSupportsVolumeSizesKey,
            NSURLVolumeIsReadOnlyKey]) {
        id value = nil;
        NSError *error = nil;
        BOOL ok = [url getResourceValue:&value forKey:key error:&error];
        printf("VOLUME key=%s ok=%d value=%s error=%s\n", key.UTF8String, ok,
            [value description].UTF8String ?: "nil", error.description.UTF8String ?: "nil");
    }
    fflush(stdout);
}

// New disposable fixtures only. Never modifies a supplied document/directory.
static int CreateFixtures(const char *path) {
    if (strncmp(path, "/tmp/macws-", 11) || strstr(path, "/../") ||
        strchr(path + 5, '/') || mkdir(path, 0700)) return 65;
    NSString *directory = [NSString stringWithUTF8String:path];
    NSMutableData *pdf = [NSMutableData data];
    CGDataConsumerRef consumer = CGDataConsumerCreateWithCFData((__bridge CFMutableDataRef)pdf);
    CGRect page = CGRectMake(0, 0, 200, 160);
    CGContextRef context = consumer ? CGPDFContextCreate(consumer, &page, NULL) : NULL;
    if (!context) { if (consumer) CGDataConsumerRelease(consumer); return 66; }
    CGPDFContextBeginPage(context, NULL);
    CGContextSetRGBFillColor(context, 1, 1, 1, 1);
    CGContextFillRect(context, page);
    CGContextSetRGBFillColor(context, 0.1, 0.65, 0.25, 1);
    CGContextFillRect(context, CGRectMake(20, 20, 100, 100));
    CGPDFContextEndPage(context);
    CGPDFContextClose(context);
    CGContextRelease(context);
    CGDataConsumerRelease(consumer);
    NSError *error = nil;
    NSData *txt = [@"Fresh MacWS QuickLook acceptance fixture.\nThe same small file is tested through modern and legacy APIs.\n"
        dataUsingEncoding:NSUTF8StringEncoding];
    if (![pdf writeToFile:[directory stringByAppendingPathComponent:@"document.pdf"]
                 options:NSDataWritingWithoutOverwriting error:&error] ||
        ![txt writeToFile:[directory stringByAppendingPathComponent:@"notes.txt"]
                 options:NSDataWritingWithoutOverwriting error:&error]) {
        printf("FIXTURE error=%s\n", error.description.UTF8String ?: "nil"); return 67;
    }
    printf("FIXTURE directory=%s pdf-bytes=%lu txt-bytes=%lu\n", path,
        (unsigned long)pdf.length, (unsigned long)txt.length);
    return 0;
}

int main(int argc, const char *argv[]) {
    if (argc != 3 || argv[1][0] != '/' ||
        (strcmp(argv[2], "raw") && strcmp(argv[2], "icon") &&
         strcmp(argv[2], "legacy-raw") && strcmp(argv[2], "legacy-icon") &&
         strcmp(argv[2], "trace-legacy-raw") &&
         strcmp(argv[2], "fixtures"))) return 64;
    alarm(10);
    @autoreleasepool {
        if (!strcmp(argv[2], "fixtures")) return CreateFixtures(argv[1]);
        NSURL *url = [NSURL fileURLWithPath:[NSString stringWithUTF8String:argv[1]]];
        PrintVolume(url);
        BOOL tracing = !strcmp(argv[2], "trace-legacy-raw");
        if (tracing && !ObserveOwnReplies()) return 68;
        if (!strncmp(argv[2], "legacy-", 7) || tracing) {
            NSDictionary *options = @{
                (__bridge NSString *)kQLThumbnailOptionIconModeKey: @(!strcmp(argv[2], "legacy-icon")),
                (__bridge NSString *)kQLThumbnailOptionScaleFactorKey: @1};
            CGImageRef image = QLThumbnailImageCreate(kCFAllocatorDefault,
                (__bridge CFURLRef)url, CGSizeMake(64, 64), (__bridge CFDictionaryRef)options);
            unsigned colored = 0, visible = ImagePixels(image, &colored);
            printf("THUMBNAIL mode=%s image=%zux%zu visible=%u colored=%u\n", argv[2],
                image ? CGImageGetWidth(image) : 0, image ? CGImageGetHeight(image) : 0,
                visible, colored);
            if (image) CGImageRelease(image);
            fflush(stdout);
            return visible ? 0 : 3;
        }
        QLThumbnailGenerationRequest *request = [[QLThumbnailGenerationRequest alloc]
            initWithFileAtURL:url size:CGSizeMake(64, 64) scale:1
            representationTypes:QLThumbnailGenerationRequestRepresentationTypeAll];
        request.iconMode = !strcmp(argv[2], "icon");
        __block _Atomic unsigned callbacks = 0;
        __block _Atomic BOOL finished = NO;
        __block _Atomic BOOL visibleThumbnail = NO;
        [[QLThumbnailGenerator sharedGenerator] generateRepresentationsForRequest:request
            updateHandler:^(QLThumbnailRepresentation *thumbnail,
                            QLThumbnailRepresentationType type, NSError *error) {
                CGImageRef image = thumbnail.CGImage;
                size_t width = image ? CGImageGetWidth(image) : 0;
                size_t height = image ? CGImageGetHeight(image) : 0;
                unsigned colored = 0, visible = ImagePixels(image, &colored);
                printf("THUMBNAIL mode=%s type=%ld actual-type=%ld image=%zux%zu visible=%u colored=%u error=%s\n",
                    argv[2], (long)type, thumbnail ? (long)thumbnail.type : -1L,
                    width, height, visible, colored,
                    error.description.UTF8String ?: "nil");
                fflush(stdout);
                atomic_fetch_add(&callbacks, 1);
                if (type == QLThumbnailRepresentationTypeThumbnail) {
                    atomic_store(&visibleThumbnail, image && visible && !error);
                    atomic_store(&finished, YES);
                }
            }];
        NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:8];
        while (!atomic_load(&finished) && deadline.timeIntervalSinceNow > 0)
            [[NSRunLoop currentRunLoop] runUntilDate:
                [NSDate dateWithTimeIntervalSinceNow:0.02]];
        [[QLThumbnailGenerator sharedGenerator] cancelRequest:request];
        printf("THUMBNAIL completed=%d callbacks=%u visible-thumbnail=%d\n",
            atomic_load(&finished), atomic_load(&callbacks),
            atomic_load(&visibleThumbnail));
        fflush(stdout);
        if (!atomic_load(&finished)) return 2;
        return atomic_load(&visibleThumbnail) ? 0 : 3;
    }
}

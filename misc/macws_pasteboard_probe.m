#import <AppKit/AppKit.h>
#import <Foundation/Foundation.h>

#include <CommonCrypto/CommonDigest.h>

static NSString *Digest(NSData *data) {
    uint8_t bytes[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256(data.bytes, (CC_LONG)data.length, bytes);
    NSMutableString *result = [NSMutableString string];
    for (NSUInteger index = 0; index < sizeof(bytes); index++)
        [result appendFormat:@"%02x", bytes[index]];
    return result;
}

static BOOL WriteFixture(NSPasteboard *pasteboard, NSString *filePath) {
    NSPasteboardItem *rich = [NSPasteboardItem new];
    NSDictionary<NSString *, NSData *> *representations = @{
        @"public.utf8-plain-text":
            [@"MacWS rich clipboard fixture" dataUsingEncoding:NSUTF8StringEncoding],
        @"public.html":
            [@"<b>MacWS rich clipboard fixture</b>"
                dataUsingEncoding:NSUTF8StringEncoding],
        @"public.rtf":
            [@"{\\rtf1\\ansi MacWS rich clipboard fixture}"
                dataUsingEncoding:NSUTF8StringEncoding],
        @"com.macwsguide.probe": [@"opaque-custom-representation"
            dataUsingEncoding:NSUTF8StringEncoding],
    };
    for (NSString *type in representations)
        if (![rich setData:representations[type] forType:type]) return NO;

    NSPasteboardItem *file = [NSPasteboardItem new];
    NSURL *url = [NSURL fileURLWithPath:filePath];
    if (![file setString:url.absoluteString forType:NSPasteboardTypeFileURL])
        return NO;
    [pasteboard clearContents];
    return [pasteboard writeObjects:@[rich, file]];
}

static int CoordinateCopy(NSString *sourcePath, NSString *destinationPath) {
    NSURL *sourceURL = [NSURL fileURLWithPath:sourcePath];
    NSURL *destinationURL = [NSURL fileURLWithPath:destinationPath];
    NSFileCoordinator *coordinator =
        [[NSFileCoordinator alloc] initWithFilePresenter:nil];
    __block BOOL accessorRan = NO;
    __block BOOL copied = NO;
    __block NSError *copyError = nil;
    NSError *coordinationError = nil;
    [coordinator coordinateReadingItemAtURL:sourceURL
                                    options:0
                           writingItemAtURL:destinationURL
                                    options:NSFileCoordinatorWritingForReplacing
                                      error:&coordinationError
                                 byAccessor:^(NSURL *coordinatedSourceURL,
                                              NSURL *coordinatedDestinationURL) {
        accessorRan = YES;
        NSFileManager *manager = NSFileManager.defaultManager;
        [manager removeItemAtURL:coordinatedDestinationURL error:nil];
        copied = [manager copyItemAtURL:coordinatedSourceURL
                                  toURL:coordinatedDestinationURL
                                  error:&copyError];
    }];
    fprintf(stdout,
            "coordinate-copy accessor=%s copied=%s coordination_error=%s copy_error=%s\n",
            accessorRan ? "yes" : "no", copied ? "yes" : "no",
            coordinationError.localizedDescription.UTF8String ?: "none",
            copyError.localizedDescription.UTF8String ?: "none");
    return accessorRan && copied && !coordinationError ? 0 : 1;
}

int main(int argc, const char **argv) {
    @autoreleasepool {
        if (argc == 4 && strcmp(argv[1], "--coordinate-copy") == 0)
            return CoordinateCopy([NSString stringWithUTF8String:argv[2]],
                                  [NSString stringWithUTF8String:argv[3]]);
        if (argc == 3 && strcmp(argv[1], "--inspect-native-url") == 0) {
            NSPasteboard *temporary = [NSPasteboard pasteboardWithUniqueName];
            NSURL *url = [NSURL fileURLWithPath:
                [NSString stringWithUTF8String:argv[2]]];
            [temporary clearContents];
            BOOL ok = [temporary writeObjects:@[url]];
            fprintf(stdout, "native-url=%s types=%s\n", ok ? "written" : "failed",
                [[temporary.types componentsJoinedByString:@","] UTF8String]);
            [temporary releaseGlobally];
            return ok ? 0 : 1;
        }
        if (argc == 3 && (strcmp(argv[1], "--write-fixture") == 0 ||
                          strcmp(argv[1], "--write-drag-fixture") == 0)) {
            NSPasteboard *pasteboard = strcmp(argv[1],
                "--write-drag-fixture") == 0
                ? [NSPasteboard pasteboardWithName:NSPasteboardNameDrag]
                : NSPasteboard.generalPasteboard;
            BOOL ok = WriteFixture(pasteboard,
                [NSString stringWithUTF8String:argv[2]]);
            fprintf(stdout, "fixture=%s change=%ld\n", ok ? "written" : "failed",
                    (long)pasteboard.changeCount);
            return ok ? 0 : 1;
        }
        NSPasteboard *pasteboard = argc == 2 &&
            strcmp(argv[1], "--read-drag") == 0
            ? [NSPasteboard pasteboardWithName:NSPasteboardNameDrag]
            : NSPasteboard.generalPasteboard;
        NSMutableArray *items = [NSMutableArray array];
        for (NSPasteboardItem *item in pasteboard.pasteboardItems) {
            NSMutableArray *types = [NSMutableArray array];
            for (NSPasteboardType type in item.types) {
                NSData *data = [item dataForType:type];
                NSString *utf8 = data.length <= 512
                    ? [[NSString alloc] initWithData:data
                                             encoding:NSUTF8StringEncoding] : nil;
                NSURL *url = utf8.length ? [NSURL URLWithString:utf8] : nil;
                [types addObject:@{
                    @"type": type,
                    @"bytes": @(data.length),
                    @"sha256": data.length ? Digest(data) : @"",
                    @"utf8": utf8 ?: (data.length <= 512
                        ? @"<non-utf8>" : @"<large>"),
                    @"resolved_path": url.filePathURL.path ?: @""
                }];
            }
            [items addObject:@{ @"representations": types }];
        }
        NSDictionary *report = @{
            @"change_count": @(pasteboard.changeCount),
            @"items": items,
            @"types": pasteboard.types ?: @[],
            @"file_urls": [[pasteboard readObjectsForClasses:@[NSURL.class]
                options:@{NSPasteboardURLReadingFileURLsOnlyKey: @YES}]
                valueForKey:@"path"] ?: @[]
        };
        NSData *json = [NSJSONSerialization dataWithJSONObject:report
                                                       options:0 error:nil];
        fwrite(json.bytes, 1, json.length, stdout);
        fputc('\n', stdout);
    }
    return 0;
}

/* Own CPU-only pixels; a stock macOS test of the diagnostic observer, not Office. */
#include <CoreGraphics/CoreGraphics.h>
#include <ImageIO/ImageIO.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static CGImageRef make_image(size_t width, size_t height, size_t row) {
    unsigned char *bytes = calloc(height, row);
    if (!bytes) return NULL;
    for (size_t y = 0; y < height; y++) {
        for (size_t x = 0; x < width; x++) {
            size_t p = y * row + x * 4;
            bytes[p] = 12; bytes[p + 1] = 45; bytes[p + 2] = 78; bytes[p + 3] = 255;
        }
    }
    CFDataRef data = CFDataCreate(kCFAllocatorDefault, bytes, height * row);
    free(bytes);
    CGDataProviderRef provider = CGDataProviderCreateWithCFData(data);
    CGColorSpaceRef space = CGColorSpaceCreateDeviceRGB();
    CGImageRef image = CGImageCreate(width, height, 8, 32, row, space,
        kCGBitmapByteOrder32Big | kCGImageAlphaPremultipliedLast,
        provider, NULL, false, kCGRenderingIntentDefault);
    CGColorSpaceRelease(space);
    CGDataProviderRelease(provider);
    CFRelease(data);
    return image;
}

int main(void) {
    alarm(5);
#if defined(MACWS_CGIMAGE_PPT_WELCOME_FIXTURE)
    const size_t width = 309, height = 250, source_row = 1280;
    const size_t canvas_width = 512, canvas_height = 300, canvas_row = 2048;
#else
    const size_t width = 17, height = 131, source_row = 128;
    const size_t canvas_width = 64, canvas_height = 180, canvas_row = 256;
#endif
    CGImageRef small = make_image(12, 12, 64);
    unsigned char *pixels = calloc(canvas_height, canvas_row);
    CGColorSpaceRef space = CGColorSpaceCreateDeviceRGB();
    CGContextRef context = CGBitmapContextCreate(pixels, canvas_width,
        canvas_height, 8, canvas_row, space,
        kCGBitmapByteOrder32Big | kCGImageAlphaPremultipliedLast);
    if (!context || !pixels || !small) return 5;
    CGContextTranslateCTM(context, 2, 3);
    CGContextDrawImage(context, CGRectMake(1, 1, 12, 12), small);
#if defined(MACWS_CGIMAGE_PPT_WELCOME_FIXTURE)
    /* More than the entire budget's worth of real, non-target large draws. */
    CGImageRef ribbon = make_image(230, 20, 960);
    CGImageRef other_large = make_image(17, 131, 128);
    if (!ribbon || !other_large) return 7;
    for (unsigned i = 0; i < 64; i++)
        CGContextDrawImage(context, CGRectMake(3, 5, 230, 20), ribbon);
    CGContextDrawImage(context, CGRectMake(3, 5, 17, 131), other_large);
    CGImageRelease(ribbon); CGImageRelease(other_large);
#endif
    CGImageRef image = make_image(width, height, source_row);
    if (!image) return 1;
    CFMutableDataRef encoded = CFDataCreateMutable(kCFAllocatorDefault, 0);
    CGImageDestinationRef writer = CGImageDestinationCreateWithData(encoded,
        CFSTR("public.png"), 1, NULL);
    if (!writer) return 2;
    CGImageDestinationAddImage(writer, image, NULL);
    if (!CGImageDestinationFinalize(writer)) return 3;
    CGImageSourceRef source = CGImageSourceCreateWithData(encoded, NULL);
    CGImageRef decoded = CGImageSourceCreateImageAtIndex(source, 0, NULL);
    if (!decoded || CGImageGetWidth(decoded) != width || CGImageGetHeight(decoded) != height)
        return 4;
    for (unsigned i = 0; i < 64; i++)
        CGContextDrawImage(context, CGRectMake(3, 5, width, height), decoded);
    CGContextFlush(context);
    uint64_t hash = UINT64_C(14695981039346656037);
    unsigned colored = 0;
    for (size_t i = 0; i < canvas_height * canvas_row; i++) {
        hash ^= pixels[i]; hash *= UINT64_C(1099511628211);
    }
    for (size_t y = 0; y < canvas_height; y++)
        for (size_t x = 0; x < canvas_width; x++)
            if (pixels[y * canvas_row + x * 4 + 3]) colored++;
    printf("CGIMAGE-PROBE width=%zu height=%zu bpr=%zu colored=%u hash=%016llx\n",
        CGImageGetWidth(decoded), CGImageGetHeight(decoded),
        CGImageGetBytesPerRow(decoded), colored, (unsigned long long)hash);
    CGContextRelease(context); CGColorSpaceRelease(space); free(pixels);
    CGImageRelease(decoded); CFRelease(source); CFRelease(writer); CFRelease(encoded);
    CGImageRelease(image); CGImageRelease(small);
    return colored > 2000 ? 0 : 6;
}

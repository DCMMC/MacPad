/* Process-local DIAGNOSTIC only; never linked into production libmachook.
 * Observe metadata of at most 32 large images. Never inspect pixel memory,
 * change an argument/result, install a code-page hook, or consult a flag file.
 * Inject only into an explicitly owned test process with DYLD_INSERT_LIBRARIES.
 */
#include <CoreGraphics/CoreGraphics.h>
#include <ImageIO/ImageIO.h>
#include <dlfcn.h>
#include <errno.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

enum { MACWS_CG_OBSERVATION_LIMIT = 32 };
static _Atomic unsigned observations;
static _Thread_local int observing;

static int selected_dimensions(size_t width, size_t height) {
#if defined(MACWS_CGIMAGE_PPT_WELCOME_FIXTURE)
    /* Diagnostic build for the exact four PNGs in our owned Welcome copy.
     * Compile-time only: no file flag, environment gate, or production path.
     * In particular, startup ribbon images (230x20) cannot spend this budget.
     */
    return (width == 284 && height == 102) ||
           (width == 309 && height == 250) ||
           (width == 282 && height == 28) ||
           (width == 471 && height == 56);
#else
    return width > 100 || height > 100;
#endif
}

static void write_line(const char *line, size_t length) {
    while (length) {
        ssize_t written = write(STDERR_FILENO, line, length);
        if (written < 0 && errno == EINTR) continue;
        if (written <= 0) break;
        line += written;
        length -= (size_t)written;
    }
}

static void observe_image(const char *api, CGImageRef image, const void *caller,
                          CGContextRef context, const CGRect *destination) {
    if (!image || observing ||
        atomic_load_explicit(&observations, memory_order_relaxed) >=
            MACWS_CG_OBSERVATION_LIMIT) return;
    int saved_errno = errno;
    observing = 1;
    size_t width = CGImageGetWidth(image), height = CGImageGetHeight(image);
    if (!selected_dimensions(width, height)) goto out;
    unsigned ordinal = atomic_load_explicit(&observations, memory_order_relaxed);
    do {
        if (ordinal >= MACWS_CG_OBSERVATION_LIMIT) goto out;
    } while (!atomic_compare_exchange_weak_explicit(&observations, &ordinal,
        ordinal + 1, memory_order_relaxed, memory_order_relaxed));

    Dl_info owner = {0};
    const char *module = "unknown";
    uintptr_t offset = 0;
    if (dladdr(caller, &owner) && owner.dli_fname) {
        const char *slash = strrchr(owner.dli_fname, '/');
        module = slash ? slash + 1 : owner.dli_fname;
        offset = (uintptr_t)caller - (uintptr_t)owner.dli_fbase;
    }
    uint64_t thread = 0;
    (void)pthread_threadid_np(NULL, &thread);
    CGRect rect = destination ? *destination : CGRectZero;
    CGAffineTransform matrix = context ? CGContextGetCTM(context) :
                                        CGAffineTransformIdentity;
    char line[1024];
    int count = snprintf(line, sizeof(line),
        "CGIMAGE-OBS diagnostic=1 pid=%d tid=%llu sample=%u/%u api=%s "
        "image=%p width=%zu height=%zu bpr=%zu bpc=%zu bpp=%zu "
        "alpha=%u bitmap=0x%x context=%p has-destination=%u "
        "rect=[%.9g,%.9g,%.9g,%.9g] ctm=[%.9g,%.9g,%.9g,%.9g,%.9g,%.9g] "
        "caller=%s+0x%llx\n",
        getpid(), (unsigned long long)thread, ordinal + 1,
        MACWS_CG_OBSERVATION_LIMIT, api, (const void *)image,
        width, height, CGImageGetBytesPerRow(image),
        CGImageGetBitsPerComponent(image), CGImageGetBitsPerPixel(image),
        (unsigned)CGImageGetAlphaInfo(image), (unsigned)CGImageGetBitmapInfo(image),
        (void *)context, destination != NULL,
        (double)rect.origin.x, (double)rect.origin.y,
        (double)rect.size.width, (double)rect.size.height,
        (double)matrix.a, (double)matrix.b, (double)matrix.c, (double)matrix.d,
        (double)matrix.tx, (double)matrix.ty, module, (unsigned long long)offset);
    if (count > 0 && (size_t)count < sizeof(line)) write_line(line, (size_t)count);
out:
    observing = 0;
    errno = saved_errno;
}

static void observed_CGContextDrawImage(CGContextRef context, CGRect rect,
                                       CGImageRef image) {
    observe_image("CGContextDrawImage", image,
        __builtin_extract_return_addr(__builtin_return_address(0)), context, &rect);
    CGContextDrawImage(context, rect, image);
}

static CGImageRef observed_CGImageCreate(size_t width, size_t height,
        size_t bitsPerComponent, size_t bitsPerPixel, size_t bytesPerRow,
        CGColorSpaceRef space, CGBitmapInfo bitmapInfo, CGDataProviderRef provider,
        const CGFloat *decode, bool shouldInterpolate, CGColorRenderingIntent intent) {
    CGImageRef image = CGImageCreate(width, height, bitsPerComponent, bitsPerPixel,
        bytesPerRow, space, bitmapInfo, provider, decode, shouldInterpolate, intent);
    observe_image("CGImageCreate", image,
        __builtin_extract_return_addr(__builtin_return_address(0)), NULL, NULL);
    return image;
}

static CGImageRef observed_CGImageSourceCreateImageAtIndex(CGImageSourceRef source,
        size_t index, CFDictionaryRef options) {
    CGImageRef image = CGImageSourceCreateImageAtIndex(source, index, options);
    observe_image("CGImageSourceCreateImageAtIndex", image,
        __builtin_extract_return_addr(__builtin_return_address(0)), NULL, NULL);
    return image;
}

#define OBSERVER_INTERPOSE(replacement, replacee) \
    __attribute__((used, section("__DATA,__interpose"))) \
    static const struct { const void *replacement; const void *replacee; } \
        interpose_##replacee = { (const void *)&replacement, (const void *)&replacee }

OBSERVER_INTERPOSE(observed_CGContextDrawImage, CGContextDrawImage);
OBSERVER_INTERPOSE(observed_CGImageCreate, CGImageCreate);
OBSERVER_INTERPOSE(observed_CGImageSourceCreateImageAtIndex, CGImageSourceCreateImageAtIndex);

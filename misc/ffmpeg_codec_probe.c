// Runtime probe for the exact libffmpeg.dylib bundled with Electron/VSCode.
// It intentionally uses only dlopen/dlsym and opaque FFmpeg types so the
// result cannot be affected by headers or a second FFmpeg installation.

#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

typedef struct AVCodec AVCodec;
typedef struct AVCodecContext AVCodecContext;
typedef struct AVDictionary AVDictionary;

typedef const AVCodec *(*find_decoder_by_name_fn)(const char *name);
typedef AVCodecContext *(*alloc_context_fn)(const AVCodec *codec);
typedef int (*open_codec_fn)(AVCodecContext *context,
                             const AVCodec *codec,
                             AVDictionary **options);
typedef void (*free_context_fn)(AVCodecContext **context);
typedef unsigned (*version_fn)(void);

static void *required_symbol(void *image, const char *name) {
    void *symbol = dlsym(image, name);
    if (!symbol) {
        fprintf(stderr, "missing symbol %s: %s\n", name, dlerror());
        exit(2);
    }
    return symbol;
}

int main(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "usage: %s /path/to/libffmpeg.dylib\n", argv[0]);
        return 64;
    }

    void *image = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
    if (!image) {
        fprintf(stderr, "dlopen failed: %s\n", dlerror());
        return 1;
    }

    find_decoder_by_name_fn find_decoder =
        (find_decoder_by_name_fn)required_symbol(image,
                                                  "avcodec_find_decoder_by_name");
    alloc_context_fn alloc_context =
        (alloc_context_fn)required_symbol(image, "avcodec_alloc_context3");
    open_codec_fn open_codec =
        (open_codec_fn)required_symbol(image, "avcodec_open2");
    free_context_fn free_context =
        (free_context_fn)required_symbol(image, "avcodec_free_context");
    version_fn version =
        (version_fn)required_symbol(image, "avcodec_version");

    const unsigned encoded_version = version();
    printf("avcodec_version=%u.%u.%u raw=0x%08x\n",
           encoded_version >> 16,
           (encoded_version >> 8) & 0xff,
           encoded_version & 0xff,
           encoded_version);

    const char *names[] = {"aac", "aac_latm", "h264", "hevc", "vp9", "av1",
                           "opus", NULL};
    for (const char **name = names; *name; ++name) {
        const AVCodec *codec = find_decoder(*name);
        if (!codec) {
            printf("decoder=%s found=no\n", *name);
            continue;
        }
        AVCodecContext *context = alloc_context(codec);
        const int open_result = context ? open_codec(context, codec, NULL) : -1;
        printf("decoder=%s found=yes context=%s open=%d\n",
               *name, context ? "yes" : "no", open_result);
        if (context) free_context(&context);
    }

    dlclose(image);
    return 0;
}

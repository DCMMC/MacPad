// Minimal one-shot loader diagnostic for an exact dylib path.

#include <dlfcn.h>
#include <stdio.h>

int main(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "usage: %s /path/to/image.dylib\n", argv[0]);
        return 64;
    }
    dlerror();
    void *image = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
    const char *error = dlerror();
    printf("dlopen path=%s image=%p error=%s\n", argv[1], image,
           error ? error : "none");
    return image ? 0 : 1;
}

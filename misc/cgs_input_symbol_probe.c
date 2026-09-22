#include <dlfcn.h>
#include <stdio.h>

int main(void) {
    static const char *const names[] = {
        "CGSPostMouseEvent",
        "CGSPostMouseEvents",
        "CGSPostKeyboardEvent",
        "CGPostMouseEvent",
        "CGPostKeyboardEvent",
        "CGPostScrollWheelEvent",
        "CGEventPost",
        "CGEventPostToPid",
        "CGEventPostToPSN",
    };
    void *image = dlopen(
        "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics",
        RTLD_LAZY | RTLD_LOCAL);
    printf("CoreGraphics=%p error=%s\n", image, dlerror() ?: "none");
    for (unsigned index = 0; index < sizeof(names) / sizeof(names[0]); index++) {
        dlerror();
        void *symbol = dlsym(image ?: RTLD_DEFAULT, names[index]);
        const char *error = dlerror();
        printf("%-28s %p error=%s\n", names[index], symbol,
               error ?: "none");
    }
    if (image) dlclose(image);
    return 0;
}

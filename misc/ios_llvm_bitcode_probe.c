// Read-only, one-shot diagnosis using the actual installed iOS LLVM reader.
// No compiler-service attachment or error-handler replacement. Never shipped
// in the runtime. The public LLVM C API returns its original parse error.
#include <dlfcn.h>
#include <fcntl.h>
#include <stdio.h>
#include <sys/stat.h>
#include <unistd.h>

int main(int argc, char **argv) {
    if (argc != 2 && argc != 4) return 64;
    struct stat st;
    if (stat(argv[1], &st) || st.st_size <= 0 || st.st_size > 1024 * 1024)
        return 65;
    alarm(10);
    void *library = dlopen("/usr/lib/libLLVM.dylib", RTLD_NOW | RTLD_LOCAL);
    if (!library) { fprintf(stderr, "LLVM load: %s\n", dlerror()); return 66; }
    int (*create)(const char *, void **, char **) =
        dlsym(library, "LLVMCreateMemoryBufferWithContentsOfFile");
    int (*parse)(void *, void **, char **) = dlsym(library, "LLVMParseBitcode");
    void (*disposeBuffer)(void *) = dlsym(library, "LLVMDisposeMemoryBuffer");
    void (*disposeModule)(void *) = dlsym(library, "LLVMDisposeModule");
    void (*disposeMessage)(char *) = dlsym(library, "LLVMDisposeMessage");
    if (!create || !parse || !disposeBuffer || !disposeModule || !disposeMessage) {
        fprintf(stderr, "LLVM C API unavailable create=%p parse=%p\n", create, parse);
        return 67;
    }
    void *buffer = NULL, *module = NULL;
    char *error = NULL;
    int status = create(argv[1], &buffer, &error);
    if (!status) status = parse(buffer, &module, &error);
    fprintf(stderr, "LLVM-PARSE path=%s status=%d module=%p error=%s\n",
            argv[1], status, module, error ? error : "nil");
    if (!status && argc == 4) {
        // Diagnostic writer A/B: change only the target triple using Apple's
        // matching bitcode writer, preserving its pointer/attribute dialect.
        void (*setTarget)(void *, const char *) = dlsym(library, "LLVMSetTarget");
        int (*writeModule)(void *, const char *) = dlsym(library, "LLVMWriteBitcodeToFile");
        int output = -1;
        if (!setTarget || !writeModule ||
            (output = open(argv[2], O_WRONLY | O_CREAT | O_EXCL, 0600)) < 0) {
            status = 1;
        } else {
            close(output);
            setTarget(module, argv[3]);
            status = writeModule(module, argv[2]);
            fprintf(stderr, "LLVM-WRITE path=%s target=%s status=%d\n",
                    argv[2], argv[3], status);
            if (status) unlink(argv[2]);
        }
    }
    if (error) disposeMessage(error);
    if (module) disposeModule(module);
    if (buffer) disposeBuffer(buffer);
    return status ? 1 : 0;
}

// Offline AIR assembly/disassembly with the *installed compiler's* LLVM ABI.
// Upstream LLVM auto-upgrades pointer/attribute records that iOS 16 cannot
// decode (runtime: Invalid value / Unknown attribute kind (71)). Using the
// matching reader/writer preserves Apple's original AIR dialect. This tool
// does not optimize, lower instructions, suppress errors or change targets;
// metal2metal still performs its explicit target/semantic transformations.
#include <dlfcn.h>
#include <fcntl.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

int main(int argc, char **argv) {
    const char *name = strrchr(argv[0], '/');
    name = name ? name + 1 : argv[0];
    bool disassemble = strcmp(name, "macws-llvm-dis") == 0;
    if ((!disassemble && strcmp(name, "macws-llvm-as")) ||
        argc != 4 || strcmp(argv[2], "-o")) {
        fprintf(stderr, "usage: macws-llvm-{dis,as} INPUT -o NEW_OUTPUT\n");
        return 64;
    }
    struct stat st;
    // Textual expansion can be much larger than AIR (Ventura scan_add:
    // 3.2 MB bitcode -> 25.4 MB IR). Bound one module, not the whole library.
    off_t maximum = (disassemble ? 16LL : 64LL) * 1024 * 1024;
    if (stat(argv[1], &st) || !S_ISREG(st.st_mode) ||
        st.st_size <= 0 || st.st_size > maximum) {
        fprintf(stderr, "%s: invalid input or exceeds %lld-byte limit: %s\n",
                name, (long long)maximum, argv[1]);
        return 65;
    }
    alarm(30);
    void *library = dlopen("/usr/lib/libLLVM.dylib", RTLD_NOW | RTLD_LOCAL);
    if (!library) { fprintf(stderr, "Apple LLVM: %s\n", dlerror()); return 66; }
    void *(*contextCreate)(void) = dlsym(library, "LLVMContextCreate");
    void (*contextDispose)(void *) = dlsym(library, "LLVMContextDispose");
    int (*createBuffer)(const char *, void **, char **) =
        dlsym(library, "LLVMCreateMemoryBufferWithContentsOfFile");
    int (*parseBitcode)(void *, void *, void **, char **) =
        dlsym(library, "LLVMParseBitcodeInContext");
    int (*parseIR)(void *, void *, void **, char **) =
        dlsym(library, "LLVMParseIRInContext");
    int (*printModule)(void *, const char *, char **) =
        dlsym(library, "LLVMPrintModuleToFile");
    int (*writeBitcode)(void *, const char *) =
        dlsym(library, "LLVMWriteBitcodeToFile");
    void (*disposeBuffer)(void *) = dlsym(library, "LLVMDisposeMemoryBuffer");
    void (*disposeModule)(void *) = dlsym(library, "LLVMDisposeModule");
    void (*disposeMessage)(char *) = dlsym(library, "LLVMDisposeMessage");
    if (!contextCreate || !contextDispose || !createBuffer || !parseBitcode ||
        !parseIR || !printModule || !writeBitcode || !disposeBuffer ||
        !disposeModule || !disposeMessage) return 67;
    void *context = contextCreate(), *buffer = NULL, *module = NULL;
    char *error = NULL;
    bool outputOwned = false;
    int status = context ? createBuffer(argv[1], &buffer, &error) : 1;
    if (!status) {
        if (disassemble) {
            status = parseBitcode(context, buffer, &module, &error);
        } else {
            // LLVMParseIRInContext takes ownership, including failure paths.
            status = parseIR(context, buffer, &module, &error);
            buffer = NULL;
        }
    }
    if (!status) {
        int fd = open(argv[3], O_WRONLY | O_CREAT | O_EXCL, 0600);
        if (fd < 0) {
            perror(argv[3]);
            status = 1;
        } else {
            outputOwned = true;
            close(fd);
            status = disassemble ? printModule(module, argv[3], &error)
                                 : writeBitcode(module, argv[3]);
        }
    }
    if (status) {
        fprintf(stderr, "%s: %s: %s\n", name, argv[1], error ?: "failed");
        if (outputOwned) unlink(argv[3]);
    }
    if (error) disposeMessage(error);
    if (module) disposeModule(module);
    if (buffer) disposeBuffer(buffer);
    if (context) contextDispose(context);
    return status ? 1 : 0;
}

// Isolated executable contract test: no target process writes or attachment.
#include "../include/macws_code_pointer.h"
#include <dlfcn.h>
#include <stdio.h>
#include <unistd.h>

#if !defined(__aarch64__)
#error This probe verifies the actual ARM64 instruction alignment contract.
#endif

extern int MacWSAligned0(void);
extern int MacWSAligned4(void);
extern uintptr_t MacWSRawAligned0(void);
extern uintptr_t MacWSRawAligned4(void);
__asm__(
    ".text\n.p2align 3\n.globl _MacWSAligned0\n"
    "_MacWSAligned0:\nmov w0, #40\nret\n"
    ".p2align 3\nnop\n.globl _MacWSAligned4\n"
    "_MacWSAligned4:\nmov w0, #44\nret\n"
    ".p2align 2\n.globl _MacWSRawAligned0\n"
    "_MacWSRawAligned0:\nadrp x0, _MacWSAligned0@PAGE\nadd x0, x0, _MacWSAligned0@PAGEOFF\nret\n"
    ".globl _MacWSRawAligned4\n"
    "_MacWSRawAligned4:\nadrp x0, _MacWSAligned4@PAGE\nadd x0, x0, _MacWSAligned4@PAGEOFF\nret\n");

int main(int argc, char **argv) {
    alarm(5);
    uintptr_t expected[] = {MacWSRawAligned0(), MacWSRawAligned4()};
    uintptr_t actual[] = {MacWSCodeAddress((void *)MacWSAligned0),
                          MacWSCodeAddress((void *)MacWSAligned4)};
    int ok = (expected[0] & 7) == 0 && (expected[1] & 7) == 4 &&
        actual[0] == expected[0] && actual[1] == expected[1] &&
        MacWSAligned0() == 40 && MacWSAligned4() == 44;
    for (unsigned index = 0; index < 2; index++) {
        intptr_t delta = (intptr_t)actual[index] - (intptr_t)expected[0];
        uint32_t instruction = 0x94000000u | ((uint32_t)(delta >> 2) & 0x03ffffffu);
        intptr_t decoded = ((int32_t)(instruction << 6) >> 4);
        ok &= (uintptr_t)((intptr_t)expected[0] + decoded) == expected[index];
        printf("CODE-POINTER index=%u raw=%#lx stripped=%#lx alignment=%lu branch=%#x\n",
            index, expected[index], actual[index], actual[index] & 7, instruction);
    }
    if (argc == 2) {
        // The compiler UUID guard must leave this executable untouched.
        // Loading here does not restart or load any live compiler worker.
        void *image = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
        printf("CODE-POINTER isolated-dlopen=%s error=%s\n",
            image ? "PASS" : "FAIL", image ? "none" : dlerror());
        ok &= image != NULL;
    } else if (argc != 1) return 64;
    printf("CODE-POINTER result=%s\n", ok ? "PASS" : "FAIL");
    return ok ? 0 : 1;
}

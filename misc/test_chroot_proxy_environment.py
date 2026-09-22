"""The shared XPC transition must carry the real root, not a feature flag."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ChrootProxyEnvironment(unittest.TestCase):
    def compile_and_run(self, source, stdin=False):
        with tempfile.TemporaryDirectory(prefix="macws-proxy-namespace-") as td:
            binary = str(Path(td) / "test")
            command = [os.environ.get("CC", "cc"), "-std=c11", "-Wall", "-Wextra",
                       "-Werror", "-I", str(ROOT / "include")]
            command += ["-x", "c", "-"] if stdin else [str(source)]
            subprocess.run(command + ["-o", binary], check=True, text=True,
                           input=source if stdin else None)
            subprocess.run([binary], check=True, timeout=5)

    def test_real_canonical_path_and_all_failures(self):
        self.compile_and_run(ROOT / "misc/test_chroot_proxy_environment.c")

    def test_actual_production_environment_builder(self):
        source = (ROOT / "ViewBridgeChrootProxy/main.c").read_text()
        key_function = source.split("static int MacWSHasEnvironmentKey(", 1)[1]
        key_function = "static int MacWSHasEnvironmentKey(" + key_function.split(
            "\nstatic const char *MacWSEnvironmentValue", 1)[0]
        block = source.split("    size_t environmentCount = 0;", 1)[1]
        block = "size_t environmentCount = 0;" + block.split(
            "\n    if (MacWSSyscall1(MACWS_SYS_chroot", 1)[0]
        fixture = r'''
#include "macws_chroot_environment.h"
#include <assert.h>
#include <setjmp.h>
#include <string.h>
#define MACWS_MAX_ENVIRONMENT 256
static jmp_buf failure;
static void MacWSExit(int code) { longjmp(failure, code); }
/* KEY_FUNCTION */
static char *targetEnvironment[MACWS_MAX_ENVIRONMENT];
static void build(char **envp, int isSettingsExtension) {
    char *insertLibrary="DYLD_INSERT_LIBRARIES=/usr/local/lib/libmachook.dylib";
    char *appExtension="MACWS_APP_EXTENSION=1", *home="HOME=/Users/root";
    char *temporaryDirectory="TMPDIR=/tmp", *nanoZone="MallocNanoZone=0";
    char *rootEnvironment=MACWS_CHROOT_ROOT_PREFIX "/actual/kernel/root";
    /* BUILD_BLOCK */
}
int main(void) {
    char *small[]={"XPC_FLAGS=0x123", "MACWS_CHROOT_HOST_ROOT=/wrong/stale",
       "HOME=/other", "XPC_NULL_BOOTSTRAP=unchanged", "MallocNanoZone=1", NULL};
    build(small, 0);
    assert(!strcmp(targetEnvironment[0],small[0]));
    assert(!strcmp(targetEnvironment[1],small[3]));
    assert(!strcmp(targetEnvironment[2],"DYLD_INSERT_LIBRARIES=/usr/local/lib/libmachook.dylib"));
    assert(!strcmp(targetEnvironment[6],MACWS_CHROOT_ROOT_PREFIX "/actual/kernel/root"));
    assert(targetEnvironment[7] == NULL);
    build(small, 1);
    assert(!strcmp(targetEnvironment[2],"MACWS_APP_EXTENSION=1"));
    char *large[252];
    for (int i=0;i<250;i++) large[i]="KEEP=original";
    large[250]=NULL;
    build(large, 0);
    assert(targetEnvironment[255] == NULL);
    assert(!strcmp(targetEnvironment[254],MACWS_CHROOT_ROOT_PREFIX "/actual/kernel/root"));
    large[250]="XPC_LAST=must-not-be-silently-dropped";large[251]=NULL;
    int status=setjmp(failure);
    if (!status) { build(large,0); assert(!"oversized environment silently truncated"); }
    assert(status==119);
    return 0;
}
'''
        fixture = fixture.replace("/* KEY_FUNCTION */", key_function).replace(
            "/* BUILD_BLOCK */", block)
        self.compile_and_run(fixture, stdin=True)

    def test_namespace_precedes_any_chroot_and_uses_checked_syscall(self):
        source = (ROOT / "ViewBridgeChrootProxy/main.c").read_text()
        main = source.split("int main(", 1)[1]
        self.assertLess(main.index("MacWSBuildChrootRootEnvironment"),
                        main.index("MacWSSyscall1(MACWS_SYS_chroot"))
        self.assertIn("svc #0x80\\n\\tcneg x0, x0, cs", source)
        self.assertIn("MacWSCheckedSyscall3)) MacWSExit(118)", source)


if __name__ == "__main__":
    unittest.main()

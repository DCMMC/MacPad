"""Execute the real observation blocks with counted, side-effect-free stubs.

This checks that diagnostic-off avoids the queries themselves, not merely
stderr output. It does not claim native Metal initialization is unnecessary.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def guarded_block(source, anchor):
    start = source.index("if (macws_runtime_diagnostics_enabled())", source.index(anchor))
    opening = source.index("{", start)
    depth = 1
    cursor = opening + 1
    while depth:
        if source[cursor] == "{":
            depth += 1
        elif source[cursor] == "}":
            depth -= 1
        cursor += 1
    return source[start:cursor]


class AGXObservationContract(unittest.TestCase):
    def test_observations_do_not_execute_with_diagnostics_off(self):
        compiler = shutil.which("clang") or shutil.which("cc")
        if not compiler:
            self.skipTest("a host C compiler is required")
        hooks = (ROOT / "libmachook/mac_hooks.m").read_text()
        metal = (ROOT / "libmachook/Metal_hooks.x").read_text()
        cases = [
            (hooks, "// Skip the observation itself in production", "Class agxbuf", 1),
            (hooks, "// Dump first 6 with class name.", "class_getName(c)", 4),
            (hooks, "// Historical symbol-availability observation only:", "objc_duplicateClass", 1),
            (metal, "// Symbol-availability observations do not bind", "probeSyms", 5),
        ]
        functions = []
        checks = []
        for index, (source, anchor, witness, enabled_count) in enumerate(cases):
            block = guarded_block(source, anchor)
            self.assertIn(witness, block)
            functions.append("static void observation_%d(void) {\n%s\n}" % (index, block))
            checks.append("""
    enabled = 0; queries = 0; observation_%d();
    if (queries != 0) return %d;
    enabled = 1; queries = 0; observation_%d();
    if (queries != %d) return %d;
""" % (index, 10 + index, index, enabled_count, 20 + index))
        harness = """
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
typedef void *Class;
#define RTLD_DEFAULT ((void *)0)
static int enabled, queries;
static size_t n = 2;
static uint64_t classlist[] = {1, 2};
static int macws_runtime_diagnostics_enabled(void) { return enabled; }
static Class objc_getClass(const char *name) {
    (void)name; queries++; return (void *)(uintptr_t)1;
}
static const char *class_getName(Class c) {
    (void)c; queries++; return "AGXDiagnosticStub";
}
static void *dlsym(void *handle, const char *name) {
    (void)handle; (void)name; queries++; return (void *)(uintptr_t)1;
}
""" + "\n".join(functions) + "\nint main(void) {\n" + "".join(checks) + "\nreturn 0;\n}\n"
        with tempfile.TemporaryDirectory(prefix="macws-agx-observation-") as directory:
            source = Path(directory) / "observations.c"
            program = Path(directory) / "observations"
            source.write_text(harness)
            built = subprocess.run([compiler, "-std=gnu11", "-O0", "-Wall", "-Wextra", "-Werror",
                                    str(source), "-o", str(program)], capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            ran = subprocess.run([str(program)], capture_output=True, text=True, timeout=5)
            self.assertEqual(ran.returncode, 0, ran.stderr)

    def test_functional_native_setup_remains_outside_observation_blocks(self):
        hooks = (ROOT / "libmachook/mac_hooks.m").read_text()
        metal = (ROOT / "libmachook/Metal_hooks.x").read_text()
        registration = hooks[hooks.index("// Register each class with libobjc via objc_readClassPair."):
                             hooks.index("// Also try sending +alloc to verify")]
        self.assertIn('dlsym(RTLD_DEFAULT, "objc_readClassPair")', registration)
        self.assertIn("Class rr = readPair(cc, ii);", registration)
        plugin = metal[metal.index("%hookf(Class, getMetalPluginClassForService"):
                       metal.index("// dlopen on the inner binary does NOT register an NSBundle.")]
        self.assertIn("if (macws_agx_native_enabled())", plugin)
        self.assertIn("iogpu = dlopen(iogpuPaths[i], RTLD_GLOBAL | RTLD_NOW);", plugin)
        self.assertIn('void *h = dlopen("/System/Library/Extensions/AGXMetal13_3.bundle/', plugin)


if __name__ == "__main__":
    unittest.main()

"""Execute the actual production kind-5 compose wrapper with owned C fixtures.

No LLVM, Metal, GPU, service or hook is invoked. These tests cover wrapper
control flow/ownership; the separate native replay and pixel probes establish
the real compiler ABI and compatibility contract.
"""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def actual_wrapper():
    source = (ROOT / "MTLCompilerBypassOSCheck/Tweak.x").read_text()
    begin = source.index("static void *MacWSComposeImageFilters(")
    end = source.index("\nstatic bool MacWSCompilerSymbolMatches(", begin)
    return source[begin:end]


PREAMBLE = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

typedef void *(*MacWSComposeImageFiltersFn)(void *, void *, void *, void *);
typedef const char *(*MacWSLLVMGetTargetFn)(void *);
typedef void (*MacWSLLVMSetTargetFn)(void *, const char *);
static MacWSComposeImageFiltersFn gOriginalComposeImageFilters;
static MacWSLLVMGetTargetFn gImageFilterGetTarget;
static MacWSLLVMSetTargetFn gImageFilterSetTarget;
static _Thread_local uint32_t gImageFilterRequestModuleCount;
static bool MacWSCompilerDiagnosticsEnabled(void) { return false; }
#define MTLPatchLog(...) ((void)0)
'''


FIXTURE = r'''
static const char macos[] = "air64-apple-macosx13.4.0";
static const char catalyst[] = "air64-apple-ios19.0.0-macabi";
typedef struct { const char *target; unsigned sets; } OwnedModule;
static unsigned getter_calls, sets, originals, expected_gets, expected_sets;
static bool consumed, consume_in_original;
static void *expected_arguments[4];
static const char *initial_targets[3];
static void *table_page, *module_page;
static size_t page_size;
static int result_token, error_token;
static bool original_failure;
static uintptr_t expected_bounds[3];

static const char *GetTarget(void *opaque) {
    assert(!consumed && !originals);
    OwnedModule *module = opaque;
    assert(module >= (OwnedModule *)module_page &&
           module < (OwnedModule *)module_page + 3);
    ++getter_calls;
    return module->target;
}

static void SetTarget(void *opaque, const char *target) {
    assert(!consumed && !originals);
    // In particular, no setter is permitted before the last target validates.
    assert(getter_calls == expected_gets && expected_sets);
    assert(target && !strcmp(target, catalyst));
    OwnedModule *module = opaque;
    assert(module >= (OwnedModule *)module_page &&
           module < (OwnedModule *)module_page + 3);
    assert(!module->sets && module->target && !strcmp(module->target, macos));
    module->target = target;
    ++module->sets;
    ++sets;
}

static void *Original(void *modules, void *functions, void *info, void *error) {
    assert(!originals && !consumed);
    assert(modules == expected_arguments[0]);
    assert(functions == expected_arguments[1]);
    assert(info == expected_arguments[2]);
    assert(error == expected_arguments[3]);
    if (modules) assert(!memcmp(modules, expected_bounds, sizeof(expected_bounds)));
    assert(getter_calls == expected_gets && sets == expected_sets);
    OwnedModule *owned = module_page;
    for (unsigned i = 0; i < 3; ++i) {
        if (expected_sets) {
            assert(owned[i].sets == 1 && !strcmp(owned[i].target, catalyst));
        } else {
            // A rejected last module must not leave earlier modules changed.
            assert(!owned[i].sets && owned[i].target == initial_targets[i]);
        }
    }
    ++originals;
    *(void **)error = &error_token;
    if (consume_in_original) {
        // Model the real linker's consumption. Any later raw table/module
        // access by the extracted production wrapper faults, not just a mock
        // API invocation. No user or framework memory is involved.
        assert(!mprotect(table_page, page_size, PROT_NONE));
        assert(!mprotect(module_page, page_size, PROT_NONE));
        consumed = true;
    }
    return original_failure ? NULL : &result_token;
}

int main(int argc, char **argv) {
    assert(argc == 2);
    alarm(5);
    page_size = (size_t)sysconf(_SC_PAGESIZE);
    assert(page_size >= 4096);
    table_page = mmap(NULL, page_size, PROT_READ | PROT_WRITE,
                      MAP_PRIVATE | MAP_ANON, -1, 0);
    module_page = mmap(NULL, page_size, PROT_READ | PROT_WRITE,
                       MAP_PRIVATE | MAP_ANON, -1, 0);
    assert(table_page != MAP_FAILED && module_page != MAP_FAILED);
    OwnedModule *owned = module_page;
    void **table = table_page;
    for (unsigned i = 0; i < 3; ++i) {
        owned[i].target = macos;
        table[i] = &owned[i];
    }
    uintptr_t bounds[3] = {(uintptr_t)table, (uintptr_t)(table + 3),
                           (uintptr_t)(table + 3)};
    void *vector = bounds;
    gImageFilterRequestModuleCount = 3;
    expected_gets = 3;
    const char *which = argv[1];
    if (!strcmp(which, "supported")) {
        expected_sets = 3;
    } else if (!strcmp(which, "original-failure")) {
        expected_sets = 3;
        original_failure = true;
    } else if (!strcmp(which, "consumed")) {
        expected_sets = 3;
        consume_in_original = true;
    } else if (!strcmp(which, "zero-scope")) {
        gImageFilterRequestModuleCount = 0;
        expected_gets = 0;
        vector = NULL;
    } else if (!strcmp(which, "oversized-scope")) {
        gImageFilterRequestModuleCount = 4097;
        expected_gets = 0;
        vector = NULL;
    } else if (!strcmp(which, "null-vector")) {
        vector = NULL;
        expected_gets = 0;
    } else if (!strcmp(which, "null-begin")) {
        bounds[0] = 0;
        expected_gets = 0;
    } else if (!strcmp(which, "unaligned-begin")) {
        ++bounds[0]; ++bounds[1]; ++bounds[2];
        expected_gets = 0;
    } else if (!strcmp(which, "reversed-end")) {
        bounds[1] = bounds[0] - 8;
        expected_gets = 0;
    } else if (!strcmp(which, "reversed-capacity")) {
        bounds[2] = bounds[1] - 8;
        expected_gets = 0;
    } else if (!strcmp(which, "unaligned-end")) {
        --bounds[1];
        expected_gets = 0;
    } else if (!strcmp(which, "unaligned-capacity")) {
        ++bounds[2];
        expected_gets = 0;
    } else if (!strcmp(which, "count-mismatch")) {
        gImageFilterRequestModuleCount = 2;
        expected_gets = 0;
    } else if (!strcmp(which, "oversized-capacity")) {
        bounds[2] = bounds[0] + 4097U * sizeof(void *);
        expected_gets = 0;
    } else if (!strcmp(which, "wrapped-end")) {
        bounds[0] = UINTPTR_MAX - 7;
        bounds[1] = 16;
        bounds[2] = 24;
        expected_gets = 0;
    } else if (!strcmp(which, "duplicate")) {
        table[1] = table[0];
        expected_gets = 1;
    } else if (!strcmp(which, "null-last-module")) {
        table[2] = NULL;
        expected_gets = 2;
    } else if (!strcmp(which, "unknown-last")) {
        owned[2].target = "air64-apple-macosx99.0.0";
    } else if (!strcmp(which, "native-ios-last")) {
        owned[2].target = "air64-apple-ios16.3.0";
    } else if (!strcmp(which, "catalyst-last")) {
        owned[2].target = catalyst;
    } else if (!strcmp(which, "null-last-target")) {
        owned[2].target = NULL;
    } else {
        return 64;
    }
    for (unsigned i = 0; i < 3; ++i) initial_targets[i] = owned[i].target;
    int functions = 11, info = 22;
    void *error = &info;
    expected_arguments[0] = vector;
    expected_arguments[1] = &functions;
    expected_arguments[2] = &info;
    expected_arguments[3] = &error;
    if (vector) memcpy(expected_bounds, vector, sizeof(expected_bounds));
    gOriginalComposeImageFilters = Original;
    gImageFilterGetTarget = GetTarget;
    gImageFilterSetTarget = SetTarget;
    void *result = MacWSComposeImageFilters(vector, &functions, &info, &error);
    assert(result == (original_failure ? NULL : &result_token) && originals == 1);
    assert(getter_calls == expected_gets && sets == expected_sets);
    assert(functions == 11 && info == 22 && error == &error_token);
    assert(!munmap(table_page, page_size));
    assert(!munmap(module_page, page_size));
    printf("PASS %s getters=%u setters=%u original=%u\n", which, getter_calls, sets, originals);
    return 0;
}
'''


class MetalImageFilterComposeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="macws-compose-wrapper-")
        cls.binary = str(Path(cls.tmp.name) / "compose-test")
        subprocess.run(
            [os.environ.get("CC", "clang"), "-x", "c", "-std=c11", "-O1",
             "-Wall", "-Wextra", "-Werror", "-fsanitize=undefined",
             "-fno-sanitize-recover=all", "-", "-o", cls.binary],
            input=PREAMBLE + actual_wrapper() + FIXTURE, text=True,
            check=True, timeout=30,
        )

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_case(self, case):
        result = subprocess.run([self.binary, case], text=True,
                                capture_output=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS " + case, result.stdout)

    def test_supported_setters_all_run_before_original_and_preserve_abi(self):
        self.run_case("supported")

    def test_consumed_modules_and_table_are_never_accessed_after_original(self):
        self.run_case("consumed")

    def test_original_failure_and_error_output_are_not_replaced(self):
        self.run_case("original-failure")

    def test_invalid_scope_or_bounds_never_access_modules_or_call_setters(self):
        for case in ("zero-scope", "oversized-scope", "null-vector", "null-begin",
                     "unaligned-begin", "reversed-end", "reversed-capacity",
                     "unaligned-end", "unaligned-capacity", "count-mismatch",
                     "oversized-capacity", "wrapped-end"):
            with self.subTest(case=case):
                self.run_case(case)

    def test_null_and_duplicate_modules_do_not_partially_mutate(self):
        for case in ("duplicate", "null-last-module"):
            with self.subTest(case=case):
                self.run_case(case)

    def test_last_target_mismatch_or_null_prevents_every_setter(self):
        for case in ("unknown-last", "native-ios-last", "catalyst-last",
                     "null-last-target"):
            with self.subTest(case=case):
                self.run_case(case)


if __name__ == "__main__":
    unittest.main()

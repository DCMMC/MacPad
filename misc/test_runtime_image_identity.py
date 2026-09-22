"""Bound checks for the read-only mapped-image identity diagnostic."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "misc/macws_runtime_image_identity.c"


class RuntimeImageIdentity(unittest.TestCase):
    def test_no_attachment_or_target_modification(self):
        source = PROBE.read_text()
        for mutation in ("thread_suspend(", "task_suspend(", "mach_vm_write(",
                         "ptrace(", "mach_vm_protect(", "getenv("):
            self.assertNotIn(mutation, source)
        for bound in ("alarm(15)", "MAX_IMAGES 4096u", "MAX_TEXT_BYTES (8u * 1024u * 1024u)",
                      "allowed(argv[i], images)", "allowed(base_name(executable), processes)"):
            self.assertIn(bound, source)

    @unittest.skipUnless(sys.platform == "darwin", "Mach-O fixture harness uses Darwin SDK")
    def test_actual_parser_rejects_out_of_bounds_fixture(self):
        compiler = shutil.which("clang")
        if not compiler:
            self.skipTest("clang is unavailable")
        # Include the production parser, redirecting its only remote-read API
        # to a bounded in-memory fixture. No task port is opened by this test.
        harness = """
#define main diagnostic_main
#define mach_vm_read_overwrite fixture_read
#include "%s"
#undef main
static unsigned char fixture[1024];
kern_return_t fixture_read(vm_map_t task, mach_vm_address_t address,
    mach_vm_size_t size, mach_vm_address_t destination, mach_vm_size_t *copied) {
    (void)task;
    uintptr_t start = (uintptr_t)fixture;
    if (address < start || address - start > sizeof(fixture) ||
        size > sizeof(fixture) - (address - start)) return KERN_INVALID_ADDRESS;
    memcpy((void *)destination, (void *)(uintptr_t)address, size);
    *copied = size; return KERN_SUCCESS;
}
static void prepare(void) {
    memset(fixture, 0, sizeof(fixture));
    struct mach_header_64 *h = (void *)fixture;
    h->magic = MH_MAGIC_64; h->cputype = CPU_TYPE_ARM64; h->ncmds = 2;
    h->sizeofcmds = sizeof(struct uuid_command) + sizeof(struct segment_command_64) + sizeof(struct section_64);
    struct uuid_command *uuid = (void *)(h + 1);
    uuid->cmd = LC_UUID; uuid->cmdsize = sizeof(*uuid); uuid->uuid[0] = 1;
    struct segment_command_64 *s = (void *)(uuid + 1);
    s->cmd = LC_SEGMENT_64; s->cmdsize = sizeof(*s) + sizeof(struct section_64);
    strcpy(s->segname, "__TEXT"); s->vmaddr = 0x100000000; s->vmsize = sizeof(fixture);
    s->initprot = VM_PROT_READ | VM_PROT_EXECUTE; s->nsects = 1;
    struct section_64 *text = (void *)(s + 1);
    strcpy(text->sectname, "__text"); text->addr = s->vmaddr + 512; text->size = 32;
}
int main(void) {
    const char *consumers[] = {"Terminal", "bash", "Finder", "Preview", "Weather",
        "Electron", "iconservicesagent", "com.apple.quicklook.ThumbnailsAgent", NULL};
    for (unsigned i = 0; consumers[i]; ++i)
        if (!allowed(consumers[i], processes)) return 10;
    if (allowed("arbitrary-private-app", processes) ||
        allowed("Documents", images) || allowed("private.dylib", images)) return 11;
    struct mach_header_64 *h = (void *)fixture;
    struct uuid_command *u = (void *)(h + 1);
    struct segment_command_64 *s = (void *)(u + 1);
    struct section_64 *text = (void *)(s + 1);
    prepare(); if (!image_identity(0, (uintptr_t)fixture, "fixture")) return 1;
    prepare(); h->sizeofcmds = MAX_COMMAND_BYTES + 1;
    if (image_identity(0, (uintptr_t)fixture, "fixture")) return 2;
    prepare(); u->cmdsize = 0;
    if (image_identity(0, (uintptr_t)fixture, "fixture")) return 3;
    prepare(); s->nsects = UINT32_MAX;
    if (image_identity(0, (uintptr_t)fixture, "fixture")) return 4;
    prepare(); text->size = MAX_TEXT_BYTES + 1;
    if (image_identity(0, (uintptr_t)fixture, "fixture")) return 5;
    prepare(); text->addr = s->vmaddr - 1;
    if (image_identity(0, (uintptr_t)fixture, "fixture")) return 6;
    prepare(); text->size = s->vmsize;
    if (image_identity(0, (uintptr_t)fixture, "fixture")) return 7;
    prepare(); h->ncmds = 2049;
    if (image_identity(0, (uintptr_t)fixture, "fixture")) return 8;
    return 0;
}
""" % str(PROBE)
        with tempfile.TemporaryDirectory(prefix="macws-image-bounds-") as directory:
            source = Path(directory) / "fixture.c"
            binary = Path(directory) / "fixture"
            source.write_text(harness)
            built = subprocess.run([compiler, "-O0", "-Wall", "-Wextra", "-Werror",
                                    "-Wno-deprecated-declarations", str(source), "-o", str(binary)],
                                   capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=5)
            self.assertEqual(ran.returncode, 0, ran.stderr)


if __name__ == "__main__":
    unittest.main()

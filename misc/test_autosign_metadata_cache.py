import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class AutosignMetadataCache(unittest.TestCase):
    def test_changed_bytes_replacement_and_failed_capture_are_not_cached(self):
        with tempfile.TemporaryDirectory() as folder:
            binary = pathlib.Path(folder) / 'probe'
            subprocess.run(['clang', '-x', 'c', '-Wno-deprecated-declarations',
                            '-I', str(ROOT), '-', '-o', str(binary)],
                           input=br'''
#define main autosignd_main
#include "autosignd/main.c"
#undef main
static void put(const char *path, const char *value) {
    int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0600);
    write(fd, value, strlen(value)); close(fd);
}
int main(int argc, char **argv) {
    (void)argc;
    char *path = argv[1], output[128];
    char *args[] = {"/bin/cat", path, NULL};
    put(path, "before");
    if (capture_macho_metadata(args, path, output, sizeof(output)) ||
        strcmp(output, "before")) return 1;
    if (capture_macho_metadata(args, path, output, sizeof(output)) ||
        strcmp(output, "before") || g_metadata_next != 1) return 2;
    put(path, "after!");
    if (capture_macho_metadata(args, path, output, sizeof(output)) ||
        strcmp(output, "after!")) return 3;
    unlink(path); put(path, "replaced");
    if (capture_macho_metadata(args, path, output, sizeof(output)) ||
        strcmp(output, "replaced")) return 4;
    char *bad[] = {"/not/a/tool", path, NULL};
    if (capture_macho_metadata(bad, path, output, sizeof(output)) == 0) return 5;
    unlink(path);
    return 0;
}
''', check=True, capture_output=True)
            result = subprocess.run([str(binary), str(pathlib.Path(folder) / 'image')],
                                    capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_trustcache_inventory_is_still_live(self):
        source = (ROOT / 'autosignd/main.c').read_text()
        body = source.split('static int current_hashes_are_trusted(')[1]
        body = body.split('static int normalize_rootfs_path(')[0]
        self.assertIn('code_signature_pages_are_valid(path)', body)
        self.assertIn('int status = capture(argv, inventory, inventory_size)', body)
        self.assertNotIn('capture_macho_metadata(', body)


if __name__ == '__main__':
    unittest.main()

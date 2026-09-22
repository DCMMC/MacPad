"""Native streaming-copy checks and Open-In integration contracts."""
import hashlib
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class FileImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.temp.name) / 'copy-probe'
        subprocess.run(['clang', '-x', 'c', '-Wno-deprecated-declarations',
                        '-I', str(ROOT / 'include'), '-', '-o', str(cls.binary)],
                       input=b'''
#include <fcntl.h>
#include <stdio.h>
#include <errno.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
static const char *mode;
static int sourceFD, readCalls, writeCalls;
static ssize_t probeRead(int fd, void *data, size_t count) {
    if (fd == sourceFD && ++readCalls == 1 && !strcmp(mode, "eintr-read")) {
        errno = EINTR;
        return -1;
    }
    ssize_t result = read(fd, data, count);
    if (fd == sourceFD && readCalls == 1 && result > 0) {
        if (!strcmp(mode, "truncate-source")) ftruncate(fd, 1);
        if (!strcmp(mode, "grow-source")) {
            struct stat st;
            fstat(fd, &st);
            ftruncate(fd, st.st_size + 1);
        }
        if (!strcmp(mode, "mutate-source")) {
            struct stat st;
            fstat(fd, &st);
            struct timespec times[2] = {st.st_atimespec, st.st_mtimespec};
            times[1].tv_sec++;
            pwrite(fd, "X", 1, 0);
            futimens(fd, times);
        }
    }
    return result;
}
static ssize_t probeWrite(int fd, const void *data, size_t count) {
    if (++writeCalls == 1 && !strcmp(mode, "eintr-write")) {
        errno = EINTR;
        return -1;
    }
    if (!strcmp(mode, "short-write") && count > 7) count = 7;
    return write(fd, data, count);
}
static int probeSync(int fd) {
    int result = fsync(fd);
    if (!strcmp(mode, "corrupt-destination")) pwrite(fd, "X", 1, 0);
    return result;
}
#define read probeRead
#define write probeWrite
#define fsync probeSync
#include "macws_file_copy.h"
int main(int argc, char **argv) {
    mode = argc > 3 ? argv[3] : "normal";
    int source = sourceFD = open(argv[1], O_RDWR);
    int destination = open(argv[2], (!strcmp(mode, "write-only") ? O_WRONLY : O_RDWR) |
                            O_CREAT | O_EXCL, 0600);
    uint64_t bytes = 0;
    int result = MacWSCopyStableRegularFile(source, destination, &bytes);
    int saved = errno;
    close(source); close(destination);
    if (result != 0 && destination >= 0) unlink(argv[2]);
    printf("result=%d errno=%d bytes=%llu\\n", result, saved,
           (unsigned long long)bytes);
    return result == 0 ? 0 : 1;
}
''', check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def copy(self, content, bad_destination=False):
        with tempfile.TemporaryDirectory() as folder:
            source, destination = Path(folder) / 'source', Path(folder) / 'copy'
            source.write_bytes(content)
            args = [str(self.binary), str(source), str(destination)]
            if bad_destination:
                args.append('write-only')
            result = subprocess.run(args, capture_output=True, text=True)
            self.assertEqual(source.read_bytes(), content)
            if bad_destination:
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(destination.exists())
            else:
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertEqual(hashlib.sha256(destination.read_bytes()).digest(),
                                 hashlib.sha256(content).digest())

    def test_binary_and_empty_files(self):
        self.copy(bytes(range(256)) * 1025)
        self.copy(b'')

    def test_large_file_is_not_limited_by_inline_archive_limit(self):
        # Exercise >64 MiB while keeping the test itself memory-bounded too.
        with tempfile.TemporaryDirectory() as folder:
            source, destination = Path(folder) / 'large', Path(folder) / 'copy'
            with source.open('wb') as stream:
                for index in range(80):
                    stream.write(bytes([index]) * (1024 * 1024))
                stream.write(b'end-of-large-file!!')
            result = subprocess.run([str(self.binary), str(source), str(destination)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(source.stat().st_size, destination.stat().st_size)
            def digest(path):
                value = hashlib.sha256()
                with path.open('rb') as stream:
                    while block := stream.read(128 * 1024):
                        value.update(block)
                return value.digest()
            self.assertEqual(digest(source), digest(destination))

    def test_failed_verification_does_not_publish_file(self):
        self.copy(b'not a completed transfer', bad_destination=True)

    def test_interruptions_and_partial_writes_preserve_all_bytes(self):
        content = bytes(range(256)) * 1031
        for mode in ('eintr-read', 'eintr-write', 'short-write'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                source, destination = Path(folder) / 'source', Path(folder) / 'copy'
                source.write_bytes(content)
                result = subprocess.run([str(self.binary), str(source),
                                         str(destination), mode], capture_output=True)
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertEqual(destination.read_bytes(), content)

    def test_changed_source_and_corrupt_output_are_not_published(self):
        for mode in ('truncate-source', 'grow-source', 'mutate-source',
                     'corrupt-destination'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                source, destination = Path(folder) / 'source', Path(folder) / 'copy'
                source.write_bytes(bytes(range(256)) * 1031)
                result = subprocess.run([str(self.binary), str(source),
                                         str(destination), mode], capture_output=True)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertFalse(destination.exists())

    def test_provider_batch_is_all_items_and_never_overall_timeout_subset(self):
        source = (ROOT / 'MacWSHost/MacWSInteropClient.m').read_text()
        batch = source.split('- (void)sendLoadedProviderSlots:', 1)[1].split(
            '- (void)publishItemProviders:', 1)[0]
        self.assertIn('archiveItems.count == slots.count && slots.count > 0', batch)
        self.assertIn('MacWSRemoveUnpublishedProviderFiles(slots)', batch)
        watchdog = source.split('dispatch_source_set_event_handler(deadlineTimer, ^{', 1)[1].split(
            'dispatch_resume(deadlineTimer)', 1)[0]
        self.assertIn('finished || callbackActive', watchdog)
        self.assertIn('completion(NO,', watchdog)
        self.assertNotIn('sendLoadedProviderSlots', watchdog)

    def test_photos_provider_routes_do_not_regress_notes_or_files(self):
        source = (ROOT / 'MacWSHost/MacWSInteropClient.m').read_text()
        provider = source.split(
            'static NSItemProvider *MacWSFileDragItemProvider', 1)[1].split(
            'static NSURL *MacWSHostDataContainerURL', 1)[0]
        # Photos gets the abstract data representation it checks before it
        # asks for bytes; the concrete file registrations remain present for
        # Files/Notes and preserve the original filename.
        self.assertIn('registerFileRepresentationForTypeIdentifier:type', provider)
        self.assertIn('UTTypeImage.identifier', provider)
        self.assertIn('registerDataRepresentationForTypeIdentifier:', provider)

        schedule = source.split(
            '- (void)publishItemProviders:', 1)[1].split(
            'dispatch_queue_t loadQueue', 1)[0]
        self.assertIn('namedSingleImageDataFirst', schedule)
        data_first = schedule.index('@"order": @(-3.5)')
        direct = schedule.index('@"kind": @"direct-item"')
        self.assertLess(data_first, direct)
        self.assertIn('!namedSingleImageDataFirst && materializationType',
                      schedule)

    def test_registration_imports_copy_without_claiming_type_ownership(self):
        info = plistlib.loads((ROOT / 'MacWSHost/Resources/Info.plist').read_bytes())
        self.assertFalse(info['LSSupportsOpeningDocumentsInPlace'])
        entry, = info['CFBundleDocumentTypes']
        self.assertEqual(entry['LSHandlerRank'], 'Alternate')
        self.assertEqual(set(entry['LSItemContentTypes']),
                         {'public.data', 'public.content'})

    def test_cold_and_warm_url_delivery_and_appkit_ack_route(self):
        host = (ROOT / 'MacWSHost/main.m').read_text()
        self.assertIn('[self scene:scene openURLContexts:contexts]', host)
        self.assertIn('if (context.URL.isFileURL)', host)
        self.assertIn('openExternalDocumentURL:context.URL', host)
        daemon = (ROOT / 'macwshostd/main.m').read_text()
        self.assertIn('ResolveImportedDocumentApplication(', daemon)
        self.assertIn('WaitForOpenDocumentAck(', daemon)


if __name__ == '__main__':
    unittest.main()

// autosignd — on-demand sign + trustcache daemon (runs on the iOS side).
//
// libmachook (injected into every macOS chroot process) interposes the
// exec/posix_spawn family and, before each exec, sends the target binary's
// (chroot-absolute) path to this daemon over a unix socket. The daemon
// translates the path into the rootfs, extracts the Mach-O CDHash with `ldid`
// (ad-hoc signing it first if unsigned), and registers the CDHash with the
// jailbreak trustcache via `jbctl` — the privileged operation that can only run
// in an iOS-platform process (the chroot's macOS dyld refuses to load
// libjailbreak.dylib, so the chroot cannot call jbclient_* directly).
//
// Protocol:
//   "<chroot-path>\n"  sign/trustcache that chroot-absolute path
//   "DEBUG\n"         mark the connecting process CS_DEBUGGED
//   "PING\n"          verify that the published socket has a live server
//
// The second operation replaces libmachook's historical constructor-time
// fork()+ptrace JIT bootstrap.  Forking while dyld is still running image
// initializers leaves dyld's atfork lock lifecycle exposed to the macOS/iOS
// libSystem boundary; Steam CEF runtime-confirmed the resulting permanent
// wait in dlopen_from().  The daemon obtains the peer PID from the Unix socket,
// so a client cannot nominate an unrelated process.

#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <signal.h>
#include <spawn.h>
#include <sys/socket.h>
#include <sys/file.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <sys/stat.h>
#include <time.h>
#include <limits.h>
#include <stdint.h>
#include <sys/mman.h>
#include <CommonCrypto/CommonDigest.h>

extern char **environ;

// Socket path as seen from the iOS side (== chroot /tmp/autosignd.sock).
#define SOCK_PATH   "/var/mnt/rootfs/tmp/autosignd.sock"
#define LOCK_PATH   "/var/mnt/rootfs/tmp/autosignd.lock"
#define ROOTFS      "/var/mnt/rootfs"
#define LDID        "/var/jb/usr/bin/ldid"
#define JBCTL       "/var/jb/usr/bin/jbctl"
#define OTOOL       "/var/jb/usr/bin/otool"
#define ENT         "/var/jb/usr/macOS/bin/entitlements.plist"

static const char *kArches[] = { "arm64", "arm64e", "x86_64", NULL };

typedef struct {
    char *path;
    dev_t device;
    ino_t inode;
    off_t size;
    struct timespec mtime;
    struct timespec ctime;
} ValidatedSignature;

static ValidatedSignature *g_validated_signatures;
static size_t g_validated_signature_count;
static size_t g_validated_signature_capacity;

// ── simple in-memory set of already-processed rootfs paths ──────────────────
static char **g_seen = NULL;
static size_t g_seen_n = 0, g_seen_cap = 0;

static int seen(const char *p) {
    for (size_t i = 0; i < g_seen_n; i++)
        if (strcmp(g_seen[i], p) == 0) return 1;
    return 0;
}
static void mark_seen(const char *p) {
    if (g_seen_n == g_seen_cap) {
        g_seen_cap = g_seen_cap ? g_seen_cap * 2 : 64;
        g_seen = realloc(g_seen, g_seen_cap * sizeof(char *));
    }
    g_seen[g_seen_n++] = strdup(p);
}

static void clear_seen(void) {
    for (size_t index = 0; index < g_seen_n; index++) free(g_seen[index]);
    g_seen_n = 0;
}

static void logmsg(const char *fmt, ...) {
    char ts[32];
    time_t t = time(NULL);
    struct tm tm;
    localtime_r(&t, &tm);
    strftime(ts, sizeof(ts), "%H:%M:%S", &tm);
    fprintf(stderr, "[%s] ", ts);
    va_list ap; va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    fputc('\n', stderr);
    fflush(stderr);
}

// Run argv[], capture up to outsz-1 bytes of stdout. Returns child exit code,
// or -1 on spawn failure.
static int capture(char *const argv[], char *out, size_t outsz) {
    int pipefd[2];
    if (pipe(pipefd) != 0) return -1;

    posix_spawn_file_actions_t fa;
    posix_spawn_file_actions_init(&fa);
    posix_spawn_file_actions_adddup2(&fa, pipefd[1], STDOUT_FILENO);
    posix_spawn_file_actions_addclose(&fa, pipefd[0]);
    posix_spawn_file_actions_addclose(&fa, pipefd[1]);

    pid_t pid;
    int rc = posix_spawn(&pid, argv[0], &fa, NULL, argv, environ);
    posix_spawn_file_actions_destroy(&fa);
    close(pipefd[1]);
    if (rc != 0) { close(pipefd[0]); return -1; }

    size_t off = 0;
    if (out && outsz) {
        ssize_t n;
        while (off < outsz - 1 && (n = read(pipefd[0], out + off, outsz - 1 - off)) > 0)
            off += (size_t)n;
        out[off] = '\0';
    }
    // drain anything left so the child doesn't block on a full pipe
    char junk[256];
    while (read(pipefd[0], junk, sizeof(junk)) > 0) {}
    close(pipefd[0]);

    int status = 0;
    waitpid(pid, &status, 0);
    return WIFEXITED(status) ? WEXITSTATUS(status) : -1;
}

static int stat_matches(const struct stat *left, const struct stat *right);

// Cache only immutable Mach-O metadata, never a successful admission or the
// live trustcache inventory. Runtime socket timings on 2026-09-13 measured
// 0.386..0.399 s for an unchanged true/sysctl, versus 0.000082 s for PING.
// Each request invoked otool twice and ldid three times despite unchanged
// file identity. Re-resolve dependency paths and verify live trust as before.
typedef struct {
    char *key;
    char *output;
    struct stat identity;
} MetadataCapture;
enum { METADATA_CACHE_ENTRIES = 128 };
static MetadataCapture g_metadata[METADATA_CACHE_ENTRIES];
static size_t g_metadata_next;
static size_t g_metadata_bytes;

static int capture_macho_metadata(char *const argv[], const char *path,
                                  char *output, size_t capacity) {
    struct stat before, after;
    if (!output || !capacity || stat(path, &before) != 0 ||
        !S_ISREG(before.st_mode)) return -1;
    char key[PATH_MAX + 256] = {0};
    size_t used = 0;
    for (size_t index = 0; argv[index]; index++) {
        size_t length = strlen(argv[index]) + 1;
        if (length > sizeof(key) - used) return capture(argv, output, capacity);
        memcpy(key + used, argv[index], length);
        // Argument boundaries cannot collide with spaces in a pathname.
        key[used + length - 1] = '\n';
        used += length;
    }
    if (used >= sizeof(key)) return capture(argv, output, capacity);
    key[used] = '\0';
    for (size_t index = 0; index < METADATA_CACHE_ENTRIES; index++) {
        MetadataCapture *entry = &g_metadata[index];
        if (entry->key && strcmp(entry->key, key) == 0 &&
            stat_matches(&entry->identity, &before) &&
            strlen(entry->output) < capacity) {
            strlcpy(output, entry->output, capacity);
            return 0;
        }
    }
    output[0] = '\0';
    int result = capture(argv, output, capacity);
    if (result != 0) return result;
    size_t length = strlen(output) + 1;
    if (result != 0 || length >= capacity || length > 64 * 1024 ||
        stat(path, &after) != 0 || !stat_matches(&before, &after)) return result;
    // At most 128 entries and 1 MiB of actual output; oversized metadata is
    // simply uncached. No process-lifetime growth from a package build.
    MetadataCapture *entry = &g_metadata[g_metadata_next++ % METADATA_CACHE_ENTRIES];
    if (entry->output) g_metadata_bytes -= strlen(entry->output) + 1;
    free(entry->key);
    free(entry->output);
    memset(entry, 0, sizeof(*entry));
    if (g_metadata_bytes + length > 1024 * 1024) return result;
    entry->key = strdup(key);
    entry->output = strdup(output);
    if (!entry->key || !entry->output) {
        free(entry->key); free(entry->output);
        memset(entry, 0, sizeof(*entry));
        return result;
    }
    entry->identity = after;
    g_metadata_bytes += length;
    return result;
}

// Extract the "CDHash=<hex>" value (if any) for one arch slice into hash[].
static int cdhash_for_arch(const char *path, const char *arch, char *hash, size_t hsz) {
    char buf[8192];
    char *const argv[] = { (char *)LDID, "-arch", (char *)arch, "-h", (char *)path, NULL };
    if (capture_macho_metadata(argv, path, buf, sizeof(buf)) != 0) return 0;
    char *p = strstr(buf, "CDHash=");
    if (!p) return 0;
    p += 7;
    size_t i = 0;
    while (i < hsz - 1 && ((*p >= '0' && *p <= '9') || (*p >= 'a' && *p <= 'f') ||
                           (*p >= 'A' && *p <= 'F'))) {
        hash[i++] = *p++;
    }
    hash[i] = '\0';
    return i > 0;
}

static void trustcache_add(const char *hash) {
    char *const argv[] = { (char *)JBCTL, "trustcache", "add", (char *)hash, NULL };
    capture(argv, NULL, 0);
}

static int adhoc_sign(const char *path, int dependency) {
    char executable_sflag[] = "-S" ENT;
    char library_sflag[] = "-S";
    // A loaded dylib must not carry the executable compatibility entitlement
    // profile. iPadOS logged "has entitlements but is not a main binary" for
    // third-party libraries signed that way. Entry executables retain the
    // established MacWS profile; dependencies get a plain ad-hoc signature.
    if (dependency) {
        // Do not pass -M here: merging would preserve executable-only
        // entitlements already embedded by an older autosignd revision.
        char *const argv[] = {
            (char *)LDID, library_sflag, (char *)path, NULL
        };
        return capture(argv, NULL, 0);
    }
    char *const argv[] = {
        (char *)LDID, executable_sflag, "-M", (char *)path, NULL
    };
    return capture(argv, NULL, 0);
}

static uint32_t read_u32(const uint8_t *bytes, int little_endian) {
    if (little_endian)
        return (uint32_t)bytes[0] | ((uint32_t)bytes[1] << 8) |
               ((uint32_t)bytes[2] << 16) | ((uint32_t)bytes[3] << 24);
    return ((uint32_t)bytes[0] << 24) | ((uint32_t)bytes[1] << 16) |
           ((uint32_t)bytes[2] << 8) | (uint32_t)bytes[3];
}

static uint64_t read_u64(const uint8_t *bytes, int little_endian) {
    uint64_t low = read_u32(bytes + (little_endian ? 0 : 4), little_endian);
    uint64_t high = read_u32(bytes + (little_endian ? 4 : 0), little_endian);
    return low | (high << 32);
}

static int pread_all(int fd, void *buffer, size_t length, off_t offset) {
    uint8_t *cursor = buffer;
    while (length) {
        ssize_t count = pread(fd, cursor, length, offset);
        if (count > 0) {
            cursor += count;
            length -= (size_t)count;
            offset += count;
            continue;
        }
        if (count < 0 && errno == EINTR) continue;
        return 0;
    }
    return 1;
}

static int stat_matches(const struct stat *left, const struct stat *right) {
    return left->st_dev == right->st_dev && left->st_ino == right->st_ino &&
           left->st_size == right->st_size &&
           left->st_mtimespec.tv_sec == right->st_mtimespec.tv_sec &&
           left->st_mtimespec.tv_nsec == right->st_mtimespec.tv_nsec &&
           left->st_ctimespec.tv_sec == right->st_ctimespec.tv_sec &&
           left->st_ctimespec.tv_nsec == right->st_ctimespec.tv_nsec;
}

static int validated_signature_cached(const char *path,
                                      const struct stat *status) {
    for (size_t index = 0; index < g_validated_signature_count; index++) {
        ValidatedSignature *entry = &g_validated_signatures[index];
        if (strcmp(entry->path, path) == 0 &&
            entry->device == status->st_dev &&
            entry->inode == status->st_ino && entry->size == status->st_size &&
            entry->mtime.tv_sec == status->st_mtimespec.tv_sec &&
            entry->mtime.tv_nsec == status->st_mtimespec.tv_nsec &&
            entry->ctime.tv_sec == status->st_ctimespec.tv_sec &&
            entry->ctime.tv_nsec == status->st_ctimespec.tv_nsec)
            return 1;
    }
    return 0;
}

static void cache_validated_signature(const char *path,
                                      const struct stat *status) {
    for (size_t index = 0; index < g_validated_signature_count; index++) {
        ValidatedSignature *entry = &g_validated_signatures[index];
        if (strcmp(entry->path, path) != 0) continue;
        entry->device = status->st_dev;
        entry->inode = status->st_ino;
        entry->size = status->st_size;
        entry->mtime = status->st_mtimespec;
        entry->ctime = status->st_ctimespec;
        return;
    }
    if (g_validated_signature_count == g_validated_signature_capacity) {
        size_t capacity = g_validated_signature_capacity
                            ? g_validated_signature_capacity * 2 : 64;
        ValidatedSignature *entries = realloc(
            g_validated_signatures, capacity * sizeof(*entries));
        if (!entries) return;
        g_validated_signatures = entries;
        g_validated_signature_capacity = capacity;
    }
    ValidatedSignature *entry =
        &g_validated_signatures[g_validated_signature_count++];
    memset(entry, 0, sizeof(*entry));
    entry->path = strdup(path);
    if (!entry->path) {
        g_validated_signature_count--;
        return;
    }
    entry->device = status->st_dev;
    entry->inode = status->st_ino;
    entry->size = status->st_size;
    entry->mtime = status->st_mtimespec;
    entry->ctime = status->st_ctimespec;
}

static size_t code_digest(uint8_t hash_type, const void *bytes, size_t length,
                          uint8_t digest[CC_SHA512_DIGEST_LENGTH]) {
    if (length > UINT32_MAX) return 0;
    switch (hash_type) {
        case 1:
            CC_SHA1(bytes, (CC_LONG)length, digest);
            return CC_SHA1_DIGEST_LENGTH;
        case 2:
        case 3:
            CC_SHA256(bytes, (CC_LONG)length, digest);
            return CC_SHA256_DIGEST_LENGTH;
        case 4:
            CC_SHA384(bytes, (CC_LONG)length, digest);
            return CC_SHA384_DIGEST_LENGTH;
        default:
            return 0;
    }
}

static int code_directory_pages_are_valid(int fd, uint64_t slice_offset,
                                          uint64_t slice_size,
                                          const uint8_t *directory,
                                          size_t available) {
    if (available < 44 || read_u32(directory, 0) != 0xfade0c02) return 0;
    uint32_t length = read_u32(directory + 4, 0);
    uint32_t version = read_u32(directory + 8, 0);
    uint32_t hash_offset = read_u32(directory + 16, 0);
    uint32_t code_slots = read_u32(directory + 28, 0);
    uint64_t code_limit = read_u32(directory + 32, 0);
    uint8_t hash_size = directory[36];
    uint8_t hash_type = directory[37];
    uint8_t page_shift = directory[39];
    if (length < 44 || length > available || page_shift < 9 ||
        page_shift > 20 || hash_size == 0 || hash_size > 64)
        return 0;
    if (version >= 0x20300 && length >= 64 && code_limit == 0)
        code_limit = read_u64(directory + 56, 0);
    uint64_t page_size = UINT64_C(1) << page_shift;
    uint64_t expected_slots = (code_limit + page_size - 1) / page_size;
    uint64_t hashes_end = (uint64_t)hash_offset +
                          (uint64_t)code_slots * hash_size;
    if (code_limit > slice_size || expected_slots != code_slots ||
        hashes_end > length) return 0;

    uint8_t *page = malloc((size_t)page_size);
    if (!page) return 0;
    int valid = 1;
    for (uint32_t slot = 0; slot < code_slots; slot++) {
        uint64_t page_offset = (uint64_t)slot * page_size;
        size_t page_length = (size_t)((code_limit - page_offset) < page_size
                            ? (code_limit - page_offset) : page_size);
        if (!pread_all(fd, page, page_length,
                       (off_t)(slice_offset + page_offset))) {
            valid = 0;
            break;
        }
        uint8_t digest[CC_SHA512_DIGEST_LENGTH];
        size_t digest_length = code_digest(hash_type, page, page_length,
                                           digest);
        const uint8_t *expected = directory + hash_offset +
                                  (uint64_t)slot * hash_size;
        if (digest_length < hash_size ||
            memcmp(digest, expected, hash_size) != 0) {
            valid = 0;
            break;
        }
    }
    free(page);
    return valid;
}

static int macho_slice_signature_is_valid(int fd, uint64_t slice_offset,
                                          uint64_t slice_size) {
    uint8_t header[32];
    if (slice_size < 28 || !pread_all(fd, header, sizeof(header),
                                      (off_t)slice_offset)) return 0;
    int little_endian;
    size_t header_size;
    if (memcmp(header, "\xcf\xfa\xed\xfe", 4) == 0) {
        little_endian = 1; header_size = 32;
    } else if (memcmp(header, "\xfe\xed\xfa\xcf", 4) == 0) {
        little_endian = 0; header_size = 32;
    } else if (memcmp(header, "\xce\xfa\xed\xfe", 4) == 0) {
        little_endian = 1; header_size = 28;
    } else if (memcmp(header, "\xfe\xed\xfa\xce", 4) == 0) {
        little_endian = 0; header_size = 28;
    } else {
        return 0;
    }
    uint32_t command_count = read_u32(header + 16, little_endian);
    uint32_t commands_size = read_u32(header + 20, little_endian);
    if ((uint64_t)header_size + commands_size > slice_size ||
        commands_size > 32 * 1024 * 1024) return 0;
    uint8_t *commands = malloc(commands_size);
    if (!commands || !pread_all(fd, commands, commands_size,
                                (off_t)(slice_offset + header_size))) {
        free(commands);
        return 0;
    }

    uint32_t signature_offset = 0, signature_size = 0;
    size_t cursor = 0;
    for (uint32_t index = 0; index < command_count; index++) {
        if (cursor + 8 > commands_size) break;
        uint32_t command = read_u32(commands + cursor, little_endian);
        uint32_t command_size = read_u32(commands + cursor + 4,
                                         little_endian);
        if (command_size < 8 || cursor + command_size > commands_size) break;
        if (command == 0x1d && command_size >= 16) {
            signature_offset = read_u32(commands + cursor + 8,
                                        little_endian);
            signature_size = read_u32(commands + cursor + 12,
                                      little_endian);
            break;
        }
        cursor += command_size;
    }
    free(commands);
    if (!signature_size || (uint64_t)signature_offset + signature_size >
                           slice_size || signature_size > 64 * 1024 * 1024)
        return 0;
    uint8_t *signature = malloc(signature_size);
    if (!signature || !pread_all(fd, signature, signature_size,
                                 (off_t)(slice_offset + signature_offset))) {
        free(signature);
        return 0;
    }

    int valid = 1, found = 0;
    if (signature_size < 12 || read_u32(signature, 0) != 0xfade0cc0) {
        valid = 0;
    } else {
        uint32_t super_length = read_u32(signature + 4, 0);
        uint32_t count = read_u32(signature + 8, 0);
        if (super_length < 12 || super_length > signature_size || count >
            (super_length - 12) / 8) {
            valid = 0;
        } else {
            for (uint32_t index = 0; index < count; index++) {
                uint32_t offset = read_u32(signature + 12 + index * 8 + 4,
                                           0);
                if (offset + 8 > super_length ||
                    read_u32(signature + offset, 0) != 0xfade0c02)
                    continue;
                found = 1;
                if (!code_directory_pages_are_valid(
                        fd, slice_offset, slice_size, signature + offset,
                        super_length - offset)) {
                    valid = 0;
                    break;
                }
            }
        }
    }
    free(signature);
    return valid && found;
}

static int code_signature_pages_are_valid(const char *path) {
    struct stat before, after;
    if (stat(path, &before) != 0 || !S_ISREG(before.st_mode)) return 0;
    if (validated_signature_cached(path, &before)) return 1;
    int fd = open(path, O_RDONLY);
    if (fd < 0) return 0;
    uint8_t header[8];
    int valid = pread_all(fd, header, sizeof(header), 0);
    if (valid && (memcmp(header, "\xca\xfe\xba\xbe", 4) == 0 ||
                  memcmp(header, "\xca\xfe\xba\xbf", 4) == 0 ||
                  memcmp(header, "\xbe\xba\xfe\xca", 4) == 0 ||
                  memcmp(header, "\xbf\xba\xfe\xca", 4) == 0)) {
        int little_endian = header[0] == 0xbe || header[0] == 0xbf;
        int fat64 = header[3] == 0xbf || header[0] == 0xbf;
        uint32_t count = read_u32(header + 4, little_endian);
        size_t entry_size = fat64 ? 32 : 20;
        if (count == 0 || count > 64) valid = 0;
        size_t table_size = valid ? (size_t)count * entry_size : 0;
        uint8_t *table = valid ? malloc(table_size) : NULL;
        if (valid && (!table || !pread_all(fd, table, table_size, 8)))
            valid = 0;
        for (uint32_t index = 0; valid && index < count; index++) {
            const uint8_t *entry = table + (size_t)index * entry_size;
            uint64_t offset = fat64 ? read_u64(entry + 8, little_endian)
                                    : read_u32(entry + 8, little_endian);
            uint64_t size = fat64 ? read_u64(entry + 16, little_endian)
                                  : read_u32(entry + 12, little_endian);
            if (offset > (uint64_t)before.st_size ||
                size > (uint64_t)before.st_size - offset ||
                !macho_slice_signature_is_valid(fd, offset, size))
                valid = 0;
        }
        free(table);
    } else if (valid) {
        valid = macho_slice_signature_is_valid(fd, 0,
                                                (uint64_t)before.st_size);
    }
    valid = valid && fstat(fd, &after) == 0 && stat_matches(&before, &after);
    close(fd);
    if (valid) cache_validated_signature(path, &after);
    return valid;
}

// Both matching CodeDirectory page hashes and a current CDHash in Dopamine's
// dynamic trustcache are required witnesses that this exact Mach-O has already
// completed the project's signing policy.
// Preserve it verbatim. Re-running ldid is not semantically idempotent for all
// vendor signatures: Steam 1785799196 runtime-confirmed that a second pass
// grew three child executables by 64 bytes after sign_installed.sh had already
// synchronized package/*.installed, causing the updater to restore them and
// enter a verify/update loop.
static int current_hashes_are_trusted(const char *path) {
    if (!code_signature_pages_are_valid(path)) return 0;
    char hashes[3][128] = {{0}};
    size_t hash_count = 0;
    for (const char **arch = kArches; *arch; arch++) {
        if (cdhash_for_arch(path, *arch, hashes[hash_count],
                            sizeof(hashes[hash_count]))) {
            hash_count++;
        }
    }
    if (hash_count == 0) return 0;

    // 3,500 entries occupy well below this bound on the target. If the
    // inventory ever grows past it, capture() drains the command and the
    // conservative result is a re-sign, never a false trusted decision.
    size_t inventory_size = 1024 * 1024;
    char *inventory = calloc(1, inventory_size);
    if (!inventory) return 0;
    char *const argv[] = {
        (char *)JBCTL, "trustcache", "info", NULL
    };
    int status = capture(argv, inventory, inventory_size);
    if (status != 0) {
        free(inventory);
        return 0;
    }
    for (char *cursor = inventory; *cursor; cursor++) {
        if (*cursor >= 'A' && *cursor <= 'Z')
            *cursor = (char)(*cursor - 'A' + 'a');
    }
    int trusted = 1;
    for (size_t index = 0; index < hash_count; index++) {
        for (char *cursor = hashes[index]; *cursor; cursor++) {
            if (*cursor >= 'A' && *cursor <= 'Z')
                *cursor = (char)(*cursor - 'A' + 'a');
        }
        if (!strstr(inventory, hashes[index])) {
            trusted = 0;
            break;
        }
    }
    free(inventory);
    return trusted;
}

static int normalize_rootfs_path(const char *input, char *output,
                                 size_t output_size) {
    static const char prefix[] = ROOTFS "/";
    if (!input || !output || output_size <= sizeof(ROOTFS) ||
        strncmp(input, prefix, sizeof(prefix) - 1) != 0)
        return 0;

    char copy[PATH_MAX];
    if (strlcpy(copy, input + sizeof(prefix) - 1, sizeof(copy)) >=
        sizeof(copy)) return 0;
    const char *components[PATH_MAX / 2];
    size_t count = 0;
    char *state = NULL;
    for (char *item = strtok_r(copy, "/", &state); item;
         item = strtok_r(NULL, "/", &state)) {
        if (strcmp(item, ".") == 0 || item[0] == '\0') continue;
        if (strcmp(item, "..") == 0) {
            if (count == 0) return 0;
            count--;
            continue;
        }
        if (count >= sizeof(components) / sizeof(components[0])) return 0;
        components[count++] = item;
    }

    size_t used = strlcpy(output, ROOTFS, output_size);
    if (used >= output_size) return 0;
    for (size_t index = 0; index < count; index++) {
        if (strlcat(output, "/", output_size) >= output_size ||
            strlcat(output, components[index], output_size) >= output_size)
            return 0;
    }
    return count != 0;
}

static int package_dependency_path(const char *path) {
    static const char *prefixes[] = {
        ROOTFS "/opt/local/",
        ROOTFS "/opt/homebrew/",
        ROOTFS "/usr/local/",
        NULL
    };
    for (const char **prefix = prefixes; *prefix; prefix++) {
        if (strncmp(path, *prefix, strlen(*prefix)) == 0) return 1;
    }
    return 0;
}

static int directory_for_path(const char *path, char *directory,
                              size_t directory_size) {
    if (!path || strlcpy(directory, path, directory_size) >= directory_size)
        return 0;
    char *slash = strrchr(directory, '/');
    if (!slash || slash == directory) return 0;
    *slash = '\0';
    return 1;
}

static int expand_loader_token(const char *value, const char *loader_dir,
                               const char *executable_dir, char *expanded,
                               size_t expanded_size) {
    static const char loader[] = "@loader_path";
    static const char executable[] = "@executable_path";
    const char *base = NULL;
    const char *suffix = NULL;
    if (strncmp(value, loader, sizeof(loader) - 1) == 0) {
        base = loader_dir;
        suffix = value + sizeof(loader) - 1;
    } else if (strncmp(value, executable, sizeof(executable) - 1) == 0) {
        base = executable_dir;
        suffix = value + sizeof(executable) - 1;
    } else if (value[0] == '/') {
        int length = snprintf(expanded, expanded_size, "%s%s", ROOTFS,
                              value);
        return length > 0 && (size_t)length < expanded_size;
    } else {
        return 0;
    }
    if (!base || !suffix || (*suffix && *suffix != '/')) return 0;
    int length = snprintf(expanded, expanded_size, "%s%s", base, suffix);
    return length > 0 && (size_t)length < expanded_size;
}

enum { MAX_MACHO_RPATHS = 64, MAX_MACHO_DEPENDENCIES = 512 };

static size_t load_rpaths(const char *path, const char *loader_dir,
                          const char *executable_dir,
                          char rpaths[MAX_MACHO_RPATHS][PATH_MAX]) {
    const size_t output_size = 128 * 1024;
    char *output = calloc(1, output_size);
    if (!output) return 0;
    char *const argv[] = { (char *)OTOOL, "-l", (char *)path, NULL };
    if (capture_macho_metadata(argv, path, output, output_size) != 0) {
        free(output);
        return 0;
    }
    size_t count = 0;
    int expecting_path = 0;
    char *state = NULL;
    for (char *line = strtok_r(output, "\n", &state); line;
         line = strtok_r(NULL, "\n", &state)) {
        while (*line == ' ' || *line == '\t') line++;
        if (strcmp(line, "cmd LC_RPATH") == 0) {
            expecting_path = 1;
            continue;
        }
        if (!expecting_path || strncmp(line, "path ", 5) != 0) continue;
        expecting_path = 0;
        char *value = line + 5;
        char *offset = strstr(value, " (offset ");
        if (offset) *offset = '\0';
        char candidate[PATH_MAX];
        if (count < MAX_MACHO_RPATHS &&
            expand_loader_token(value, loader_dir, executable_dir,
                                candidate, sizeof(candidate)) &&
            normalize_rootfs_path(candidate, rpaths[count], PATH_MAX)) {
            count++;
        }
    }
    free(output);
    return count;
}

static void process_path_recursive(const char *realpath,
                                   const char *executable_dir,
                                   unsigned depth, int dependency);

static void process_dependencies(const char *realpath,
                                 const char *executable_dir,
                                 unsigned depth) {
    if (depth >= 32) {
        logmsg("dependency closure exceeded depth limit at: %s", realpath);
        return;
    }
    char loader_dir[PATH_MAX];
    if (!directory_for_path(realpath, loader_dir, sizeof(loader_dir))) return;

    char (*rpaths)[PATH_MAX] = calloc(MAX_MACHO_RPATHS, PATH_MAX);
    const size_t output_size = 256 * 1024;
    char *output = calloc(1, output_size);
    if (!rpaths || !output) {
        free(rpaths);
        free(output);
        logmsg("dependency closure allocation failed at: %s", realpath);
        return;
    }
    size_t rpath_count = load_rpaths(realpath, loader_dir, executable_dir,
                                     rpaths);
    char *const argv[] = { (char *)OTOOL, "-L", (char *)realpath, NULL };
    if (capture_macho_metadata(argv, realpath, output, output_size) != 0) {
        free(output);
        free(rpaths);
        return;
    }

    size_t dependency_count = 0;
    char *state = NULL;
    for (char *line = strtok_r(output, "\n", &state); line;
         line = strtok_r(NULL, "\n", &state)) {
        if (dependency_count >= MAX_MACHO_DEPENDENCIES) {
            logmsg("dependency closure exceeded image limit at: %s",
                   realpath);
            break;
        }
        while (*line == ' ' || *line == '\t') line++;
        char *metadata = strstr(line, " (compatibility version ");
        if (!metadata) continue;
        *metadata = '\0';

        char candidate[PATH_MAX] = {0};
        char normalized[PATH_MAX] = {0};
        int resolved = 0;
        if (strncmp(line, "@rpath/", 7) == 0) {
            for (size_t index = 0; index < rpath_count; index++) {
                int length = snprintf(candidate, sizeof(candidate), "%s/%s",
                                      rpaths[index], line + 7);
                if (length <= 0 || (size_t)length >= sizeof(candidate) ||
                    !normalize_rootfs_path(candidate, normalized,
                                           sizeof(normalized))) continue;
                struct stat st;
                if (stat(normalized, &st) == 0 && S_ISREG(st.st_mode)) {
                    resolved = 1;
                    break;
                }
            }
        } else if (expand_loader_token(line, loader_dir, executable_dir,
                                       candidate, sizeof(candidate)) &&
                   normalize_rootfs_path(candidate, normalized,
                                          sizeof(normalized))) {
            resolved = 1;
        }
        if (!resolved || !package_dependency_path(normalized)) continue;
        struct stat st;
        if (stat(normalized, &st) != 0 || !S_ISREG(st.st_mode)) continue;
        dependency_count++;
        process_path_recursive(normalized, executable_dir, depth + 1, 1);
    }
    free(output);
    free(rpaths);
}

// Ad-hoc re-sign + trustcache every Mach-O slice of one rootfs path and the
// package-manager dependency closure dyld will load for it. Runtime-confirmed
// with Terminal's /opt/local/bin/htop: its executable CDHash was trusted, but
// dyld rejected /opt/local/lib/libncurses.6.dylib as "code signature invalid".
// Signing only the exec target therefore cannot establish the launch invariant.
static void process_path_recursive(const char *realpath,
                                   const char *executable_dir,
                                   unsigned depth, int dependency) {
    if (seen(realpath)) return;

    struct stat st;
    if (stat(realpath, &st) != 0 || !S_ISREG(st.st_mode)) { mark_seen(realpath); return; }

    // Mark before walking LC_LOAD_DYLIB edges so cyclic dylib graphs terminate.
    // Dependencies are still checked even when this image's current CDHash is
    // trusted: that exact htop/main + libncurses/untrusted split is the failure
    // this closure repairs.
    mark_seen(realpath);
    process_dependencies(realpath, executable_dir, depth);

    if (current_hashes_are_trusted(realpath)) {
        logmsg("preserved already-trusted signature: %s", realpath);
        return;
    }

    // An untrusted Apple/vendor signature still needs conversion: iOS AMFI can
    // reject it despite a newly added stock CDHash because its platform and
    // library-validation policy is not the MacWS chroot policy. Re-sign once,
    // then register the resulting hashes. Subsequent requests take the exact
    // trusted-byte fast path above.
    int sign_status = adhoc_sign(realpath, dependency);
    if (sign_status != 0) {
        logmsg("ad-hoc signing failed status=%d: %s", sign_status, realpath);
        return;
    }
    // ldid can grow LC_CODE_SIGNATURE while signing a transformed thin image;
    // the first pass then describes the pre-growth layout. Validate the actual
    // code pages before trusting the CDHash and give that known layout case one
    // bounded settling pass.
    if (!code_signature_pages_are_valid(realpath)) {
        logmsg("first signing pass left invalid page hashes; retrying: %s",
               realpath);
        sign_status = adhoc_sign(realpath, dependency);
        if (sign_status != 0 || !code_signature_pages_are_valid(realpath)) {
            logmsg("ad-hoc signature validation failed status=%d: %s",
                   sign_status, realpath);
            return;
        }
    }

    char hash[128];
    int added = 0;
    for (const char **a = kArches; *a; a++) {
        if (cdhash_for_arch(realpath, *a, hash, sizeof(hash))) {
            trustcache_add(hash);
            added++;
        }
    }
    if (added) logmsg("signed+trusted (%d slice%s): %s", added, added == 1 ? "" : "s", realpath);
}

static void process_path(const char *realpath) {
    char executable_dir[PATH_MAX];
    if (!directory_for_path(realpath, executable_dir, sizeof(executable_dir)))
        return;
    process_path_recursive(realpath, executable_dir, 0, 0);
}

// Map a chroot-absolute path to its rootfs location and process it.
static void handle_request(const char *chroot_path) {
    if (chroot_path[0] != '/') return;          // only absolute paths
    char real[1024];
    int n = snprintf(real, sizeof(real), "%s%s", ROOTFS, chroot_path);
    if (n <= 0 || (size_t)n >= sizeof(real)) return;
    // g_seen terminates cycles within one Mach-O dependency graph. It cannot
    // be a process-lifetime pathname cache: package managers replace bytes at
    // the same path, and a later exec must evaluate the replacement's CDHash.
    clear_seen();
    process_path(real);
    clear_seen();
}

static int mark_peer_debugged(pid_t peer_pid) {
    if (peer_pid <= 1) return 0;
    char pid_string[32];
    int length = snprintf(pid_string, sizeof(pid_string), "%d", peer_pid);
    if (length <= 0 || (size_t)length >= sizeof(pid_string)) return 0;
    char output[512] = {0};
    char *const argv[] = {
        (char *)JBCTL, "proc_set_debugged", pid_string, NULL
    };
    int status = capture(argv, output, sizeof(output));
    if (status != 0) {
        logmsg("proc_set_debugged pid=%d failed status=%d output=%s",
               peer_pid, status, output[0] ? output : "(empty)");
        return 0;
    }
    return 1;
}

static int probe_server(void) {
    int socket_fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (socket_fd < 0) return 1;
    struct sockaddr_un address = {0};
    address.sun_family = AF_UNIX;
    strncpy(address.sun_path, SOCK_PATH, sizeof(address.sun_path) - 1);
    if (connect(socket_fd, (struct sockaddr *)&address, sizeof(address)) != 0) {
        close(socket_fd);
        return 1;
    }
    static const char request[] = "PING\n";
    if (write(socket_fd, request, sizeof(request) - 1) !=
        (ssize_t)(sizeof(request) - 1)) {
        close(socket_fd);
        return 1;
    }
    char reply[4] = {0};
    size_t offset = 0;
    while (offset < 3) {
        ssize_t count = read(socket_fd, reply + offset, 3 - offset);
        if (count > 0) {
            offset += (size_t)count;
            continue;
        }
        if (count < 0 && errno == EINTR) continue;
        close(socket_fd);
        return 1;
    }
    close(socket_fd);
    return memcmp(reply, "OK\n", 3) == 0 ? 0 : 1;
}

int main(int argc, char *argv[]) {
    signal(SIGPIPE, SIG_IGN);
    signal(SIGCHLD, SIG_DFL);

    if (argc == 2 && strcmp(argv[1], "--probe") == 0)
        return probe_server();

    // The lifecycle helper is the normal owner, but package configuration and
    // GUI recovery can overlap. Runtime on 2026-08-30 found one live autosignd
    // whose pathname had disappeared; every ViewBridge child then failed its
    // JIT handshake with connect_errno=61. Source inspection confirms that a
    // second daemon unconditionally unlinked SOCK_PATH before binding. Hold a
    // process-lifetime lock before touching that name so no concurrent direct
    // launch can detach the active listener from the filesystem namespace.
    int lock_fd = open(LOCK_PATH, O_CREAT | O_RDWR, 0600);
    if (lock_fd < 0 || flock(lock_fd, LOCK_EX | LOCK_NB) != 0) {
        logmsg("singleton lock %s: %s", LOCK_PATH, strerror(errno));
        if (lock_fd >= 0) close(lock_fd);
        return 2;
    }
    (void)ftruncate(lock_fd, 0);
    dprintf(lock_fd, "%d\n", getpid());

    unlink(SOCK_PATH);
    int s = socket(AF_UNIX, SOCK_STREAM, 0);
    if (s < 0) { logmsg("socket: %s", strerror(errno)); return 1; }

    struct sockaddr_un addr = {0};
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, SOCK_PATH, sizeof(addr.sun_path) - 1);
    if (bind(s, (struct sockaddr *)&addr, sizeof(addr)) != 0) {
        logmsg("bind %s: %s", SOCK_PATH, strerror(errno));
        return 1;
    }
    chmod(SOCK_PATH, 0666);   // allow any chroot uid to connect
    if (listen(s, 64) != 0) { logmsg("listen: %s", strerror(errno)); return 1; }
    logmsg("autosignd listening on %s", SOCK_PATH);

    for (;;) {
        int c = accept(s, NULL, NULL);
        if (c < 0) { if (errno == EINTR) continue; break; }

        char buf[1024];
        size_t off = 0;
        ssize_t r;
        while (off < sizeof(buf) - 1 && (r = read(c, buf + off, sizeof(buf) - 1 - off)) > 0) {
            off += (size_t)r;
            if (memchr(buf, '\n', off)) break;
        }
        buf[off] = '\0';
        char *nl = strchr(buf, '\n');
        if (nl) *nl = '\0';

        int ok = 1;
        if (strcmp(buf, "DEBUG") == 0) {
            pid_t peer_pid = -1;
            socklen_t peer_length = sizeof(peer_pid);
            if (getsockopt(c, SOL_LOCAL, LOCAL_PEERPID,
                           &peer_pid, &peer_length) != 0 ||
                peer_length != sizeof(peer_pid)) {
                logmsg("LOCAL_PEERPID failed: %s", strerror(errno));
                ok = 0;
            } else {
                ok = mark_peer_debugged(peer_pid);
            }
        } else if (strcmp(buf, "PING") == 0) {
            // The reply below is the complete liveness contract.
        } else if (buf[0]) {
            handle_request(buf);
        }

        write(c, ok ? "OK\n" : "ERR\n", ok ? 3 : 4);
        close(c);
    }
    close(s);
    unlink(SOCK_PATH);
    close(lock_fd);
    return 0;
}

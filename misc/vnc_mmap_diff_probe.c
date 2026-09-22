// Read-only diagnostic for the native-AGX VNC shared framebuffer.
//
// Captures seqlock-stable generations under the producer's advisory flock and
// reports exact changed-pixel and 16x16 dirty-tile counts.  This distinguishes
// genuinely dense WindowServer output from a sparse update whose one bounding
// rectangle makes OSXvnc encode mostly unchanged pixels.

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

enum { kHeaderBytes = 16, kTile = 16 };

static uint64_t monotonic_ns(void) {
    struct timespec value = {0};
    if (clock_gettime(CLOCK_MONOTONIC, &value) != 0) return 0;
    return (uint64_t)value.tv_sec * 1000000000ull +
        (uint64_t)value.tv_nsec;
}

static int capture_generation(int fd, const void *mapping, size_t map_size,
        uint32_t width, uint32_t height, uint32_t stride,
        uint64_t prior_sequence, uint32_t *destination,
        uint64_t *captured_sequence) {
    size_t pixel_bytes = (size_t)stride * height;
    const uint8_t *pixels = (const uint8_t *)mapping + kHeaderBytes;
    const volatile uint64_t *sequence = (const volatile uint64_t *)(
        pixels + pixel_bytes);
    if ((const uint8_t *)(sequence + 1) >
        (const uint8_t *)mapping + map_size) return EINVAL;

    for (;;) {
        uint64_t candidate = __atomic_load_n(sequence, __ATOMIC_ACQUIRE);
        if (candidate != 0 && !(candidate & 1u) &&
            candidate != prior_sequence) {
            if (flock(fd, LOCK_SH) != 0) return errno;
            uint64_t before = __atomic_load_n(sequence, __ATOMIC_ACQUIRE);
            if (before != 0 && !(before & 1u) &&
                before != prior_sequence) {
                for (uint32_t y = 0; y < height; y++) {
                    memcpy(destination + (size_t)y * width,
                           pixels + (size_t)y * stride,
                           (size_t)width * sizeof(uint32_t));
                }
                uint64_t after = __atomic_load_n(sequence, __ATOMIC_ACQUIRE);
                (void)flock(fd, LOCK_UN);
                if (before == after && !(after & 1u)) {
                    *captured_sequence = after;
                    return 0;
                }
            } else {
                (void)flock(fd, LOCK_UN);
            }
        }
        usleep(1000);
    }
}

int main(int argc, char **argv) {
    const char *path = argc > 1 ? argv[1] :
        "/var/mnt/rootfs/private/tmp/macws_vnc_fb";
    unsigned pairs = argc > 2 ? (unsigned)strtoul(argv[2], NULL, 0) : 12;
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) {
        fprintf(stderr, "open %s: %s\n", path, strerror(errno));
        return 1;
    }
    struct stat status = {0};
    if (fstat(fd, &status) != 0 || status.st_size < 32) {
        fprintf(stderr, "fstat/size: %s\n", strerror(errno));
        return 1;
    }
    size_t map_size = (size_t)status.st_size;
    const void *mapping = mmap(NULL, map_size, PROT_READ, MAP_SHARED, fd, 0);
    if (mapping == MAP_FAILED) {
        fprintf(stderr, "mmap: %s\n", strerror(errno));
        return 1;
    }
    const uint32_t *header = mapping;
    uint32_t width = header[1], height = header[2], stride = header[3];
    if (header[0] != 0x564e4346u || width == 0 || height == 0 ||
        stride < width * 4u || width > 8192 || height > 8192) {
        fprintf(stderr, "invalid header magic=%#x %ux%u stride=%u\n",
                header[0], width, height, stride);
        return 1;
    }
    size_t pixel_count = (size_t)width * height;
    uint32_t *prior = malloc(pixel_count * sizeof(*prior));
    uint32_t *current = malloc(pixel_count * sizeof(*current));
    size_t tile_columns = (width + kTile - 1u) / kTile;
    size_t tile_rows = (height + kTile - 1u) / kTile;
    uint8_t *dirty_tiles = calloc(tile_columns * tile_rows, 1);
    if (!prior || !current || !dirty_tiles) {
        fprintf(stderr, "allocation failed\n");
        return 1;
    }

    uint64_t sequence = 0;
    int error = capture_generation(fd, mapping, map_size, width, height,
                                   stride, 0, prior, &sequence);
    if (error != 0) {
        fprintf(stderr, "initial capture: %s\n", strerror(error));
        return 1;
    }
    printf("VNC_DIFF start=%" PRIu64 " size=%ux%u tiles=%zux%zu\n",
           sequence, width, height, tile_columns, tile_rows);
    fflush(stdout);

    uint64_t prior_time = monotonic_ns();
    for (unsigned sample = 1; sample <= pairs; sample++) {
        uint64_t next_sequence = 0;
        error = capture_generation(fd, mapping, map_size, width, height,
                                   stride, sequence, current,
                                   &next_sequence);
        if (error != 0) {
            fprintf(stderr, "capture: %s\n", strerror(error));
            return 1;
        }
        uint64_t now = monotonic_ns();
        memset(dirty_tiles, 0, tile_columns * tile_rows);
        uint64_t changed_pixels = 0;
        uint32_t min_x = width, min_y = height, max_x = 0, max_y = 0;
        for (uint32_t y = 0; y < height; y++) {
            const uint32_t *a = prior + (size_t)y * width;
            const uint32_t *b = current + (size_t)y * width;
            for (uint32_t x = 0; x < width; x++) {
                if (a[x] == b[x]) continue;
                changed_pixels++;
                if (x < min_x) min_x = x;
                if (y < min_y) min_y = y;
                if (x + 1u > max_x) max_x = x + 1u;
                if (y + 1u > max_y) max_y = y + 1u;
                dirty_tiles[(size_t)(y / kTile) * tile_columns +
                            x / kTile] = 1;
            }
        }
        uint64_t dirty_tile_count = 0;
        for (size_t tile = 0; tile < tile_columns * tile_rows; tile++)
            dirty_tile_count += dirty_tiles[tile] != 0;
        uint64_t bounding_pixels = changed_pixels == 0 ? 0 :
            (uint64_t)(max_x - min_x) * (max_y - min_y);
        printf("VNC_DIFF sample=%u sequence=%" PRIu64
               " delta_ms=%.3f changed=%" PRIu64 " (%.3f%%)"
               " dirty_tiles=%" PRIu64 "/%zu (%.3f%%)"
               " bounds=%u,%u %ux%u bounding_pixels=%" PRIu64 "\n",
               sample, next_sequence,
               prior_time && now > prior_time ?
                   (double)(now - prior_time) / 1000000.0 : 0.0,
               changed_pixels, 100.0 * changed_pixels / pixel_count,
               dirty_tile_count, tile_columns * tile_rows,
               100.0 * dirty_tile_count / (tile_columns * tile_rows),
               changed_pixels ? min_x : 0, changed_pixels ? min_y : 0,
               changed_pixels ? max_x - min_x : 0,
               changed_pixels ? max_y - min_y : 0, bounding_pixels);
        fflush(stdout);
        uint32_t *temporary = prior;
        prior = current;
        current = temporary;
        sequence = next_sequence;
        prior_time = now;
    }
    return 0;
}

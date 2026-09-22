#include <errno.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/fsgetpath.h>
#include <sys/mount.h>
#include <sys/stat.h>

static void probe(const char *label, fsid_t fsid, uint64_t object_id) {
    char path[4096] = {0};
    errno = 0;
    ssize_t length = fsgetpath(path, sizeof(path), &fsid, object_id);
    printf("%s fsid=%d,%d object=%" PRIu64 " result=%zd errno=%d path=%s\n",
           label, fsid.val[0], fsid.val[1], object_id, length, errno,
           length >= 0 ? path : "");
}

int main(int argc, const char **argv) {
    if (argc != 3) {
        fprintf(stderr, "usage: %s volume-id object-id\n", argv[0]);
        return 2;
    }
    int32_t volume_id = (int32_t)strtol(argv[1], NULL, 10);
    uint64_t object_id = strtoull(argv[2], NULL, 10);
    struct statfs filesystem = {0};
    struct stat root = {0};
    if (statfs("/", &filesystem) != 0 || stat("/", &root) != 0) {
        perror("statfs/stat");
        return 1;
    }
    printf("root fsid=%d,%d st_dev=%d inode=%" PRIu64 "\n",
           filesystem.f_fsid.val[0], filesystem.f_fsid.val[1], root.st_dev,
           (uint64_t)root.st_ino);
    probe("root-fsid", filesystem.f_fsid, object_id);
    fsid_t encoded_zero = {{volume_id, 0}};
    probe("encoded-zero", encoded_zero, object_id);
    fsid_t encoded_root = {{volume_id, filesystem.f_fsid.val[1]}};
    probe("encoded-root", encoded_root, object_id);
    return 0;
}

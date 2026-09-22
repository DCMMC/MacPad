// mountdevfs — mount a fresh devfs at the given mountpoint (default the chroot
// /dev). The chrooted macOS rootfs otherwise has no /dev/ptmx, so pty-based
// programs fail: Terminal.app's forkpty() -> open("/dev/ptmx") returns ENOENT
// ("forkpty: No such file or directory") and no shell can spawn.
//
// Why a dedicated iOS tool instead of the obvious alternatives:
//   * mount_bindfs (the project's bind helper) mounts READ-ONLY, so /dev/ptmx
//     can't be opened O_RDWR (EROFS) even though the node is exposed.
//   * the macOS /sbin/mount_devfs run inside the chroot is EPERM'd — the iOS
//     kernel denies mount(2) to a chrooted macOS-platform process (even with
//     the project entitlements).
//   * iOS has no devfs mount helper, so `mount -t devfs` can't find
//     mount_devfs and fails.
// An iOS-native binary that calls mount(2) directly, trustcached so it runs as
// a platform binary, is permitted to mount devfs (same as mount_bindfs).
//
// Idempotent: if the mountpoint is already a devfs, it does nothing, so
// postinst.sh can call it on every (re)install.
#include <sys/mount.h>
#include <sys/param.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>

static int already_devfs(const char *path) {
    struct statfs sfs;
    if (statfs(path, &sfs) != 0) return 0;
    return strcmp(sfs.f_fstypename, "devfs") == 0;
}

int main(int argc, char **argv) {
    const char *mp = (argc >= 2) ? argv[1] : "/var/mnt/rootfs/dev";
    if (already_devfs(mp)) {
        printf("devfs already mounted at %s\n", mp);
        return 0;
    }
    if (mount("devfs", mp, 0, NULL) != 0) {
        fprintf(stderr, "mount devfs %s: %s\n", mp, strerror(errno));
        return 1;
    }
    printf("mounted devfs at %s\n", mp);
    return 0;
}

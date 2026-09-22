#ifndef MACWS_CHROOT_IDENTITY_H
#define MACWS_CHROOT_IDENTITY_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/mount.h>

// Darwin's versioned PROC_PIDVNODEPATHINFO record. iPhoneOS16.5 omits the
// private libproc header; validate all used offsets against Apple's actual
// struct whenever it is present (including the executable host fixtures).
enum { MacWSRootVnodeFlavor = 9, MacWSRootVnodeRecordSize = 2352,
       MacWSRootVnodeDeviceOffset = 1176, MacWSRootVnodeModeOffset = 1180,
       MacWSRootVnodeInodeOffset = 1184, MacWSRootVnodeFSIDOffset = 1320,
       MacWSRootVnodePathOffset = 1328, MacWSRootVnodePathSize = 1024 };
#if __has_include(<sys/proc_info.h>)
#include <sys/proc_info.h>
#ifdef __cplusplus
#define MACWS_ROOT_ASSERT static_assert
#else
#define MACWS_ROOT_ASSERT _Static_assert
#endif
MACWS_ROOT_ASSERT(sizeof(struct proc_vnodepathinfo) == MacWSRootVnodeRecordSize, "root vnode record ABI");
MACWS_ROOT_ASSERT(offsetof(struct proc_vnodepathinfo, pvi_rdir.vip_vi.vi_stat.vst_dev) == MacWSRootVnodeDeviceOffset, "root vnode device ABI");
MACWS_ROOT_ASSERT(offsetof(struct proc_vnodepathinfo, pvi_rdir.vip_vi.vi_stat.vst_mode) == MacWSRootVnodeModeOffset, "root vnode mode ABI");
MACWS_ROOT_ASSERT(offsetof(struct proc_vnodepathinfo, pvi_rdir.vip_vi.vi_stat.vst_ino) == MacWSRootVnodeInodeOffset, "root vnode inode ABI");
MACWS_ROOT_ASSERT(offsetof(struct proc_vnodepathinfo, pvi_rdir.vip_vi.vi_fsid) == MacWSRootVnodeFSIDOffset, "root vnode fsid ABI");
MACWS_ROOT_ASSERT(offsetof(struct proc_vnodepathinfo, pvi_rdir.vip_path) == MacWSRootVnodePathOffset, "root vnode path ABI");
#undef MACWS_ROOT_ASSERT
#endif

typedef struct { unsigned char bytes[MacWSRootVnodeRecordSize]; } MacWSRootVnodeRecord;

static inline bool MacWSRootVnodeMatchesProcessRoot(
    const MacWSRootVnodeRecord *record, int received,
    const struct stat *root, const struct statfs *filesystem) {
    if (!record || received != MacWSRootVnodeRecordSize || !root ||
        !filesystem || !S_ISDIR(root->st_mode)) return false;
    uint32_t device = 0;
    uint16_t mode = 0;
    uint64_t inode = 0;
    fsid_t fsid = {{0, 0}};
    memcpy(&device, record->bytes + MacWSRootVnodeDeviceOffset, sizeof(device));
    memcpy(&mode, record->bytes + MacWSRootVnodeModeOffset, sizeof(mode));
    memcpy(&inode, record->bytes + MacWSRootVnodeInodeOffset, sizeof(inode));
    memcpy(&fsid, record->bytes + MacWSRootVnodeFSIDOffset, sizeof(fsid));
    const char *path = (const char *)record->bytes + MacWSRootVnodePathOffset;
    // Native non-chroot processes have an empty rdir, not a root that may be
    // inferred from an environment string. Reject partial/corrupt identities.
    return device && inode && S_ISDIR(mode) && path[0] == '/' &&
        memchr(path, '\0', MacWSRootVnodePathSize) &&
        device == (uint32_t)root->st_dev && inode == (uint64_t)root->st_ino &&
        (fsid.val[0] || fsid.val[1]) &&
        fsid.val[0] == filesystem->f_fsid.val[0] &&
        fsid.val[1] == filesystem->f_fsid.val[1];
}

#endif

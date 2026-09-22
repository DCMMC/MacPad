#include "macws_chroot_identity.h"
#include <assert.h>
#include <stdio.h>

int main(void) {
    MacWSRootVnodeRecord record = {{0}}, good;
    struct stat root = {0}; struct statfs filesystem = {0};
    root.st_dev = 17; root.st_ino = 1234; root.st_mode = S_IFDIR | 0755;
    filesystem.f_fsid.val[0] = 17; filesystem.f_fsid.val[1] = 26;
    assert(!MacWSRootVnodeMatchesProcessRoot(&record, sizeof(record), &root, &filesystem));
    uint32_t dev = 17; uint16_t mode = S_IFDIR | 0755; uint64_t ino = 1234;
    memcpy(record.bytes + MacWSRootVnodeDeviceOffset, &dev, sizeof(dev));
    memcpy(record.bytes + MacWSRootVnodeModeOffset, &mode, sizeof(mode));
    memcpy(record.bytes + MacWSRootVnodeInodeOffset, &ino, sizeof(ino));
    memcpy(record.bytes + MacWSRootVnodeFSIDOffset, &filesystem.f_fsid, sizeof(fsid_t));
    strcpy((char *)record.bytes + MacWSRootVnodePathOffset, "/");
    good = record;
    assert(MacWSRootVnodeMatchesProcessRoot(&record, sizeof(record), &root, &filesystem));
    assert(!MacWSRootVnodeMatchesProcessRoot(&record, 0, &root, &filesystem));
    assert(!MacWSRootVnodeMatchesProcessRoot(&record, -1, &root, &filesystem));
    assert(!MacWSRootVnodeMatchesProcessRoot(&record, sizeof(record)-1, &root, &filesystem));
    assert(!MacWSRootVnodeMatchesProcessRoot(&record, sizeof(record)+1, &root, &filesystem));
    assert(!MacWSRootVnodeMatchesProcessRoot(NULL, sizeof(record), &root, &filesystem));
    assert(!MacWSRootVnodeMatchesProcessRoot(&record, sizeof(record), NULL, &filesystem));
    assert(!MacWSRootVnodeMatchesProcessRoot(&record, sizeof(record), &root, NULL));
    const size_t mismatches[] = {MacWSRootVnodeDeviceOffset, MacWSRootVnodeInodeOffset,
                                 MacWSRootVnodeFSIDOffset, MacWSRootVnodeFSIDOffset+4};
    for (size_t i=0;i<sizeof(mismatches)/sizeof(mismatches[0]);i++) {
        record = good; record.bytes[mismatches[i]] ^= 1;
        assert(!MacWSRootVnodeMatchesProcessRoot(&record, sizeof(record), &root, &filesystem));
    }
    record = good; mode = S_IFREG | 0755;
    memcpy(record.bytes + MacWSRootVnodeModeOffset, &mode, sizeof(mode));
    assert(!MacWSRootVnodeMatchesProcessRoot(&record, sizeof(record), &root, &filesystem));
    record = good; root.st_mode = S_IFREG | 0755;
    assert(!MacWSRootVnodeMatchesProcessRoot(&record, sizeof(record), &root, &filesystem));
    root.st_mode = S_IFDIR | 0755;
    record = good; record.bytes[MacWSRootVnodePathOffset] = '\0';
    assert(!MacWSRootVnodeMatchesProcessRoot(&record, sizeof(record), &root, &filesystem));
    record = good; record.bytes[MacWSRootVnodePathOffset] = 'x';
    assert(!MacWSRootVnodeMatchesProcessRoot(&record, sizeof(record), &root, &filesystem));
    record = good; memset(record.bytes + MacWSRootVnodePathOffset, '/', MacWSRootVnodePathSize);
    assert(!MacWSRootVnodeMatchesProcessRoot(&record, sizeof(record), &root, &filesystem));
    puts("kernel self-root identity contract PASS");
    return 0;
}

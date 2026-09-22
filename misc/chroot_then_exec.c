// chroot_then_exec.c — chroot(root) + exec(child) — mimics launchdchrootexec
// minus libmachook injection and posix_spawn attrs.
//
//   $0 <chroot_target> <child_binary>
//
// Used to find which part of launchdchrootexec breaks sel=0x9.
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char **argv) {
    if (argc < 3) {
        fprintf(stderr, "Usage: %s <chroot_target> <child_binary>\n", argv[0]);
        return 1;
    }
    const char *root = argv[1];
    const char *child = argv[2];
    if (chroot(root) < 0) { perror("chroot"); return 1; }
    fprintf(stderr, "chrooted to %s, exec'ing %s\n", root, child);
    execl(child, child, NULL);
    perror("execl");
    return 1;
}

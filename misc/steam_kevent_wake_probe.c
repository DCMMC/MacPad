#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/event.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

static const char *const kClientPath =
    "/private/tmp/macws_steam_kevent_probe.sock";
static const char *const kServerPath =
    "/var/mnt/rootfs/private/tmp/macws_steam_kevent_probe.sock";

static double monotonic_seconds(void) {
    struct timespec value = {0};
    if (clock_gettime(CLOCK_MONOTONIC, &value) != 0) return -1.0;
    return (double)value.tv_sec + (double)value.tv_nsec / 1000000000.0;
}

static int make_socket(void) {
    int descriptor = socket(AF_UNIX, SOCK_STREAM, 0);
    if (descriptor < 0) perror("socket");
    return descriptor;
}

static int run_server(void) {
    int listener = make_socket();
    if (listener < 0) return 2;
    unlink(kServerPath);
    struct sockaddr_un address = {0};
    address.sun_family = AF_UNIX;
    strlcpy(address.sun_path, kServerPath, sizeof(address.sun_path));
    if (bind(listener, (const struct sockaddr *)&address, sizeof(address)) ||
        chmod(kServerPath, 0777) || listen(listener, 1)) {
        perror("server setup");
        return 3;
    }
    puts("SERVER ready");
    fflush(stdout);
    int client = accept(listener, NULL, NULL);
    if (client < 0) { perror("accept"); return 4; }
    unsigned char request = 0;
    if (read(client, &request, 1) != 1) { perror("read request"); return 5; }
    usleep(1000000);
    unsigned char reply = 0x5a;
    ssize_t amount = write(client, &reply, 1);
    printf("SERVER write=%zd errno=%d\n", amount, amount == 1 ? 0 : errno);
    close(client);
    close(listener);
    unlink(kServerPath);
    return amount == 1 ? 0 : 6;
}

static int run_client(void) {
    int descriptor = make_socket();
    if (descriptor < 0) return 2;
    struct sockaddr_un address = {0};
    address.sun_family = AF_UNIX;
    strlcpy(address.sun_path, kClientPath, sizeof(address.sun_path));
    if (connect(descriptor, (const struct sockaddr *)&address,
                sizeof(address))) {
        perror("connect");
        return 3;
    }
    unsigned char request = 0xa5;
    if (write(descriptor, &request, 1) != 1) { perror("write request"); return 4; }
    int queue = kqueue();
    if (queue < 0) { perror("kqueue"); return 5; }
    struct kevent change = {0};
    EV_SET(&change, descriptor, EVFILT_READ, EV_ADD | EV_ENABLE, 0, 0, NULL);
    double started = monotonic_seconds();
    struct kevent event = {0};
    int count = kevent(queue, &change, 1, &event, 1, NULL);
    double elapsed = monotonic_seconds() - started;
    if (count != 1) { perror("kevent"); return 6; }
    unsigned char reply = 0;
    ssize_t amount = read(descriptor, &reply, 1);
    printf("CLIENT event_filter=%d event_flags=%#x data=%lld read=%zd "
           "reply=%#x elapsed=%.6f errno=%d\n",
           event.filter, event.flags, (long long)event.data, amount, reply,
           elapsed,
           amount == 1 ? 0 : errno);
    return amount == 1 && reply == 0x5a ? 0 : 7;
}

int main(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "usage: %s server|client\n", argv[0]);
        return 1;
    }
    return strcmp(argv[1], "server") == 0 ? run_server() :
           strcmp(argv[1], "client") == 0 ? run_client() : 1;
}

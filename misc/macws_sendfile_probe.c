// Isolated contract probe: temporary files/socketpairs only, no GUI/services.
// Links the actual replacement below. Never call stock sendfile on iPadOS:
// --stock-reference is only for running this executable on a real macOS host.
#include <assert.h>
#include <TargetConditionals.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/uio.h>
#include <unistd.h>

static int sendInterrupt, sendCalls, readInterrupt, vectorInterrupt, vectorCalls;
static ssize_t ProbeSend(int fd, const void *bytes, size_t size, int flags) {
    if (sendInterrupt && sendCalls++ != 0) { errno = EINTR; return -1; }
    if (sendInterrupt && size > 13) size = 13;
    return send(fd, bytes, size, flags);
}
static ssize_t ProbeRead(int fd, void *bytes, size_t size, off_t offset) {
    if (readInterrupt) { errno = EINTR; return -1; }
    return pread(fd, bytes, size, offset);
}
static ssize_t ProbeWritev(int fd, const struct iovec *vectors, int count) {
    if (!vectorInterrupt) return writev(fd, vectors, count);
    if (vectorCalls++ != 0) { errno = EAGAIN; return -1; }
    assert(count > 0 && vectors[0].iov_len >= 3);
    struct iovec prefix = vectors[0]; prefix.iov_len = 3;
    return writev(fd, &prefix, 1);
}
#define send ProbeSend
#define pread ProbeRead
#define writev ProbeWritev
#define MACWS_SENDFILE_UNIT_TEST 1
#include "../libmachook/Compatibility/MacWSSendfile.c"
#undef send
#undef pread
#undef writev

static int File(const void *bytes, size_t length) {
    char path[] = "/tmp/macws-sendfile-probe.XXXXXX";
    int fd = mkstemp(path); assert(fd >= 0); assert(unlink(path) == 0);
    assert(write(fd, bytes, length) == (ssize_t)length);
    return fd;
}
static void Pair(int sockets[2]) {
    assert(socketpair(AF_UNIX, SOCK_STREAM, 0, sockets) == 0);
}
static void Nonblocking(int fd) {
    assert(fcntl(fd, F_SETFL, fcntl(fd, F_GETFL) | O_NONBLOCK) == 0);
}
static void Receive(int fd, const void *expected, size_t size) {
    unsigned char bytes[4096]; assert(size < sizeof(bytes));
    ssize_t amount = recv(fd, bytes, sizeof(bytes), MSG_DONTWAIT);
    assert(amount == (ssize_t)size && !memcmp(bytes, expected, size));
}
static void NoData(int fd) {
    char byte;
    assert(recv(fd, &byte, 1, MSG_DONTWAIT) == -1 && errno == EAGAIN);
}

static void Basic(void) {
    int input = File("0123456789", 10), sockets[2]; Pair(sockets);
    assert(lseek(input, 7, SEEK_SET) == 7);
    off_t length = 4;
    assert(MacWSSendfile(input, sockets[0], 2, &length, NULL, 0) == 0);
    assert(length == 4 && lseek(input, 0, SEEK_CUR) == 7);
    Receive(sockets[1], "2345", 4);
    length = 0;
    assert(MacWSSendfile(input, sockets[0], 6, &length, NULL, 0) == 0);
    assert(length == 4); Receive(sockets[1], "6789", 4);
    length = 9;
    assert(MacWSSendfile(input, sockets[0], 12, &length, NULL, 0) == 0);
    assert(length == 0); NoData(sockets[1]);
    close(input); close(sockets[0]); close(sockets[1]);
    puts("PASS basic offset/EOF/length/input-position");
}

typedef int (*SendfileFunction)(int, int, off_t, off_t *, struct sf_hdtr *, int);
static void Headers(SendfileFunction function, const char *name) {
    const off_t requests[] = {0, 3, 8, -1};
    const char *expected[] = {"HEAD!23456789TAIL", "HEAD!TAIL", "HEAD!234TAIL", "HEAD!TAIL"};
    for (unsigned i = 0; i < sizeof(requests)/sizeof(requests[0]); ++i) {
        int input = File("0123456789", 10), sockets[2]; Pair(sockets);
        struct iovec head[] = {{"HE", 2}, {"AD!", 3}}, tail = {"TAIL", 4};
        struct sf_hdtr vectors = {head, 2, &tail, 1};
        off_t length = requests[i];
        assert(function(input, sockets[0], 2, &length, &vectors, 0) == 0);
        assert(length == (off_t)strlen(expected[i]));
        Receive(sockets[1], expected[i], strlen(expected[i]));
        assert(head[0].iov_len == 2 && head[1].iov_len == 3 && tail.iov_len == 4);
        close(input); close(sockets[0]); close(sockets[1]);
    }
    printf("PASS %s headers/file-budget/trailers\n", name);
}

static void Errors(void) {
    int input = File("abc", 3), output = File("", 0), sockets[2]; Pair(sockets);
    off_t length = 3;
    assert(MacWSSendfile(input, output, 0, &length, NULL, 0) == -1 && errno == ENOTSOCK);
    assert(length == 0 && lseek(output, 0, SEEK_END) == 0);
    // This is the existing libuv fallback contract: rejected optimization
    // must leave both file offsets/data untouched, then real copy can run.
    char bytes[3]; assert(pread(input, bytes, 3, 0) == 3);
    assert(write(output, bytes, 3) == 3);
    char copied[3]; assert(pread(output, copied, 3, 0) == 3 && !memcmp(copied,"abc",3));
    length = 3;
    assert(MacWSSendfile(-1, sockets[0], 0, &length, NULL, 0) == -1 && errno == EBADF && !length);
    length = 3;
    assert(MacWSSendfile(input, -1, 0, &length, NULL, 0) == -1 && errno == EBADF && !length);
    length = 3;
    assert(MacWSSendfile(input, sockets[0], -1, &length, NULL, 0) == -1 && errno == EINVAL && !length);
    length = 3;
    assert(MacWSSendfile(input, sockets[0], 0, &length, NULL, 1) == -1 && errno == EINVAL && !length);
    assert(MacWSSendfile(input, sockets[0], 0, NULL, NULL, 0) == -1 && errno == EINVAL);
    int disconnected = socket(AF_INET, SOCK_STREAM, 0); assert(disconnected >= 0);
    length = 3;
    assert(MacWSSendfile(input, disconnected, 0, &length, NULL, 0) == -1 && errno == ENOTCONN && !length);
    int pipes[2]; assert(pipe(pipes) == 0); length = 3;
    assert(MacWSSendfile(pipes[0], sockets[0], 0, &length, NULL, 0) == -1 && errno == ENOTSUP && !length);
    int datagrams[2]; assert(socketpair(AF_UNIX, SOCK_DGRAM, 0, datagrams) == 0); length=3;
    assert(MacWSSendfile(input, datagrams[0], 0, &length, NULL, 0) == -1 && errno == EINVAL && !length);
    NoData(sockets[1]);
    close(input); close(output); close(sockets[0]); close(sockets[1]); close(disconnected);
    close(pipes[0]); close(pipes[1]); close(datagrams[0]); close(datagrams[1]);
    puts("PASS descriptor-validation/real-libuv-copy-fallback/no-side-effects");
}

static void Faults(void) {
    int input = File("abc",3), sockets[2]; Pair(sockets); off_t length=3;
    assert(MacWSSendfile(input,sockets[0],0,(off_t *)(uintptr_t)1,NULL,0)==-1 && errno==EFAULT);
    assert(MacWSSendfile(input,sockets[0],0,&length,(struct sf_hdtr *)(uintptr_t)1,0)==-1 && errno==EFAULT && !length);
    struct sf_hdtr vectors = {(struct iovec *)(uintptr_t)1,1,NULL,0}; length=3;
    assert(MacWSSendfile(input,sockets[0],0,&length,&vectors,0)==-1 && errno==EFAULT && !length);
    struct iovec bad = {(void *)(uintptr_t)1,3}; vectors.headers=&bad; length=3;
    assert(MacWSSendfile(input,sockets[0],0,&length,&vectors,0)==-1 && errno==EFAULT && !length);
    vectors.hdr_cnt=-1; length=3;
    assert(MacWSSendfile(input,sockets[0],0,&length,&vectors,0)==-1 && errno==EINVAL && !length);
    vectors.hdr_cnt=IOV_MAX+1; length=3;
    assert(MacWSSendfile(input,sockets[0],0,&length,&vectors,0)==-1 && errno==EINVAL && !length);
    NoData(sockets[1]);
    size_t page=(size_t)getpagesize();
    off_t *readonly=mmap(NULL,page,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);
    assert(readonly!=MAP_FAILED); *readonly=3; assert(mprotect(readonly,page,PROT_READ)==0);
    assert(MacWSSendfile(input,sockets[0],99,readonly,NULL,0)==0 && *readonly==3);
    assert(munmap(readonly,page)==0);
    close(input); close(sockets[0]); close(sockets[1]);
    puts("PASS EFAULT metadata/payload/vector-bounds/best-effort-length-copyout");
}

static void ErrorPriority(SendfileFunction function, const char *name) {
    int input=File("abc",3), sockets[2]; Pair(sockets); off_t length=3;
    assert(function(-1,sockets[0],0,&length,NULL,0)==-1 && errno==EBADF && !length);
    length=3;
    assert(function(input,-1,0,&length,NULL,0)==-1 && errno==EBADF && !length);
    length=3;
    assert(function(input,input,0,&length,NULL,0)==-1 && errno==ENOTSOCK && !length);
    assert(function(-1,sockets[0],0,(off_t *)(uintptr_t)1,NULL,0)==-1 && errno==EBADF);
    assert(function(input,input,0,(off_t *)(uintptr_t)1,NULL,0)==-1 && errno==ENOTSOCK);
    size_t page=(size_t)getpagesize();
    off_t *readonly=mmap(NULL,page,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);
    assert(readonly!=MAP_FAILED); *readonly=3; assert(mprotect(readonly,page,PROT_READ)==0);
    assert(function(input,sockets[0],99,readonly,NULL,0)==0 && *readonly==3);
    assert(function(-1,sockets[0],0,readonly,NULL,0)==-1 && errno==EBADF && *readonly==3);
    assert(munmap(readonly,page)==0); NoData(sockets[1]);
    close(input);close(sockets[0]);close(sockets[1]);
    printf("PASS %s error-priority/early-length-update/readonly-copyout\n",name);
}

static void ClosedPeer(SendfileFunction function, const char *name) {
    for (int scenario=0;scenario<4;++scenario) {
        int input=File("abcdefgh",8), sockets[2]; Pair(sockets);
        struct iovec head={"HEAD",4}, tail={"TAIL",4};
        struct sf_hdtr vectors={NULL,0,NULL,0};
        if (scenario==1 || scenario==3) {vectors.headers=&head;vectors.hdr_cnt=1;}
        if (scenario==2) {vectors.trailers=&tail;vectors.trl_cnt=1;}
        if (scenario==3) {
            int yes=1;
            assert(setsockopt(sockets[0],SOL_SOCKET,SO_NOSIGPIPE,&yes,sizeof(yes))==0);
        }
        assert(close(sockets[1])==0);
        off_t length=8;
        assert(function(input,sockets[0],0,&length,&vectors,0)==-1 &&
               errno==ENOTCONN && !length);
        assert(lseek(input,0,SEEK_CUR)==8);
        close(input);close(sockets[0]);
    }
    printf("PASS %s closed-AF_UNIX-peer/ENOTCONN/no-prefix\n",name);
}

static void Interrupts(void) {
    const char data[]="abcdefghijklmnopqrstuvwxyz";
    int input=File(data,26), sockets[2]; Pair(sockets);
    off_t length=26; sendInterrupt=1; sendCalls=0;
    assert(MacWSSendfile(input,sockets[0],0,&length,NULL,0)==-1 && errno==EINTR && length==13);
    Receive(sockets[1],data,13); sendInterrupt=0; length=13;
    assert(MacWSSendfile(input,sockets[0],13,&length,NULL,0)==0 && length==13);
    Receive(sockets[1],data+13,13);
    readInterrupt=1; length=26;
    assert(MacWSSendfile(input,sockets[0],0,&length,NULL,0)==-1 && errno==EINTR && length==0);
    readInterrupt=0; NoData(sockets[1]);
    struct iovec head={"HEADER",6}, tail={"TRAILER",7};
    struct sf_hdtr vectors={&head,1,&tail,1}; length=0; vectorInterrupt=1; vectorCalls=0;
    assert(MacWSSendfile(input,sockets[0],0,&length,&vectors,0)==-1 && errno==EAGAIN && length==3);
    vectorInterrupt=0; Receive(sockets[1],"HEA",3); NoData(sockets[1]);
    close(input); close(sockets[0]); close(sockets[1]);
    puts("PASS EINTR/partial-prefix/resume/header-EAGAIN/no-hidden-retry");
}

static void NonblockingTransfer(void) {
    enum { Size=196608 }; unsigned char *data=malloc(Size), *copy=malloc(Size);
    assert(data && copy); for (int i=0;i<Size;++i) data[i]=(unsigned char)(i%251);
    int input=File(data,Size), sockets[2]; Pair(sockets); int capacity=4096;
    assert(setsockopt(sockets[0],SOL_SOCKET,SO_SNDBUF,&capacity,sizeof(capacity))==0);
    Nonblocking(sockets[0]); Nonblocking(sockets[1]); assert(lseek(input,37,SEEK_SET)==37);
    size_t sent=0, received=0; int blocked=0, turns=0;
    while (sent<Size) {
        assert(++turns<256); off_t length=Size-(off_t)sent;
        int result=MacWSSendfile(input,sockets[0],(off_t)sent,&length,NULL,0);
        assert(result==0 || (result==-1 && errno==EAGAIN)); blocked += result==-1;
        assert(length>0 && length<=Size-(off_t)sent); sent+=(size_t)length;
        while (received<sent) {
            ssize_t n=recv(sockets[1],copy+received,sent-received,0);
            assert(n>0); received+=(size_t)n;
        }
        NoData(sockets[1]);
    }
    assert(blocked>0 && received==Size && !memcmp(data,copy,Size));
    assert(lseek(input,0,SEEK_CUR)==37);
    close(input); close(sockets[0]); close(sockets[1]); free(data); free(copy);
    puts("PASS real-nonblocking-EAGAIN/196608-exact-bytes/resume/input-offset");
}

int main(int argc,char **argv) {
    alarm(10);
    if (argc==2 && !strcmp(argv[1],"--stock-reference")) {
#if TARGET_OS_IPHONE
        fputs("Stock sendfile reference is unsafe on iPadOS; use macOS.\n", stderr);
        return 77;
#else
        Headers(sendfile,"stock-macOS"); ErrorPriority(sendfile,"stock-macOS");
        ClosedPeer(sendfile,"stock-macOS"); return 0;
#endif
    }
    assert(argc==1);
    Basic(); Headers(MacWSSendfile,"adapter"); Errors(); Faults(); ErrorPriority(MacWSSendfile,"adapter");
    ClosedPeer(MacWSSendfile,"adapter"); Interrupts(); NonblockingTransfer();
    puts("SENDFILE CONTRACT PASS"); return 0;
}

// A real macOS caller of the public sendfile symbol, not a linked copy of the
// adapter. Run only as an owned isolated process with a candidate injected.
#include <assert.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/uio.h>
#include <unistd.h>

int main(void) {
    alarm(5);
    typedef int (*Function)(int,int,off_t,off_t *,struct sf_hdtr *,int);
    Function replacement=(Function)dlsym(RTLD_DEFAULT,"MacWSSendfile");
    Dl_info image={0};
    if (!replacement || !dladdr((void *)replacement,&image)) {
        fputs("candidate sendfile adapter is not mapped\n",stderr); return 77;
    }
    printf("pid=%d adapter-image=%s public-symbol-interposed=%d\n",
        getpid(),image.dli_fname,sendfile==replacement);
    if (sendfile!=replacement) return 1;
    char path[]="/tmp/macws-sendfile-api.XXXXXX";
    int input=mkstemp(path);assert(input>=0);assert(unlink(path)==0);
    assert(write(input,"0123456789",10)==10 && lseek(input,7,SEEK_SET)==7);
    off_t length=10;
    assert(sendfile(input,input,0,&length,NULL,0)==-1 && errno==ENOTSOCK && !length);
    assert(lseek(input,0,SEEK_CUR)==7);
    int pair[2];assert(socketpair(AF_UNIX,SOCK_STREAM,0,pair)==0);
    struct iovec head={"HEADER",6},tail={"TAIL",4};
    struct sf_hdtr vectors={&head,1,&tail,1};length=9;
    assert(sendfile(input,pair[0],2,&length,&vectors,0)==0 && length==13);
    char bytes[32]={0};assert(recv(pair[1],bytes,sizeof(bytes),MSG_DONTWAIT)==13);
    assert(!memcmp(bytes,"HEADER234TAIL",13) && lseek(input,0,SEEK_CUR)==7);
    close(input);close(pair[0]);close(pair[1]);
    puts("PUBLIC SENDFILE API PASS file-fallback/header-offset-trailer/exact-bytes");
    return 0;
}

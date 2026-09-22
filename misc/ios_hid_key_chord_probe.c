// Bounded native HID chord probe. Exercises UIKit, not the MacWS socket.
// Explicit invocation only; no installation/boot hook. Releases every key on
// ordinary failure or SIGINT/SIGTERM. Never use while somebody is typing.
#include <CoreFoundation/CoreFoundation.h>
#include <dlfcn.h>
#include <mach/mach_time.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

typedef CFTypeRef (*CreateClient)(CFAllocatorRef);
typedef CFTypeRef (*CreateKey)(CFAllocatorRef,uint64_t,uint16_t,uint16_t,Boolean,uint32_t);
typedef void (*Dispatch)(CFTypeRef,CFTypeRef);
typedef void (*Sender)(CFTypeRef,uint64_t);
typedef void (*SetTime)(CFTypeRef,uint64_t);
static volatile sig_atomic_t stopped;
static void stop(int sig) { (void)sig; stopped=1; }
static uint16_t usage(const char *name) {
    if (strlen(name)==1 && name[0]>='a' && name[0]<='z') return 4+name[0]-'a';
    const struct {const char *name; uint16_t value;} keys[]={
        {"control",224},{"right-control",228},{"shift",225},{"right-shift",229},
        {"option",226},{"right-option",230},{"command",227},{"right-command",231},
        {"tab",43},{"return",40},{"escape",41},{"delete",42},{"space",44},
        {"left",80},{"right",79},{"up",82},{"down",81}};
    for(unsigned i=0;i<sizeof(keys)/sizeof(*keys);i++)
        if(!strcmp(name,keys[i].name))return keys[i].value;
    return 0;
}
int main(int argc,char **argv) {
    unsigned holdMillis=0;
    if(argc>1 && !strncmp(argv[1],"--hold-ms=",10)) {
        char *end=NULL;
        unsigned long value=strtoul(argv[1]+10,&end,10);
        if(!argv[1][10] || *end || value>3000)return 64;
        holdMillis=(unsigned)value;
        argc--;argv++;
    }
    if(argc<2 || argc>5){fprintf(stderr,"usage: probe [--hold-ms=0..3000] [modifier ...] key\n");return 64;}
    uint16_t keys[4]={0};
    for(int i=1;i<argc;i++) {
        keys[i-1]=usage(argv[i]);
        if(!keys[i-1] || (i<argc-1 && keys[i-1]<224))return 64;
        for(int j=1;j<i;j++)if(keys[i-1]==keys[j-1])return 64;
    }
    void *library=dlopen("/System/Library/Frameworks/IOKit.framework/IOKit",RTLD_NOW|RTLD_LOCAL);
    if(!library)return 69;
    CreateClient create=(CreateClient)dlsym(library,"IOHIDEventSystemClientCreateSimpleClient");
    CreateKey key=(CreateKey)dlsym(library,"IOHIDEventCreateKeyboardEvent");
    Dispatch dispatch=(Dispatch)dlsym(library,"IOHIDEventSystemClientDispatchEvent");
    Sender sender=(Sender)dlsym(library,"IOHIDEventSetSenderID");
    SetTime setTime=(SetTime)dlsym(library,"IOHIDEventSetTimeStamp");
    if(!create||!key||!dispatch||!sender||!setTime)return 69;
    CFTypeRef client=create(kCFAllocatorDefault);
    if(!client)return 70;
    signal(SIGINT,stop);signal(SIGTERM,stop);
    CFTypeRef downs[4]={0},ups[4]={0};int status=0,held=0;
    // Allocate all release events before dispatching the first down.
    for(int i=0;i<argc-1;i++) {
        downs[i]=key(kCFAllocatorDefault,mach_absolute_time(),7,keys[i],true,0);
        ups[i]=key(kCFAllocatorDefault,mach_absolute_time(),7,keys[i],false,0);
        if(!downs[i]||!ups[i]){status=70;goto done;}
        sender(downs[i],0x8000000817319372ULL);sender(ups[i],0x8000000817319372ULL);
    }
    for(int i=0;i<argc-1 && !stopped;i++) {
        setTime(downs[i],mach_absolute_time());
        dispatch(client,downs[i]);held++;usleep(60000);
    }
    if(holdMillis && !stopped) {
        printf("native-HID held=%d hold-ms=%u\n",held,holdMillis);fflush(stdout);
        for(unsigned elapsed=0;elapsed<holdMillis && !stopped;elapsed+=10)
            usleep(10000);
    }
done:
    for(int i=held-1;i>=0;i--){setTime(ups[i],mach_absolute_time());dispatch(client,ups[i]);usleep(60000);}
    for(int i=0;i<4;i++){if(downs[i])CFRelease(downs[i]);if(ups[i])CFRelease(ups[i]);}
    CFRelease(client);dlclose(library);
    printf("native-HID chord keys=%d released=%d status=%d interrupted=%d\n",argc-1,held,status,stopped);
    return stopped ? 130 : status;
}

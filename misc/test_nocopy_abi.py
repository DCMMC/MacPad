"""Execute the actual NoCopy wire helper and extracted production routing bodies.

These tests do not issue GPU or IOKit requests. The separate on-device contract
probe is required to establish real allocation ownership and GPU visibility.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CC = os.environ.get("CC", "cc")


def compile_run(source, *, language="c", options=()):
    with tempfile.TemporaryDirectory(prefix="macws-nocopy-test-") as tmp:
        binary = str(Path(tmp) / "test")
        command = [CC, "-x", language, "-Wall", "-Wextra", "-Werror",
                   "-I", str(ROOT / "include"), *options, "-", "-o", binary]
        subprocess.run(command, input=source, text=True, check=True, timeout=30)
        subprocess.run([binary], check=True, timeout=10)


class NoCopyABI(unittest.TestCase):
    def test_wire_guards_source_immutability_tail_and_thread_isolation(self):
        compile_run((ROOT / "misc/test_nocopy_abi.c").read_text(),
                    options=("-std=c11", "-pthread", "-fsanitize=undefined",
                             "-fno-sanitize-recover=all"))

    def test_c_and_cpp_share_real_declaration_and_tls_symbol(self):
        with tempfile.TemporaryDirectory(prefix="macws-nocopy-linkage-") as tmp:
            obj = str(Path(tmp) / "provider.o")
            binary = str(Path(tmp) / "test")
            provider = r'''
#include "macws_nocopy_abi.h"
__thread struct MacWSNoCopyScope *g_macws_nocopy_scope;
bool MacWSAGXNoCopyABIReady(const void *a, const void *b) { return a && b; }
'''
            consumer = r'''
#include "macws_nocopy_abi.h"
#include <cassert>
int main() {
    MacWSNoCopyScope scope = {0x100000,16384,16384,nullptr};
    assert(!g_macws_nocopy_scope);
    g_macws_nocopy_scope = &scope;
    assert(MacWSAGXNoCopyABIReady(&scope, &scope));
    assert(!MacWSAGXNoCopyABIReady(nullptr, &scope));
    return 0;
}
'''
            subprocess.run([CC, "-x", "c", "-std=c11", "-Wall", "-Wextra",
                            "-Werror", "-I", str(ROOT / "include"), "-c", "-",
                            "-o", obj], input=provider, text=True, check=True)
            subprocess.run([os.environ.get("CXX", "c++"), "-x", "c++", "-std=c++11",
                            "-Wall", "-Wextra", "-Werror", "-I", str(ROOT / "include"),
                            "-", "-x", "none", obj, "-o", binary], input=consumer,
                           text=True, check=True)
            subprocess.run([binary], check=True, timeout=5)

    def test_real_ioconnect_branch_skips_generic_only_for_verified_wire(self):
        source = (ROOT / "libmachook/mac_hooks.m").read_text()
        start = source.index("    BOOL translated_nocopy = NO;")
        end = source.index("\n    if(agxIsRes && !translated_nocopy) {", start)
        block = source[start:end]
        generic_condition = source[end:source.index("\n", end + 1)]
        fixture = r'''
#include "macws_nocopy_abi.h"
#include <assert.h>
#include <stdlib.h>
typedef int BOOL;
#define NO 0
#define YES 1
#define kIOReturnBadArgument (-7)
__thread struct MacWSNoCopyScope *g_macws_nocopy_scope;
static int forwarded, generic, accounted;
static size_t sent_size;
static uint8_t sent[256];
static BOOL IOConnectIsIOGPU(unsigned client) { return client == 7; }
static int route(unsigned client, unsigned selector,
                 const void *inStruct, size_t inStructCnt) {
    unsigned char shadowbuf[256];
    unsigned char *qbuf = NULL;
    uint8_t agxType = 0;
    int agxIsRes = IOConnectIsIOGPU(client) && selector == 9 && inStruct &&
        inStructCnt >= 96 && inStructCnt <= sizeof(shadowbuf);
/* ACTUAL_PRODUCTION_BRANCH */
/* ACTUAL_GENERIC_GUARD */
        ++generic; // Sentinel for the existing generic translator.
    }
    ++forwarded;
    sent_size = inStructCnt;
    if (inStruct && inStructCnt <= sizeof(sent)) memcpy(sent,inStruct,inStructCnt);
    // A real call/result consumer follows the branch in production.
    if (agxIsRes && agxType == 0x80) ++accounted;
    return 123;
}
static void put(uint8_t *p, size_t offset, uint64_t value) {
    memcpy(p + offset,&value,8);
}
int main(void) {
    uint8_t request[104] = {0}, saved[104];
    struct MacWSNoCopyScope scope = {0x1042e4000,16384,16384,NULL};
    put(request,0,0x80); put(request,0x30,1);
    put(request,0x38,scope.bytes); put(request,0x40,scope.bytes);
    put(request,0x48,scope.length); put(request,0x60,0x918273645ULL);
    memcpy(saved,request,104); g_macws_nocopy_scope = &scope;
    assert(route(7,9,request,104)==123);
    assert(forwarded==1 && generic==0 && accounted==1 && sent_size==96);
    assert(MacWSNoCopyRead64(sent+0x30)==scope.bytes);
    assert(MacWSNoCopyRead64(sent+0x38)==scope.bytes);
    assert(MacWSNoCopyRead64(sent+0x40)==scope.length);
    assert(MacWSNoCopyRead64(sent+0x58)==0x918273645ULL);
    assert(!memcmp(saved,request,104));
    put(request,0x30,0);
    assert(route(7,9,request,104)==kIOReturnBadArgument);
    assert(route(7,9,request,96)==kIOReturnBadArgument);
    assert(forwarded==1 && generic==0 && accounted==1);
    // A nested native-parent/type80 request cannot silently receive a
    // second layout rewrite while the verified ordinary scope is active.
    memcpy(request,saved,104); request[0x15] |= 8;
    assert(route(7,9,request,104)==kIOReturnBadArgument);
    assert(forwarded==1);
    memcpy(request,saved,104);
    assert(route(8,9,request,104)==123 && generic==0);
    assert(route(7,8,request,104)==123 && generic==0);
    request[0]=0x82;
    assert(route(7,9,request,104)==123 && generic==1);
    request[0]=0x80; g_macws_nocopy_scope=NULL;
    assert(route(7,9,request,104)==123 && generic==2);
    assert(forwarded==5 && accounted==1);
    return 0;
}
'''
        fixture = fixture.replace("/* ACTUAL_PRODUCTION_BRANCH */", block)
        fixture = fixture.replace("/* ACTUAL_GENERIC_GUARD */", generic_condition)
        compile_run(fixture, options=("-std=c11", "-fsanitize=undefined",
                                      "-fno-sanitize-recover=all"))

    @unittest.skipUnless(sys.platform == "darwin", "uses real Objective-C exceptions")
    def test_real_objc_scope_restores_on_nil_exception_nested_and_skip(self):
        source = (ROOT / "libmachook/Metal_hooks.x").read_text()
        start = source.index("                if (nocopy_abi_ready && macws_agx_native_enabled() &&")
        # Stop at the following real branch, not a dated historical comment.
        end = source.index("                if (macws_agx_native_enabled() &&", start)
        block = source[start:end]
        fixture = r'''
#import <Foundation/Foundation.h>
#include "macws_nocopy_abi.h"
#include <assert.h>
__thread struct MacWSNoCopyScope *g_macws_nocopy_scope;
static BOOL nocopy_abi_ready = YES, native_enabled = YES;
static unsigned calls, legacy, depth;
static int mode;
static id token, device;
static SEL bytes_sel;
static void (^expected_deallocator)(void *,NSUInteger);
static void *expected_bytes=(void *)(uintptr_t)0x1042e4000;
static NSUInteger expected_options;
static struct MacWSNoCopyScope *prior;
static BOOL macws_agx_native_enabled(void) { return native_enabled; }
static id wrapper(id self,id dev,void *bytes,NSUInteger length,NSUInteger opt,
                  void (^deallocator)(void *,NSUInteger),uint64_t pinnedGPUAddress);
static id original(id self,SEL cmd,id dev,void *bytes,NSUInteger length,NSUInteger opt,
                   void (^deallocator)(void *,NSUInteger),uint64_t pin) {
    ++calls;
    assert(self==token && dev==device && cmd==bytes_sel);
    assert(bytes==expected_bytes && length==(NSUInteger)vm_page_size);
    assert(opt==expected_options && !pin && deallocator==expected_deallocator);
    struct MacWSNoCopyScope *scope=g_macws_nocopy_scope;
    assert(scope && scope->bytes==(uintptr_t)bytes && scope->length==length);
    assert(scope->page_size==(size_t)vm_page_size);
    if (mode==1) return nil;
    if (mode==2) @throw [NSException exceptionWithName:@"NoCopyTest" reason:nil userInfo:nil];
    if (mode==3 && !depth) {
        assert(scope->previous==prior); ++depth;
        assert(wrapper(self,dev,bytes,length,opt,deallocator,pin)==self);
        --depth; assert(g_macws_nocopy_scope==scope);
    } else if (depth) {
        assert(scope->previous && scope->previous->previous==prior);
    } else assert(scope->previous==prior);
    return self;
}
static id (*s_orig)(id,SEL,id,void *,NSUInteger,NSUInteger,
                    void (^)(void *,NSUInteger),uint64_t)=original;
static id wrapper(id self,id dev,void *bytes,NSUInteger length,NSUInteger opt,
                  void (^deallocator)(void *,NSUInteger),uint64_t pinnedGPUAddress) {
/* ACTUAL_SCOPE */
    ++legacy; return device;
}
int main(void) {
    @autoreleasepool {
        token=[NSObject new]; device=[NSObject new];
        bytes_sel=sel_registerName("initWithDevice:bytes:length:options:deallocator:pinnedGPUAddress:");
        __block unsigned deallocations=0;
        expected_deallocator=^(void *p,NSUInteger n){(void)p;(void)n;++deallocations;};
        struct MacWSNoCopyScope outer={0x200000,16384,16384,NULL};
        prior=&outer; g_macws_nocopy_scope=prior;
        for (NSUInteger storage=0;storage<2;++storage) {
            expected_options=storage<<4;
            for (mode=0;mode<=3;++mode) {
                BOOL caught=NO;
                @try {
                    id result=wrapper(token,device,expected_bytes,vm_page_size,
                                      expected_options,expected_deallocator,0);
                    assert(result==(mode==1?nil:token));
                } @catch (NSException *exception) {
                    assert(mode==2 && [exception.name isEqualToString:@"NoCopyTest"]);
                    caught=YES;
                }
                assert(caught==(mode==2));
                assert(g_macws_nocopy_scope==prior && deallocations==0 && legacy==0);
            }
        }
        assert(calls==10);
        mode=0; expected_options=0;
        // Unsupported shapes retain the existing path without installing TLS.
        assert(wrapper(token,device,expected_bytes,vm_page_size,0,expected_deallocator,1)==device);
        assert(wrapper(token,device,expected_bytes,vm_page_size,2<<4,expected_deallocator,0)==device);
        assert(wrapper(token,device,(char *)expected_bytes+1,vm_page_size,0,expected_deallocator,0)==device);
        assert(wrapper(token,device,expected_bytes,vm_page_size-1,0,expected_deallocator,0)==device);
        nocopy_abi_ready=NO;
        assert(wrapper(token,device,expected_bytes,vm_page_size,0,expected_deallocator,0)==device);
        nocopy_abi_ready=YES; native_enabled=NO;
        assert(wrapper(token,device,expected_bytes,vm_page_size,0,expected_deallocator,0)==device);
        assert(calls==10 && legacy==6 && g_macws_nocopy_scope==prior && !deallocations);
        g_macws_nocopy_scope=NULL;
        [token release]; [device release];
    }
    return 0;
}
'''
        compile_run(fixture.replace("/* ACTUAL_SCOPE */", block),
                    language="objective-c", options=("-fblocks", "-fno-objc-arc",
                    "-fobjc-exceptions", "-framework", "Foundation"))

    def test_actual_version_gate_rejects_unknown_producers_and_kernel(self):
        source = (ROOT / "libmachook/mac_hooks.m").read_text()
        start = source.index("bool MacWSAGXNoCopyABIReady(")
        end = source.index("\nIOReturn IOConnectCallMethod_new", start)
        gate = source[start:end]
        fixture = r'''
#include "macws_nocopy_abi.h"
#include <assert.h>
typedef struct { const void *dli_fbase; } Dl_info;
static int bad_uuid,bad_dl,bad_sysctl;
static size_t returned_size=6;
static const char *kernel="20D67";
static int dladdr(const void *p,Dl_info *out) {
    if (bad_dl) return 0;
    out->dli_fbase=(uintptr_t)p<0x20000000?(void *)0x10000000:(void *)0x20000000;
    return 1;
}
static int macws_macho_uuid_matches(const void *base,const uint8_t *uuid) {
    return !bad_uuid && (base==(void *)0x10000000?uuid[0]==0x72:uuid[0]==0xce);
}
static int macws_real_sysctlbyname(const char *name,void *out,size_t *size,void *input,size_t n) {
    assert(!strcmp(name,"kern.osversion") && !input && !n);
    strcpy(out,kernel); *size=returned_size; return bad_sysctl;
}
/* ACTUAL_GATE */
int main(void) {
    const void *agx=(void *)(uintptr_t)(0x10000000+0x1f4bb4);
    const void *iogpu=(void *)(uintptr_t)(0x20000000+0x1c24);
    assert(MacWSAGXNoCopyABIReady(agx,iogpu));
    assert(!MacWSAGXNoCopyABIReady(NULL,iogpu));
    assert(!MacWSAGXNoCopyABIReady(agx,NULL));
    assert(!MacWSAGXNoCopyABIReady((const char *)agx+4,iogpu));
    assert(!MacWSAGXNoCopyABIReady(agx,(const char *)iogpu+4));
    bad_uuid=1; assert(!MacWSAGXNoCopyABIReady(agx,iogpu)); bad_uuid=0;
    bad_dl=1; assert(!MacWSAGXNoCopyABIReady(agx,iogpu)); bad_dl=0;
    bad_sysctl=-1; assert(!MacWSAGXNoCopyABIReady(agx,iogpu)); bad_sysctl=0;
    returned_size=0; assert(!MacWSAGXNoCopyABIReady(agx,iogpu));
    returned_size=32; assert(!MacWSAGXNoCopyABIReady(agx,iogpu));
    returned_size=33; assert(!MacWSAGXNoCopyABIReady(agx,iogpu)); returned_size=6;
    kernel="20D68"; assert(!MacWSAGXNoCopyABIReady(agx,iogpu));
    kernel="20D67x"; assert(!MacWSAGXNoCopyABIReady(agx,iogpu));
    return 0;
}
'''
        compile_run(fixture.replace("/* ACTUAL_GATE */", gate), options=("-std=c11",))


if __name__ == "__main__":
    unittest.main()

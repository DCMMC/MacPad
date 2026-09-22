"""Execute the real data-constructor/router functions with owned ObjC fixtures."""
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "libmachook/Metal_hooks.x").read_text()


def function(name):
    match = re.search(r"\b" + re.escape(name) + r"\s*\([^;{]*?\)\s*\{", SOURCE)
    if not match:
        raise AssertionError(f"definition missing: {name}")
    start = SOURCE.rfind("\nstatic ", 0, match.start()) + 1
    pos = match.end()
    depth = 1
    # These selected bodies contain no braces in string literals/comments.
    while depth:
        depth += (SOURCE[pos] == "{") - (SOURCE[pos] == "}")
        pos += 1
    return SOURCE[start:pos]


PREAMBLE = r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <objc/runtime.h>
#import <CommonCrypto/CommonDigest.h>
#include <assert.h>
#include <stdatomic.h>
#include <dlfcn.h>
#include <ptrauth.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
static BOOL nativeEnabled=YES;
static BOOL macws_agx_native_enabled(void) { return nativeEnabled; }
static BOOL macws_runtime_diagnostics_enabled(void) { return NO; }
static const void *kMacWSMetal2MetalLibraryRouteKey=&kMacWSMetal2MetalLibraryRouteKey;
static const void *kMacWSMetal2MetalRouteCheckedKey=&kMacWSMetal2MetalRouteCheckedKey;
static const void *kMacWSMetal2MetalDataObservedKey=&kMacWSMetal2MetalDataObservedKey;
static const void *kMacWSMetal2MetalCompanionKey=&kMacWSMetal2MetalCompanionKey;
static NSArray *testRoutes;
static NSUInteger hashes;
static NSUInteger maps;
static BOOL failMap;
static dispatch_data_t observedMap(dispatch_data_t data,const void **bytes,size_t *size) {
    maps++;
    if(failMap) { *bytes=NULL;*size=0;return NULL; }
    return dispatch_data_create_map(data,bytes,size);
}
#define dispatch_data_create_map observedMap
static NSData *actual_sha256(const void *,size_t);
static NSData *macws_metal2metal_sha256(const void *bytes,size_t size) {
    hashes++; return actual_sha256(bytes,size);
}
@interface TestLibrary : NSObject
@property(nonatomic) NSUInteger nameReads;
@end
@implementation TestLibrary
- (NSArray *)functionNames { self.nameReads++; return @[@"bitmapVS",@"bitmapPS"]; }
@end
static NSArray *macws_metal2metal_routes(void) { return testRoutes; }
static uint64_t macws_source_fnv1a64(const void *bytes,size_t size) {
    uint64_t value=14695981039346656037ULL;
    for(size_t i=0;i<size;i++) { value^=((const uint8_t *)bytes)[i]; value*=1099511628211ULL; }
    return value;
}
'''

CONSTRUCTOR_STUBS = r'''
static _Atomic uint32_t g_macws_new_library_data_count=0;
static const size_t kMacWSANGLEDefaultMacOSBytes=12;
static const size_t kMacWSSteamANGLEDefaultMacOSBytes=12345;
static uint64_t kMacWSANGLEDefaultMacOSHash;
static const uint64_t kMacWSSteamANGLEDefaultMacOSHash=0;
typedef struct { size_t source_length; uint64_t source_hash; } MacWSStrayMetalLibrary;
static const MacWSStrayMetalLibrary kMacWSStrayMetalLibraries[]={{1,0}};
static dispatch_data_t replacementData;
static BOOL macws_is_stray_metal_library_length(size_t length) { (void)length; return NO; }
static BOOL macws_is_stray_process(void) { return NO; }
static dispatch_data_t macws_angle_default_macabi_library(void) { return replacementData; }
static dispatch_data_t macws_steam_angle_default_macabi_library(void) { return NULL; }
static dispatch_data_t macws_stray_macabi_library(size_t index) { (void)index; return NULL; }
static dispatch_data_t macws_dynamic_stray_macabi_library(size_t length,uint64_t hash) { (void)length;(void)hash;return NULL; }
static BOOL macws_validate_retargetable_metal_library(const void *bytes,size_t length,BOOL source,uint64_t hash) { (void)bytes;(void)length;(void)source;(void)hash;return NO; }
static void macws_remember_unknown_stray_library(dispatch_data_t data,size_t length,uint64_t hash,uint32_t sequence) { (void)data;(void)length;(void)hash;(void)sequence; }
static dispatch_data_t macws_retarget_stray_metal_library(const void *bytes,size_t length,uint64_t hash) { (void)bytes;(void)length;(void)hash;return NULL; }
static void macws_create_half_float_library_variant(id self,SEL selector,id result,size_t length,uint64_t hash) { (void)self;(void)selector;(void)result;(void)length;(void)hash; }
static TestLibrary *constructorResult;
static NSError *constructorError;
static dispatch_data_t seenData;
static unsigned constructorCalls;
static id originalConstructor(id self,SEL selector,dispatch_data_t data,NSError **error) {
    (void)self;(void)selector;constructorCalls++;seenData=data;
    if(error)*error=constructorError;return constructorResult;
}
static id (*g_macws_new_library_data_orig)(id,SEL,dispatch_data_t,NSError **)=originalConstructor;
'''

MAIN = r'''
static dispatch_data_t blob(const char *text) {
    return dispatch_data_create(text,strlen(text),NULL,DISPATCH_DATA_DESTRUCTOR_DEFAULT);
}
static NSString *hex(NSData *data) {
    NSMutableString *text=[NSMutableString string];
    for(NSUInteger i=0;i<data.length;i++)[text appendFormat:@"%02x",((const unsigned char *)data.bytes)[i]];
    return text;
}
static NSMutableDictionary *route(const char *text) {
    const MacWSMetal2MetalRuntimeObjects *k=macws_metal2metal_runtime_objects();
    NSData *digest=actual_sha256(text,strlen(text));
    return [@{k->source:@{k->size:@(strlen(text)),k->sha256:hex(digest)},
              k->sourceDigestInternal:digest,
              k->sourceNamesInternal:[NSSet setWithArray:@[@"bitmapVS",@"bitmapPS"]]} mutableCopy];
}
int main(int argc,char **argv) {
    assert(argc==2);alarm(10);
    @autoreleasepool {
        const MacWSMetal2MetalRuntimeObjects *k=macws_metal2metal_runtime_objects();
        NSMutableDictionary *a=route("originalAAAA"),*b=route("originalBBBB");
        a[k->requiresSourceIdentity]=@YES;
        b[k->requiresSourceIdentity]=@YES;
        dispatch_data_t dataA=blob("originalAAAA"),dataB=blob("originalBBBB"),unknown=blob("originalZZZZ");
        testRoutes=@[a,b];
        kMacWSANGLEDefaultMacOSHash=macws_source_fnv1a64("originalAAAA",12);
        constructorResult=[TestLibrary new];
        constructorError=[NSError errorWithDomain:@"test-error" code:123 userInfo:nil];
        NSError *error=nil;
        assert(macws_new_library_data_compat(nil,NULL,dataA,&error)==constructorResult);
        assert(constructorCalls==1&&seenData==dataA&&error==constructorError);
        assert(macws_metal2metal_route_for_library(constructorResult)==a);
        assert(constructorResult.nameReads==0);
        NSUInteger before=hashes;
        for(int i=0;i<20;i++)assert(macws_metal2metal_route_for_library(constructorResult)==a);
        assert(hashes==before&&constructorResult.nameReads==0);
        // A cached returned library is re-attributed to the actual observed
        // data, never kept on the prior same-function-name version.
        assert(macws_new_library_data_compat(nil,NULL,dataB,&error)==constructorResult);
        assert(macws_metal2metal_route_for_library(constructorResult)==b);
        assert(macws_new_library_data_compat(nil,NULL,unknown,&error)==constructorResult);
        assert(macws_metal2metal_route_for_library(constructorResult)==nil);
        assert(constructorResult.nameReads==0);
        // No size candidate => no dispatch map or digest, yet authoritative
        // negative attribution prevents the name-only fallback.
        dispatch_data_t small=blob("small");before=hashes;NSUInteger previousMaps=maps;
        assert(macws_new_library_data_compat(nil,NULL,small,&error)==constructorResult);
        assert(hashes==before&&maps==previousMaps&&seenData==small);
        assert(macws_metal2metal_route_for_library(constructorResult)==nil);
        failMap=YES;before=hashes;
        macws_metal2metal_attribute_data_library(constructorResult,dataA,NULL,0,NO);
        assert(hashes==before&&macws_metal2metal_route_for_library(constructorResult)==nil);
        failMap=NO;
        // Duplicate byte identities are ambiguous, even identical manifests.
        testRoutes=@[a,[a mutableCopy]];
        macws_metal2metal_attribute_data_library(constructorResult,dataA,NULL,0,NO);
        assert(macws_metal2metal_route_for_library(constructorResult)==nil);
        // An ANGLE/Stray replacement cannot receive the source bytes' route.
        testRoutes=@[a,b];replacementData=dataB;before=hashes;
        assert(macws_new_library_data_compat(nil,NULL,dataA,&error)==constructorResult);
        assert(seenData==dataB&&error==constructorError);
        assert(hashes==before&&macws_metal2metal_route_for_library(constructorResult)==nil);
        replacementData=NULL;
        // Failure returns exactly the original nil and NSError without work.
        constructorResult=nil;before=hashes;
        assert(macws_new_library_data_compat(nil,NULL,dataA,&error)==nil);
        assert(error==constructorError&&hashes==before);
        // Matching names cannot attribute an unobserved private library to
        // an Office route, nor can malformed policy values broaden routing.
        testRoutes=@[a];TestLibrary *sameNames=[TestLibrary new];
        assert(macws_metal2metal_route_for_library(sameNames)==nil);
        assert(macws_metal2metal_route_for_library(sameNames)==nil);
        assert(sameNames.nameReads==1);
        for(id policy in @[@NO,@"invalid",@[]]) {
            NSMutableDictionary *strict=[a mutableCopy];strict[k->requiresSourceIdentity]=policy;
            testRoutes=@[strict];
            assert(macws_metal2metal_route_for_library([TestLibrary new])==nil);
        }
        // Exact URL attribution (set upstream before fallback) is also kept.
        TestLibrary *urlLibrary=[TestLibrary new];
        objc_setAssociatedObject(urlLibrary,kMacWSMetal2MetalLibraryRouteKey,a,OBJC_ASSOCIATION_RETAIN_NONATOMIC);
        assert(macws_metal2metal_route_for_library(urlLibrary)==a&&urlLibrary.nameReads==0);
        // Existing system manifests without this policy retain their private
        // complete-function-name fallback and its per-object cache.
        [a removeObjectForKey:k->requiresSourceIdentity];
        TestLibrary *privateLibrary=[TestLibrary new];testRoutes=@[a];
        assert(macws_metal2metal_route_for_library(privateLibrary)==a);
        assert(macws_metal2metal_route_for_library(privateLibrary)==a);
        assert(privateLibrary.nameReads==1);
        // Missing/malformed SHA and source-byte drift fail manifest admission.
        assert(!macws_metal2metal_digest_from_hex(nil));
        assert(!macws_metal2metal_digest_from_hex(@"xx"));
        assert(!macws_metal2metal_digest_from_hex([@"z" stringByPaddingToLength:64 withString:@"z" startingAtIndex:0]));
        NSData *owned=[NSData dataWithBytes:"originalAAAA" length:12];
        NSString *path=[NSString stringWithUTF8String:argv[1]];
        assert([owned writeToFile:path atomically:YES]);
        NSMutableDictionary *identity=[@{k->size:@12,k->sha256:hex(actual_sha256(owned.bytes,owned.length)),
          k->fnv1a64:[NSString stringWithFormat:@"%016llx",(unsigned long long)macws_source_fnv1a64(owned.bytes,owned.length)]} mutableCopy];
        assert(macws_metal2metal_identity_matches(identity,path));
        identity[k->sha256]=hex(actual_sha256("originalBBBB",12));
        assert(!macws_metal2metal_identity_matches(identity,path));
        [identity removeObjectForKey:k->sha256];
        assert(!macws_metal2metal_identity_matches(identity,path));
        // Manifest absence is fail-closed even with the expected function set.
        testRoutes=@[];TestLibrary *unrouted=[TestLibrary new];
        macws_metal2metal_attribute_data_library(unrouted,dataA,NULL,0,NO);
        assert(!macws_metal2metal_route_for_library(unrouted)&&unrouted.nameReads==0);
        puts("PASS exact bytes, versions, ambiguity, negative cache, substitutions, original errors, manifest SHA");
    }
    return 0;
}
'''


class MetalDataRouteIdentity(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "darwin" and shutil.which("clang"),
                         "requires native Foundation runtime")
    def test_extracted_constructor_and_router(self):
        start = SOURCE.index("typedef struct {", SOURCE.index("kMacWSMetal2MetalDataObservedKey"))
        end = SOURCE.index("} MacWSMetal2MetalRuntimeObjects;", start) + len("} MacWSMetal2MetalRuntimeObjects;")
        names = ("macws_metal2metal_runtime_string", "macws_metal2metal_runtime_objects",
                 "macws_metal2metal_hex64", "macws_metal2metal_sha256",
                 "macws_metal2metal_digest_from_hex", "macws_metal2metal_identity_matches",
                 "macws_metal2metal_attribute_data_library", "macws_metal2metal_route_for_library")
        bodies = "\n".join(function(name).replace(
            "static NSData *macws_metal2metal_sha256(", "static NSData *actual_sha256(") for name in names)
        program = PREAMBLE + SOURCE[start:end] + bodies + CONSTRUCTOR_STUBS + function("macws_new_library_data_compat") + MAIN
        with tempfile.TemporaryDirectory(prefix="macws-metal-data-route-") as directory:
            source = Path(directory) / "test.m"
            binary = Path(directory) / "test"
            source.write_text(program)
            build = subprocess.run(["clang", "-fblocks", "-O1", "-Werror", "-Wno-deprecated-declarations",
                                    str(source), "-framework", "Foundation", "-framework", "Metal", "-o", str(binary)],
                                   capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            result = subprocess.run([str(binary), str(Path(directory) / "owned.bin")],
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("PASS exact bytes", result.stdout)

    def test_data_observation_precedes_transient_release(self):
        body = function("macws_new_library_data_compat")
        self.assertLess(body.index("g_macws_new_library_data_orig(self"),
                        body.index("macws_metal2metal_attribute_data_library("))
        self.assertLess(body.index("macws_metal2metal_attribute_data_library("),
                        body.index("dispatch_release(transient_replacement)"))
        attribution = function("macws_metal2metal_attribute_data_library")
        self.assertNotIn("functionNames", attribution)
        self.assertNotIn("contentsOf", attribution)
        self.assertNotIn("getenv", attribution)
        self.assertLess(attribution.index("if (candidate_size)"), attribution.index("dispatch_data_create_map"))
        self.assertIn("kMacWSMetal2MetalDataObservedKey", function("macws_new_library_url_metal2metal"))

    def test_validated_digest_is_cached_at_one_time_manifest_admission(self):
        loader = function("macws_metal2metal_routes")
        self.assertIn("dispatch_once(&once", loader)
        self.assertIn("macws_metal2metal_identity_matches(source, source_path)", loader)
        self.assertIn("macws_metal2metal_identity_matches(output, output_path)", loader)
        self.assertIn("route[keys->sourceDigestInternal]", loader)
        self.assertIn("macws_metal2metal_digest_from_hex(source[keys->sha256])", loader)


if __name__ == "__main__":
    unittest.main()

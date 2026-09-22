"""Execute the opt-in shader observer with fake ObjC objects, never a GPU."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class OfficeShaderObserver(unittest.TestCase):
    def test_diagnostic_only_and_early_actual_factory_discovery(self):
        source = (ROOT / "misc/macws_office_shader_observer.m").read_text()
        self.assertNotIn("macws_office_shader_observer", (ROOT / "libmachook/Makefile").read_text())
        for forbidden in ("getenv(", "constructor))", "objc_getClass(", "objc_copyClassList",
                          "MSHookFunction", "newCommandQueue", "setConstantValue:"):
            self.assertNotIn(forbidden, source)
        self.assertIn("(const void *)&MTLCreateSystemDefaultDevice", source)
        self.assertIn("(const void *)&MTLCopyAllDevices", source)
        self.assertIn("SHADER_OBS_LIMIT = 8, SHADER_CLASS_LIMIT = 8", source)

    @unittest.skipUnless(sys.platform == "darwin", "requires Objective-C/Foundation/Metal headers")
    def test_original_arguments_errors_exceptions_errno_factories_and_budgets(self):
        fixture = r'''
#define MACWS_OFFICE_SHADER_TEST 1
#include "misc/macws_office_shader_observer.m"
#include <assert.h>
static id expected_library, expected_function, expected_device;
static NSArray *expected_devices;
static dispatch_data_t expected_data;
static NSString *expected_name;
static MTLFunctionConstantValues *expected_constants;
static NSError **expected_error_slot;
static NSError *original_error;
static unsigned loaded, specialized, created, copied;
static int mode;
@interface ShaderTestLibraryBase : NSObject
- (id)newFunctionWithName:(NSString *)name constantValues:(MTLFunctionConstantValues *)constants error:(NSError **)error;
@end
@implementation ShaderTestLibraryBase
- (id)newFunctionWithName:(NSString *)name constantValues:(MTLFunctionConstantValues *)constants error:(NSError **)error {
    assert(errno==EDOM && name==expected_name && constants==expected_constants && error==expected_error_slot);
    ++specialized; errno=ERANGE;
    if(mode==2) @throw [NSException exceptionWithName:@"OriginalFunction" reason:nil userInfo:nil];
    if(mode==1) { if(error)*error=original_error; return nil; }
    return expected_function;
}
@end
@interface ShaderTestLibrary : ShaderTestLibraryBase @end
@implementation ShaderTestLibrary @end
@interface ShaderTestLibraryChild : ShaderTestLibrary @end
@implementation ShaderTestLibraryChild @end
@interface ShaderTestLibraryOverride : ShaderTestLibrary @end
@implementation ShaderTestLibraryOverride
- (id)newFunctionWithName:(NSString *)name constantValues:(MTLFunctionConstantValues *)constants error:(NSError **)error {
    return [super newFunctionWithName:name constantValues:constants error:error];
}
@end
@interface ShaderTestSibling : ShaderTestLibraryBase @end
@implementation ShaderTestSibling @end
@interface ShaderBadLibrary : NSObject
- (id)newFunctionWithName:(NSString *)name constantValues:(NSUInteger)constants error:(NSError **)error;
@end
@implementation ShaderBadLibrary
- (id)newFunctionWithName:(NSString *)name constantValues:(NSUInteger)constants error:(NSError **)error {
    (void)name;(void)constants;(void)error;return nil;
}
@end
@interface ShaderTestDeviceBase : NSObject
- (id)newLibraryWithData:(dispatch_data_t)data error:(NSError **)error;
@end
@implementation ShaderTestDeviceBase
- (id)newLibraryWithData:(dispatch_data_t)data error:(NSError **)error {
    assert(errno==EDOM && data==expected_data && error==expected_error_slot); ++loaded; errno=EPIPE;
    if(mode==4) @throw [NSException exceptionWithName:@"OriginalLibrary" reason:nil userInfo:nil];
    if(mode==3) { if(error)*error=original_error; return nil; }
    return expected_library;
}
@end
@interface ShaderTestDevice : ShaderTestDeviceBase @end
@implementation ShaderTestDevice @end
static id shader_test_create(void) { assert(errno==EDOM); ++created; errno=ECHILD; return expected_device; }
static NSArray *shader_test_copy(void) { assert(errno==EDOM); ++copied; errno=E2BIG; return expected_devices; }
static void call_function(NSString *name, NSError **slot) {
    expected_name=name; expected_error_slot=slot; errno=EDOM;
    id actual=[(ShaderTestLibraryBase *)expected_library newFunctionWithName:name constantValues:expected_constants error:slot];
    assert(actual==(mode==1?nil:expected_function) && errno==ERANGE);
}
int main(void) {
    @autoreleasepool {
        expected_device=[ShaderTestDevice new];
        expected_library=[ShaderTestLibrary new]; expected_function=[NSObject new];
        expected_constants=[MTLFunctionConstantValues new];
        original_error=[[NSError alloc] initWithDomain:@"MTLLibraryErrorDomain" code:3
            userInfo:@{NSLocalizedDescriptionKey:@"Target OS is incompatible"}];
        const char data[]="owned fixture";
        expected_data=dispatch_data_create(data,sizeof(data),NULL,DISPATCH_DATA_DESTRUCTOR_DEFAULT);
        expected_devices=[[NSArray alloc] initWithObjects:expected_device,nil];
        SEL function_selector=@selector(newFunctionWithName:constantValues:error:);
        SEL library_selector=@selector(newLibraryWithData:error:);
        IMP stock_function=class_getMethodImplementation([ShaderTestLibraryBase class],function_selector);
        IMP stock_library=class_getMethodImplementation([ShaderTestDeviceBase class],library_selector);
        errno=EDOM; assert(shader_create_device()==expected_device && errno==ECHILD && created==1);
        errno=EDOM; assert(shader_copy_devices()==expected_devices && errno==E2BIG && copied==1);
        assert(class_getMethodImplementation([ShaderTestDeviceBase class],library_selector)==stock_library);
        // Error slots are deliberately invalid object values on success. The
        // observer must neither inspect them nor overwrite the original slot.
        NSError *error=(NSError *)(uintptr_t)1; expected_error_slot=&error;
        errno=EDOM; assert([expected_device newLibraryWithData:expected_data error:&error]==expected_library);
        assert(errno==EPIPE && loaded==1 && error==(NSError *)(uintptr_t)1);
        assert(class_getMethodImplementation([ShaderTestLibraryBase class],function_selector)==stock_function);
        assert(class_getMethodImplementation([ShaderTestSibling class],function_selector)==stock_function);
        call_function(@"otherPS",&error); assert(!atomic_load(&function_samples));
        call_function(@"bitmapPS",&error); assert(error==(NSError *)(uintptr_t)1);
        mode=1; error=nil; call_function(@"bitmapStraightSrcAlphaPS",&error); assert(error==original_error);
        call_function(@"bitmapPS",NULL); // NSError ** must remain NULL.
        mode=2; expected_name=@"bitmapVS"; expected_error_slot=&error; errno=EDOM;
        BOOL caught=NO;
        @try { [(ShaderTestLibraryBase *)expected_library newFunctionWithName:expected_name constantValues:expected_constants error:&error]; }
        @catch(NSException *exception) { caught=[exception.name isEqual:@"OriginalFunction"]; }
        assert(caught && errno==ERANGE);
        mode=0;
        // A later discovered concrete subclass may inherit our hook. Save the
        // actual ancestor original, not our replacement (which would recurse).
        id old_library=expected_library; expected_library=[ShaderTestLibraryChild new];
        expected_error_slot=&error; errno=EDOM;
        assert([expected_device newLibraryWithData:expected_data error:&error]==expected_library && errno==EPIPE);
        call_function(@"bitmapMirrorPS",&error);
        for(unsigned i=0;i<16;++i)call_function(@"bitmapPS",&error);
        assert(atomic_load(&function_samples)==8 && specialized==22);
        ShaderBadLibrary *bad=[ShaderBadLibrary new];
        IMP bad_original=class_getMethodImplementation([ShaderBadLibrary class],function_selector);
        assert(!shader_install(libraries,bad,function_selector,YES));
        assert(class_getMethodImplementation([ShaderBadLibrary class],function_selector)==bad_original);
        mode=3; error=nil; expected_error_slot=&error; errno=EDOM;
        assert(![expected_device newLibraryWithData:expected_data error:&error] && error==original_error && errno==EPIPE);
        mode=4; errno=EDOM; caught=NO;
        @try { [expected_device newLibraryWithData:expected_data error:&error]; }
        @catch(NSException *exception) { caught=[exception.name isEqual:@"OriginalLibrary"]; }
        assert(caught && errno==EPIPE);
        mode=0;
        for(unsigned i=0;i<20;++i) { errno=EDOM;
            assert([expected_device newLibraryWithData:expected_data error:&error]==expected_library && errno==EPIPE); }
        assert(atomic_load(&library_samples)==8 && loaded==24);
        // An actual subclass override calling the already observed superclass
        // must forward once to the correct original, never recurse.
        id child=expected_library; expected_library=[ShaderTestLibraryOverride new];
        errno=EDOM;
        assert([expected_device newLibraryWithData:expected_data error:&error]==expected_library && errno==EPIPE);
        unsigned before=specialized; call_function(@"bitmapPS",&error); assert(specialized==before+1);
        [bad release]; [old_library release]; [child release]; [expected_library release]; [expected_function release];
        [expected_constants release]; [original_error release]; [expected_device release];
        [expected_devices release]; dispatch_release(expected_data);
        puts("SHADER-OBSERVER forwarding/errors/exceptions/errno/factories/budgets/inheritance PASS");
    }
    return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix="macws-office-shader-test-") as directory:
            binary = str(Path(directory) / "test")
            subprocess.run(["xcrun", "clang", "-x", "objective-c", "-fno-objc-arc", "-fblocks",
                            "-Wall", "-Wextra", "-Werror", "-I", str(ROOT), "-",
                            "-framework", "Foundation", "-framework", "Metal", "-o", binary],
                           input=fixture, text=True, check=True, timeout=30)
            result = subprocess.run([binary], text=True, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("forwarding/errors/exceptions/errno/factories/budgets/inheritance PASS", result.stdout)
            self.assertEqual(sum(x.startswith("OFFICE-SHADER function ") for x in result.stderr.splitlines()), 8)
            self.assertEqual(sum(x.startswith("OFFICE-SHADER load ") for x in result.stderr.splitlines()), 8)
            self.assertIn("domain=MTLLibraryErrorDomain code=3", result.stderr)
            self.assertIn("description=Target OS is incompatible", result.stderr)
            self.assertIn("exception=OriginalFunction", result.stderr)


if __name__ == "__main__":
    unittest.main()

"""CPU-only execution of the diagnostic observer; no actual Metal device/queue."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class OfficeUploadObserver(unittest.TestCase):
    def test_diagnostic_is_not_production_linked(self):
        source = (ROOT / "misc/macws_office_upload_observer.m").read_text()
        self.assertNotIn("macws_office_upload_observer", (ROOT / "libmachook/Makefile").read_text())
        for forbidden in ("getenv(", "MSHookFunction", "waitUntilCompleted", "newCommandQueue",
                          "objc_copyClassList", "memcpy(bytes", "deallocator(bytes"):
            self.assertNotIn(forbidden, source)
        self.assertIn("OBS_LIMIT = 8, OBS_READ_LIMIT = 1024 * 1024", source)

    @unittest.skipUnless(sys.platform == "darwin", "requires Objective-C and CoreGraphics")
    def test_real_cpu_bitmap_original_method_arguments_errno_and_budgets(self):
        fixture = r'''
#define MACWS_OFFICE_UPLOAD_TEST 1
#include "misc/macws_office_upload_observer.m"
#include <assert.h>
static void *last_bytes;
static NSUInteger last_length, last_options;
static void (^last_callback)(void *,NSUInteger);
static unsigned original_calls, callbacks, registered, committed;
static unsigned copied, encoder_calls;
static unsigned view_calls, reads;
static unsigned pipeline_calls, texture_binds, fragment_calls, vertex_calls, render_calls;
static int original_mode;
static id texture, expected_descriptor, expected_pipeline, render, render_descriptor, render_target;
static NSError **expected_error_pointer;
static float opacity=0.75f, matrix_values[12]={1,0,0,0,0,1,0,0,0.5f,0.25f,1,0};
@interface TestFunction : NSObject { NSString *function_name; }
- (id)initWithName:(NSString *)name;
@end
@implementation TestFunction
- (id)initWithName:(NSString *)name { if((self=[super init]))function_name=[name copy]; return self; }
- (NSString *)name { return function_name; }
- (void)dealloc { [function_name release]; [super dealloc]; }
@end
@interface TestPipeline : NSObject { id vertex_function, fragment_function; NSArray *attachments; }
@end
@implementation TestPipeline
- (id)init {
    if((self=[super init])) {
        vertex_function=[[TestFunction alloc] initWithName:@"bitmapVS"];
        fragment_function=[[TestFunction alloc] initWithName:@"bitmapIgnoreSrcAlphaPS"];
        NSMutableArray *a=[NSMutableArray array];
        for(unsigned i=0;i<8;++i) {
            MTLRenderPipelineColorAttachmentDescriptor *d=[MTLRenderPipelineColorAttachmentDescriptor new];
            d.pixelFormat=i?MTLPixelFormatInvalid:MTLPixelFormatBGRA8Unorm;
            d.blendingEnabled=YES; d.sourceRGBBlendFactor=MTLBlendFactorOne;
            d.destinationRGBBlendFactor=MTLBlendFactorOneMinusSourceAlpha;
            [a addObject:d]; [d release];
        }
        attachments=[a copy];
    } return self;
}
- (id)vertexFunction { return vertex_function; }
- (id)fragmentFunction { return fragment_function; }
- (NSUInteger)rasterSampleCount { return 4; }
- (id)colorAttachments { return attachments; }
- (void)dealloc { [vertex_function release]; [fragment_function release]; [attachments release]; [super dealloc]; }
@end
@interface TestRender : NSObject
- (void)setFragmentTexture:(id)value atIndex:(NSUInteger)index;
- (void)setFragmentBytes:(const void *)bytes length:(NSUInteger)length atIndex:(NSUInteger)index;
- (void)setVertexBytes:(const void *)bytes length:(NSUInteger)length atIndex:(NSUInteger)index;
@end
@implementation TestRender
- (void)setFragmentTexture:(id)value atIndex:(NSUInteger)index {
    assert(errno==EDOM && (value==texture || !value) && index<2); ++texture_binds;
    if(original_mode==8) @throw [NSException exceptionWithName:@"OriginalBind" reason:nil userInfo:nil];
    errno=EPIPE;
}
- (void)setFragmentBytes:(const void *)bytes length:(NSUInteger)length atIndex:(NSUInteger)index {
    assert(errno==EDOM && bytes==&opacity && length==4 && index<2); ++fragment_calls; errno=EPIPE;
}
- (void)setVertexBytes:(const void *)bytes length:(NSUInteger)length atIndex:(NSUInteger)index {
    assert(errno==EDOM && bytes==matrix_values && length==48 && (index==2 || index==3)); ++vertex_calls; errno=EPIPE;
}
@end
@interface TestBuffer : NSObject
- (id)newTextureWithDescriptor:(id)descriptor offset:(NSUInteger)offset bytesPerRow:(NSUInteger)row;
@end
@implementation TestBuffer
- (void *)contents { return last_bytes; }
- (MTLStorageMode)storageMode { return MTLStorageModeManaged; }
- (NSUInteger)length { return last_length; }
- (id)newTextureWithDescriptor:(id)descriptor offset:(NSUInteger)offset bytesPerRow:(NSUInteger)row {
    assert(errno==EDOM && descriptor==expected_descriptor && offset==16 && row==1248);
    ++view_calls;
    if(original_mode==5) @throw [NSException exceptionWithName:@"OriginalView" reason:nil userInfo:nil];
    errno=ENOSPC; return original_mode==4?nil:texture;
}
@end
static TestBuffer *buffer;
@interface TestTexture : NSObject @end
@implementation TestTexture
- (NSUInteger)width { return 309; }
- (NSUInteger)height { return 250; }
- (NSUInteger)depth { return 1; }
- (MTLPixelFormat)pixelFormat { return MTLPixelFormatBGRA8Unorm; }
- (MTLStorageMode)storageMode { return MTLStorageModeShared; }
- (NSUInteger)sampleCount { return 1; }
- (NSUInteger)mipmapLevelCount { return 1; }
- (MTLTextureType)textureType { return MTLTextureType2D; }
- (MTLTextureUsage)usage { return MTLTextureUsageShaderRead; }
- (id)buffer { return buffer; }
- (NSUInteger)bufferOffset { return 16; }
- (NSUInteger)bufferBytesPerRow { return 1248; }
- (void)getBytes:(void *)destination bytesPerRow:(NSUInteger)row fromRegion:(MTLRegion)region mipmapLevel:(NSUInteger)level {
    ++reads;
    assert(row==1236 && region.size.width==309 && region.size.height==250 && !level);
    for(size_t y=0;y<region.size.height;++y)
        memcpy((uint8_t *)destination+y*row,(uint8_t *)last_bytes+y*1280,row);
}
@end
@interface TestPrivateTexture : TestTexture @end
@implementation TestPrivateTexture
- (MTLStorageMode)storageMode { return MTLStorageModePrivate; }
@end
@interface TestMSAATexture : TestTexture @end
@implementation TestMSAATexture
- (NSUInteger)sampleCount { return 4; }
- (MTLStorageMode)storageMode { return MTLStorageModePrivate; }
@end
@interface TestPassAttachment : NSObject @end
@implementation TestPassAttachment
- (id)texture { return render_target; }
- (id)resolveTexture { return texture; }
- (MTLLoadAction)loadAction { return MTLLoadActionClear; }
- (MTLStoreAction)storeAction { return MTLStoreActionMultisampleResolve; }
- (NSUInteger)level { return 0; }
- (NSUInteger)slice { return 0; }
- (NSUInteger)depthPlane { return 0; }
- (NSUInteger)resolveLevel { return 0; }
- (NSUInteger)resolveSlice { return 0; }
- (NSUInteger)resolveDepthPlane { return 0; }
@end
@interface TestPass : NSObject @end
@implementation TestPass
- (id)colorAttachments { return @[[[TestPassAttachment new] autorelease]]; }
- (id)depthAttachment { return nil; }
- (id)stencilAttachment { return nil; }
@end
@interface TestBlit : NSObject
- (void)copyFromBuffer:(id)source sourceOffset:(NSUInteger)offset
    sourceBytesPerRow:(NSUInteger)row sourceBytesPerImage:(NSUInteger)image
    sourceSize:(MTLSize)size toTexture:(id)target destinationSlice:(NSUInteger)slice
    destinationLevel:(NSUInteger)level destinationOrigin:(MTLOrigin)origin;
@end
@implementation TestBlit
- (void)copyFromBuffer:(id)source sourceOffset:(NSUInteger)offset
    sourceBytesPerRow:(NSUInteger)row sourceBytesPerImage:(NSUInteger)image
    sourceSize:(MTLSize)size toTexture:(id)target destinationSlice:(NSUInteger)slice
    destinationLevel:(NSUInteger)level destinationOrigin:(MTLOrigin)origin {
    assert(errno==EDOM); ++copied;
    assert(source==buffer && target==texture && offset==32 && row==1248 && image==312000);
    assert(size.width==309 && size.height==250 && size.depth==1 && slice==2 && level==3);
    assert(origin.x==4 && origin.y==5 && origin.z==6);
    if (original_mode==3) @throw [NSException exceptionWithName:@"OriginalCopy" reason:nil userInfo:nil];
    errno=E2BIG;
}
@end
static TestBlit *encoder;
@interface TestDeviceBase : NSObject
- (id)newBufferWithBytesNoCopy:(void *)bytes length:(NSUInteger)length
                      options:(MTLResourceOptions)options deallocator:(void (^)(void *,NSUInteger))callback;
- (id)newRenderPipelineStateWithDescriptor:(id)descriptor error:(NSError **)error;
@end
@implementation TestDeviceBase
- (id)newRenderPipelineStateWithDescriptor:(id)descriptor error:(NSError **)error {
    assert(errno==EDOM && descriptor==expected_pipeline && error==expected_error_pointer);
    ++pipeline_calls;
    if(original_mode==7) @throw [NSException exceptionWithName:@"OriginalPipeline" reason:nil userInfo:nil];
    if(original_mode==6 && error) *error=[NSError errorWithDomain:@"OriginalMetal" code:123 userInfo:nil];
    errno=EIO; return original_mode==6?nil:texture;
}
- (id)newBufferWithBytesNoCopy:(void *)bytes length:(NSUInteger)length
                      options:(MTLResourceOptions)options deallocator:(void (^)(void *,NSUInteger))callback {
    assert(errno==EDOM); ++original_calls;
    last_bytes=bytes; last_length=length; last_options=options; last_callback=callback;
    if (original_mode==2) @throw [NSException exceptionWithName:@"OriginalNoCopy" reason:nil userInfo:nil];
    errno=ERANGE; return original_mode==1 ? nil : buffer;
}
@end
@interface TestDevice : TestDeviceBase @end
@implementation TestDevice @end
@interface SiblingDevice : TestDeviceBase @end
@implementation SiblingDevice @end
@interface TestCommand : NSObject {
    void (^completion)(id);
}
- (void)commit;
- (void)addCompletedHandler:(void (^)(id))handler;
- (MTLCommandBufferStatus)status;
- (NSError *)error;
- (id)blitCommandEncoder;
- (id)renderCommandEncoderWithDescriptor:(id)descriptor;
@end
@implementation TestCommand
- (void)addCompletedHandler:(void (^)(id))handler {
    ++registered; assert(!completion); completion=[handler copy]; errno=EINVAL;
}
- (void)commit {
    assert(errno==EDOM); ++committed;
    if (original_mode==2) @throw [NSException exceptionWithName:@"OriginalCommit" reason:nil userInfo:nil];
    if (completion) { completion(self); [completion release]; completion=nil; }
    errno=EIO;
}
- (MTLCommandBufferStatus)status { return MTLCommandBufferStatusCompleted; }
- (NSError *)error { return nil; }
- (id)blitCommandEncoder {
    assert(errno==EDOM); ++encoder_calls; errno=EAGAIN;
    return original_mode==1 ? nil : encoder;
}
- (id)renderCommandEncoderWithDescriptor:(id)descriptor {
    assert(errno==EDOM && descriptor==render_descriptor); ++render_calls;
    errno=EAGAIN; return original_mode==1?nil:render;
}
- (void)dealloc { [completion release]; [super dealloc]; }
@end
int main(void) {
    @autoreleasepool {
        buffer=[TestBuffer new]; TestDevice *device=[TestDevice new];
        encoder=[TestBlit new]; texture=[TestTexture new];
        render=[TestRender new]; expected_pipeline=[TestPipeline new];
        render_descriptor=[TestPass new]; render_target=[TestMSAATexture new];
        Method sibling=class_getInstanceMethod([SiblingDevice class],
            @selector(newBufferWithBytesNoCopy:length:options:deallocator:));
        IMP sibling_before=method_getImplementation(sibling);
        install_device(device); install_device(device);
        assert(installed_class==[TestDevice class]);
        assert(method_getImplementation(sibling)==sibling_before);
        install_command_class([TestCommand class]);
        TestCommand *command=[TestCommand new]; errno=EDOM; [command commit];
        assert(errno==EIO && committed==1 && registered==0);
        size_t width=309,height=250,stride=1280,span=stride*height;
        uint8_t *pixels=calloc(1,span), *stock=calloc(1,span), *imagebytes=calloc(1,span);
        for (size_t y=0;y<height;++y) for(size_t x=0;x<width;++x) {
            imagebytes[y*stride+x*4]=0x40;
            imagebytes[y*stride+x*4+1]=0x30;
            imagebytes[y*stride+x*4+2]=0x20;
            imagebytes[y*stride+x*4+3]=0xff;
        }
        CGColorSpaceRef colors=CGColorSpaceCreateDeviceRGB();
        CGBitmapInfo info=kCGBitmapByteOrder32Big|kCGImageAlphaPremultipliedLast;
        CGContextRef imagecontext=CGBitmapContextCreate(imagebytes,width,height,8,stride,colors,info);
        CGContextRef output=CGBitmapContextCreate(pixels,width,height,8,stride,colors,info);
        CGContextRef reference=CGBitmapContextCreate(stock,width,height,8,stride,colors,info);
        assert(output && reference && imagecontext);
        CGImageRef image=CGBitmapContextCreateImage(imagecontext);
        CGRect rect=CGRectMake(0,0,width,height);
        errno=EDOM; CGContextDrawImage(reference,rect,image); int expected_errno=errno;
        errno=EDOM; observed_draw(output,rect,image);
        assert(errno==expected_errno && !memcmp(pixels,stock,span));
        assert(atomic_load(&draws)==1 && match_range(pixels,span)==1);
        assert(match_range(pixels+span,1)==0);
        assert(match_range((void *)(uintptr_t)(UINTPTR_MAX-2),4)==0);
        void (^callback)(void *,NSUInteger)=^(void *p,NSUInteger n){(void)p;(void)n;++callbacks;};
        for (unsigned i=0;i<10;++i) {
            original_mode=i==1?1:0; errno=EDOM;
            id result=[device newBufferWithBytesNoCopy:pixels length:span options:0x10 deallocator:callback];
            assert(result==(original_mode==1?nil:buffer) && errno==ERANGE);
            assert(last_bytes==pixels && last_length==span && last_options==0x10 && last_callback==callback);
        }
        assert(original_calls==10 && !callbacks && atomic_load(&uploads)==8);
        assert(installed_buffer_class==[TestBuffer class]);
        expected_descriptor=texture;
        for(unsigned i=0;i<10;++i) {
            original_mode=i==1?4:0; errno=EDOM;
            id view=[buffer newTextureWithDescriptor:expected_descriptor offset:16 bytesPerRow:1248];
            assert(view==(original_mode==4?nil:texture) && errno==ENOSPC);
        }
        assert(view_calls==10 && atomic_load(&views)==8);
        original_mode=5; errno=EDOM; BOOL view_caught=NO;
        @try { [buffer newTextureWithDescriptor:expected_descriptor offset:16 bytesPerRow:1248]; }
        @catch(NSException *e) { assert([e.name isEqualToString:@"OriginalView"]); view_caught=YES; }
        assert(view_caught && view_calls==11 && !observing);
        NSError *pipeline_error=nil; expected_error_pointer=&pipeline_error;
        for(unsigned i=0;i<10;++i) {
            original_mode=i==1?6:0; pipeline_error=nil; errno=EDOM;
            id result=[device newRenderPipelineStateWithDescriptor:expected_pipeline error:&pipeline_error];
            assert(result==(original_mode==6?nil:texture) && errno==EIO);
            assert(original_mode!=6 || (pipeline_error.code==123 && [pipeline_error.domain isEqualToString:@"OriginalMetal"]));
        }
        assert(pipeline_calls==10 && atomic_load(&pipelines)==8);
        original_mode=7; errno=EDOM; BOOL pipeline_caught=NO;
        @try { [device newRenderPipelineStateWithDescriptor:expected_pipeline error:&pipeline_error]; }
        @catch(NSException *e) { assert([e.name isEqualToString:@"OriginalPipeline"]); pipeline_caught=YES; }
        assert(pipeline_caught && pipeline_calls==11);
        original_mode=0; errno=EDOM;
        assert([command renderCommandEncoderWithDescriptor:render_descriptor]==render && errno==EAGAIN);
        assert(installed_render_class==[TestRender class] && render_calls==1);
        errno=EDOM; [render setFragmentTexture:texture atIndex:0]; assert(errno==EPIPE && !encoder_draw(render));
        errno=EDOM; [render setFragmentBytes:&opacity length:4 atIndex:0]; assert(errno==EPIPE && !atomic_load(&opacities));
        assert(!atomic_load(&passes));
        for(unsigned i=0;i<10;++i) {
            errno=EDOM; assert([command renderCommandEncoderWithDescriptor:render_descriptor]==render && errno==EAGAIN);
            errno=EDOM; [render setFragmentTexture:texture atIndex:0]; assert(errno==EPIPE && !encoder_draw(render));
            errno=EDOM; [render setFragmentTexture:texture atIndex:1]; assert(errno==EPIPE && encoder_draw(render));
            errno=EDOM; [render setFragmentBytes:&opacity length:4 atIndex:0]; assert(errno==EPIPE);
            errno=EDOM; [render setVertexBytes:matrix_values length:48 atIndex:3]; assert(errno==EPIPE);
        }
        assert(texture_binds==21 && fragment_calls==11 && vertex_calls==10 && render_calls==11);
        assert(atomic_load(&bindings)==8 && atomic_load(&opacities)==8 && atomic_load(&matrices)==8);
        assert(atomic_load(&passes)==8);
        errno=EDOM; [render setFragmentTexture:nil atIndex:1]; assert(errno==EPIPE && !encoder_draw(render));
        original_mode=8; errno=EDOM; BOOL bind_caught=NO;
        @try { [render setFragmentTexture:texture atIndex:1]; }
        @catch(NSException *e) { assert([e.name isEqualToString:@"OriginalBind"]); bind_caught=YES; }
        assert(bind_caught && !encoder_draw(render));
        original_mode=2; errno=EDOM; BOOL caught=NO;
        @try { [device newBufferWithBytesNoCopy:pixels length:span options:0x10 deallocator:callback]; }
        @catch(NSException *e) { assert([e.name isEqualToString:@"OriginalNoCopy"]); caught=YES; }
        assert(caught && !observing && original_calls==11 && !callbacks);
        original_mode=0;
        // Only the actual encoder returned by the original command is hooked.
        errno=EDOM; assert([command blitCommandEncoder]==encoder && errno==EAGAIN);
        assert(encoder_calls==1 && installed_blit_class==[TestBlit class]);
        original_mode=1; errno=EDOM;
        assert([command blitCommandEncoder]==nil && errno==EAGAIN && encoder_calls==2);
        original_mode=0;
        for(unsigned i=0;i<11;++i) {
            last_bytes=i==0?(void *)(uintptr_t)0x1000:pixels;
            errno=EDOM;
            [encoder copyFromBuffer:buffer sourceOffset:32 sourceBytesPerRow:1248
                sourceBytesPerImage:312000 sourceSize:MTLSizeMake(309,250,1)
                toTexture:texture destinationSlice:2 destinationLevel:3
                destinationOrigin:MTLOriginMake(4,5,6)];
            assert(errno==E2BIG && copied==i+1);
            if(i==0) assert(atomic_load(&blits)==0);
        }
        assert(atomic_load(&blits)==8);
        original_mode=3; caught=NO; errno=EDOM;
        @try {
            [encoder copyFromBuffer:buffer sourceOffset:32 sourceBytesPerRow:1248
                sourceBytesPerImage:312000 sourceSize:MTLSizeMake(309,250,1)
                toTexture:texture destinationSlice:2 destinationLevel:3
                destinationOrigin:MTLOriginMake(4,5,6)];
        } @catch(NSException *e) { assert([e.name isEqualToString:@"OriginalCopy"]); caught=YES; }
        assert(caught && copied==12 && !observing);
        original_mode=0;
        // Startup draws 1 through 4 may not spend the completion budget.
        for(unsigned i=1;i<=4;++i) {
            assert(atomic_load(&draws)==i); errno=EDOM; [command commit];
            assert(errno==EIO && registered==0 && atomic_load(&commits)==0);
            observed_draw(output,rect,image);
        }
        assert(atomic_load(&draws)==5);
        // Readback plan only accepts an exact Shared 2D byte-addressable region.
        ReadbackPlan plan=plan_readback((id)buffer,0,1280,MTLSizeMake(309,250,1),texture,0,0,MTLOriginMake(0,0,0));
        assert(plan.matched && plan.valid && plan.bytes==309*250*4 && plan.row==1236);
        ReadbackPlan invalid=plan_readback((id)buffer,span,1280,MTLSizeMake(309,250,1),texture,0,0,MTLOriginMake(0,0,0));
        assert(invalid.matched && !invalid.valid);
        invalid=plan_readback((id)buffer,0,1235,MTLSizeMake(309,250,1),texture,0,0,MTLOriginMake(0,0,0));
        assert(!invalid.valid);
        invalid=plan_readback((id)buffer,0,1280,MTLSizeMake(309,250,2),texture,0,0,MTLOriginMake(0,0,0));
        assert(!invalid.valid);
        invalid=plan_readback((id)buffer,0,1280,MTLSizeMake(309,250,1),texture,0,0,MTLOriginMake(1,0,0));
        assert(!invalid.valid);
        TestPrivateTexture *private_texture=[TestPrivateTexture new];
        invalid=plan_readback((id)buffer,0,1280,MTLSizeMake(309,250,1),(id)private_texture,0,0,MTLOriginMake(0,0,0));
        assert(!invalid.valid); [private_texture release];
        MacWSOfficeUploadRecord *record=[MacWSOfficeUploadRecord new]; record->ordinal=1;
        assert(reserve_readback(record,(id)buffer));
        assert(!reserve_readback(record,(id)buffer)); // At most one CPU hash per record.
        capture_readback(record,plan,texture);
        assert(record->texture==texture && record->captured);
        errno=EDOM; complete_readback(record,(id)command);
        assert(errno==EDOM && record->finished && !record->texture && reads==1);
        capture_readback(record,plan,texture); assert(!record->texture);
        [record release];
        for (unsigned i=0;i<10;++i) { errno=EDOM; [command commit]; assert(errno==EIO); }
        assert(committed==15 && registered==8 && atomic_load(&commits)==8);
        original_mode=2; errno=EDOM; caught=NO;
        @try { [command commit]; }
        @catch(NSException *e) { assert([e.name isEqualToString:@"OriginalCommit"]); caught=YES; }
        assert(caught && committed==16);
        // Independently bound a >1 MiB context, still with the exact image.
        size_t large_span=4096*300;
        uint8_t *large_pixels=calloc(1,large_span);
        CGContextRef large=CGBitmapContextCreate(large_pixels,1024,300,8,4096,colors,info);
        assert(large); observed_draw(large,rect,image);
        CGContextRelease(large); free(large_pixels);
        for(unsigned i=0;i<20;++i) observed_draw(output,rect,image);
        assert(atomic_load(&draws)==8 && !memcmp(pixels,stock,span));
        CGImageRelease(image); CGContextRelease(imagecontext);
        CGContextRelease(output); CGContextRelease(reference); CGColorSpaceRelease(colors);
        free(imagebytes); free(pixels); free(stock);
        [command release]; [device release]; [buffer release]; [encoder release]; [texture release];
        [expected_pipeline release]; [render release];
        [render_descriptor release]; [render_target release];
        puts("OFFICE-UPLOAD-TEST pixels/arguments/result/errno/budgets/callback PASS");
    }
    return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix="macws-office-upload-test-") as directory:
            binary = str(Path(directory) / "test")
            command = ["xcrun", "clang", "-x", "objective-c", "-fno-objc-arc", "-fblocks",
                       "-Wall", "-Wextra", "-Werror", "-I", str(ROOT), "-",
                       "-framework", "Foundation", "-framework", "CoreGraphics",
                       "-framework", "Metal", "-o", binary]
            subprocess.run(command, input=fixture, text=True, check=True, timeout=30)
            result = subprocess.run([binary], text=True, capture_output=True, check=True, timeout=15)
            self.assertIn("pixels/arguments/result/errno/budgets/callback PASS", result.stdout)
            lines = result.stderr.splitlines()
            self.assertEqual(sum(line.startswith("OFFICE-BITMAP ") for line in lines), 8)
            self.assertEqual(sum(line.startswith("OFFICE-UPLOAD phase=before") for line in lines), 8)
            self.assertEqual(sum(line.startswith("OFFICE-UPLOAD phase=after") for line in lines), 8)
            self.assertEqual(sum(line.startswith("OFFICE-COMMAND pid=") for line in lines), 8)
            self.assertEqual(sum(line.startswith("OFFICE-BLIT pid=") for line in lines), 8)
            self.assertEqual(sum(line.startswith("OFFICE-VIEW phase=before") for line in lines), 8)
            self.assertEqual(sum(line.startswith("OFFICE-VIEW phase=after") for line in lines), 8)
            self.assertEqual(sum(line.startswith("OFFICE-BIND pid=") for line in lines), 8)
            self.assertEqual(sum(line.startswith("OFFICE-MASK pid=") for line in lines), 8)
            self.assertEqual(sum(line.startswith("OFFICE-PASS pid=") for line in lines), 24)
            self.assertIn("format=80 storage=2 samples=4", result.stderr)
            self.assertIn("load=2 store=2", result.stderr)
            self.assertIn("resolve-format=80 resolve-storage=0 resolve-samples=1", result.stderr)
            self.assertIn("observed=1 texture0=", result.stderr)
            self.assertEqual(sum(line.startswith("OFFICE-OPACITY pid=") for line in lines), 8)
            self.assertEqual(sum(line.startswith("OFFICE-MATRIX pid=") for line in lines), 8)
            self.assertEqual(sum(line.startswith("OFFICE-PIPELINE phase=before") for line in lines), 8)
            self.assertEqual(sum(line.startswith("OFFICE-PIPELINE phase=after") for line in lines), 8)
            self.assertIn("vertex=bitmapVS fragment=bitmapIgnoreSrcAlphaPS samples=4", result.stderr)
            self.assertIn("domain=OriginalMetal code=123", result.stderr)
            self.assertIn("raw=3f400000 value=0.75", result.stderr)
            self.assertIn("buffer-offset=16 buffer-row=1248", result.stderr)
            self.assertIn("OFFICE-READBACK pid=", result.stderr)
            self.assertIn("equal=1", result.stderr)
            self.assertIn("row=1248 image=312000 size=[309,250,1]", result.stderr)
            self.assertIn("slice=2 level=3 origin=[4,5,6]", result.stderr)
            self.assertIn("texture-size=[309,250,1] format=80 storage=0 samples=1 mips=1 type=2", result.stderr)
            self.assertIn("commit-start-draw=5", result.stderr)
            self.assertIn("alpha-samples=77250 alpha-nonzero=77250", result.stderr)
            self.assertIn("size=309x250 stride=1280", result.stderr)
            self.assertIn("span=1228800 read=1048576", result.stderr)


if __name__ == "__main__":
    unittest.main()

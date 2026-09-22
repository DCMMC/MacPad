// Explicit, standalone diagnostic; never run during application startup.
// Build on macOS:
// clang -arch arm64 -mmacosx-version-min=13.0 -Wall -Wextra -Werror \
//   -framework Foundation -framework CoreGraphics -framework Metal \
//   -framework QuartzCore -framework IOSurface macws_ca_surface_probe.m -o probe
// Run once for each control: probe bgra; probe b3a8. --trace observes only this
// probe's texture imports. All resources are owned, 16x16, on one Metal queue;
// no WindowServer surface, remote app context, UI or marker file is touched.
// A completed command buffer alone is not a pass: center pixels must be colored
// and opaque. The known failing b3a8 consumer returns status 9, not success.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <QuartzCore/QuartzCore.h>
#import <IOSurface/IOSurfaceRef.h>
#include <mach-o/dyld.h>
#include <mach-o/loader.h>
#include <objc/runtime.h>
#include <dlfcn.h>
#include <unistd.h>

static id (*originalImport)(id,SEL,MTLTextureDescriptor *,IOSurfaceRef,NSUInteger);
static unsigned importCount;
static BOOL (*originalFamily)(id,SEL,NSUInteger);
static unsigned familyCount;
static BOOL traceFamily(id device,SEL sel,NSUInteger family) {
    BOOL result=originalFamily(device,sel,family);
    unsigned index=++familyCount;
    if(index<=32) {
        Dl_info caller={0};dladdr(__builtin_return_address(0),&caller);
        printf("ca-family[%u] caller=%s family=%lu result=%d\n",index,
            caller.dli_fname?caller.dli_fname:"?",(unsigned long)family,result);
    }
    return result;
}
static id traceImport(id device,SEL sel,MTLTextureDescriptor *desc,
                      IOSurfaceRef surface,NSUInteger plane) {
    unsigned index=++importCount;
    if(index<=8) {
        Dl_info caller={0};dladdr(__builtin_return_address(0),&caller);
        printf("ca-import[%u] caller=%s pf=%lu size=%lux%lu plane=%lu usage=%lu storage=%lu surface-pf=%x planes=%zu\n",
            index,caller.dli_fname?caller.dli_fname:"?",
            (unsigned long)desc.pixelFormat,(unsigned long)desc.width,
            (unsigned long)desc.height,(unsigned long)plane,
            (unsigned long)desc.usage,(unsigned long)desc.storageMode,
            IOSurfaceGetPixelFormat(surface),IOSurfaceGetPlaneCount(surface));
    }
    id result=originalImport(device,sel,desc,surface,plane);
    if(index<=8)printf("ca-import[%u] result=%p\n",index,result);
    return result;
}

static void provenance(void) {
    const struct mach_header *mainHeader=_dyld_get_image_header(0);
    printf("main-cputype=%d cpusubtype=%#x\n",mainHeader->cputype,
        (unsigned)mainHeader->cpusubtype);
    for (uint32_t i=0; i<_dyld_image_count(); ++i) {
        const char *name=_dyld_get_image_name(i);
        if (!name || !strstr(name,"libmachook")) continue;
        const struct mach_header_64 *h=(const void *)_dyld_get_image_header(i);
        const uint8_t *p=(const void *)(h+1);
        printf("library=%s cpusubtype=%#x",name,(unsigned)h->cpusubtype);
        for (uint32_t j=0;j<h->ncmds;++j) {
            const struct load_command *c=(const void *)p;
            if(c->cmd==LC_UUID) {
                const struct uuid_command *u=(const void *)p;
                printf(" uuid=");
                for(unsigned k=0;k<16;++k)printf("%02x",u->uuid[k]);
            }
            p+=c->cmdsize;
        }
        puts("");
    }
}
int main(int argc,char **argv) {
    if(argc!=2 && !(argc==3 && !strcmp(argv[2],"--trace")))return 2;
    alarm(10);setvbuf(stdout,NULL,_IONBF,0);
    @autoreleasepool {
        BOOL planar=!strcmp(argv[1],"b3a8");
        if(!planar && strcmp(argv[1],"bgra"))return 2;
        provenance();
        id<MTLDevice>device=MTLCreateSystemDefaultDevice();
        id<MTLCommandQueue>queue=[device newCommandQueue];
        if(!device || !queue)return 3;
        NSMutableDictionary *props=[@{
            @"IOSurfaceWidth":@16,@"IOSurfaceHeight":@16,
            @"IOSurfaceBytesPerElement":planar?@5:@4,
            @"IOSurfaceBytesPerRow":@128,@"IOSurfaceAllocSize":@16384,
            @"IOSurfacePixelFormat":@((uint32_t)(planar?'b3a8':'BGRA'))
        } mutableCopy];
        if(planar)props[@"IOSurfacePlaneInfo"]=@[
            @{@"IOSurfacePlaneWidth":@16,@"IOSurfacePlaneHeight":@16,
              @"IOSurfacePlaneBytesPerElement":@4,@"IOSurfacePlaneBytesPerRow":@64,
              @"IOSurfacePlaneOffset":@0,@"IOSurfacePlaneSize":@1024},
            @{@"IOSurfacePlaneWidth":@16,@"IOSurfacePlaneHeight":@16,
              @"IOSurfacePlaneBytesPerElement":@1,@"IOSurfacePlaneBytesPerRow":@64,
              @"IOSurfacePlaneOffset":@1024,@"IOSurfacePlaneSize":@1024}];
        IOSurfaceRef surface=IOSurfaceCreate((__bridge CFDictionaryRef)props);
        if(!surface)return 4;
        MTLTextureDescriptor *td=[MTLTextureDescriptor
            texture2DDescriptorWithPixelFormat:planar?(MTLPixelFormat)550:MTLPixelFormatBGRA8Unorm
            width:16 height:16 mipmapped:NO];
        td.storageMode=MTLStorageModeShared;
        td.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;
        id<MTLTexture>input=[device newTextureWithDescriptor:td iosurface:surface plane:0];
        td.pixelFormat=MTLPixelFormatBGRA8Unorm;
        id<MTLTexture>output=[device newTextureWithDescriptor:td];
        if(!input || !output)return 5;
        MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];
        rp.colorAttachments[0].texture=input;
        rp.colorAttachments[0].loadAction=MTLLoadActionClear;
        rp.colorAttachments[0].storeAction=MTLStoreActionStore;
        rp.colorAttachments[0].clearColor=MTLClearColorMake(.25,.5,.75,1);
        id<MTLCommandBuffer>clear=[queue commandBuffer];
        id<MTLRenderCommandEncoder>enc=[clear renderCommandEncoderWithDescriptor:rp];
        if(!enc)return 6;
        [enc endEncoding];[clear commit];[clear waitUntilCompleted];
        printf("kind=%s surface=%u clear-status=%lu error=%s\n",argv[1],
            IOSurfaceGetID(surface),(unsigned long)clear.status,
            clear.error?clear.error.description.UTF8String:"none");
        if(clear.status!=MTLCommandBufferStatusCompleted)return 7;
        if(argc==3) {
            typedef uint64_t (*GetProtection)(IOSurfaceRef);
            GetProtection getProtection=(GetProtection)dlsym(RTLD_DEFAULT,
                "IOSurfaceGetProtectionOptions");
            if(getProtection)printf("surface-protection-options=%#llx\n",
                (unsigned long long)getProtection(surface));
            SEL selector=@selector(newTextureWithDescriptor:iosurface:plane:);
            Class cls=object_getClass(device);
            Method method=class_getInstanceMethod(cls,selector);
            if(!method)return 10;
            originalImport=(void *)method_getImplementation(method);
            class_replaceMethod(cls,selector,(IMP)traceImport,method_getTypeEncoding(method));
            SEL familySelector=@selector(supportsFamily:);
            Method familyMethod=class_getInstanceMethod(cls,familySelector);
            if(!familyMethod)return 11;
            originalFamily=(void *)method_getImplementation(familyMethod);
            Dl_info owner={0};dladdr((void *)originalFamily,&owner);
            printf("family-implementation=%s offset=%#lx\n",
                owner.dli_fname?owner.dli_fname:"?",
                (unsigned long)((uintptr_t)originalFamily-(uintptr_t)owner.dli_fbase));
            class_replaceMethod(cls,familySelector,(IMP)traceFamily,
                method_getTypeEncoding(familyMethod));
            typedef CFDictionaryRef (*CopyValues)(IOSurfaceRef);
            CopyValues copyValues=(CopyValues)dlsym(RTLD_DEFAULT,"IOSurfaceCopyAllValues");
            if(copyValues) {CFDictionaryRef values=copyValues(surface);
                if(values) {CFShow(values);CFRelease(values);}}
        }
        CGColorSpaceRef color=CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
        CARenderer *renderer=[CARenderer rendererWithMTLTexture:output
            options:@{kCARendererMetalCommandQueue:queue,
                      kCARendererColorSpace:(__bridge id)color}];
        CALayer *root=[CALayer layer];root.frame=CGRectMake(0,0,16,16);
        CALayer *content=[CALayer layer];content.frame=CGRectMake(4,4,8,8);
        content.contents=(__bridge id)surface;
        content.contentsGravity=kCAGravityResize;
        [root addSublayer:content];
        renderer.bounds=CGRectMake(0,0,16,16);renderer.layer=root;
        [CATransaction flush];
        [renderer beginFrameAtTime:CACurrentMediaTime() timeStamp:NULL];
        [renderer addUpdateRect:renderer.bounds];[renderer render];[renderer endFrame];
        id<MTLCommandBuffer>fence=[queue commandBuffer];
        [fence commit];[fence waitUntilCompleted];
        printf("ca-fence-status=%lu error=%s\n",(unsigned long)fence.status,
            fence.error?fence.error.description.UTF8String:"none");
        if(fence.status!=MTLCommandBufferStatusCompleted)return 8;
        uint8_t bytes[16*16*4]={0};
        [output getBytes:bytes bytesPerRow:64 fromRegion:MTLRegionMake2D(0,0,16,16) mipmapLevel:0];
        unsigned nonzero=0,opaque=0;
        for(unsigned y=4;y<12;++y)for(unsigned x=4;x<12;++x){
            unsigned i=(y*16+x)*4;
            if(bytes[i]||bytes[i+1]||bytes[i+2])++nonzero;
            if(bytes[i+3]>=250)++opaque;
        }
        unsigned c=(8*16+8)*4;
        printf("ca-center-bgra=%u,%u,%u,%u colored=%u/64 opaque=%u/64\n",
            bytes[c],bytes[c+1],bytes[c+2],bytes[c+3],nonzero,opaque);
        renderer.layer=nil;
        CFRelease(surface);CGColorSpaceRelease(color);
        return nonzero==64 && opaque==64?0:9;
    }
}

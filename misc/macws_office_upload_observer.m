/* Explicitly injected, process-local DIAGNOSTIC. Never production-linked.
 * Observe only the four images in our owned PPT Welcome fixture. No code-page
 * patch, pixel replacement, callback wrapping, flag, or command submission.
 * v4: join selected views to bitmap fragment binding/constants/pipeline creation.
 * Compile -fno-objc-arc -fblocks, Foundation/CoreGraphics/Metal.
 */
#import <Foundation/Foundation.h>
#import <CoreGraphics/CoreGraphics.h>
#import <Metal/Metal.h>
#import <objc/runtime.h>
#include <errno.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

enum { OBS_LIMIT = 8, OBS_READ_LIMIT = 1024 * 1024, OBS_MAIN_DRAW_START = 5 };
typedef struct { uintptr_t base; size_t length; unsigned ordinal; } ImageRange;
static ImageRange ranges[OBS_LIMIT];
static pthread_mutex_t ranges_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t install_lock = PTHREAD_MUTEX_INITIALIZER;
static _Atomic unsigned draws, uploads, commits, blits, readbacks, views;
static _Atomic unsigned bindings, opacities, matrices, pipelines, passes;
static _Thread_local int observing;
static Class installed_class;
typedef id (*NoCopyIMP)(id, SEL, void *, NSUInteger, MTLResourceOptions,
                       void (^)(void *, NSUInteger));
static NoCopyIMP original_nocopy;
static void (*original_commit)(id, SEL);
static id (*original_blit_encoder)(id, SEL);
static id (*original_render_encoder)(id, SEL, id);
static Class installed_command_class;
static Class installed_blit_class;
static Class installed_buffer_class;
static id (*original_view)(id, SEL, id, NSUInteger, NSUInteger);
static id (*original_pipeline)(id, SEL, id, NSError **);
static Class installed_render_class;
static void (*original_fragment_texture)(id, SEL, id, NSUInteger);
static void (*original_fragment_bytes)(id, SEL, const void *, NSUInteger, NSUInteger);
static void (*original_vertex_bytes)(id, SEL, const void *, NSUInteger, NSUInteger);
typedef struct { uintptr_t object; unsigned draw; } SelectedView;
typedef struct { uintptr_t encoder; unsigned draw; } SelectedEncoder;
static SelectedView selected_views[OBS_LIMIT];
static SelectedEncoder selected_encoders[OBS_LIMIT];
typedef void (*CopyIMP)(id, SEL, id, NSUInteger, NSUInteger, NSUInteger, MTLSize,
                        id, NSUInteger, NSUInteger, MTLOrigin);
static CopyIMP original_copy;
static char record_key;
static char pass_key;
static char mask_key;
static void install_buffer(id buffer);
static void install_render(id encoder);

@interface MacWSOfficeUploadRecord : NSObject {
@public
    pthread_mutex_t lock;
    BOOL finished, reserved, captured, cpu_valid;
    id<MTLTexture> texture;
    MTLRegion region;
    size_t tight_row, byte_count;
    uint64_t cpu_hash;
    unsigned ordinal, draw;
    char reason[96];
}
@end
@implementation MacWSOfficeUploadRecord
- (id)init {
    if ((self = [super init])) pthread_mutex_init(&lock, NULL);
    return self;
}
- (void)dealloc {
    [texture release]; pthread_mutex_destroy(&lock); [super dealloc];
}
@end

typedef struct {
    BOOL matched, valid;
    unsigned draw;
    MTLRegion region;
    size_t row, bytes;
    uint64_t hash;
    const char *reason;
} ReadbackPlan;

static void emit(const char *line, size_t length) {
    while (length) {
        ssize_t n = write(STDERR_FILENO, line, length);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) return;
        line += n; length -= (size_t)n;
    }
}
static unsigned claim(_Atomic unsigned *counter) {
    unsigned value = atomic_load_explicit(counter, memory_order_relaxed);
    do { if (value >= OBS_LIMIT) return 0; }
    while (!atomic_compare_exchange_weak_explicit(counter, &value, value + 1,
            memory_order_relaxed, memory_order_relaxed));
    return value + 1;
}
static BOOL selected(size_t w, size_t h) {
    return (w == 284 && h == 102) || (w == 309 && h == 250) ||
           (w == 282 && h == 28) || (w == 471 && h == 56);
}
static unsigned match_range(const void *pointer, size_t length) {
    uintptr_t base = (uintptr_t)pointer;
    if (!base || !length || length > UINTPTR_MAX - base) return 0;
    unsigned result = 0;
    pthread_mutex_lock(&ranges_lock);
    for (unsigned i = 0; i < OBS_LIMIT; ++i) {
        ImageRange range = ranges[i];
        if (range.length && (base >= range.base
            ? base - range.base < range.length
            : range.base - base < length)) { result = range.ordinal; break; }
    }
    pthread_mutex_unlock(&ranges_lock);
    return result;
}

static uint64_t tight_hash(const uint8_t *data, size_t row, size_t height,
                           size_t stride, size_t *nonzero) {
    uint64_t hash = UINT64_C(14695981039346656037);
    size_t nz = 0;
    for (size_t y = 0; y < height; ++y) for (size_t x = 0; x < row; ++x) {
        uint8_t value = data[y * stride + x];
        hash ^= value; hash *= UINT64_C(1099511628211); nz += value != 0;
    }
    if (nonzero) *nonzero = nz;
    return hash;
}

static ReadbackPlan plan_readback(id<MTLBuffer> buffer, NSUInteger offset,
        NSUInteger stride, MTLSize size, id<MTLTexture> texture,
        NSUInteger slice, NSUInteger level, MTLOrigin origin) {
    ReadbackPlan plan = {0};
    const uint8_t *data = buffer.contents;
    NSUInteger length = buffer.length;
    plan.draw = match_range(data, length);
    plan.matched = plan.draw != 0;
    plan.reason = "unmatched";
    if (!plan.matched) return plan;
    plan.reason = "unsupported-texture";
    MTLPixelFormat format = texture.pixelFormat;
    if (!texture || texture.storageMode != MTLStorageModeShared ||
        texture.textureType != MTLTextureType2D || texture.sampleCount != 1 ||
        slice || level || origin.z || size.depth != 1 ||
        (format != MTLPixelFormatBGRA8Unorm && format != MTLPixelFormatBGRA8Unorm_sRGB &&
         format != MTLPixelFormatRGBA8Unorm && format != MTLPixelFormatRGBA8Unorm_sRGB)) return plan;
    plan.reason = "invalid-region";
    NSUInteger width = texture.width, height = texture.height;
    if (!size.width || !size.height || origin.x > width || origin.y > height ||
        size.width > width - origin.x || size.height > height - origin.y ||
        size.width > SIZE_MAX / 4) return plan;
    plan.row = size.width * 4;
    if (size.height > OBS_READ_LIMIT / plan.row) return plan;
    plan.bytes = plan.row * size.height;
    plan.reason = "invalid-source-span";
    if (!data || stride < plan.row || offset > length ||
        (size.height - 1) > SIZE_MAX / stride) return plan;
    size_t last_row = (size.height - 1) * stride;
    if (last_row > length - offset || plan.row > length - offset - last_row ||
        length > UINTPTR_MAX - (uintptr_t)data) return plan;
    plan.hash = tight_hash(data + offset, plan.row, size.height, stride, NULL);
    plan.region = (MTLRegion){origin, size};
    plan.valid = YES; plan.reason = "ready";
    return plan;
}

static void capture_readback(MacWSOfficeUploadRecord *record, ReadbackPlan plan, id texture) {
    if (!plan.matched) return;
    pthread_mutex_lock(&record->lock);
    if (!record->finished && !record->captured) {
        record->captured = YES; record->draw = plan.draw;
        record->cpu_valid = plan.valid; record->region = plan.region;
        record->tight_row = plan.row; record->byte_count = plan.bytes; record->cpu_hash = plan.hash;
        snprintf(record->reason, sizeof(record->reason), "%s", plan.reason);
        // Only retain a texture we can safely read. No command or encoder is
        // stored here, so the two independent record owners cannot form a cycle.
        if (plan.valid) record->texture = [texture retain];
    }
    pthread_mutex_unlock(&record->lock);
}

static BOOL reserve_readback(MacWSOfficeUploadRecord *record, id<MTLBuffer> buffer) {
    if (!match_range(buffer.contents, buffer.length)) return NO;
    pthread_mutex_lock(&record->lock);
    BOOL okay = !record->finished && !record->reserved;
    if (okay) record->reserved = YES;
    pthread_mutex_unlock(&record->lock);
    return okay;
}

static void complete_readback(MacWSOfficeUploadRecord *record, id<MTLCommandBuffer> command) {
    int saved_errno = errno;
    pthread_mutex_lock(&record->lock);
    record->finished = YES;
    id<MTLTexture> texture = record->texture; record->texture = nil;
    BOOL captured = record->captured, valid = record->cpu_valid;
    MTLRegion region = record->region;
    size_t row = record->tight_row, bytes = record->byte_count;
    uint64_t expected = record->cpu_hash;
    unsigned ordinal = record->ordinal, draw = record->draw;
    char reason[96]; memcpy(reason, record->reason, sizeof(reason));
    pthread_mutex_unlock(&record->lock);
    uint8_t *copy = NULL;
    @try {
        MTLCommandBufferStatus status = command.status;
        const char *skip = !captured ? "no-matched-copy" : !valid ? reason :
                           status != MTLCommandBufferStatusCompleted ? "command-not-completed" : NULL;
        if (!skip && (!texture || texture.storageMode != MTLStorageModeShared)) skip = "not-shared";
        if (!skip) {
            copy = malloc(bytes);
            if (!copy) skip = "allocation-failed";
        }
        char line[768]; int n;
        if (skip) {
            n = snprintf(line, sizeof(line),
                "OFFICE-READBACK pid=%d sample=%u/8 draw=%u status=%lu skip=%s\n",
                getpid(), ordinal, draw, (unsigned long)status, skip);
        } else {
            [texture getBytes:copy bytesPerRow:row fromRegion:region mipmapLevel:0];
            size_t nonzero = 0;
            uint64_t hash = tight_hash(copy, row, region.size.height, row, &nonzero);
            char prefix[33] = {0}; static const char hex[] = "0123456789abcdef";
            for (size_t i = 0; i < bytes && i < 16; ++i) {
                prefix[i*2] = hex[copy[i] >> 4]; prefix[i*2+1] = hex[copy[i] & 15];
            }
            n = snprintf(line, sizeof(line),
                "OFFICE-READBACK pid=%d sample=%u/8 draw=%u status=%lu texture=%p "
                "region=[%lu,%lu,%lu,%lu] bytes=%zu row=%zu cpu-fnv64=%016llx "
                "gpu-fnv64=%016llx equal=%u nonzero=%zu prefix16=%s\n",
                getpid(), ordinal, draw, (unsigned long)status, (void *)texture,
                (unsigned long)region.origin.x, (unsigned long)region.origin.y,
                (unsigned long)region.size.width, (unsigned long)region.size.height,
                bytes, row, (unsigned long long)expected, (unsigned long long)hash,
                expected == hash, nonzero, prefix);
        }
        if (n > 0 && (size_t)n < sizeof(line)) emit(line, (size_t)n);
    } @catch (NSException *exception) {
        (void)exception;
        static const char line[] = "OFFICE-READBACK skip=diagnostic-getter-exception\n";
        emit(line, sizeof(line)-1);
    } @finally { free(copy); [texture release]; errno = saved_errno; }
}

static id observed_nocopy(id self, SEL cmd, void *bytes, NSUInteger length,
        MTLResourceOptions options, void (^deallocator)(void *, NSUInteger)) {
    int entering_errno = errno;
    unsigned match = observing ? 0 : match_range(bytes, length);
    unsigned ordinal = match ? claim(&uploads) : 0;
    if (ordinal) {
        observing = 1;
        char line[512];
        int n = snprintf(line, sizeof(line),
            "OFFICE-UPLOAD phase=before pid=%d sample=%u/8 draw=%u device=%p "
            "bytes=%p length=%lu options=%#lx deallocator=%p\n",
            getpid(), ordinal, match, (void *)self, bytes,
            (unsigned long)length, (unsigned long)options, (void *)deallocator);
        if (n > 0 && (size_t)n < sizeof(line)) emit(line, (size_t)n);
        observing = 0;
    }
    errno = entering_errno;
    // Preserve the original selector, ownership, exact callback and result.
    id result = original_nocopy(self, cmd, bytes, length, options, deallocator);
    int result_errno = errno;
    if (result && match) install_buffer(result);
    if (ordinal) {
        observing = 1;
        @try {
            void *contents = result ? [(id<MTLBuffer>)result contents] : NULL;
            NSUInteger storage = result ? [(id<MTLBuffer>)result storageMode] : NSUIntegerMax;
            char line[512];
            int n = snprintf(line, sizeof(line),
                "OFFICE-UPLOAD phase=after pid=%d sample=%u/8 draw=%u result=%p "
                "contents=%p storage=%lu alias=%u errno=%d\n",
                getpid(), ordinal, match, (void *)result, contents,
                (unsigned long)storage, contents == bytes, result_errno);
            if (n > 0 && (size_t)n < sizeof(line)) emit(line, (size_t)n);
        } @catch (NSException *exception) {
            (void)exception; // Diagnostic property failure cannot change result.
        } @finally { observing = 0; }
    }
    errno = result_errno;
    return result;
}

static BOOL type_is(Method method, unsigned index, const char *expected) {
    char *type = index == UINT_MAX ? method_copyReturnType(method) :
                                   method_copyArgumentType(method, index);
    BOOL okay = type && strcmp(type, expected) == 0;
    free(type); return okay;
}

typedef struct {
    uintptr_t object;
    NSUInteger width,height,depth,format,storage,samples,type;
} PassTexture;
typedef struct {
    PassTexture texture,resolve;
    NSUInteger load,store,level,slice,depth_plane,resolve_level,resolve_slice,resolve_depth_plane;
} PassAttachment;
typedef struct { PassAttachment color,depth,stencil; } PassSnapshot;
static PassTexture pass_texture(id<MTLTexture> texture) {
    return (PassTexture){(uintptr_t)texture,texture.width,texture.height,texture.depth,
        texture.pixelFormat,texture.storageMode,texture.sampleCount,texture.textureType};
}
static PassAttachment pass_attachment(MTLRenderPassAttachmentDescriptor *attachment) {
    return (PassAttachment){pass_texture(attachment.texture),pass_texture(attachment.resolveTexture),
        attachment.loadAction,attachment.storeAction,attachment.level,attachment.slice,
        attachment.depthPlane,attachment.resolveLevel,attachment.resolveSlice,attachment.resolveDepthPlane};
}
static void capture_pass(id encoder, MTLRenderPassDescriptor *descriptor) {
    if(atomic_load_explicit(&passes,memory_order_relaxed)>=OBS_LIMIT)return;
    @try {
        PassSnapshot snapshot={pass_attachment(descriptor.colorAttachments[0]),
            pass_attachment(descriptor.depthAttachment),pass_attachment(descriptor.stencilAttachment)};
        // Own only numeric metadata, never the pass descriptor or a texture.
        NSData *data=[NSData dataWithBytes:&snapshot length:sizeof(snapshot)];
        objc_setAssociatedObject(encoder,&pass_key,data,OBJC_ASSOCIATION_RETAIN_NONATOMIC);
    } @catch(NSException *exception) { (void)exception; }
}
static void emit_pass(id encoder,unsigned draw) {
    NSData *data=objc_getAssociatedObject(encoder,&pass_key);
    if(data.length!=sizeof(PassSnapshot))return;
    PassSnapshot snapshot; memcpy(&snapshot,data.bytes,sizeof(snapshot));
    objc_setAssociatedObject(encoder,&pass_key,nil,OBJC_ASSOCIATION_RETAIN_NONATOMIC);
    unsigned ordinal=claim(&passes); if(!ordinal)return;
    const char *names[3]={"color0","depth","stencil"};
    PassAttachment attachments[3]={snapshot.color,snapshot.depth,snapshot.stencil};
    for(unsigned i=0;i<3;++i) {
        PassAttachment a=attachments[i]; char line[1024];
        int n=snprintf(line,sizeof(line),
            "OFFICE-PASS pid=%d sample=%u/8 draw=%u encoder=%p attachment=%s "
            "texture=%p size=[%lu,%lu,%lu] format=%lu storage=%lu samples=%lu type=%lu "
            "load=%lu store=%lu level=%lu slice=%lu plane=%lu "
            "resolve=%p resolve-size=[%lu,%lu,%lu] resolve-format=%lu resolve-storage=%lu resolve-samples=%lu "
            "resolve-level=%lu resolve-slice=%lu resolve-plane=%lu\n",
            getpid(),ordinal,draw,(void *)encoder,names[i],(void *)a.texture.object,
            (unsigned long)a.texture.width,(unsigned long)a.texture.height,(unsigned long)a.texture.depth,
            (unsigned long)a.texture.format,(unsigned long)a.texture.storage,(unsigned long)a.texture.samples,
            (unsigned long)a.texture.type,(unsigned long)a.load,(unsigned long)a.store,(unsigned long)a.level,
            (unsigned long)a.slice,(unsigned long)a.depth_plane,(void *)a.resolve.object,
            (unsigned long)a.resolve.width,(unsigned long)a.resolve.height,(unsigned long)a.resolve.depth,
            (unsigned long)a.resolve.format,(unsigned long)a.resolve.storage,(unsigned long)a.resolve.samples,
            (unsigned long)a.resolve_level,(unsigned long)a.resolve_slice,(unsigned long)a.resolve_depth_plane);
        if(n>0 && (size_t)n<sizeof(line))emit(line,(size_t)n);
    }
}

static void remember_view(id view, unsigned ordinal, unsigned draw) {
    if (!view || !ordinal || ordinal > OBS_LIMIT) return;
    pthread_mutex_lock(&ranges_lock);
    selected_views[ordinal - 1] = (SelectedView){(uintptr_t)view, draw};
    pthread_mutex_unlock(&ranges_lock);
}
static unsigned select_encoder_view(id encoder, id texture) {
    unsigned draw = 0, empty = OBS_LIMIT;
    pthread_mutex_lock(&ranges_lock);
    for (unsigned i = 0; i < OBS_LIMIT; ++i)
        if (texture && selected_views[i].object == (uintptr_t)texture) { draw = selected_views[i].draw; break; }
    for (unsigned i = 0; i < OBS_LIMIT; ++i) {
        if (selected_encoders[i].encoder == (uintptr_t)encoder) {
            selected_encoders[i].draw = draw;
            pthread_mutex_unlock(&ranges_lock); return draw;
        }
        if (!selected_encoders[i].encoder || !selected_encoders[i].draw) empty = i;
    }
    if (draw && empty < OBS_LIMIT) selected_encoders[empty] = (SelectedEncoder){(uintptr_t)encoder, draw};
    pthread_mutex_unlock(&ranges_lock);
    return draw;
}
static unsigned encoder_draw(id encoder) {
    unsigned draw = 0;
    pthread_mutex_lock(&ranges_lock);
    for (unsigned i = 0; i < OBS_LIMIT; ++i)
        if (selected_encoders[i].encoder == (uintptr_t)encoder) { draw = selected_encoders[i].draw; break; }
    pthread_mutex_unlock(&ranges_lock); return draw;
}

static void observed_fragment_texture(id self, SEL cmd, id texture, NSUInteger index) {
    // The real binding must occur first; failed/throwing calls are not joined.
    original_fragment_texture(self, cmd, texture, index);
    int result_errno = errno;
    if(index==0 && atomic_load_explicit(&bindings,memory_order_relaxed)<OBS_LIMIT) {
        @try {
            PassTexture mask=pass_texture(texture);
            NSData *data=[NSData dataWithBytes:&mask length:sizeof(mask)];
            objc_setAssociatedObject(self,&mask_key,data,OBJC_ASSOCIATION_RETAIN_NONATOMIC);
        } @catch(NSException *exception) { (void)exception; }
    }
    if (index == 1) {
        unsigned draw = select_encoder_view(self, texture);
        unsigned ordinal = draw ? claim(&bindings) : 0;
        if (ordinal) {
            @try {
                emit_pass(self,draw);
                id<MTLTexture> view = texture;
                char line[512];
                int n = snprintf(line, sizeof(line),
                    "OFFICE-BIND pid=%d sample=%u/8 draw=%u encoder=%p texture=%p index=%lu "
                    "size=[%lu,%lu] format=%lu storage=%lu\n", getpid(), ordinal, draw,
                    (void *)self, (void *)texture, (unsigned long)index, (unsigned long)view.width,
                    (unsigned long)view.height, (unsigned long)view.pixelFormat, (unsigned long)view.storageMode);
                if (n > 0 && (size_t)n < sizeof(line)) emit(line, (size_t)n);
                NSData *mask_data=objc_getAssociatedObject(self,&mask_key);
                PassTexture mask={0}; BOOL known=mask_data.length==sizeof(mask);
                if(known)memcpy(&mask,mask_data.bytes,sizeof(mask));
                n=snprintf(line,sizeof(line),
                    "OFFICE-MASK pid=%d sample=%u/8 draw=%u encoder=%p observed=%u texture0=%p "
                    "size=[%lu,%lu,%lu] format=%lu storage=%lu samples=%lu type=%lu\n",
                    getpid(),ordinal,draw,(void *)self,known,(void *)mask.object,
                    (unsigned long)mask.width,(unsigned long)mask.height,(unsigned long)mask.depth,
                    (unsigned long)mask.format,(unsigned long)mask.storage,(unsigned long)mask.samples,(unsigned long)mask.type);
                if(n>0 && (size_t)n<sizeof(line))emit(line,(size_t)n);
            } @catch (NSException *exception) { (void)exception; }
        }
    }
    errno = result_errno;
}

static void observed_fragment_bytes(id self, SEL cmd, const void *bytes, NSUInteger length, NSUInteger index) {
    int saved_errno = errno;
    unsigned draw = length == 4 && index == 0 && bytes ? encoder_draw(self) : 0;
    unsigned ordinal = draw ? claim(&opacities) : 0;
    if (ordinal) {
        uint32_t bits; float value;
        memcpy(&bits, bytes, sizeof(bits)); memcpy(&value, bytes, sizeof(value));
        char line[384];
        int n = snprintf(line, sizeof(line),
            "OFFICE-OPACITY pid=%d sample=%u/8 draw=%u encoder=%p index=0 length=4 raw=%08x value=%.9g\n",
            getpid(), ordinal, draw, (void *)self, bits, (double)value);
        if (n > 0 && (size_t)n < sizeof(line)) emit(line, (size_t)n);
    }
    errno = saved_errno; original_fragment_bytes(self, cmd, bytes, length, index);
}

static void observed_vertex_bytes(id self, SEL cmd, const void *bytes, NSUInteger length, NSUInteger index) {
    int saved_errno = errno;
    unsigned draw = length == 48 && index == 3 && bytes ? encoder_draw(self) : 0;
    unsigned ordinal = draw ? claim(&matrices) : 0;
    if (ordinal) {
        float matrix[12]; uint32_t bits[12];
        memcpy(matrix, bytes, sizeof(matrix)); memcpy(bits, bytes, sizeof(bits));
        char line[1024];
        int n = snprintf(line, sizeof(line),
            "OFFICE-MATRIX pid=%d sample=%u/8 draw=%u encoder=%p index=3 length=48 "
            "value=[%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g] "
            "raw=[%08x,%08x,%08x,%08x,%08x,%08x,%08x,%08x,%08x,%08x,%08x,%08x]\n",
            getpid(), ordinal, draw, (void *)self,
            (double)matrix[0], (double)matrix[1], (double)matrix[2], (double)matrix[3],
            (double)matrix[4], (double)matrix[5], (double)matrix[6], (double)matrix[7],
            (double)matrix[8], (double)matrix[9], (double)matrix[10], (double)matrix[11],
            bits[0],bits[1],bits[2],bits[3],bits[4],bits[5],bits[6],bits[7],bits[8],bits[9],bits[10],bits[11]);
        if (n > 0 && (size_t)n < sizeof(line)) emit(line, (size_t)n);
    }
    errno = saved_errno; original_vertex_bytes(self, cmd, bytes, length, index);
}

static void install_render(id encoder) {
    if (!encoder) return;
    pthread_mutex_lock(&install_lock);
    if (installed_render_class) { pthread_mutex_unlock(&install_lock); return; }
    Class cls = object_getClass(encoder);
    SEL texture_sel = @selector(setFragmentTexture:atIndex:);
    SEL fragment_sel = @selector(setFragmentBytes:length:atIndex:);
    SEL vertex_sel = @selector(setVertexBytes:length:atIndex:);
    Method texture = class_getInstanceMethod(cls, texture_sel);
    Method fragment = class_getInstanceMethod(cls, fragment_sel);
    Method vertex = class_getInstanceMethod(cls, vertex_sel);
    BOOL valid = texture && fragment && vertex && method_getNumberOfArguments(texture) == 4 &&
        type_is(texture,UINT_MAX,"v") && type_is(texture,0,"@") && type_is(texture,1,":") &&
        type_is(texture,2,"@") && type_is(texture,3,@encode(NSUInteger));
    for (unsigned i=0; valid && i<2; ++i) {
        Method method = i ? vertex : fragment;
        valid = method_getNumberOfArguments(method)==5 && type_is(method,UINT_MAX,"v") &&
            type_is(method,0,"@") && type_is(method,1,":") && type_is(method,2,@encode(const void *)) &&
            type_is(method,3,@encode(NSUInteger)) && type_is(method,4,@encode(NSUInteger));
    }
    if (valid) {
        original_fragment_texture=(void (*)(id,SEL,id,NSUInteger))method_getImplementation(texture);
        original_fragment_bytes=(void (*)(id,SEL,const void *,NSUInteger,NSUInteger))method_getImplementation(fragment);
        original_vertex_bytes=(void (*)(id,SEL,const void *,NSUInteger,NSUInteger))method_getImplementation(vertex);
        Method methods[3]={texture,fragment,vertex}; SEL selectors[3]={texture_sel,fragment_sel,vertex_sel};
        IMP hooks[3]={(IMP)observed_fragment_texture,(IMP)observed_fragment_bytes,(IMP)observed_vertex_bytes};
        for(unsigned i=0;i<3;++i)
            if(!class_addMethod(cls,selectors[i],hooks[i],method_getTypeEncoding(methods[i])))
                method_setImplementation(class_getInstanceMethod(cls,selectors[i]),hooks[i]);
        installed_render_class=cls;
    }
    char line[256]; int n=snprintf(line,sizeof(line),"OFFICE-RENDER install=%s class=%s\n",
        valid?"ready":"unsupported-signature",class_getName(cls));
    if(n>0 && (size_t)n<sizeof(line))emit(line,(size_t)n);
    pthread_mutex_unlock(&install_lock);
}

static id observed_render_encoder(id self, SEL cmd, id descriptor) {
    id result=original_render_encoder(self,cmd,descriptor);
    int saved_errno=errno;
    if(result) {
        select_encoder_view(result,nil);
        objc_setAssociatedObject(result,&mask_key,nil,OBJC_ASSOCIATION_RETAIN_NONATOMIC);
        capture_pass(result,descriptor); install_render(result);
    }
    errno=saved_errno; return result;
}

static id observed_pipeline(id self, SEL cmd, id descriptor, NSError **error) {
    int saved_errno=errno;
    unsigned ordinal=0;
    @try {
        MTLRenderPipelineDescriptor *desc=descriptor;
        NSString *vertex=desc.vertexFunction.name, *fragment=desc.fragmentFunction.name;
        if([vertex isEqualToString:@"bitmapVS"] || [fragment hasPrefix:@"bitmap"]) {
            ordinal=claim(&pipelines);
            if(ordinal) {
                char line[768]; int n=snprintf(line,sizeof(line),
                    "OFFICE-PIPELINE phase=before pid=%d sample=%u/8 descriptor=%p vertex=%.128s fragment=%.128s samples=%lu\n",
                    getpid(),ordinal,(void *)descriptor,vertex?vertex.UTF8String:"nil",
                    fragment?fragment.UTF8String:"nil",(unsigned long)desc.rasterSampleCount);
                if(n>0 && (size_t)n<sizeof(line))emit(line,(size_t)n);
                for(NSUInteger i=0;i<8;++i) {
                    MTLRenderPipelineColorAttachmentDescriptor *a=desc.colorAttachments[i];
                    if(a.pixelFormat==MTLPixelFormatInvalid)continue;
                    n=snprintf(line,sizeof(line),
                        "OFFICE-PIPELINE attachment sample=%u index=%lu format=%lu blend=%u rgb-op=%lu alpha-op=%lu src-rgb=%lu dst-rgb=%lu src-alpha=%lu dst-alpha=%lu write-mask=%#lx\n",
                        ordinal,(unsigned long)i,(unsigned long)a.pixelFormat,a.blendingEnabled,
                        (unsigned long)a.rgbBlendOperation,(unsigned long)a.alphaBlendOperation,
                        (unsigned long)a.sourceRGBBlendFactor,(unsigned long)a.destinationRGBBlendFactor,
                        (unsigned long)a.sourceAlphaBlendFactor,(unsigned long)a.destinationAlphaBlendFactor,
                        (unsigned long)a.writeMask);
                    if(n>0 && (size_t)n<sizeof(line))emit(line,(size_t)n);
                }
            }
        }
    } @catch(NSException *exception) { (void)exception; }
    errno=saved_errno;
    id result=original_pipeline(self,cmd,descriptor,error);
    int result_errno=errno;
    if(ordinal) {
        @try {
            NSError *failure=!result && error?*error:nil;
            char line[512]; int n=snprintf(line,sizeof(line),
                "OFFICE-PIPELINE phase=after pid=%d sample=%u/8 result=%p error=%p domain=%.128s code=%ld errno=%d\n",
                getpid(),ordinal,(void *)result,(void *)failure,failure?failure.domain.UTF8String:"none",
                (long)(failure?failure.code:0),result_errno);
            if(n>0 && (size_t)n<sizeof(line))emit(line,(size_t)n);
        } @catch(NSException *exception) { (void)exception; }
    }
    errno=result_errno; return result;
}

static id observed_view(id self, SEL cmd, id descriptor, NSUInteger offset, NSUInteger row) {
    int entering_errno = errno;
    unsigned ordinal = 0, match = 0;
    if (!observing && atomic_load_explicit(&views, memory_order_relaxed) < OBS_LIMIT) {
        observing = 1;
        @try {
            id<MTLBuffer> source = self;
            match = match_range(source.contents, source.length);
            ordinal = match ? claim(&views) : 0;
            if (ordinal) {
                MTLTextureDescriptor *desc = descriptor;
                char line[768];
                int n = snprintf(line, sizeof(line),
                    "OFFICE-VIEW phase=before pid=%d sample=%u/8 draw=%u buffer=%p "
                    "contents=%p length=%lu buffer-storage=%lu descriptor=%p "
                    "size=[%lu,%lu,%lu] type=%lu format=%lu storage=%lu usage=%#lx "
                    "samples=%lu mips=%lu offset=%lu row=%lu\n",
                    getpid(), ordinal, match, (void *)self, source.contents,
                    (unsigned long)source.length, (unsigned long)source.storageMode,
                    (void *)descriptor, (unsigned long)desc.width, (unsigned long)desc.height,
                    (unsigned long)desc.depth, (unsigned long)desc.textureType,
                    (unsigned long)desc.pixelFormat, (unsigned long)desc.storageMode,
                    (unsigned long)desc.usage, (unsigned long)desc.sampleCount,
                    (unsigned long)desc.mipmapLevelCount, (unsigned long)offset, (unsigned long)row);
                if (n > 0 && (size_t)n < sizeof(line)) emit(line, (size_t)n);
            }
        } @catch (NSException *exception) { (void)exception; }
        @finally { observing = 0; }
    }
    errno = entering_errno;
    id result = original_view(self, cmd, descriptor, offset, row);
    int result_errno = errno;
    if (ordinal) remember_view(result, ordinal, match);
    if (ordinal) {
        observing = 1;
        @try {
            id<MTLTexture> view = result;
            char line[768];
            int n = snprintf(line, sizeof(line),
                "OFFICE-VIEW phase=after pid=%d sample=%u/8 draw=%u result=%p "
                "size=[%lu,%lu,%lu] type=%lu format=%lu storage=%lu usage=%#lx "
                "samples=%lu mips=%lu parent-buffer=%p buffer-offset=%lu buffer-row=%lu errno=%d\n",
                getpid(), ordinal, match, (void *)result,
                (unsigned long)view.width, (unsigned long)view.height, (unsigned long)view.depth,
                (unsigned long)view.textureType, (unsigned long)view.pixelFormat,
                (unsigned long)view.storageMode, (unsigned long)view.usage,
                (unsigned long)view.sampleCount, (unsigned long)view.mipmapLevelCount,
                (void *)view.buffer, (unsigned long)view.bufferOffset,
                (unsigned long)view.bufferBytesPerRow, result_errno);
            if (n > 0 && (size_t)n < sizeof(line)) emit(line, (size_t)n);
        } @catch (NSException *exception) { (void)exception; }
        @finally { observing = 0; }
    }
    errno = result_errno;
    return result;
}

static void install_buffer(id buffer) {
    if (!buffer) return;
    pthread_mutex_lock(&install_lock);
    if (installed_buffer_class) { pthread_mutex_unlock(&install_lock); return; }
    Class cls = object_getClass(buffer);
    SEL selector = @selector(newTextureWithDescriptor:offset:bytesPerRow:);
    Method method = class_getInstanceMethod(cls, selector);
    BOOL valid = method && method_getNumberOfArguments(method) == 5 &&
        type_is(method, UINT_MAX, "@") && type_is(method, 0, "@") && type_is(method, 1, ":") &&
        type_is(method, 2, "@") && type_is(method, 3, @encode(NSUInteger)) &&
        type_is(method, 4, @encode(NSUInteger));
    if (valid) {
        original_view = (id (*)(id,SEL,id,NSUInteger,NSUInteger))method_getImplementation(method);
        if (!class_addMethod(cls, selector, (IMP)observed_view, method_getTypeEncoding(method)))
            method_setImplementation(class_getInstanceMethod(cls, selector), (IMP)observed_view);
        installed_buffer_class = cls;
    }
    char line[256];
    int n = snprintf(line, sizeof(line), "OFFICE-VIEW install=%s class=%s\n",
                     valid ? "ready" : "unsupported-signature", class_getName(cls));
    if (n > 0 && (size_t)n < sizeof(line)) emit(line, (size_t)n);
    pthread_mutex_unlock(&install_lock);
}

static void observed_copy(id self, SEL cmd, id buffer, NSUInteger offset,
        NSUInteger row, NSUInteger image, MTLSize size, id texture,
        NSUInteger slice, NSUInteger level, MTLOrigin origin) {
    int saved_errno = errno;
    if (!observing && atomic_load_explicit(&blits, memory_order_relaxed) < OBS_LIMIT) {
        observing = 1;
        @try {
            void *contents = [(id<MTLBuffer>)buffer contents];
            NSUInteger length = [(id<MTLBuffer>)buffer length];
            unsigned match = match_range(contents, length);
            unsigned ordinal = match ? claim(&blits) : 0;
            if (ordinal) {
                id<MTLTexture> target = texture;
                char line[1024];
                int n = snprintf(line, sizeof(line),
                    "OFFICE-BLIT pid=%d sample=%u/8 draw=%u encoder=%p buffer=%p "
                    "contents=%p length=%lu offset=%lu row=%lu image=%lu "
                    "size=[%lu,%lu,%lu] texture=%p slice=%lu level=%lu origin=[%lu,%lu,%lu] "
                    "texture-size=[%lu,%lu,%lu] format=%lu storage=%lu samples=%lu mips=%lu type=%lu\n",
                    getpid(), ordinal, match, (void *)self, (void *)buffer, contents,
                    (unsigned long)length, (unsigned long)offset, (unsigned long)row,
                    (unsigned long)image, (unsigned long)size.width, (unsigned long)size.height,
                    (unsigned long)size.depth, (void *)texture, (unsigned long)slice,
                    (unsigned long)level, (unsigned long)origin.x, (unsigned long)origin.y,
                    (unsigned long)origin.z, (unsigned long)target.width, (unsigned long)target.height,
                    (unsigned long)target.depth, (unsigned long)target.pixelFormat,
                    (unsigned long)target.storageMode, (unsigned long)target.sampleCount,
                    (unsigned long)target.mipmapLevelCount, (unsigned long)target.textureType);
                if (n > 0 && (size_t)n < sizeof(line)) emit(line, (size_t)n);
            }
        } @catch (NSException *exception) { (void)exception; }
        @finally { observing = 0; }
    }
    MacWSOfficeUploadRecord *record = [objc_getAssociatedObject(self, &record_key) retain];
    ReadbackPlan plan = {0};
    if (record) {
        @try {
            if (reserve_readback(record, buffer))
                plan = plan_readback(buffer, offset, row, size, texture, slice, level, origin);
        }
        @catch (NSException *exception) { (void)exception; }
    }
    errno = saved_errno;
    @try {
        original_copy(self, cmd, buffer, offset, row, image, size, texture, slice, level, origin);
        int result_errno = errno;
        if (record) capture_readback(record, plan, texture);
        errno = result_errno;
    } @finally { int exit_errno = errno; [record release]; errno = exit_errno; }
}

static void install_blit(id encoder) {
    if (!encoder) return;
    pthread_mutex_lock(&install_lock);
    if (installed_blit_class) { pthread_mutex_unlock(&install_lock); return; }
    Class cls = object_getClass(encoder);
    SEL selector = @selector(copyFromBuffer:sourceOffset:sourceBytesPerRow:sourceBytesPerImage:sourceSize:toTexture:destinationSlice:destinationLevel:destinationOrigin:);
    Method method = class_getInstanceMethod(cls, selector);
    BOOL valid = method && method_getNumberOfArguments(method) == 11 &&
        type_is(method, UINT_MAX, "v") && type_is(method, 0, "@") && type_is(method, 1, ":") &&
        type_is(method, 2, "@") && type_is(method, 3, @encode(NSUInteger)) &&
        type_is(method, 4, @encode(NSUInteger)) && type_is(method, 5, @encode(NSUInteger)) &&
        type_is(method, 6, @encode(MTLSize)) && type_is(method, 7, "@") &&
        type_is(method, 8, @encode(NSUInteger)) && type_is(method, 9, @encode(NSUInteger)) &&
        type_is(method, 10, @encode(MTLOrigin));
    if (valid) {
        original_copy = (CopyIMP)method_getImplementation(method);
        if (!class_addMethod(cls, selector, (IMP)observed_copy, method_getTypeEncoding(method)))
            method_setImplementation(class_getInstanceMethod(cls, selector), (IMP)observed_copy);
        installed_blit_class = cls;
    }
    char line[256];
    int n = snprintf(line, sizeof(line), "OFFICE-BLIT install=%s class=%s\n",
                     valid ? "ready" : "unsupported-signature", class_getName(cls));
    if (n > 0 && (size_t)n < sizeof(line)) emit(line, (size_t)n);
    pthread_mutex_unlock(&install_lock);
}

static id observed_blit_encoder(id self, SEL cmd) {
    id result = original_blit_encoder(self, cmd);
    int saved_errno = errno;
    install_blit(result);
    if (result && atomic_load_explicit(&draws, memory_order_relaxed) >= OBS_MAIN_DRAW_START) {
        unsigned ordinal = claim(&readbacks);
        if (ordinal) {
            MacWSOfficeUploadRecord *record = [MacWSOfficeUploadRecord new];
            if (record) {
                record->ordinal = ordinal;
                @try {
                    objc_setAssociatedObject(result, &record_key, record, OBJC_ASSOCIATION_RETAIN_NONATOMIC);
                    [(id<MTLCommandBuffer>)self addCompletedHandler:^(id<MTLCommandBuffer> command) {
                        complete_readback(record, command);
                    }];
                } @finally { [record release]; }
            }
        }
    }
    errno = saved_errno;
    return result;
}

static void observed_commit(id self, SEL cmd) {
    int saved_errno = errno;
    unsigned ordinal = atomic_load_explicit(&draws, memory_order_relaxed) >= OBS_MAIN_DRAW_START ?
                       claim(&commits) : 0;
    if (ordinal) {
        [(id<MTLCommandBuffer>)self addCompletedHandler:^(id<MTLCommandBuffer> command) {
            int callback_errno = errno;
            NSError *error = command.error;
            const char *domain = error ? error.domain.UTF8String : "none";
            char line[512];
            int n = snprintf(line, sizeof(line),
                "OFFICE-COMMAND pid=%d sample=%u/8 command=%p status=%lu "
                "error=%p domain=%.128s code=%ld\n", getpid(), ordinal,
                (void *)command, (unsigned long)command.status, (void *)error,
                domain ? domain : "null", (long)(error ? error.code : 0));
            if (n > 0 && (size_t)n < sizeof(line)) emit(line, (size_t)n);
            errno = callback_errno;
        }];
    }
    errno = saved_errno;
    // No wait, status override, exception interception, or extra submission.
    original_commit(self, cmd);
}

static void install_command_class(Class cls) {
    if (!cls) return;
    pthread_mutex_lock(&install_lock);
    if (installed_command_class) { pthread_mutex_unlock(&install_lock); return; }
    SEL selector = @selector(commit);
    Method method = class_getInstanceMethod(cls, selector);
    Method completed = class_getInstanceMethod(cls, @selector(addCompletedHandler:));
    SEL blit_selector = @selector(blitCommandEncoder);
    Method blit = class_getInstanceMethod(cls, blit_selector);
    BOOL blit_valid = blit && method_getNumberOfArguments(blit) == 2 &&
        type_is(blit, UINT_MAX, "@") && type_is(blit, 0, "@") && type_is(blit, 1, ":");
    SEL render_selector=@selector(renderCommandEncoderWithDescriptor:);
    Method render=class_getInstanceMethod(cls,render_selector);
    BOOL render_valid=render && method_getNumberOfArguments(render)==3 &&
        type_is(render,UINT_MAX,"@") && type_is(render,0,"@") && type_is(render,1,":") && type_is(render,2,"@");
    BOOL valid = method && completed && method_getNumberOfArguments(method) == 2 &&
        method_getNumberOfArguments(completed) == 3 &&
        type_is(method, UINT_MAX, "v") && type_is(method, 0, "@") && type_is(method, 1, ":") &&
        type_is(completed, UINT_MAX, "v") && type_is(completed, 0, "@") &&
        type_is(completed, 1, ":") && type_is(completed, 2, "@?");
    if (valid) {
        original_commit = (void (*)(id,SEL))method_getImplementation(method);
        if (!class_addMethod(cls, selector, (IMP)observed_commit, method_getTypeEncoding(method)))
            method_setImplementation(class_getInstanceMethod(cls, selector), (IMP)observed_commit);
        installed_command_class = cls;
        if (blit_valid) {
            original_blit_encoder = (id (*)(id,SEL))method_getImplementation(blit);
            if (!class_addMethod(cls, blit_selector, (IMP)observed_blit_encoder, method_getTypeEncoding(blit)))
                method_setImplementation(class_getInstanceMethod(cls, blit_selector), (IMP)observed_blit_encoder);
        }
        if(render_valid) {
            original_render_encoder=(id (*)(id,SEL,id))method_getImplementation(render);
            if(!class_addMethod(cls,render_selector,(IMP)observed_render_encoder,method_getTypeEncoding(render)))
                method_setImplementation(class_getInstanceMethod(cls,render_selector),(IMP)observed_render_encoder);
        }
    }
    char line[256];
    int n = snprintf(line, sizeof(line), "OFFICE-COMMAND install=%s class=%s blit-discovery=%s render-discovery=%s commit-start-draw=%u\n",
                     valid ? "ready" : "unsupported-signature", class_getName(cls),
                     valid && blit_valid ? "ready" : "unsupported-signature",
                     valid && render_valid ? "ready" : "unsupported-signature", OBS_MAIN_DRAW_START);
    if (n > 0 && (size_t)n < sizeof(line)) emit(line, (size_t)n);
    pthread_mutex_unlock(&install_lock);
}

static void install_device(id device) {
    if (!device) return;
    pthread_mutex_lock(&install_lock);
    if (installed_class) { pthread_mutex_unlock(&install_lock); return; }
    Class cls = object_getClass(device);
    SEL selector = @selector(newBufferWithBytesNoCopy:length:options:deallocator:);
    Method method = class_getInstanceMethod(cls, selector);
    SEL pipeline_selector=@selector(newRenderPipelineStateWithDescriptor:error:);
    Method pipeline=class_getInstanceMethod(cls,pipeline_selector);
    BOOL pipeline_valid=pipeline && method_getNumberOfArguments(pipeline)==4 &&
        type_is(pipeline,UINT_MAX,"@") && type_is(pipeline,0,"@") && type_is(pipeline,1,":") &&
        type_is(pipeline,2,"@") && type_is(pipeline,3,@encode(NSError **));
    BOOL valid = method && method_getNumberOfArguments(method) == 6 &&
        type_is(method, UINT_MAX, "@") && type_is(method, 0, "@") &&
        type_is(method, 1, ":") && type_is(method, 2, "^v") &&
        type_is(method, 3, @encode(NSUInteger)) &&
        type_is(method, 4, @encode(MTLResourceOptions)) && type_is(method, 5, "@?");
    if (valid) {
        original_nocopy = (NoCopyIMP)method_getImplementation(method);
        // Add an override on this concrete class if it inherited the method;
        // never modify an inherited Method and accidentally hook sibling devices.
        if (!class_addMethod(cls, selector, (IMP)observed_nocopy,
                             method_getTypeEncoding(method)))
            method_setImplementation(class_getInstanceMethod(cls, selector),
                                     (IMP)observed_nocopy);
        installed_class = cls;
        if(pipeline_valid) {
            original_pipeline=(id (*)(id,SEL,id,NSError **))method_getImplementation(pipeline);
            if(!class_addMethod(cls,pipeline_selector,(IMP)observed_pipeline,method_getTypeEncoding(pipeline)))
                method_setImplementation(class_getInstanceMethod(cls,pipeline_selector),(IMP)observed_pipeline);
        }
    }
    char line[256];
    int n = snprintf(line, sizeof(line), "OFFICE-UPLOAD install=%s class=%s pipeline=%s\n",
                     valid ? "ready" : "unsupported-signature", class_getName(cls),
                     valid && pipeline_valid?"ready":"unsupported-signature");
    if (n > 0 && (size_t)n < sizeof(line)) emit(line, (size_t)n);
    pthread_mutex_unlock(&install_lock);
}

static void observe_bitmap(CGContextRef context, CGImageRef image, CGRect rect) {
    if (!context || !image || observing ||
        atomic_load_explicit(&draws, memory_order_relaxed) >= OBS_LIMIT) return;
    int saved_errno = errno;
    observing = 1;
    @try {
        size_t iw = CGImageGetWidth(image), ih = CGImageGetHeight(image);
        if (!selected(iw, ih)) return;
        unsigned ordinal = claim(&draws);
        if (!ordinal) return;
        size_t width = CGBitmapContextGetWidth(context), height = CGBitmapContextGetHeight(context);
        size_t stride = CGBitmapContextGetBytesPerRow(context);
        size_t bpp = CGBitmapContextGetBitsPerPixel(context), bpc = CGBitmapContextGetBitsPerComponent(context);
        CGBitmapInfo bitmap = CGBitmapContextGetBitmapInfo(context);
        CGImageAlphaInfo alpha = CGBitmapContextGetAlphaInfo(context);
        uint8_t *data = CGBitmapContextGetData(context);
        size_t span = stride && height <= SIZE_MAX / stride ? stride * height : 0;
        if (!data || span > UINTPTR_MAX - (uintptr_t)data) span = 0;
        size_t count = span < OBS_READ_LIMIT ? span : OBS_READ_LIMIT;
        uint64_t hash = UINT64_C(14695981039346656037);
        size_t nonzero = 0, alpha_nonzero = 0, alpha_samples = 0;
        unsigned alpha_offset = UINT_MAX;
        // Statistics only for documented 8-bit RGBA/ARGB variants. Never
        // interpret skip-alpha, float, packed, or unknown byte-order layouts.
        if (bpc == 8 && bpp == 32 && !(bitmap & kCGBitmapFloatComponents)) {
            unsigned order = bitmap & kCGBitmapByteOrderMask;
            BOOL first = alpha == kCGImageAlphaPremultipliedFirst || alpha == kCGImageAlphaFirst;
            BOOL last = alpha == kCGImageAlphaPremultipliedLast || alpha == kCGImageAlphaLast;
            if ((first || last) && order == kCGBitmapByteOrder32Little) alpha_offset = first ? 3 : 0;
            if ((first || last) && order == kCGBitmapByteOrder32Big) alpha_offset = first ? 0 : 3;
        }
        for (size_t i = 0; i < count; ++i) { hash ^= data[i]; hash *= UINT64_C(1099511628211); nonzero += data[i] != 0; }
        char prefix[33] = {0};
        static const char hex[] = "0123456789abcdef";
        for (size_t i = 0; i < count && i < 16; ++i) {
            prefix[i * 2] = hex[data[i] >> 4]; prefix[i * 2 + 1] = hex[data[i] & 15];
        }
        if (alpha_offset != UINT_MAX && width <= SIZE_MAX / 4 && width * 4 <= stride) {
            for (size_t row = 0; row < height && row * stride < count; ++row)
                for (size_t col = 0; col < width; ++col) {
                    size_t offset = row * stride + col * 4 + alpha_offset;
                    if (offset >= count) break;
                    ++alpha_samples; alpha_nonzero += data[offset] != 0;
                }
        }
        if (span) {
            pthread_mutex_lock(&ranges_lock);
            ranges[ordinal - 1] = (ImageRange){(uintptr_t)data, span, ordinal};
            pthread_mutex_unlock(&ranges_lock);
        }
        char line[768];
        int n = snprintf(line, sizeof(line),
            "OFFICE-BITMAP pid=%d sample=%u/8 image=%zux%zu context=%p data=%p "
            "size=%zux%zu stride=%zu bpc=%zu bpp=%zu bitmap=%#x alpha=%u "
            "span=%zu read=%zu fnv64=%016llx prefix16=%s nonzero=%zu alpha-offset=%u "
            "alpha-samples=%zu alpha-nonzero=%zu rect=[%.9g,%.9g,%.9g,%.9g]\n",
            getpid(), ordinal, iw, ih, (void *)context, data, width, height, stride,
            bpc, bpp, (unsigned)bitmap, (unsigned)alpha, span, count,
            (unsigned long long)hash, prefix, nonzero, alpha_offset, alpha_samples, alpha_nonzero,
            (double)rect.origin.x, (double)rect.origin.y, (double)rect.size.width, (double)rect.size.height);
        if (n > 0 && (size_t)n < sizeof(line)) emit(line, (size_t)n);
    } @catch (NSException *exception) { (void)exception; }
    @finally { observing = 0; errno = saved_errno; }
}

static void observed_draw(CGContextRef context, CGRect rect, CGImageRef image) {
#ifndef MACWS_OFFICE_UPLOAD_TEST
    if (!observing && image && selected(CGImageGetWidth(image),CGImageGetHeight(image)) &&
        atomic_load_explicit(&draws, memory_order_relaxed) < OBS_LIMIT) {
        int saved_errno = errno;
        observing = 1;
        @try {
            id device = MTLCreateSystemDefaultDevice();
            install_device(device);
            [device release];
            // Concrete class witnessed in the active AGX image; still verify
            // its public signatures. No class-list scan or new command queue.
            install_command_class(objc_getClass("AGXG13GFamilyCommandBuffer"));
        } @finally { observing = 0; errno = saved_errno; }
    }
#endif
    CGContextDrawImage(context, rect, image);
    observe_bitmap(context, image, rect);
}

#ifndef MACWS_OFFICE_UPLOAD_TEST
__attribute__((used, section("__DATA,__interpose")))
static const struct { const void *replacement; const void *replacee; } draw_interpose = {
    (const void *)&observed_draw, (const void *)&CGContextDrawImage
};
#endif

// macwsallocd — iOS-native helper that allocates IOSurface-backed memory
// regions on behalf of the chroot WindowServer. WS in AGX-native mode hits
// a kernel-side block on sel=0xa (IOGPUResourceCreate) for heap-creates —
// the user-client it opens doesn't expose that selector. This daemon runs
// in iOS-native context (sees the real AGX user-client), allocates an
// IOSurface of the requested size (kernel-blessed shared GPU memory), and
// returns the IOSurface mach send-right back to the chroot via XPC.
//
// Listens on the launchd-published mach service `com.macwsguide.alloc`.
// See layout/Library/LaunchDaemons/com.macwsguide.alloc.plist.
//
// Protocol:
//   request dict:
//     "op"      = "alloc-iosurf"
//     "size"    = u64  (byte count, capped at 256 MB)
//     "options" = u64  (MTLResourceOptions passed through; informational)
//   reply dict:
//     "result"  = "ok" | "size_invalid" | "io_create_fail"
//     "surface" = mach send-right for IOSurface (if ok)
//     "alloc_size" = u64 (actual allocation, page-rounded)

@import Foundation;
@import IOSurface;
@import Darwin;
#include <xpc/xpc.h>
#include <dispatch/dispatch.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <mach/mach.h>
// mach_vm.h is unsupported in iOS SDK — manually declare what we need.
extern kern_return_t mach_make_memory_entry_64(
    vm_map_t target_task, memory_object_size_t *size,
    memory_object_offset_t offset, vm_prot_t permission,
    mach_port_t *object_handle, mach_port_t parent_entry);
#ifndef MAP_MEM_NAMED_CREATE
#define MAP_MEM_NAMED_CREATE 0x020000
#endif
#include <IOKit/IOKitLib.h>

// AGX user-client borrowing — opens IOAcceleratorES from iOS-native context
// where kernel runs the full privileged init (sets per-UC device->0x108 size
// limit, this->0x100 enable byte, etc.), then sends the mach port for the
// io_connect_t back to chroot WS. The chroot then uses the borrowed
// connection for direct IOConnectCallMethod calls — the kernel processes
// each call against the UC's stored state which was set when WE opened it.
//
// This sidesteps the macOS-userland-on-iOS-kernel rejection where IOServiceOpen
// returns a UC whose privileged init step was skipped.
static io_connect_t g_borrow_conn = MACH_PORT_NULL;
static dispatch_once_t g_borrow_once;

static void borrow_serve(xpc_object_t event) {
    dispatch_once(&g_borrow_once, ^{
        io_service_t agx = IOServiceGetMatchingService(kIOMasterPortDefault,
                                                       IOServiceMatching("IOAcceleratorES"));
        if (agx == IO_OBJECT_NULL) {
            NSLog(@"borrow-agx-conn: IOServiceGetMatchingService(IOAcceleratorES) returned NULL");
            return;
        }
        // type=1 matches iOS Metal's "full IOGPUDevice user-client" type.
        // Helper has iOS-Apple-signed-equivalent context so kernel runs the
        // privileged init paths that set device->0x108 etc. on the UC.
        kern_return_t kr = IOServiceOpen(agx, mach_task_self(), 1, &g_borrow_conn);
        IOObjectRelease(agx);
        if (kr != KERN_SUCCESS) {
            NSLog(@"borrow-agx-conn: IOServiceOpen failed kr=%#x", kr);
            g_borrow_conn = MACH_PORT_NULL;
            return;
        }
        NSLog(@"borrow-agx-conn: opened AGX io_connect_t = %u", g_borrow_conn);
    });

    // Crash-safety: wrap the reply construction so a malformed mach right
    // can never explode the daemon (the last test triggered EXC_GUARD
    // ILLEGAL_MOVE when the io_connect_t reuse was racy, spinning launchd
    // respawn → 124 load average on the device).
    @try {
        xpc_connection_t peer = xpc_dictionary_get_remote_connection(event);
        xpc_object_t r = xpc_dictionary_create_reply(event);
        NSLog(@"borrow-agx-conn: building reply r=%p peer=%p g_borrow_conn=%u",
            r, peer, g_borrow_conn);
        if (!r || !peer) {
            NSLog(@"borrow-agx-conn: no reply context (r=%p peer=%p)", r, peer);
            return;
        }
        if (g_borrow_conn != MACH_PORT_NULL) {
            // xpc_dictionary_set_mach_send DOES NOT consume the send right
            // (it's a copy-send, not move-send by default for XPC). So no
            // mod_refs needed — we can just set it and keep g_borrow_conn
            // valid for future borrowers. The earlier mod_refs+1 we tried
            // was probably what triggered the EXC_GUARD on an io_connect_t,
            // since io_connect_t mach ports may be guarded by IOKit.
            NSLog(@"borrow-agx-conn: about to set_mach_send port=%u", g_borrow_conn);
            xpc_dictionary_set_string(r, "result", "ok");
            xpc_dictionary_set_mach_send(r, "connect", g_borrow_conn);
            NSLog(@"borrow-agx-conn: set_mach_send completed");
        } else {
            xpc_dictionary_set_string(r, "result", "open_failed");
        }
        NSLog(@"borrow-agx-conn: about to send_message");
        xpc_connection_send_message(peer, r);
        NSLog(@"borrow-agx-conn served: g_borrow_conn=%u (send done)", g_borrow_conn);
    } @catch (NSException *e) {
        NSLog(@"borrow-agx-conn: EXCEPTION %@", e);
    }
}

static void alloc_serve(xpc_object_t event) {
    if (xpc_get_type(event) != XPC_TYPE_DICTIONARY) return;
    const char *op = xpc_dictionary_get_string(event, "op");
    if (!op) return;
    if (strcmp(op, "borrow-agx-conn") == 0) {
        borrow_serve(event);
        return;
    }
    if (strcmp(op, "make-mem-entry") == 0) {
        // 2026-06-19 — mach_make_memory_entry_64 helper.
        // For chroot WS's type=0x80 standalone client-buffer sel=0xa path,
        // iOS kernel rejects raw mmap'd CPU VAs. The expected input is a
        // mach memory entry created via `mach_make_memory_entry_64` (or
        // equivalent IOMemoryDescriptor flavor) where the entry's owner
        // credentials let iOS kernel safely map it for GPU access.
        //
        // We run in iOS-native context so this call works without funky
        // entitlement issues. We mmap + mlock the buffer and create the
        // entry with MAP_MEM_NAMED_CREATE, then send the entry port to
        // the caller.
        //
        // Open question: does the entry mach port cross task boundaries
        // via XPC the way IOSurface does? IOSurface's IOSurfaceCreateMachPort
        // works fine across XPC, but io_connect_t triggers EXC_GUARD. A
        // mach memory entry is a regular Mach VM object, no IOKit guards,
        // so should be OK — but needs runtime confirmation.
        uint64_t size_req = xpc_dictionary_get_uint64(event, "size");
        xpc_connection_t peer = xpc_dictionary_get_remote_connection(event);
        xpc_object_t r = xpc_dictionary_create_reply(event);
        const char *result = "ok";
        mach_port_t entry = MACH_PORT_NULL;
        uint64_t actual = 0;
        void *cpu_buf = NULL;
        if (size_req == 0 || size_req > (256ULL * 1024 * 1024)) {
            result = "size_invalid";
        } else {
            actual = (size_req + 0xfff) & ~0xfffULL;
            cpu_buf = mmap(NULL, actual, PROT_READ | PROT_WRITE,
                MAP_PRIVATE | MAP_ANON, -1, 0);
            if (cpu_buf == MAP_FAILED) {
                result = "mmap_fail";
                cpu_buf = NULL;
            } else {
                memset(cpu_buf, 0, actual);
                (void)mlock(cpu_buf, actual);
                memory_object_size_t sz = (memory_object_size_t)actual;
                kern_return_t kr = mach_make_memory_entry_64(
                    mach_task_self(), &sz,
                    (memory_object_offset_t)cpu_buf,
                    VM_PROT_READ | VM_PROT_WRITE | MAP_MEM_NAMED_CREATE,
                    &entry, MACH_PORT_NULL);
                NSLog(@"make-mem-entry: mmap %p (%llu B) entry kr=%#x port=%u sz_out=%llu",
                    cpu_buf, actual, kr, entry, (unsigned long long)sz);
                if (kr != KERN_SUCCESS) {
                    result = "entry_fail";
                    munmap(cpu_buf, actual);
                    cpu_buf = NULL;
                    entry = MACH_PORT_NULL;
                }
            }
        }
        if (r && peer) {
            xpc_dictionary_set_string(r, "result", result);
            if (entry != MACH_PORT_NULL) {
                xpc_dictionary_set_mach_send(r, "entry", entry);
                xpc_dictionary_set_uint64(r, "size", actual);
                // Send the iOS-side CPU VA as a u64 too — informational
                // only (chroot can't dereference it; it's in our task).
                xpc_dictionary_set_uint64(r, "helper_va", (uint64_t)cpu_buf);
            }
            xpc_connection_send_message(peer, r);
        }
        if (entry != MACH_PORT_NULL) {
            mach_port_deallocate(mach_task_self(), entry);
        }
        // Do NOT munmap/free cpu_buf — the entry references it. The
        // mapping persists until all entry send-rights are dropped.
        NSLog(@"make-mem-entry size_req=%llu actual=%llu -> %s port=%u",
            size_req, actual, result, entry);
        return;
    }
    if (strcmp(op, "alloc-iosurf") != 0) return;

    uint64_t size = xpc_dictionary_get_uint64(event, "size");
    uint64_t options = xpc_dictionary_get_uint64(event, "options");
    (void)options;

    xpc_connection_t peer = xpc_dictionary_get_remote_connection(event);
    xpc_object_t r = xpc_dictionary_create_reply(event);
    const char *result = "ok";
    mach_port_t surfPort = MACH_PORT_NULL;
    uint64_t alloc_size = 0;

    if (size == 0 || size > (256ULL * 1024 * 1024)) {
        result = "size_invalid";
    } else {
        // kIOSurfaceIsGlobal=YES makes the surface looked-up-able cross-
        // process via IOSurfaceLookup(int id) — the per-task
        // IOSurfaceClient.IOSurfaceLookupFromMachPort path fails for ports
        // created in another task (runtime-confirmed 2026-06-19: chroot's
        // local-test roundtrip works; cross-process port lookup returns nil).
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
        NSDictionary *props = @{
            (id)kIOSurfaceWidth:           @(size),
            (id)kIOSurfaceHeight:          @(1),
            (id)kIOSurfaceBytesPerElement: @(1),
            (id)kIOSurfacePixelFormat:     @((uint32_t)'L008'),
            (id)kIOSurfaceIsGlobal:        @YES,
        };
#pragma clang diagnostic pop
        IOSurfaceRef surf = IOSurfaceCreate((__bridge CFDictionaryRef)props);
        if (!surf) {
            result = "io_create_fail";
        } else {
            surfPort = IOSurfaceCreateMachPort(surf);
            alloc_size = IOSurfaceGetAllocSize(surf);
            uint32_t iosurfid = IOSurfaceGetID(surf);
            // Keep surface ref retained until after reply — defense in depth.
            xpc_dictionary_set_string(r, "result", result);
            if (surfPort != MACH_PORT_NULL) {
                xpc_dictionary_set_mach_send(r, "surface", surfPort);
            }
            xpc_dictionary_set_uint64(r, "alloc_size", alloc_size);
            xpc_dictionary_set_uint64(r, "iosurface_id", iosurfid);
            xpc_connection_send_message(peer, r);
            // Self-roundtrip both APIs as sanity for the iOS-native side.
            IOSurfaceRef rt_port = (surfPort != MACH_PORT_NULL) ?
                IOSurfaceLookupFromMachPort(surfPort) : NULL;
            IOSurfaceRef rt_id = IOSurfaceLookup(iosurfid);
            NSLog(@"alloc-iosurf self-roundtrip: port=%u -> surf=%p | id=%u -> surf=%p",
                surfPort, rt_port, iosurfid, rt_id);
            if (rt_port) CFRelease(rt_port);
            if (rt_id) CFRelease(rt_id);
            CFRelease(surf);
            if (surfPort != MACH_PORT_NULL) {
                mach_port_deallocate(mach_task_self(), surfPort);
            }
            NSLog(@"alloc-iosurf size=%llu opts=%#llx -> ok alloc=%llu port=%u id=%u (IsGlobal=YES)",
                size, options, alloc_size, surfPort, iosurfid);
            return;
        }
    }

    if (r && peer) {
        xpc_dictionary_set_string(r, "result", result);
        if (surfPort != MACH_PORT_NULL) {
            xpc_dictionary_set_mach_send(r, "surface", surfPort);
            xpc_dictionary_set_uint64(r, "alloc_size", alloc_size);
        }
        xpc_connection_send_message(peer, r);
    }
    if (surfPort != MACH_PORT_NULL) {
        mach_port_deallocate(mach_task_self(), surfPort);
    }
    NSLog(@"alloc-iosurf size=%llu options=%#llx -> %s (alloc=%llu, port=%u)",
        size, options, result, alloc_size, surfPort);
}

#include <dlfcn.h>

int main(int argc, const char **argv) {
    NSLog(@"macwsallocd starting (pid=%d)", getpid());

    // Resolve dynamically — the iOS xpc header marks this API unavailable
    // even though the symbol is exported from libSystem at runtime.
    xpc_connection_t (*createMach)(const char *, dispatch_queue_t, uint64_t) =
        dlsym(RTLD_DEFAULT, "xpc_connection_create_mach_service");
    if (!createMach) {
        NSLog(@"macwsallocd: xpc_connection_create_mach_service symbol missing");
        return 1;
    }

    dispatch_queue_t q = dispatch_queue_create("com.macwsguide.alloc", DISPATCH_QUEUE_SERIAL);
    xpc_connection_t listener = createMach(
        "com.macwsguide.alloc", q, XPC_CONNECTION_MACH_SERVICE_LISTENER);
    if (!listener) {
        NSLog(@"macwsallocd: failed to create mach listener");
        return 1;
    }
    xpc_connection_set_event_handler(listener, ^(xpc_object_t peer) {
        if (xpc_get_type(peer) != XPC_TYPE_CONNECTION) return;
        xpc_connection_set_event_handler((xpc_connection_t)peer, ^(xpc_object_t event) {
            alloc_serve(event);
        });
        xpc_connection_resume((xpc_connection_t)peer);
    });
    xpc_connection_resume(listener);
    NSLog(@"macwsallocd: published com.macwsguide.alloc");

    dispatch_main();
    return 0;
}

#import "MacWSCatalystDrawableReceiver.h"

#import "MacWSHostDiagnostics.h"

#import <IOSurface/IOSurfaceRef.h>

#include <mach/bootstrap.h>
#include <mach/mach.h>

#include "macws_catalyst_drawable_protocol.h"

extern pid_t audit_token_to_pid(audit_token_t token);

NSNotificationName const MacWSCatalystDrawableDidPresentNotification =
    @"MacWSCatalystDrawableDidPresentNotification";

static dispatch_source_t DrawableSource;
static mach_port_t DrawableReceivePort = MACH_PORT_NULL;

void MacWSStartCatalystDrawableReceiver(void) {
    static dispatch_once_t onceToken;
    dispatch_once(&onceToken, ^{
        mach_port_t receivePort = MACH_PORT_NULL;
        kern_return_t result = mach_port_allocate(
            mach_task_self(), MACH_PORT_RIGHT_RECEIVE, &receivePort);
        if (result != KERN_SUCCESS) {
            MacWSLog(@"catalyst-drawable port-allocate failed kr=%d", result);
            return;
        }
        result = mach_port_insert_right(
            mach_task_self(), receivePort, receivePort,
            MACH_MSG_TYPE_MAKE_SEND);
        if (result != KERN_SUCCESS) {
            (void)mach_port_mod_refs(mach_task_self(), receivePort,
                                    MACH_PORT_RIGHT_RECEIVE, -1);
            MacWSLog(@"catalyst-drawable port-insert failed kr=%d", result);
            return;
        }
        result = bootstrap_register(
            bootstrap_port, MACWS_CATALYST_DRAWABLE_MACH_SERVICE,
            receivePort);
        (void)mach_port_mod_refs(mach_task_self(), receivePort,
                                MACH_PORT_RIGHT_SEND, -1);
        if (result != BOOTSTRAP_SUCCESS) {
            (void)mach_port_mod_refs(mach_task_self(), receivePort,
                                    MACH_PORT_RIGHT_RECEIVE, -1);
            MacWSLog(@"catalyst-drawable bootstrap-register failed kr=%d",
                     result);
            return;
        }
        DrawableReceivePort = receivePort;
        DrawableSource = dispatch_source_create(
            DISPATCH_SOURCE_TYPE_MACH_RECV, (uintptr_t)receivePort, 0,
            dispatch_get_main_queue());
        dispatch_source_set_event_handler(DrawableSource, ^{
            for (;;) {
                _Alignas(8) uint8_t bytes[
                    sizeof(MacWSCatalystDrawableMachMessage) +
                    MAX_TRAILER_SIZE] = {0};
                MacWSCatalystDrawableMachMessage *message =
                    (MacWSCatalystDrawableMachMessage *)bytes;
                mach_msg_return_t received = mach_msg(
                    &message->header,
                    MACH_RCV_MSG | MACH_RCV_TIMEOUT |
                        MACH_RCV_TRAILER_TYPE(MACH_MSG_TRAILER_FORMAT_0) |
                        MACH_RCV_TRAILER_ELEMENTS(MACH_RCV_TRAILER_AUDIT),
                    0, sizeof(bytes), receivePort, 0, MACH_PORT_NULL);
                if (received == MACH_RCV_TIMED_OUT) break;
                if (received != MACH_MSG_SUCCESS) {
                    MacWSLog(@"catalyst-drawable receive failed kr=%d",
                             received);
                    break;
                }
                mach_port_t surfacePort = message->surfacePort.name;
                mach_msg_audit_trailer_t *trailer =
                    (mach_msg_audit_trailer_t *)(bytes +
                        round_msg(message->header.msgh_size));
                BOOL trailerValid =
                    (uint8_t *)(trailer + 1) <= bytes + sizeof(bytes) &&
                    trailer->msgh_trailer_type ==
                        MACH_MSG_TRAILER_FORMAT_0 &&
                    trailer->msgh_trailer_size >= sizeof(*trailer);
                pid_t senderPID = trailerValid
                    ? audit_token_to_pid(trailer->msgh_audit) : -1;
                BOOL envelopeValid =
                    message->header.msgh_id ==
                        MACWS_CATALYST_DRAWABLE_MACH_MESSAGE_ID &&
                    message->header.msgh_size == sizeof(*message) &&
                    (message->header.msgh_bits & MACH_MSGH_BITS_COMPLEX) &&
                    message->body.msgh_descriptor_count == 1 &&
                    message->surfacePort.type == MACH_MSG_PORT_DESCRIPTOR &&
                    MACH_PORT_VALID(surfacePort) && trailerValid;
                if (!envelopeValid) {
                    if (MACH_PORT_VALID(surfacePort))
                        (void)mach_port_deallocate(
                            mach_task_self(), surfacePort);
                    continue;
                }
                MacWSCatalystDrawableRecord record = message->record;
                if (!MacWSCatalystDrawableRecordIsValid(
                        &record, sizeof(record)) ||
                    record.ownerPID != senderPID) {
                    (void)mach_port_deallocate(mach_task_self(), surfacePort);
                    continue;
                }
                IOSurfaceRef surface = IOSurfaceLookupFromMachPort(surfacePort);
                (void)mach_port_deallocate(mach_task_self(), surfacePort);
                if (!surface) continue;
                NSData *payload = [NSData dataWithBytes:&record
                                                  length:sizeof(record)];
                NSMutableDictionary *delivery = [@{
                    @"record": payload,
                    @"surface": (__bridge id)surface,
                } mutableCopy];
                [NSNotificationCenter.defaultCenter
                    postNotificationName:
                        MacWSCatalystDrawableDidPresentNotification
                    object:delivery];
                if ((record.flags &
                        MacWSCatalystDrawableTransfersUseCount) != 0 &&
                    ![delivery[@"accepted"] boolValue]) {
                    IOSurfaceDecrementUseCount(surface);
                }
                CFRelease(surface);
            }
        });
        dispatch_resume(DrawableSource);
        MacWSLog(@"catalyst-drawable receiver-ready service=%s port=%u",
                 MACWS_CATALYST_DRAWABLE_MACH_SERVICE, receivePort);
    });
}

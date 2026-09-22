#import <Foundation/Foundation.h>
#import <Metal/Metal.h>

#include "macws_host_protocol.h"
#include "macws_stream_protocol.h"

NS_ASSUME_NONNULL_BEGIN

BOOL MacWSLegacyFramebufferFallbackEnabled(void);
BOOL MacWSAppInputEndpointReady(int32_t pid);
BOOL MacWSStreamFrameGeometryEqual(MacWSStreamFrameDescriptor left,
                                   MacWSStreamFrameDescriptor right);
NSUInteger MacWSIOSurfaceReadOnlyTextureAlignment(id<MTLDevice> device);
CGFloat MacWSDensityModeFactor(MacWSHostDisplayDensity density);
BOOL MacWSSendInputRecord(const MacWSInputRecord *record,
                          int *_Nullable errorOut);
BOOL MacWSReadCaptureAck(uint64_t *_Nullable generationOut);
void MacWSLogMetalRegistryState(void);

NS_ASSUME_NONNULL_END

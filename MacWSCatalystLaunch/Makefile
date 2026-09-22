ARCHS = arm64 arm64e
TARGET = iphone:clang:latest:16.0

include ../config/production.mk
include $(THEOS)/makefiles/common.mk

TWEAK_NAME = MacWSCatalystLaunch
MacWSCatalystLaunch_FILES = Tweak.x
MacWSCatalystLaunch_CFLAGS = -fobjc-arc -Wall -Wextra \
	-isystem $(CURDIR)/../vendor/ios-xpc
MacWSCatalystLaunch_FRAMEWORKS = Foundation

# The on-device lld otherwise emits arm64e Objective-C constant-string isa
# references through classic LC_DYLD_INFO_ONLY.  iPadOS dyld does not
# re-authenticate those pointers and the first NSString message traps in
# CFStringGetLength.  Chained fixups carry the pointer-auth metadata.  The
# aggregate device build already exports LIBMACHOOK_ON_DEVICE_BUILD=1; reuse
# that device-build discriminator here while leaving macOS ld64 builds alone.
ifneq ($(LIBMACHOOK_ON_DEVICE_BUILD),)
MacWSCatalystLaunch_LDFLAGS += -Wl,-fixup_chains
endif

include $(THEOS_MAKE_PATH)/tweak.mk

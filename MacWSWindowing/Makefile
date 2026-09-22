ARCHS = arm64 arm64e
TARGET = iphone:clang:latest:16.0
INSTALL_TARGET_PROCESSES = SpringBoard

include ../config/production.mk
include $(THEOS)/makefiles/common.mk

TWEAK_NAME = MacWSWindowing
MacWSWindowing_FILES = Tweak.x
MacWSWindowing_CFLAGS = -fobjc-arc -Wall -Wextra
MacWSWindowing_FRAMEWORKS = Foundation UIKit

# SpringBoard itself is arm64e on the target iPad. Runtime-confirmed on
# 2026-08-21 with an arm64e dlopen probe: TweakLoader rejects a thin arm64
# MacWSWindowing image as `incompatible architecture (have 'arm64', need
# 'arm64e')`; process compatibility does not make a dylib's arm64 slice
# loadable into an arm64e host.
#
# The arm64e linker boundary is stricter than merely emitting
# LC_DYLD_CHAINED_FIXUPS. Runtime-confirmed by
# SpringBoard-2026-08-28-001431.ips: the on-device lld image reached
# MacWSInstallRequestObservers, then trapped in CFHash because its
# __CFConstantStringClassReference carried invalid high bits. `dyld_info
# -fixups` identifies the structural difference: on-device lld emits a plain
# `bind` for every __cfstring class reference, while Apple's ld64 emits the
# required `auth-bind ... key=DA`. Therefore an on-device package build must
# carry a Mac-cross-linked binary and must never publish its locally linked
# intermediate image.
ifneq ($(wildcard /var/jb/var/mobile/theos/makefiles/common.mk),)
ifeq ($(strip $(MACWS_WINDOWING_CROSS_PREBUILT)),)
$(error MacWSWindowing arm64e must be linked on macOS; run misc/deploy_macwswindowing.sh once or provide MACWS_WINDOWING_CROSS_PREBUILT)
endif
endif

include $(THEOS_MAKE_PATH)/tweak.mk

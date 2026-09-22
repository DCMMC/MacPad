TARGET := macosx:clang:latest:13.0
ARCHS = arm64

include ../config/production.mk
include $(THEOS)/makefiles/common.mk

_THEOS_INTERNAL_IFLAGS_BASE = -I$(THEOS_TARGET_INCLUDE_PATH) \
	-I$(THEOS_INCLUDE_PATH) -I$(THEOS_FALLBACK_INCLUDE_PATH)

TOOL_NAME = macwsinteropd

macwsinteropd_FILES = main.m
macwsinteropd_CFLAGS = -fobjc-arc -fblocks -Wall -Wextra \
	-I$(CURDIR)/../include -isystem $(CURDIR)/../vendor/ios-xpc
macwsinteropd_FRAMEWORKS = Foundation AppKit CoreLocation
# The first signature must retain the project-wide chroot admission profile;
# without it macOS libSystem traps in os_variant_has_internal_diagnostics
# before libmachook can be injected.  Both postinst paths merge the dedicated
# location protocol rights into this base signature with ldid -M.
macwsinteropd_CODESIGN_FLAGS = -S../layout/usr/macOS/bin/entitlements.plist
macwsinteropd_INSTALL_PATH = /usr/macOS/bin

include $(THEOS_MAKE_PATH)/tool.mk

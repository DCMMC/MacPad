TARGET := macosx:clang:latest:13.0
ARCHS = arm64

include ../config/production.mk
include $(THEOS)/makefiles/common.mk

# Theos's legacy vendor IOKit headers define IOPhysicalRange differently from
# the modern macOS SDK imported by CoreGraphics/Foundation.  This target does
# not use those vendor headers; omit only that include directory rather than
# weakening module validation globally.
_THEOS_INTERNAL_IFLAGS_BASE = -I$(THEOS_TARGET_INCLUDE_PATH) \
	-I$(THEOS_INCLUDE_PATH) -I$(THEOS_FALLBACK_INCLUDE_PATH)

TOOL_NAME = macwsdisplayd

macwsdisplayd_FILES = main.m MacWSFinalCompositeReceiver.m
macwsdisplayd_CFLAGS = -fobjc-arc -fblocks -Wall -Wextra \
	-I$(CURDIR)/../include -isystem $(CURDIR)/../vendor/ios-xpc
macwsdisplayd_FRAMEWORKS = Foundation AppKit CoreFoundation CoreGraphics IOSurface
macwsdisplayd_LIBRARIES = bsm
macwsdisplayd_CODESIGN_FLAGS = -S../layout/usr/macOS/bin/entitlements.plist
macwsdisplayd_INSTALL_PATH = /usr/macOS/bin

include $(THEOS_MAKE_PATH)/tool.mk

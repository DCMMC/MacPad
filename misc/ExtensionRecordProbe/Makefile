TARGET := macosx:clang:latest:13.0
ARCHS = arm64

include $(THEOS)/makefiles/common.mk

_THEOS_INTERNAL_IFLAGS_BASE = -I$(THEOS_TARGET_INCLUDE_PATH) \
	-I$(THEOS_INCLUDE_PATH) -I$(THEOS_FALLBACK_INCLUDE_PATH)

TOOL_NAME = ExtensionRecordProbe
ExtensionRecordProbe_FILES = main.m
ExtensionRecordProbe_FRAMEWORKS = Foundation AppKit
ExtensionRecordProbe_CODESIGN_FLAGS = \
	-S../../layout/usr/macOS/bin/entitlements.plist

include $(THEOS_MAKE_PATH)/tool.mk

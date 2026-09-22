TARGET := iphone:clang:latest:14.0
ARCHS = arm64e

include $(THEOS)/makefiles/common.mk

TOOL_NAME = ExtensionKitXPCProbe

ExtensionKitXPCProbe_FILES = main.m
ExtensionKitXPCProbe_FRAMEWORKS = Foundation
ExtensionKitXPCProbe_CODESIGN_FLAGS = -S../../entitlements.plist

include $(THEOS_MAKE_PATH)/tool.mk

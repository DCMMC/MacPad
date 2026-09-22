TARGET := iphone:clang:16.5:15.0
ARCHS = arm64

include $(THEOS)/makefiles/common.mk

TOOL_NAME = LSNativeRegisterProbe
LSNativeRegisterProbe_FILES = main.m
LSNativeRegisterProbe_FRAMEWORKS = Foundation CoreServices
LSNativeRegisterProbe_CODESIGN_FLAGS = \
	-S../../MacWSHost/entitlements.plist

include $(THEOS_MAKE_PATH)/tool.mk

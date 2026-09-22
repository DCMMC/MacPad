TARGET := iphone:clang:latest:14.0
ARCHS = arm64

include $(THEOS)/makefiles/common.mk

TOOL_NAME = PinnedVAProbe

PinnedVAProbe_FILES = main.m
PinnedVAProbe_CODESIGN_FLAGS = -S$(realpath ../../entitlements.plist)
PinnedVAProbe_INSTALL_PATH = /usr/macOS/bin
PinnedVAProbe_FRAMEWORKS = Foundation Metal IOSurface

include $(THEOS_MAKE_PATH)/tool.mk

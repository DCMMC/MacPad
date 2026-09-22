TARGET := iphone:clang:latest:14.0
ARCHS = arm64

include $(THEOS)/makefiles/common.mk

TOOL_NAME = MetalDescriptorABIProbe
MetalDescriptorABIProbe_FILES = main.m
MetalDescriptorABIProbe_CFLAGS = -fobjc-arc
MetalDescriptorABIProbe_FRAMEWORKS = Foundation Metal
MetalDescriptorABIProbe_CODESIGN_FLAGS = -S../../entitlements.plist
MetalDescriptorABIProbe_INSTALL_PATH = /usr/macOS/bin

include $(THEOS_MAKE_PATH)/tool.mk

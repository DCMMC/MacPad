TARGET := iphone:clang:latest:14.0
ARCHS = arm64
include $(THEOS)/makefiles/common.mk

TOOL_NAME = HostInjectBootstrap

HostInjectBootstrap_FILES = main.m
HostInjectBootstrap_CODESIGN_FLAGS = -S../entitlements.plist
HostInjectBootstrap_INSTALL_PATH = /usr/macOS/bin
HostInjectBootstrap_LDFLAGS = -F../Frameworks
HostInjectBootstrap_FRAMEWORKS = IOMobileFramebuffer

include $(THEOS_MAKE_PATH)/tool.mk

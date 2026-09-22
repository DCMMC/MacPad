TARGET := iphone:clang:latest:14.0
ARCHS = arm64
include $(THEOS)/makefiles/common.mk

TOOL_NAME = PingMTLCompilerService

PingMTLCompilerService_FILES = main.m
PingMTLCompilerService_CODESIGN_FLAGS = -S../../entitlements.plist
PingMTLCompilerService_INSTALL_PATH = /usr/macOS/bin
PingMTLCompilerService_LDFLAGS = -F../../Frameworks
PingMTLCompilerService_FRAMEWORKS = IOMobileFramebuffer

include $(THEOS_MAKE_PATH)/tool.mk

TARGET := macosx:clang:latest:13.0
ARCHS = arm64 x86_64
include $(THEOS)/makefiles/common.mk

TOOL_NAME = WindowMaximizerObjC
SYSROOT = $(shell xcrun --sdk macosx --show-sdk-path)

WindowMaximizerObjC_FILES = main.m
WindowMaximizerObjC_FRAMEWORKS = Foundation Cocoa ApplicationServices

# Enable modules and set module cache path
WindowMaximizerObjC_CFLAGS = -fobjc-arc -fmodules -fmodules-cache-path=$(THEOS)/.modules -isysroot $(SYSROOT)
WindowMaximizerObjC_LDFLAGS = -isysroot $(SYSROOT)

WindowMaximizerObjC_CODESIGN_FLAGS = -Sentitlements.plist

include $(THEOS_MAKE_PATH)/tool.mk
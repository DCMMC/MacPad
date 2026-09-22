TARGET := macosx:clang:latest:13.0
ARCHS = arm64
include $(THEOS)/makefiles/common.mk

_THEOS_INTERNAL_IFLAGS_BASE = -I$(THEOS_TARGET_INCLUDE_PATH) \
	-I$(THEOS_INCLUDE_PATH) -I$(THEOS_FALLBACK_INCLUDE_PATH)

TOOL_NAME = KeychainProbe

KeychainProbe_FILES = main.m
KeychainProbe_CODESIGN_FLAGS = -S../../layout/usr/macOS/bin/entitlements.plist
KeychainProbe_INSTALL_PATH = /usr/macOS/bin
KeychainProbe_FRAMEWORKS = Foundation Security
KeychainProbe_CFLAGS = -fobjc-arc -Wall -Wextra

include $(THEOS_MAKE_PATH)/tool.mk

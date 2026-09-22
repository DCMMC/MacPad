TARGET := macosx:clang:latest:13.0
ARCHS = arm64

include ../config/production.mk
include $(THEOS)/makefiles/common.mk

TOOL_NAME = macwsinputd

macwsinputd_FILES = main.c
macwsinputd_CFLAGS = -x objective-c -Wall -Wextra -I$(CURDIR)/../include
macwsinputd_FRAMEWORKS = AppKit CoreFoundation CoreGraphics
macwsinputd_CODESIGN_FLAGS = -S../layout/usr/macOS/bin/entitlements.plist
macwsinputd_INSTALL_PATH = /usr/macOS/bin

include $(THEOS_MAKE_PATH)/tool.mk

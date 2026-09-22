TARGET := iphone:clang:latest:16.0
ARCHS = arm64

include ../config/production.mk
include $(THEOS)/makefiles/common.mk

TOOL_NAME = macwshostd

macwshostd_FILES = main.m
macwshostd_FRAMEWORKS = Foundation
macwshostd_CODESIGN_FLAGS = -S../entitlements.plist
macwshostd_CFLAGS = -fobjc-arc -Wall -Wextra \
	-I$(CURDIR)/../include -isystem $(CURDIR)/../vendor/ios-xpc
macwshostd_INSTALL_PATH = /usr/macOS/bin

include $(THEOS_MAKE_PATH)/tool.mk

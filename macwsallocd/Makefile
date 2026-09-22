TARGET := iphone:clang:latest:14.0
ARCHS = arm64
include ../config/production.mk
include $(THEOS)/makefiles/common.mk

TOOL_NAME = macwsallocd

macwsallocd_FILES = main.m
macwsallocd_FRAMEWORKS = Foundation IOSurface Metal IOKit
macwsallocd_CODESIGN_FLAGS = -S../entitlements.plist
macwsallocd_CFLAGS = -fobjc-arc -isystem $(CURDIR)/../vendor/ios-xpc
macwsallocd_INSTALL_PATH = /usr/macOS/bin

include $(THEOS_MAKE_PATH)/tool.mk

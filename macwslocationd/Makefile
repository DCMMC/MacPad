TARGET := iphone:clang:latest:14.0
ARCHS = arm64
include ../config/production.mk
include $(THEOS)/makefiles/common.mk

TOOL_NAME = macwslocationd

macwslocationd_FILES = main.m
macwslocationd_FRAMEWORKS = Foundation CoreLocation
macwslocationd_CFLAGS = -fobjc-arc -fblocks -Wall -Wextra \
	-I$(CURDIR)/../include -isystem $(CURDIR)/../vendor/ios-xpc
macwslocationd_CODESIGN_FLAGS = -S../layout/usr/macOS/bin/locationd-native-entitlements.plist
macwslocationd_INSTALL_PATH = /usr/macOS/bin

include $(THEOS_MAKE_PATH)/tool.mk

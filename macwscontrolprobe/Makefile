TARGET := iphone:clang:latest:16.0
ARCHS = arm64

include ../config/production.mk
include $(THEOS)/makefiles/common.mk

TOOL_NAME = macws_control_probe

macws_control_probe_FILES = ../misc/macws_control_probe.c
macws_control_probe_CODESIGN_FLAGS = -S../entitlements.plist
macws_control_probe_CFLAGS = -fblocks -Wall -Wextra \
	-I$(CURDIR)/../include -isystem $(CURDIR)/../vendor/ios-xpc
macws_control_probe_INSTALL_PATH = /usr/macOS/bin

include $(THEOS_MAKE_PATH)/tool.mk

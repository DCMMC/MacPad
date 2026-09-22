TARGET := iphone:clang:latest:16.0
ARCHS = arm64

include ../config/production.mk
include $(THEOS)/makefiles/common.mk

TOOL_NAME = macwskeychaind

macwskeychaind_FILES = main.m
macwskeychaind_FRAMEWORKS = Foundation Security
macwskeychaind_CODESIGN_FLAGS = -Sentitlements.plist
macwskeychaind_CFLAGS = -fobjc-arc -Wall -Wextra \
	-I$(CURDIR)/../include -isystem $(CURDIR)/../vendor/ios-xpc
macwskeychaind_INSTALL_PATH = /usr/macOS/bin

include $(THEOS_MAKE_PATH)/tool.mk

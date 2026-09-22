TARGET := iphone:clang:latest:14.0
ARCHS = arm64

include ../config/production.mk
include $(THEOS)/makefiles/common.mk

TOOL_NAME = macwsaudiooutd

macwsaudiooutd_FILES = main.c
macwsaudiooutd_CFLAGS = -std=c11 -Wall -Wextra -Werror -Wno-deprecated-declarations -I$(CURDIR)/../include
macwsaudiooutd_FRAMEWORKS = AudioToolbox CoreFoundation
macwsaudiooutd_CODESIGN_FLAGS = -S../entitlements.plist
macwsaudiooutd_INSTALL_PATH = /usr/macOS/bin

include $(THEOS_MAKE_PATH)/tool.mk

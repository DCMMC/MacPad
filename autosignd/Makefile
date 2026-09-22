TARGET := iphone:clang:latest:14.0
ARCHS = arm64
include ../config/production.mk
include $(THEOS)/makefiles/common.mk

TOOL_NAME = autosignd

autosignd_FILES = main.c
autosignd_CODESIGN_FLAGS = -S../entitlements.plist
autosignd_INSTALL_PATH = /usr/macOS/bin

include $(THEOS_MAKE_PATH)/tool.mk

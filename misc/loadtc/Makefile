ARCHS = arm64
include $(THEOS)/makefiles/common.mk

TOOL_NAME = loadtc

loadtc_FILES = main.m
loadtc_CFLAGS = -fobjc-arc
loadtc_CODESIGN_FLAGS = -S../entitlements.plist
loadtc_INSTALL_PATH = /usr/local/bin

include $(THEOS_MAKE_PATH)/tool.mk

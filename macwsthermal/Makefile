TARGET := iphone:clang:latest:14.0
ARCHS = arm64
include ../config/production.mk
include $(THEOS)/makefiles/common.mk

TOOL_NAME = macwsthermal

macwsthermal_FILES = main.m
macwsthermal_FRAMEWORKS = Foundation IOKit
macwsthermal_CFLAGS = -fobjc-arc -Wall -Wextra
macwsthermal_CODESIGN_FLAGS = -S../entitlements.plist
macwsthermal_INSTALL_PATH = /usr/macOS/bin

include $(THEOS_MAKE_PATH)/tool.mk

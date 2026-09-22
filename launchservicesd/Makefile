TARGET := macosx:clang:latest:13.0
ARCHS = arm64
include ../config/production.mk
include $(THEOS)/makefiles/common.mk

TOOL_NAME = launchservicesd

launchservicesd_FILES = main.m
launchservicesd_CFLAGS = -fobjc-arc
launchservicesd_CODESIGN_FLAGS = -Sentitlements.plist
launchservicesd_INSTALL_PATH = /usr/macOS/bin

include $(THEOS_MAKE_PATH)/tool.mk

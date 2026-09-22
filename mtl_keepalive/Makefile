TARGET := iphone:clang:latest:14.0
ARCHS = arm64e

include ../config/production.mk
include $(THEOS)/makefiles/common.mk

TOOL_NAME = mtl_keepalive

mtl_keepalive_FILES = main.m
mtl_keepalive_CFLAGS = -fobjc-arc
mtl_keepalive_FRAMEWORKS = Foundation Metal
mtl_keepalive_INSTALL_PATH = /usr/bin

include $(THEOS_MAKE_PATH)/tool.mk

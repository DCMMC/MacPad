ARCHS = arm64e
TARGET = iphone:clang:latest:14.0
include ../config/production.mk
include $(THEOS)/makefiles/common.mk

BUNDLE_NAME = LocationdProxy

LocationdProxy_DYNAMIC_LIBRARY = 0
LocationdProxy_BUNDLE_EXTENSION = xpc
LocationdProxy_FILES = ../ViewBridgeChrootProxy/main.c
LocationdProxy_CFLAGS = -ffreestanding -fno-builtin -fno-stack-protector -fno-unwind-tables -fno-asynchronous-unwind-tables
LocationdProxy_LDFLAGS = -nostdlib -lSystem -Wl,-e,_main
LocationdProxy_CODESIGN_FLAGS = -S../entitlements.plist
LocationdProxy_INSTALL_PATH = /usr/macOS/PrivateFrameworks/CoreLocation.framework/XPCServices

include $(THEOS_MAKE_PATH)/bundle.mk

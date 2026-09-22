ARCHS = arm64e
TARGET = iphone:clang:latest:14.0
include ../config/production.mk
include $(THEOS)/makefiles/common.mk

BUNDLE_NAME = WriteConfigProxy

WriteConfigProxy_DYNAMIC_LIBRARY = 0
WriteConfigProxy_BUNDLE_EXTENSION = xpc
WriteConfigProxy_FILES = ../ViewBridgeChrootProxy/main.c
WriteConfigProxy_CFLAGS = -ffreestanding -fno-builtin -fno-stack-protector -fno-unwind-tables -fno-asynchronous-unwind-tables
WriteConfigProxy_LDFLAGS = -nostdlib -lSystem -Wl,-e,_main
WriteConfigProxy_CODESIGN_FLAGS = -S../entitlements.plist
WriteConfigProxy_INSTALL_PATH = /usr/macOS/PrivateFrameworks/SystemAdministration.framework/XPCServices

include $(THEOS_MAKE_PATH)/bundle.mk

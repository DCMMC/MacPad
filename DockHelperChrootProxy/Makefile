ARCHS = arm64e
TARGET = iphone:clang:latest:14.0
include ../config/production.mk
include $(THEOS)/makefiles/common.mk

BUNDLE_NAME = DockHelperProxy

DockHelperProxy_DYNAMIC_LIBRARY = 0
DockHelperProxy_BUNDLE_EXTENSION = xpc
DockHelperProxy_FILES = ../ViewBridgeChrootProxy/main.c
DockHelperProxy_CFLAGS = -ffreestanding -fno-builtin -fno-stack-protector -fno-unwind-tables -fno-asynchronous-unwind-tables
DockHelperProxy_LDFLAGS = -nostdlib -lSystem -Wl,-e,_main
DockHelperProxy_CODESIGN_FLAGS = -S../entitlements.plist
DockHelperProxy_INSTALL_PATH = /usr/macOS/Frameworks/Dock.framework/Versions/A/XPCServices

include $(THEOS_MAKE_PATH)/bundle.mk

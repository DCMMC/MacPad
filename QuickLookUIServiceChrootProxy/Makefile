ARCHS = arm64e
TARGET = iphone:clang:latest:14.0
include ../config/production.mk
include $(THEOS)/makefiles/common.mk

BUNDLE_NAME = QuickLookUIServiceProxy

QuickLookUIServiceProxy_DYNAMIC_LIBRARY = 0
QuickLookUIServiceProxy_BUNDLE_EXTENSION = xpc
QuickLookUIServiceProxy_FILES = ../ViewBridgeChrootProxy/main.c
QuickLookUIServiceProxy_CFLAGS = -ffreestanding -fno-builtin -fno-stack-protector -fno-unwind-tables -fno-asynchronous-unwind-tables
QuickLookUIServiceProxy_LDFLAGS = -nostdlib -lSystem -Wl,-e,_main
QuickLookUIServiceProxy_CODESIGN_FLAGS = -S../entitlements.plist
QuickLookUIServiceProxy_INSTALL_PATH = /usr/macOS/Frameworks/QuickLookUI.framework/Versions/A/XPCServices

include $(THEOS_MAKE_PATH)/bundle.mk

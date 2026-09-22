ARCHS = arm64e
TARGET = iphone:clang:latest:14.0
include ../config/production.mk
include $(THEOS)/makefiles/common.mk

BUNDLE_NAME = ExtensionKitProxy

ExtensionKitProxy_DYNAMIC_LIBRARY = 0
ExtensionKitProxy_BUNDLE_EXTENSION = xpc
ExtensionKitProxy_FILES = ../ViewBridgeChrootProxy/main.c
ExtensionKitProxy_CFLAGS = -ffreestanding -fno-builtin -fno-stack-protector -fno-unwind-tables -fno-asynchronous-unwind-tables
ExtensionKitProxy_LDFLAGS = -nostdlib -lSystem -Wl,-e,_main
ExtensionKitProxy_CODESIGN_FLAGS = -S../entitlements.plist
ExtensionKitProxy_INSTALL_PATH = /usr/macOS/Frameworks/ExtensionFoundation.framework/Versions/A/XPCServices

include $(THEOS_MAKE_PATH)/bundle.mk

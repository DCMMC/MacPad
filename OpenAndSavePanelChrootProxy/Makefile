ARCHS = arm64e
TARGET = iphone:clang:latest:14.0
include ../config/production.mk
include $(THEOS)/makefiles/common.mk

BUNDLE_NAME = OpenAndSavePanelProxy

OpenAndSavePanelProxy_DYNAMIC_LIBRARY = 0
OpenAndSavePanelProxy_BUNDLE_EXTENSION = xpc
OpenAndSavePanelProxy_FILES = ../ViewBridgeChrootProxy/main.c
OpenAndSavePanelProxy_CFLAGS = -ffreestanding -fno-builtin -fno-stack-protector -fno-unwind-tables -fno-asynchronous-unwind-tables
OpenAndSavePanelProxy_LDFLAGS = -nostdlib -lSystem -Wl,-e,_main
OpenAndSavePanelProxy_CODESIGN_FLAGS = -S../entitlements.plist
OpenAndSavePanelProxy_INSTALL_PATH = /usr/macOS/Frameworks/AppKit.framework/Versions/C/XPCServices

include $(THEOS_MAKE_PATH)/bundle.mk

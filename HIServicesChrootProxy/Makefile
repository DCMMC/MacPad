ARCHS = arm64e
TARGET = iphone:clang:latest:14.0
include ../config/production.mk
include $(THEOS)/makefiles/common.mk

BUNDLE_NAME = HIServicesProxy

HIServicesProxy_DYNAMIC_LIBRARY = 0
HIServicesProxy_BUNDLE_EXTENSION = xpc
HIServicesProxy_FILES = ../ViewBridgeChrootProxy/main.c
HIServicesProxy_CFLAGS = -ffreestanding -fno-builtin -fno-stack-protector -fno-unwind-tables -fno-asynchronous-unwind-tables
HIServicesProxy_LDFLAGS = -nostdlib -lSystem -Wl,-e,_main
HIServicesProxy_CODESIGN_FLAGS = -S../entitlements.plist
HIServicesProxy_INSTALL_PATH = /usr/macOS/Frameworks/HIServices.framework/Versions/A/XPCServices

include $(THEOS_MAKE_PATH)/bundle.mk

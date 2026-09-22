ARCHS = arm64e
TARGET = iphone:clang:latest:14.0
include ../config/production.mk
include $(THEOS)/makefiles/common.mk

BUNDLE_NAME = GeodProxy

GeodProxy_DYNAMIC_LIBRARY = 0
GeodProxy_BUNDLE_EXTENSION = xpc
GeodProxy_FILES = ../ViewBridgeChrootProxy/main.c
GeodProxy_CFLAGS = -ffreestanding -fno-builtin -fno-stack-protector -fno-unwind-tables -fno-asynchronous-unwind-tables
GeodProxy_LDFLAGS = -nostdlib -lSystem -Wl,-e,_main
GeodProxy_CODESIGN_FLAGS = -S../entitlements.plist
GeodProxy_INSTALL_PATH = /usr/macOS/PrivateFrameworks/GeoServices.framework/Versions/A/XPCServices

include $(THEOS_MAKE_PATH)/bundle.mk

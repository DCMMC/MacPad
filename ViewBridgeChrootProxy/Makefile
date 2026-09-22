ARCHS = arm64e
TARGET = iphone:clang:latest:14.0
include ../config/production.mk
include $(THEOS)/makefiles/common.mk

BUNDLE_NAME = ViewBridgeAuxiliary

ViewBridgeAuxiliary_DYNAMIC_LIBRARY = 0
ViewBridgeAuxiliary_BUNDLE_EXTENSION = xpc
ViewBridgeAuxiliary_FILES = main.c
ViewBridgeAuxiliary_CFLAGS = -ffreestanding -fno-builtin -fno-stack-protector -fno-unwind-tables -fno-asynchronous-unwind-tables
ViewBridgeAuxiliary_LDFLAGS = -nostdlib -lSystem -Wl,-e,_main
ViewBridgeAuxiliary_CODESIGN_FLAGS = -S../entitlements.plist
ViewBridgeAuxiliary_INSTALL_PATH = /usr/macOS/Frameworks/ViewBridge.framework/Versions/A/XPCServices

include $(THEOS_MAKE_PATH)/bundle.mk

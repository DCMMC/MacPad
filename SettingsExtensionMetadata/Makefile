ARCHS = arm64e
TARGET = iphone:clang:latest:14.0

include ../config/production.mk
include $(THEOS)/makefiles/common.mk

BUNDLE_NAME = SettingsExtensionProxy

SettingsExtensionProxy_DYNAMIC_LIBRARY = 0
SettingsExtensionProxy_BUNDLE_EXTENSION = appex
SettingsExtensionProxy_FILES = ../ViewBridgeChrootProxy/main.c
SettingsExtensionProxy_CFLAGS = -ffreestanding -fno-builtin \
	-fno-stack-protector -fno-unwind-tables -fno-asynchronous-unwind-tables
SettingsExtensionProxy_LDFLAGS = -nostdlib -lSystem -Wl,-e,_main
SettingsExtensionProxy_CODESIGN_FLAGS = -S../MacWSHost/entitlements.plist
SettingsExtensionProxy_INSTALL_PATH = /Applications/MacWSCatalystLauncher.app/PlugIns

include $(THEOS_MAKE_PATH)/bundle.mk

ARCHS = arm64e
TARGET = iphone:clang:latest:14.0
include ../config/production.mk
include $(THEOS)/makefiles/common.mk

BUNDLE_NAME = SettingsExtensionProxy

SettingsExtensionProxy_DYNAMIC_LIBRARY = 0
# This freestanding first image must be registered as an application, not an
# appex.  iPadOS launchd unconditionally supplies the PluginKit `container`
# sandbox for an appex service-cache entry; xpcproxy then installs that profile
# in its spawn attributes before this image runs, so even a correctly signed
# setuid/no-sandbox executable cannot call chroot(2).  A hidden APPL entry uses
# the executable's no-container/no-sandbox contract while RBLaunchdInterface's
# preserved Appearance overlay still supplies the real extension identity and
# Mach services.
SettingsExtensionProxy_BUNDLE_EXTENSION = app
SettingsExtensionProxy_FILES = ../ViewBridgeChrootProxy/main.c
SettingsExtensionProxy_CFLAGS = -ffreestanding -fno-builtin -fno-stack-protector -fno-unwind-tables -fno-asynchronous-unwind-tables
SettingsExtensionProxy_LDFLAGS = -nostdlib -lSystem -Wl,-e,_main
SettingsExtensionProxy_CODESIGN_FLAGS = -S../MacWSHost/entitlements.plist
SettingsExtensionProxy_INSTALL_PATH = /Applications

include $(THEOS_MAKE_PATH)/bundle.mk

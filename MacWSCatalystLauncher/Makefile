ARCHS = arm64
TARGET = iphone:clang:latest:16.0

include ../config/production.mk
include $(THEOS)/makefiles/common.mk

APPLICATION_NAME = MacWSCatalystLauncher

MacWSCatalystLauncher_FILES = main.m
MacWSCatalystLauncher_FRAMEWORKS = UIKit Foundation
MacWSCatalystLauncher_CFLAGS = -fobjc-arc -Wall -Wextra \
	-I$(CURDIR)/../include
# This binary must first behave as an ordinary foreground UIKit application.
# The root WindowServer entitlement set includes BackBoard/HID *server* roles
# and makes UIKit's client-side screen handshake structurally ambiguous.  The
# launcher needs only the same narrow client entitlements as MacWSHost.  It
# remains alive as the real iOS scene owner while a separately spawned chroot
# child runs Maps with the app's own signature; replacing the carrier image
# invalidates SpringBoard's workspace connection and is therefore forbidden.
MacWSCatalystLauncher_CODESIGN_FLAGS = -S../MacWSHost/entitlements.plist

include $(THEOS_MAKE_PATH)/application.mk

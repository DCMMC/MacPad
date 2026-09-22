TARGET := iphone:clang:latest:14.0
ARCHS = arm64
include ../config/production.mk
include $(THEOS)/makefiles/common.mk

TOOL_NAME = mountdevfs

mountdevfs_FILES = main.c
# Sign ad-hoc only (no entitlements). mountdevfs must mount(2) devfs; a bare
# trustcached platform binary is allowed to, but signing it with the project
# entitlements.plist puts it in a restrictive sandbox profile that DENIES the
# mount (EPERM "Operation not permitted"). So do NOT add a CODESIGN_FLAGS
# entitlements file here.
mountdevfs_INSTALL_PATH = /usr/macOS/bin

include $(THEOS_MAKE_PATH)/tool.mk

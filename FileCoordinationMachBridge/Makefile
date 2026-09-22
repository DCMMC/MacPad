ARCHS = arm64e
TARGET = iphone:clang:latest:14.0
include ../config/production.mk
include $(THEOS)/makefiles/common.mk

BUNDLE_NAME = FileCoordinationProxy

FileCoordinationProxy_DYNAMIC_LIBRARY = 0
FileCoordinationProxy_BUNDLE_EXTENSION = xpc
FileCoordinationProxy_FILES = main.m
FileCoordinationProxy_CFLAGS = -fobjc-arc -I$(CURDIR)/../include -isystem $(CURDIR)/../vendor/ios-xpc
FileCoordinationProxy_CODESIGN_FLAGS = -S../entitlements.plist
FileCoordinationProxy_INSTALL_PATH = /usr/macOS/Frameworks/FileCoordination.framework/Versions/A/XPCServices

include $(THEOS_MAKE_PATH)/bundle.mk

ARCHS = arm64e
TARGET = iphone:clang:latest:14.0
include ../config/production.mk
include $(THEOS)/makefiles/common.mk

BUNDLE_NAME = ProgressReportingProxy

ProgressReportingProxy_DYNAMIC_LIBRARY = 0
ProgressReportingProxy_BUNDLE_EXTENSION = xpc
ProgressReportingProxy_FILES = main.m
ProgressReportingProxy_CFLAGS = -fobjc-arc -I$(CURDIR)/../include -isystem $(CURDIR)/../vendor/ios-xpc
ProgressReportingProxy_CODESIGN_FLAGS = -S../entitlements.plist
ProgressReportingProxy_INSTALL_PATH = /usr/macOS/Frameworks/FileCoordination.framework/Versions/A/XPCServices

include $(THEOS_MAKE_PATH)/bundle.mk

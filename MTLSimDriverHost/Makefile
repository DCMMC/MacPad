ARCHS = arm64
include ../config/production.mk
include $(THEOS)/makefiles/common.mk

BUNDLE_NAME = MTLSimDriverHost

MTLSimDriverHost_DYNAMIC_LIBRARY = 0
MTLSimDriverHost_BUNDLE_EXTENSION = xpc
MTLSimDriverHost_FILES = main.x
# -isystem: use the vendored iOS xpc headers (the theos iPhoneOS SDK ships
# without xpc/). See ../vendor/ios-xpc/README.md.
MTLSimDriverHost_CFLAGS = -fobjc-arc -isystem $(CURDIR)/../vendor/ios-xpc
MTLSimDriverHost_FRAMEWORKS = Foundation Metal MetalPerformanceShaders IOSurface
MTLSimDriverHost_CODESIGN_FLAGS = -S../entitlements.plist
MTLSimDriverHost_INSTALL_PATH = /usr/macOS/Frameworks/MTLSimDriver.framework/XPCServices

include $(THEOS_MAKE_PATH)/bundle.mk

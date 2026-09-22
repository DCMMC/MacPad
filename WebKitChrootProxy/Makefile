ARCHS = arm64e
TARGET = iphone:clang:latest:14.0
include ../config/production.mk
include $(THEOS)/makefiles/common.mk

BUNDLE_NAME = WebContentProxy WebNetworkingProxy WebGPUProxy SafariSandboxProxy
define WEBKIT_PROXY
$(1)_DYNAMIC_LIBRARY = 0
$(1)_BUNDLE_EXTENSION = xpc
$(1)_FILES = ../ViewBridgeChrootProxy/main.c
$(1)_RESOURCE_DIRS = Resources/$(1)
$(1)_CFLAGS = -DMACWS_WEBKIT_PROXY_EXPERIMENT=1 -ffreestanding -fno-builtin -fno-stack-protector -fno-unwind-tables -fno-asynchronous-unwind-tables
$(1)_LDFLAGS = -nostdlib -lSystem -Wl,-e,_main
$(1)_CODESIGN_FLAGS = -S../entitlements.plist
$(1)_INSTALL_PATH = /usr/macOS/Frameworks/WebKit.framework/Versions/A/XPCServices
endef
$(foreach proxy,$(BUNDLE_NAME),$(eval $(call WEBKIT_PROXY,$(proxy))))
include $(THEOS_MAKE_PATH)/bundle.mk

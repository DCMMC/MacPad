TARGET := iphone:clang:latest:14.0
ARCHS = arm64
include ../config/production.mk
include $(THEOS)/makefiles/common.mk

TOOL_NAME = macws-llvm-dis macws-llvm-as
macws-llvm-dis_FILES = main.c
macws-llvm-as_FILES = main.c
macws-llvm-dis_CFLAGS = -Wall -Wextra
macws-llvm-as_CFLAGS = -Wall -Wextra
macws-llvm-dis_CODESIGN_FLAGS = -S../entitlements.plist
macws-llvm-as_CODESIGN_FLAGS = -S../entitlements.plist
macws-llvm-dis_INSTALL_PATH = /usr/macOS/bin
macws-llvm-as_INSTALL_PATH = /usr/macOS/bin

include $(THEOS_MAKE_PATH)/tool.mk

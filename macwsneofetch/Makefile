TARGET := macosx:clang:latest:13.0
ARCHS = arm64

include ../config/production.mk
include $(THEOS)/makefiles/common.mk

TOOL_NAME = macws-neofetch

macws-neofetch_FILES = main.c
macws-neofetch_CFLAGS = -std=c11 -Wall -Wextra -Werror
macws-neofetch_CODESIGN_FLAGS = -S../layout/usr/macOS/bin/entitlements.plist
macws-neofetch_INSTALL_PATH = /usr/macOS/bin

include $(THEOS_MAKE_PATH)/tool.mk

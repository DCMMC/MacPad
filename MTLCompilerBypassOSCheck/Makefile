ARCHS = arm64 arm64e
INSTALL_TARGET_PROCESSES = MTLCompilerService

include ../config/production.mk
include $(THEOS)/makefiles/common.mk

TWEAK_NAME = MTLCompilerBypassOSCheck

MTLCompilerBypassOSCheck_FILES = Tweak.x
MTLCompilerBypassOSCheck_CFLAGS = -fobjc-arc

include $(THEOS_MAKE_PATH)/tweak.mk

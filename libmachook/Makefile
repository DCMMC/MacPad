TARGET := iphone:clang:latest:14.0
# Build both slices.  misc/build_on_ios.sh then SPLITS the fat into two THIN
# dylibs (libmachook.dylib = arm64e, libmachook_arm64.dylib = arm64), because
# the target device's dyld rejects a *fat* DYLD_INSERT_LIBRARIES dylib in this
# chroot.  launchdchrootexec and exec_hooks inspect each target Mach-O and insert
# exactly one thin dylib.  This is required because iOS 16 dyld does NOT skip
# the other compatible ARM64 subtype; loading both duplicates all hook state.
# The macOS cross-compile (misc/build.sh) ships the fat binary unchanged.
ARCHS = arm64 arm64e
include ../config/production.mk
include $(THEOS)/makefiles/common.mk

LIBRARY_NAME = libmachook

libmachook_FILES = jit.m objc_hooks.c mac_hooks.m AppInputBridge.m AudioRenderBridge.m LocalFilePanel.m DNSBridge.c MacWSFinalCompositePublisher.m Compatibility/MacWSAppleNeuralEngine.m Compatibility/MacWSCatalystInputPolicy.m Compatibility/MacWSCatalystKeychain.m Compatibility/MacWSIOKitRegistry.m Compatibility/MacWSSublimeProcess.m Compatibility/MacWSSysVSemaphore.c Compatibility/MacWSSteamNamedSemaphore.c Compatibility/MacWSSteamProcess.m Diagnostics/MacWSInputLatency.m Diagnostics/MacWSRandomDiagnostics.m Diagnostics/MacWSIdentityDiagnostics.m Diagnostics/MacWSSteamPipeDiagnostics.m os_variant_hooks.x \
  ControlCenter_hooks.x Metal_hooks.x QuartzCore_hooks.x os_log_hooks.m \
  exec_hooks.c
libmachook_FILES += Compatibility/MacWSOfficeMultiplyFilter.m
libmachook_FILES += Compatibility/MacWSSendfile.c
libmachook_FILES += Compatibility/MacWSElectronPageAllocator.c
libmachook_FRAMEWORKS = AudioToolbox CoreGraphics CoreImage ImageIO IOKit IOSurface Metal
# -isystem: use the vendored iOS xpc headers (the theos iPhoneOS SDK ships
# without xpc/). See ../vendor/ios-xpc/README.md.
# Native AGX is the production target of this tree.  mac_hooks.m keeps the
# selector/command ABI implementation behind FORCE_M1_DRIVER; without this
# compile definition a cross-build still links because undefined symbols are
# permitted, but dyld aborts at injection time when Metal_hooks.x resolves the
# missing native-AGX witnesses.  Define it in the owning target rather than
# relying on an interactive shell's CFLAGS.
libmachook_CFLAGS = -DFORCE_M1_DRIVER=1 \
  -isystem $(CURDIR)/../vendor/ios-xpc -I$(CURDIR)/../include \
  -I$(CURDIR)/.. -I$(CURDIR)/Compatibility -I$(CURDIR)/Diagnostics
libmachook_LDFLAGS = -undefined dynamic_lookup

# A direct `make package` on the iPad must be just as safe as the documented
# build_on_ios.sh entry point. Detect the target's rootless Theos installation
# when the caller omitted the variable, so the arm64e slice cannot silently be
# emitted with classic fixups and then trap in libSystem_initializer. The
# macOS cross-build has no /var/jb tree and retains Xcode ld64's native path.
ifeq ($(origin LIBMACHOOK_ON_DEVICE_BUILD),undefined)
ifneq ($(wildcard /var/jb/var/mobile/theos/makefiles/common.mk),)
LIBMACHOOK_ON_DEVICE_BUILD := 1
endif
endif

# On-device builds (misc/build_on_ios.sh passes LIBMACHOOK_ON_DEVICE_BUILD=1)
# use the on-device lld.  For the arm64e slice, lld emits the __interpose tuples
# (and ObjC class_t->data) as arm64e *authenticated* pointers but under the
# classic LC_DYLD_INFO_ONLY fixup format.  macOS arm64e dyld mis-processes those:
# the DYLD_INTERPOSE entries never register, so the real
# os_variant_has_internal_diagnostics() runs and traps (brk 1) inside
# libSystem_initializer before any of our hooks take effect.  -fixup_chains
# forces LC_DYLD_CHAINED_FIXUPS, which macOS dyld fully understands and
# re-signs/re-binds correctly at load (this also fixes the arm64e ObjC class_t
# PAC trap, so Metal_hooks.x may include arm64e ObjC under this define).
# The macOS cross-compile (misc/build.sh) uses Xcode ld64, which already emits
# chained fixups, so it must NOT receive this flag -> keep it on-device only.
ifeq ($(LIBMACHOOK_ON_DEVICE_BUILD),1)
libmachook_CFLAGS += -DLIBMACHOOK_ON_DEVICE_BUILD=1
libmachook_LDFLAGS += -Wl,-fixup_chains
endif

libmachook_INSTALL_PATH = /usr/macOS/lib

include $(THEOS_MAKE_PATH)/library.mk

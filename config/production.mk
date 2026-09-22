# Include before Theos common.mk, including for direct subproject builds.
# Retain symbols for crash symbolication without selecting Theos's -O0 path.
# Developers can still explicitly request DEBUG=1 or override OPTFLAG.
FINALPACKAGE ?= 1
OPTFLAG ?= -O2
export FINALPACKAGE OPTFLAG

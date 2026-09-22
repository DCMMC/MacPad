# Vendored iOS XPC headers

The theos `iPhoneOS16.5.sdk` ships **without** the `usr/include/xpc/` header
directory, so any iOS-target subproject that does `#include <xpc/xpc.h>`
(`MTLSimDriverHost`, `libmachook`) fails to compile with
`'xpc/xpc.h' file not found`.

These headers are vendored here and pulled in via `-isystem` in the two
subprojects' Makefiles, so the build works on any machine without patching the
global theos SDK.

## Provenance / patch

Copied from the Command Line Tools macOS SDK
(`/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk/usr/include/xpc/`), then
**`session.h` and `listener.h` were removed** and their `#include` lines deleted
from `xpc.h`. Those two headers use `OS_OBJECT_DECL_SENDABLE_CLASS`
(iOS 17+ / macOS 14+), which is absent from the iOS 16.5 `os/object.h` and
breaks compilation. The classic XPC API the project actually uses
(`xpc_connection_*`, etc.) lives in the remaining headers.

`xpc_transaction_deprecate.h` and `launch.h` are referenced by `xpc.h` only
behind `#if __has_include(...)` guards, so their absence is harmless.

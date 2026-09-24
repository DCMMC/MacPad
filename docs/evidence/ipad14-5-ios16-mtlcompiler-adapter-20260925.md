# iPad14,5 iOS 16.0 MTLCompilerService adapter evidence — 2026-09-25

## Scope

This note records the evidence for adding the iPad14,5 iOS 16.0 (20A8372)
`MTLCompilerService` executable to the existing macOS-target adapter. The
change does not select behavior from the OS version: it retains an exact Mach-O
UUID allowlist and the existing instruction-by-instruction validation.

## Binary identity and disassembly

RE-confirmed from the complete executable copied from the target device:

- SHA-256: `2f980bfb46e3d97c5a330f54c158b41de21793ec113f3d33891e3599e75faba5`
- LC_UUID: `B4745394-88D0-3739-9E17-4DE2FB12B00E`
- `+0x20e8`: `blraaz x9`
- `+0x25f0`: `blraaz x8`
- `+0x2628`: `blraaz x8`
- `+0x2770`: the same `xpc_data_create` branch used by the existing 20D67
  adapter (`0x9400047c`)

The existing iPad13,6 iOS 16.3.1 (20D67) UUID
`6D2CFE56-8D88-39AA-BC25-7FFE5058ED4E` remains in the allowlist. Unknown
executables return before any patch is installed, and both known identities
must still pass the four instruction checks.

## Runtime witnesses

Before the UUID addition, an opt-in diagnostic run on 20A8372 logged:

```text
target adapter: MTLCompilerService UUID mismatch
```

After deploying the allowlisted build, the same target logged all three
installed call sites and a real adapted reply:

```text
target adapter installed offset=0x20e8
target adapter installed offset=0x25f0
target adapter installed offset=0x2628
target adapter #1 adapted=1 cacheAdapter=1
```

Runtime-confirmed on iPad14,5 / 20A8372 with the production diagnostic marker
removed: `metal_source_probe` created both the library and function through the
real service:

```text
METAL_SOURCE_PROBE library=0x15172d190 class=_MTLLibrary errorDomain=(nil) errorCode=0 description=(nil)
METAL_SOURCE_PROBE functionName=macws_probe function=0x141667160 class=_MTLFunctionInternal
```

VS Code then rendered its editor and integrated browser through the same Metal
path. `https://www.apple.com/` loaded as the regional
`https://www.apple.com.cn/airpods/` page with live scrolling and no current
`ERR_CERT`, `ERR_NAME_NOT_RESOLVED`, `ERR_CONNECTION`, Skia shader compilation,
or unsupported-library-format entry in the VS Code profile logs.

## Compatibility boundary

This is not a global compiler bypass. The adapter remains request-scoped, the
20D67 identity and instructions are unchanged, and any unexamined
`MTLCompilerService` binary retains stock behavior until its UUID and call
sites are independently verified.

Runtime-confirmed after deploying the same fat dylib to the original
iPad13,6 / iOS 16.3.1 (20D67) environment: WindowServer and OSXvnc remained
running, and the production probe completed through the retained 20D67 UUID:

```text
METAL_SOURCE_PROBE library=0x11c62b610 class=_MTLLibrary errorDomain=(nil) errorCode=0 description=(nil)
METAL_SOURCE_PROBE functionName=macws_probe function=0x10f962cb0 class=_MTLFunctionInternal
```

The two installed injection copies had identical SHA-256
`f8d83ff349d183f92248304718ffdd3d10e2aa37939a71d26043ff67a16bf075`.
The replaced 20D67 dylibs were preserved in
`/var/jb/usr/macOS/rollback/mtlcompiler-ios16.3-regression-20260925/`.

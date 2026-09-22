You need to copy these from simulator runtime here: MTLSimDriver.framework, MTLSimImplementation.framework, MetalSerializer.framework
After that, patch MTLSimDriver

fix MetalSerializer `have 'macOS', need 'iOS'` error:

replace `/var/jb/usr/macOS/Frameworks/MetalSerializer.framework/MetalSerializer` with `MetalSerializer.new`
```
vtool -set-build-version ios 16.3 16.3 -tool ld 857.1 -output MetalSerializer.new MetalSerializer
vtool -remove-build-version iossim -output MetalSerializer.new MetalSerializer.new
codesign -f -s - MetalSerializer.new
```

```
vtool -set-build-version 1 13.0 13.0 -replace -output MetalSerializer_macos MetalSerializer
codesign -f -s - MetalSerializer_macos
```

use `vtool -show` to verify:
```
❯ vtool -show MetalSerializer.new
MetalSerializer.new (architecture x86_64):
Load command 10
      cmd LC_SOURCE_VERSION
  cmdsize 16
  version 306.5.16
Load command 22
      cmd LC_BUILD_VERSION
  cmdsize 32
 platform IOS
    minos 16.3
      sdk 16.3
   ntools 1
     tool LD
  version 857.1
MetalSerializer.new (architecture arm64):
Load command 10
      cmd LC_SOURCE_VERSION
  cmdsize 16
  version 306.5.16
Load command 22
      cmd LC_BUILD_VERSION
  cmdsize 32
 platform IOS
    minos 16.3
      sdk 16.3
   ntools 1
     tool LD
  version 857.1
❯ vtool -show MetalSerializer_macos
MetalSerializer_macos (architecture x86_64):
Load command 10
      cmd LC_SOURCE_VERSION
  cmdsize 16
  version 306.5.16
Load command 22
      cmd LC_BUILD_VERSION
  cmdsize 24
 platform MACOS
    minos 13.0
      sdk 13.0
   ntools 0
MetalSerializer_macos (architecture arm64):
Load command 10
      cmd LC_SOURCE_VERSION
  cmdsize 16
  version 306.5.16
Load command 22
      cmd LC_BUILD_VERSION
  cmdsize 24
 platform MACOS
    minos 13.0
      sdk 13.0
   ntools 0
❯ vtool -show MetalSerializer
MetalSerializer (architecture x86_64):
Load command 10
      cmd LC_BUILD_VERSION
  cmdsize 32
 platform IOSSIMULATOR
    minos 16.4
      sdk 16.4
   ntools 1
     tool LD
  version 857.1
Load command 11
      cmd LC_SOURCE_VERSION
  cmdsize 16
  version 306.5.16
MetalSerializer (architecture arm64):
Load command 10
      cmd LC_BUILD_VERSION
  cmdsize 32
 platform IOSSIMULATOR
    minos 16.4
      sdk 16.4
   ntools 1
     tool LD
  version 857.1
Load command 11
      cmd LC_SOURCE_VERSION
  cmdsize 16
  version 306.5.16
```
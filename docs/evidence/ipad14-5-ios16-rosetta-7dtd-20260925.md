# iPad14,5 iOS 16.0 Rosetta / 7 Days to Die evidence (2026-09-25)

## Scope and result

This note records the attempted launch of Steam app 251570 on an iPad14,5
(M2) running iOS 16.0 build 20A8372 and a macOS 13.4 build 22F66 chroot.

**Result: the game did not reach process start, so no FPS result exists.**  The
installed macOS depot is x86_64-only, while this iOS kernel returns
`EBADARCH` instead of constructing a Rosetta translated task.  An experimental
userspace setup got far enough to build a valid 844 MB Rosetta AOT shared
cache, but the same x86_64 exec still failed.  This separates the working AOT
daemon path from the missing kernel exec path.

The `MACWS_ROSETTA_AMFI_PUBLIC_KEY_HASH` interposer added with this evidence is
therefore an explicitly opt-in **diagnostic scaffold, not a Rosetta fix**.

## Game/depot evidence

Steam's `appmanifest_251570.acf` reported:

```text
"StateFlags"       "4"
"buildid"          "24994517"
"BytesToDownload"  "16251001312"
"BytesDownloaded"  "16251001312"
```

`lipo -info` on all three launch targets reported a single x86_64 slice:

```text
7dLauncher.app/Contents/MacOS/7dLauncher: architecture: x86_64
7DaysToDie.app/Contents/MacOS/7 Days To Die: architecture: x86_64
7DaysToDie_EAC.app/Contents/MacOS/start_protected_game: architecture: x86_64
```

Their SHA-256 values were respectively:

```text
1c283e2c692b6813e728dc885253a78c9df7b94ae62b0890a65aedc4dbe6d113
c15bdfaaad81dc72b6780a54feba1d338fd048f51bb774961e42ce9a64e291d9
db328ca13bd59914f75d7fbc88d3299c2332f5a0266037ca0a9bb2fdfabfb11c
```

Steam recorded six real launch attempts in `logs/gameprocess_log.txt`, all
ending in `OS Error 256`; `console_log.previous.txt` recorded matching
`LaunchApp failed` events for app 251570.

## Rosetta userspace findings

### Missing optional payload

RE-confirmed via the actual 22F66 `translate_tool`: before contacting the
daemon, its code checks for
`/Library/Apple/usr/libexec/oah/libRosettaRuntime`.  The initial chroot lacked
that file, so the tool's `couldn't connect to daemon` message was misleading.

The exact Apple Software Update product was `032-84877` (`BuildVersion=22F66`):

```text
https://swcdn.apple.com/content/downloads/63/26/032-84877-A_C30N4GOPDD/m0nv9wrbxxo8bllc9kf5luqeys11eu23kg/RosettaUpdateAuto.pkg
SHA-256 8ac9feb4f90934584b4a5e531a1f4a6d62ffd5c8dd2e883c7d4596a2e92f5d71
```

The package signature verified as Apple Software.  Installing its signed
payload moved `translate_tool` past the file check and into its Mach message
wait for `oahd`.

### AMFI policy mismatch

RE-confirmed via 22F66 `oahd` UUID
`51CE2A7E-F2A7-3277-B81C-A80D657FECCB`:

- `oahd+0x24fc` zeroes a caller-provided output buffer, constructs the exact
  two-word descriptor `{bytes, length}`, and calls
  `__sandbox_ms("AMFI", 0x5c, &descriptor)`.
- `oahd+0x4fa0` requests 32 bytes; a nonzero result branches to
  `oahd+0x5490`, whose literal is `Couldn't get amfi public key hash`, then
  aborts.
- Successful startup does not call `bootstrap_check_in("com.apple.oahd")`
  until `oahd+0x53c4`.

Runtime-confirmed with `misc/rosetta_amfi_hash_probe.c` on iOS 16.0:

```text
result=-1 errno=78 (Function not implemented) size=32
hash=a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5
```

The unchanged sentinel proves the iOS policy did not write an output.  The
same probe on an Apple-silicon macOS host returned success and 32 real bytes.

For diagnosis only, libmachook can now accept those captured bytes through
`MACWS_ROSETTA_AMFI_PUBLIC_KEY_HASH`.  The adapter runs only in a process named
`oahd`, only for AMFI operation `0x5c`, and only after the native iOS call has
returned `-1/ENOSYS`.  It validates all 64 hex digits and the exact 32-byte
output descriptor before copying anything.

### AOT generation succeeded

The chroot already contained the complete x86_64 dyld cache below its Preboot
Cryptex path, but `/System/Library/dyld` omitted the main cache and subcaches
`.02` through `.04`.  Linking those existing files exposed the next concrete
error: `oahd-helper` could not load Apple-signed `libRosettaAot.dylib` under
iOS AMFI.  A fresh-inode ad-hoc signature plus trust-cache registration fixed
that device-specific admission problem.

After removing the zero-byte artifact from the failed attempt,
`translate_tool` returned 0 and created:

```text
size    844357787 bytes
sha256  9af81d6597156167e9da0f410eb1990cf99432ab639a97e34dc2456db24b71ce
path    /var/db/oah/17ffa1315d9e7c6688ddb9b064ebe70cbc98a7e2d852b0a1fe0dd253872f83e2/
        9bdf650c7fc03de1bac48535aea9b6aa/dyld_shared_cache_x86_64.aot
```

This is the positive witness that the package, daemon, helper, x86 shared
cache, and AOT compiler path were functioning together.

## Kernel exec blocker

Runtime-confirmed after successful AOT generation:

```text
$ arch -x86_64 /usr/bin/uname -m
arch: posix_spawnp: /usr/bin/uname: Bad CPU type in executable
exit=1
```

The error is Darwin `EBADARCH` (86).  No x86 process was created.

RE-confirmed against the target's actual decompressed kernelcache (Darwin
22.0.0, `RELEASE_ARM64_T8112`; compressed image SHA-256
`f7fb099135b1a881349a77c460f821bc791e285e0fee8600624755f6a16fa9bd`):

- no `/usr/libexec/rosetta/runtime`, `runtime_t8027`, or `runtime_internal`
  string exists;
- no `load_rosetta` or `task_is_translated` symbol exists;
- the native AMFI Rosetta query above is `ENOSYS`.

For comparison, Apple's matching XNU 8792 source puts the runtime mapping,
translated pmap creation, translated-task marking, executable/dyld FD stack
construction, Mach message compatibility, and thread-state conversion behind
`CONFIG_ROSETTA`.  The runtime itself has an intentional trap as its ordinary
Mach-O entry and is entered through the kernel-established Rosetta ABI, so
executing it as a normal command is not an equivalent fallback.

This is not a single architecture predicate that can be safely forced.  A
correct port needs the missing translated-task kernel contract (or a complete
userspace Mach-O loader reproducing it).  Until that exists, an x86_64-only
7 Days to Die build cannot start on this iOS kernel, and a claim of normal
graphics or M2-MacBook-equivalent FPS would have no runtime evidence.

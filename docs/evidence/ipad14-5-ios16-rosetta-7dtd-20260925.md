# iPad14,5 iOS 16.0 Rosetta / 7 Days to Die evidence (2026-09-25)

## Scope and result

This note records the attempted launch of Steam app 251570 on an iPad14,5
(M2) running iOS 16.0 build 20A8372 and a macOS 13.4 build 22F66 chroot.

**Result: the stock game did not reach process start, and the experimental
arm64 rehost did not finish Mono assembly reload, so no rendered-frame or FPS
witness exists.**  The current public depot is not merely an x86 launcher:
its Unity player and Mono runtime are x86_64 too.  This iOS kernel returns
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

A complete Mach-O inventory found 14 binaries. Four plugins were universal
arm64+x86_64 (`InControlNative`, EOS, Discord, and `steam_api`), but the game
executable, `UnityPlayer.dylib`, all three Mono runtime/helper dylibs, Burst
generated library, launcher, EAC entry, Magick, and MouseLib were x86_64-only.
The player data identifies the exact engine as:

```text
2022.3.62f2 (7670c08855a9)
```

This runtime evidence disproves the initial assumption that only the launcher
needed translation. The managed assemblies and asset data are portable, but
the native player/runtime required either Rosetta or a matched arm64 player.

## Experimental arm64 Unity rehost

Unity's exact signed Mono player support package was downloaded from the
official Unity archive:

```text
UnitySetup-Mac-Mono-Support-for-Editor-2022.3.62f2.pkg
SHA-256 ada8c5ebaa3431d54a5490df95196d8b439a55d57a703c5aade9d48fd33375e9
```

Its `macos_arm64_player_nondevelopment_mono` main executable,
`UnityPlayer.dylib`, `libmonobdwgc-2.0.dylib`, `libmono-native.dylib`, and
`libMonoPosixHelper.dylib` were placed in a separately signed
`7DaysToDie-ARM.app`. The app links to the installed game's Resources, Mono
configuration, and data instead of modifying Valve's depot. Runtime-confirmed
positive progress was:

```text
Initialize engine version: 2022.3.62f2 (7670c08855a9)
[PhysX] Initialized MultithreadedTaskDispatcher with 8 workers.
Begin MonoManager ReloadAssembly
```

The arm64 rehost therefore proves that the game data can be opened by the
matched arm64 Unity player. It is not yet a playable port.

### Mono interpreter/JIT evidence

RE-confirmed via the exact arm64 Mono UUID
`E090F9F3-5091-3C8E-825D-B8632ABFBB84`:

- `mono_jit_set_aot_mode(8)` sets `mono_use_interpreter` and
  `force_use_interpreter` without enabling `mono_aot_only`; Unity's Mono source
  names this `MONO_AOT_MODE_INTERP_ONLY` (the `--interp` behavior).
- Mode 5 is the full-AOT interpreter contract and runtime-aborted at
  `aot-runtime.c:5724` because this normal player has no interpreter wrapper
  AOT modules.
- Directly setting only `mono_use_interpreter` mixed interpreter and JIT
  delegate ABIs and faulted in `interp_init_delegate+116`; that diagnostic was
  discarded.

Mode 8 first reached a real iOS incompatibility in
`pthread_jit_write_protect_np+516` (`brk #1`). `otool -Iv` identifies Mono's
late-bound `_pthread_jit_write_protect_np` slot at `__DATA,__la_symbol_ptr`
vmaddr `0x30a660`. Rebinding that symbolically identified slot to the existing
W^X compatibility implementation (only for the exact UUID and opt-in mode)
removed that trap. A subsequent no-debug run handled 331 expected page-write
faults before reaching the next genuine fault.

The bounded binary flight record captured the fault state below. A temporary
extended record used only for this diagnosis also recorded
`fault_in_recorded_executable_range=0`; that extension was removed after it
disproved the writable-JIT-page hypothesis.

```text
signal=10 code=1 thread_writable=0
pc=libmonobdwgc+0x2180b4
fault=0x10517fff8 reservations=4 executable_ranges=2
active_writers=0 handled_write_faults=331 dirty_pages=13
fault_in_recorded_executable_range=0
```

RE-confirmed via the exact dylib disassembly, `+0x2180b4` is
`mono_gc_memmove_aligned+200`, an `ldr x11, [x9, x10, lsl #3]`. Runtime
`vmmap` placed the fault address in the final eight bytes of
`104d80000-105180000 ---/rwx`; the next mapping began at `105180000` and was
RX. This is an invalid read from an uncommitted reservation, not a W^X write
fault and not a check that can safely be bypassed. The temporary hypothesis
that the page merely needed to be made writable was disproved and that patch
was removed.

The cause of Mono's invalid source range remains **THEORY**, not established
fact. The next useful evidence is the first-fault x0-x11/caller capture to
determine whether mode-8 interpreter state supplied a corrupt copy range or
whether the matched donor player expects an allocator contract absent in this
chroot.

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

This is not a single architecture predicate that can be safely forced. A
correct port needs the missing translated-task kernel contract (or a complete
userspace Mach-O loader reproducing it). Until that exists, the stock x86_64
Unity player cannot start on this iOS kernel. `RosettaLinux` is an arm64 ELF
component for a Linux guest launched by macOS Virtualization.framework, not a
drop-in macOS/iOS Mach-O translator; the device also exposes no `binfmt_misc`
and has no QEMU/Box64/FEX runtime installed. A full emulated macOS guest would
additionally lose the native display/GPU path this project is trying to
validate. The arm64 rehost is therefore the only tested userspace bypass so
far, and its Mono fault means a claim of normal graphics or
M2-MacBook-equivalent FPS would still have no runtime evidence.

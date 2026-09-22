"""Retire runtime-identified historical MacWS autoload jobs, preserving originals.

This is a bounded upgrade migration, not a feature switch. It never unloads a
service, signals an application, repairs unknown plists, or changes preferences.
The generated gui-launchd jobs remain the single application launch authority.
"""
import argparse
import hashlib
import os
from pathlib import Path
import plistlib
import re
import stat
import sys

# Exact bytes observed on the affected installation, 2026-09-19. In particular,
# a com.apple label is NOT sufficient to authorize removal of an Apple job.
# Unknown revisions are retained and reported for review, never guessed away.
RETIRED = {
    "com.apple.InstallerProgress.plist": "64742fdbce90a1a654245a3114f7849a10efc8f6ef1e5933c532bb19f1ddc2b3",
    "com.macwsguide.chrome150.debug.plist": "e7e06efd1ef316f7069cbe624c00fe1f999888f4ba64ba8c7430fb2ce18a0d4f",
    "com.macwsguide.chrome150.plist": "915328ea97aee058a01565f2b03a09db7de38deeb4dd36d808b2513d6af25809",
    "com.macwsguide.cpu-fp-probe.plist": "df8f39fb78c0f8f9e235a08efe245106df7c9233537142397bc7b41e3ae9e531",
    "com.macwsguide.jitprobe.plist": "8ff45c1a40697d5bc66d09fa97824633466c0871a6e0251360a801c78b5d586d",
    "com.macwsguide.rootdir-ios-probe.plist": "138f68f3706f58b24d20476a32c275c8385958b057d1bfe7bc1e62b1079d216f",
    "com.macwsguide.rootdir-macos-probe.plist": "b416c96dcf235053adcfbf3af54064752707481a6692ccaaf5287c5588ea1ddc",
    "com.macwsguide.steam.runtime.plist": "1fcb5c5db2030f3057d9d51504ccda04f499d2411f316d821f6db80edaf771a4",
    "com.macwsguide.systemsettings.plist": "2c6785ddd74eb9091523f0c834cd1a8b5da3b248fc8c879019085a5a0a5fb1b6",
}
# Additional historical files in the two project-owned launch directories.
# A duplicate locationd trace job is not the current clean gui-launchd job.
EXTRA_RETIRED = {
    "usr/macOS/LaunchDaemons/com.macwsguide.macos-locationd.plist": "d15d4ceff906560bd8f24d4e74301e5c8cdfe098ef400f1647492f286a8ebd4d",
    "usr/macOS/gui-launchd/com.macwsguide.sandbox-audit-probe.plist": "33bec0abbfc95c19327fd77e07270b83608e2b50e0d9076aecc3abd5b5e0dc12",
    "usr/macOS/gui-launchd/com.macwsguide.spaceprobe.plist": "de2748fc762d765f1fcaa08e94417f56e7b6ca49ba8299063f8c4dde746395d0",
    "usr/macOS/gui-launchd/com.macwsguide.glassdemo.plist": "8eb420ad86a56fb62d9c1b89571cc276dc4808853e6c04a6856449be942b3670",
    "usr/macOS/gui-launchd/com.macwsguide.iconservicesagent.diag.plist": "6c68a3d5a0f68d26a1d44180b0b4a786369a3fd4f1d5a3646880c3032fa3f244",
    "usr/macOS/gui-launchd/com.macwsguide.inputlab.plist": "16bfac3b4e87d9083abb7d27e14a82bba53fb715ed77e3540a201b0f7ada6699",
    "usr/macOS/gui-launchd/com.macwsguide.maps.plist": "daaf979705c4ba5c95893752e74f57a948f80b03b42e11cd3e88a88f6b6a52ab",
}
CURRENT = {
    "com.macwsguide.alloc.plist": ("com.macwsguide.alloc", "macwsallocd"),
    "com.macwsguide.hostd.plist": ("com.macwsguide.hostd", "macwshostd"),
    "com.macwsguide.keychain.plist": ("com.macwsguide.keychain", "macwskeychaind"),
}
LIMIT = 65536


def directory(base, relative, create=False):
    result = base
    for component in Path(relative).parts:
        result /= component
        if not result.exists() and not result.is_symlink() and create:
            result.mkdir(mode=0o700)
        info = result.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError("expected a real directory: " + str(result))
    return result


def read_regular(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > LIMIT:
            raise ValueError("expected a bounded regular plist: " + str(path))
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            content = stream.read(LIMIT + 1)
        if len(content) > LIMIT:
            raise ValueError("plist grew beyond the identity bound")
        return content, info
    finally:
        os.close(descriptor)


def identity(info):
    # nlink changes intentionally during no-clobber archival.
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_uid, info.st_gid, info.st_mode)


def sync(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def recognized_legacy(name, job, digest):
    if RETIRED.get(name) == digest:
        return True
    # Already-supported pre-gui-launchd VS Code migration. Its historical XML
    # comment is sometimes invalid; exact launch identity was the existing
    # production migration contract, not a requirement to repair its bytes.
    arguments = job.get("ProgramArguments")
    if "Program" in job and (not isinstance(arguments, list) or not arguments or
                            job["Program"] != arguments[0]):
        return False
    if (name == "com.macwsguide.glassdemo.plist" and
            job.get("Label") == "com.macwsguide.glassdemo" and
            arguments == ["/var/jb/usr/macOS/bin/launchdchrootexec", "0", "0",
                          "/var/mnt/rootfs", "/tmp/GlassDemo"]):
        return True
    return (name == "com.macwsguide.vscode.plist" and
            job.get("Label") in ("com.macwsguide.vscode", "UIKitApplication:com.macwsguide.vscode") and
            isinstance(arguments, list) and all(isinstance(value, str) for value in arguments) and
            arguments[:5] == ["/var/jb/usr/macOS/bin/launchdchrootexec", "0", "0",
                              "/var/mnt/rootfs", "/Applications/Visual Studio Code.app/Contents/MacOS/Electron"])


def migrate(prefix, check=False):
    prefix = Path(prefix).resolve(strict=True)
    if prefix == Path("/"):
        raise ValueError("expected a dedicated jailbreak prefix")
    boot = directory(prefix, "Library/LaunchDaemons")
    planned = []
    for source in sorted(boot.glob("*.plist")):
        try:
            raw, info = read_regular(source)
        except (OSError, ValueError):
            if source.name in CURRENT or source.name in RETIRED or source.name.startswith("com.macwsguide."):
                raise
            # Unrelated jailbreak jobs may legitimately be links or large.
            # This migration owns neither their content nor their policy.
            continue
        # Historically generated comments included illegal XML '--' text.
        # Strip comments for identity inspection only; archive original bytes.
        inspected = re.sub(br"<!--[\s\S]*?-->", b"", raw)
        try:
            job = plistlib.loads(inspected)
        except Exception:
            if source.name.startswith("com.macwsguide.") or b"launchdchrootexec" in raw:
                raise ValueError("unrecognized MacWS boot job: " + str(source))
            continue  # This migration does not validate unrelated iOS jobs.
        if not isinstance(job, dict):
            if source.name.startswith("com.macwsguide."):
                raise ValueError("invalid MacWS boot job: " + str(source))
            continue
        if source.name in CURRENT:
            label, program = CURRENT[source.name]
            if (job.get("Label") != label or job.get("ProgramArguments") !=
                    ["/var/jb/usr/macOS/bin/" + program] or
                    job.get("Program", "/var/jb/usr/macOS/bin/" + program) !=
                    "/var/jb/usr/macOS/bin/" + program):
                raise ValueError("current daemon identity mismatch: " + str(source))
            environment = job.get("EnvironmentVariables", {})
            allowed = {"CA_VSYNC_OFF": "1", "CA_DISABLE_SWAP_ICC": "1"} if program == "macwshostd" else {}
            if (not isinstance(environment, dict) or
                    any(allowed.get(key) != value for key, value in environment.items())):
                raise ValueError("unexpected production boot environment: " + str(source))
            continue
        owned = (source.name.startswith("com.macwsguide.") or
                 str(job.get("Label", "")).startswith(("com.macwsguide.", "UIKitApplication:com.macwsguide.")) or
                 b"launchdchrootexec" in raw)
        if not owned:
            continue
        digest = hashlib.sha256(raw).hexdigest()
        if not recognized_legacy(source.name, job, digest):
            raise ValueError("unrecognized legacy MacWS boot job retained: " + str(source))
        planned.append((source, raw, info, digest))
    for relative, expected_digest in EXTRA_RETIRED.items():
        source = prefix / relative
        if not source.exists() and not source.is_symlink():
            continue
        directory(prefix, str(Path(relative).parent))
        raw, info = read_regular(source)
        digest = hashlib.sha256(raw).hexdigest()
        if digest != expected_digest:
            raise ValueError("unrecognized legacy GUI job retained: " + str(source))
        planned.append((source, raw, info, digest))
    # Validate the full inventory before mutating any boot file.
    if check:
        return [str(source) for source, _, _, _ in planned]
    if not planned:
        return []
    archive = directory(prefix, "usr/macOS/retired-launch-jobs", create=True)
    retired = []
    for source, raw, info, digest in planned:
        latest, latest_info = read_regular(source)
        if identity(latest_info) != identity(info) or latest != raw:
            raise ValueError("boot job changed before archival: " + str(source))
        destination = archive / (source.name + "." + digest + "." +
                                 str(info.st_dev) + "." + str(info.st_ino) + ".disabled")
        if destination.exists() or destination.is_symlink():
            archived, archived_info = read_regular(destination)
            if archived != raw or identity(archived_info) != identity(info):
                raise ValueError("archive collision: " + str(destination))
        else:
            os.link(source, destination, follow_symlinks=False)
            sync(archive)
        # Refuse replacement between validation and link. An archived original
        # always exists before the autoload name is removed.
        if identity(source.lstat()) != identity(info):
            raise ValueError("boot job replaced during archival: " + str(source))
        source.unlink()
        sync(source.parent)
        retired.append(str(destination))
    return retired


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", default="/var/jb", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        paths = migrate(args.prefix, args.check)
        for path in paths:
            print(("LEGACY-BOOT pending: " if args.check else "LEGACY-BOOT archived: ") + path)
        if not paths:
            print("LEGACY-BOOT current")
        return 2 if args.check and paths else 0
    except (OSError, ValueError) as error:
        print("LEGACY-BOOT error: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

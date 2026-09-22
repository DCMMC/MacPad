"""Provision complete Office AIR translations without modifying Office bundles.

Invoke with the installed iOS Python, not via a shebang. This is production
derived-data provisioning, not a feature flag or runtime shader replacement.
The existing metal2metal CLI owns conversion and manifest verification.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import plistlib
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile

MAX_ARTIFACT = 4 * 1024 * 1024
MAX_MANIFEST = 1024 * 1024
PROFILE = "ventura13-ios19-macabi"
RUNTIME_BASE = PurePosixPath("/usr/local/share/macws/metal2metal")
APPLICATIONS = ("Microsoft Word.app", "Microsoft PowerPoint.app", "Microsoft Excel.app")
ARCHIVE_RELATIVE = Path("Contents/Resources/Arc.bundle/Metal2DShaders.metallib.zip")
# Runtime-confirmed ZIP entry has no leading slash. Office's separate
# MBUCopyZipArchivePart API uses a slash-prefixed *lookup argument*, not an
# absolute ZIP member. Do not broaden extraction based on that API string.
ENTRY_NAMES = {"Metal2DShaders.metallib"}
GENERATION_FILE = re.compile(r"^(source|translated)-([0-9a-f]{32})\.metallib$")


class ProvisionError(Exception):
    pass


def checked_path(root: Path, relative: Path, *, directories: bool = False) -> Path:
    """Reject symlink traversal in the fixed root-relative managed namespace."""
    if relative.is_absolute() or any(part in (".", "..") for part in relative.parts):
        raise ProvisionError(f"unsafe relative path: {relative}")
    current = root
    for index, part in enumerate(relative.parts):
        current /= part
        last = index == len(relative.parts) - 1
        try:
            value = current.lstat()
        except FileNotFoundError:
            if not directories:
                raise
            current.mkdir(mode=0o755, exist_ok=True)
            value = current.lstat()
        if stat.S_ISLNK(value.st_mode):
            raise ProvisionError(f"refusing symlink: {current}")
        if (not last or directories) and not stat.S_ISDIR(value.st_mode):
            raise ProvisionError(f"not a directory: {current}")
    return current


def read_regular(path: Path, limit: int) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise ProvisionError(f"not a bounded regular file: {path}")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(limit + 1)
        after = os.fstat(fd)
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if len(data) > limit or len(data) != after.st_size or any(
                getattr(before, name) != getattr(after, name) for name in fields):
            raise ProvisionError(f"file changed while reading: {path}")
        return data
    finally:
        os.close(fd)


def archive_source(path: Path) -> tuple[bytes, str]:
    archive = read_regular(path, MAX_ARTIFACT)
    with zipfile.ZipFile(io.BytesIO(archive)) as handle:
        entries = handle.infolist()
        if len(entries) != 1 or entries[0].filename not in ENTRY_NAMES:
            raise ProvisionError(f"unexpected Office shader ZIP entries: {path}")
        entry = entries[0]
        mode = entry.external_attr >> 16
        if (entry.is_dir() or stat.S_ISLNK(mode) or entry.flag_bits & 1 or
                entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED) or
                not 0 < entry.file_size <= MAX_ARTIFACT):
            raise ProvisionError(f"unsupported Office shader ZIP member: {path}")
        # Do not extract filesystem paths. The explicit read bound also applies
        # if a corrupt central directory understates the actual uncompressed size.
        with handle.open(entry) as stream:
            source = stream.read(MAX_ARTIFACT + 1)
        if len(source) != entry.file_size or len(source) > MAX_ARTIFACT or not source.startswith(b"MTLB"):
            raise ProvisionError(f"invalid Office shader member: {path}")
    return source, hashlib.sha256(archive).hexdigest()


def discover(root: Path) -> tuple[dict[str, bytes], list[str]]:
    sources: dict[str, bytes] = {}
    errors: list[str] = []
    for application in APPLICATIONS:
        try:
            try:
                path = checked_path(root, Path("Applications") / application / ARCHIVE_RELATIVE)
            except FileNotFoundError:
                continue
            source, archive_hash = archive_source(path)
            digest = hashlib.sha256(source).hexdigest()
            if digest in sources and sources[digest] != source:
                raise ProvisionError("Office source SHA256 collision")
            sources[digest] = source
            print(f"[INFO] Office shader source app={application!r} zip_sha256={archive_hash} source_sha256={digest}")
        except (ProvisionError, OSError, ValueError, zipfile.BadZipFile) as error:
            message = f"Office shader source app={application!r}: {error}"
            errors.append(message)
            print(f"[ERROR] {message}", file=sys.stderr)
    return sources, errors


def run_tool(args: argparse.Namespace, arguments: list[str]) -> None:
    command = [sys.executable, str(args.converter), *arguments]
    process = subprocess.Popen(command, start_new_session=True)
    try:
        status = process.wait(timeout=args.timeout)
    except subprocess.TimeoutExpired:
        # The converter can own an LLVM child. Kill only this newly created
        # process group, never leave its child behind or touch another service.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        raise
    if status:
        raise subprocess.CalledProcessError(status, command)


def manifest_paths(root: Path, digest: str, manifest: dict) -> tuple[Path, Path]:
    if not isinstance(manifest, dict) or manifest.get("profile") != PROFILE:
        raise ValueError("wrong Office translation profile")
    # Office's public data constructor is observable. Never enable the private
    # function-name-only fallback for an application-owned library, even if
    # another library happens to export the same complete set of names.
    if manifest.get("requires_source_identity") is not True:
        raise ValueError("Office route lacks exact source identity policy")
    expected_parent = RUNTIME_BASE / "office" / digest
    paths = []
    generation = None
    for label in ("source", "output"):
        identity = manifest.get(label)
        if not isinstance(identity, dict):
            raise ValueError("invalid Office manifest identity")
        value = identity.get("runtime_path")
        if not isinstance(value, str):
            raise ValueError("invalid Office runtime path")
        runtime = PurePosixPath(value)
        match = GENERATION_FILE.fullmatch(runtime.name)
        expected_kind = "source" if label == "source" else "translated"
        if (str(runtime) != value or runtime.parent != expected_parent or not match or
                match[1] != expected_kind or (generation and generation != match[2])):
            raise ValueError("Office manifest path is outside its exact source generation")
        generation = match[2]
        paths.append(checked_path(root, Path(*runtime.parts[1:])))
    if manifest["source"].get("sha256") != digest:
        raise ValueError("Office manifest source hash mismatch")
    return paths[0], paths[1]


def cache_valid(root: Path, digest: str, route: Path, args: argparse.Namespace) -> bool:
    try:
        manifest = plistlib.loads(read_regular(route, MAX_MANIFEST))
        source, output = manifest_paths(root, digest, manifest)
        if hashlib.sha256(read_regular(source, MAX_ARTIFACT)).hexdigest() != digest:
            return False
        read_regular(output, MAX_ARTIFACT)
        run_tool(args, ["verify-runtime-manifest", str(route), "--source", str(source), "--output", str(output)])
        return True
    except (FileNotFoundError, ValueError, plistlib.InvalidFileException, subprocess.CalledProcessError):
        return False


def sync_file(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def sync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def provision(root: Path, digest: str, source_data: bytes, args: argparse.Namespace) -> None:
    base = Path(*RUNTIME_BASE.parts[1:])
    cache = checked_path(root, base / "office" / digest, directories=True)
    routes = checked_path(root, base / "routes", directories=True)
    route = routes / f"office-metal2d-{digest}.route.plist"
    if route.is_symlink():
        raise ProvisionError(f"refusing symlink: {route}")
    if cache_valid(root, digest, route, args):
        print(f"[INFO] complete Office metal2metal route already verified source={digest}")
        return

    # Unique flat files keep all previous generations intact until the single
    # atomic route publication. A converter/verification/publication failure
    # cannot overwrite a prior source, translation, or valid route.
    generation = uuid.uuid4().hex
    source_name = f"source-{generation}.metallib"
    output_name = f"translated-{generation}.metallib"
    runtime = RUNTIME_BASE / "office" / digest
    with tempfile.TemporaryDirectory(prefix=".build-", dir=cache) as directory:
        scratch = Path(directory)
        source, output, manifest = scratch / source_name, scratch / output_name, scratch / "route.plist"
        source.write_bytes(source_data)
        run_tool(args, ["translate", str(source), str(output), "--profile", PROFILE,
                       "--llvm-dis", str(args.llvm_dis), "--llvm-as", str(args.llvm_as),
                       "--auto-lower-known-air",
                       "--runtime-manifest", str(manifest),
                       "--runtime-source-path", str(runtime / source_name),
                       "--runtime-output-path", str(runtime / output_name)])
        # Enforce size/type/path bounds before invoking the authoritative verifier.
        if read_regular(source, MAX_ARTIFACT) != source_data:
            raise ProvisionError("converter changed the source snapshot")
        if not read_regular(output, MAX_ARTIFACT).startswith(b"MTLB"):
            raise ProvisionError("converter did not produce an MTLB container")
        details = plistlib.loads(read_regular(manifest, MAX_MANIFEST))
        if (not isinstance(details, dict) or not isinstance(details.get("source"), dict) or
                not isinstance(details.get("output"), dict) or details.get("profile") != PROFILE or
                details.get("source", {}).get("runtime_path") != str(runtime / source_name) or
                details.get("output", {}).get("runtime_path") != str(runtime / output_name)):
            raise ProvisionError("converter manifest has unexpected runtime identities")
        details["requires_source_identity"] = True
        manifest.write_bytes(plistlib.dumps(details))
        run_tool(args, ["verify-runtime-manifest", str(manifest), "--source", str(source), "--output", str(output)])
        for path in (source, output, manifest):
            path.chmod(0o644)
            sync_file(path)
        # Same-filesystem hard-link publication refuses a pre-existing target,
        # including a malicious symlink; only our fresh names are ever created.
        os.link(source, cache / source_name, follow_symlinks=False)
        os.link(output, cache / output_name, follow_symlinks=False)
        sync_dir(cache)
        # The route may be in a different directory but shares the rootfs. Its
        # atomic rename is the commit point after both immutable files are durable.
        os.replace(manifest, route)
        sync_dir(routes)
    print(f"[INFO] installed complete Office metal2metal route source={digest} generation={generation}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rootfs", type=Path, default=Path("/var/mnt/rootfs"))
    parser.add_argument("--converter", type=Path, default=Path("/var/jb/usr/macOS/bin/metal2metal.py"))
    parser.add_argument("--llvm-dis", type=Path, default=Path("/var/jb/usr/macOS/bin/macws-llvm-dis"))
    parser.add_argument("--llvm-as", type=Path, default=Path("/var/jb/usr/macOS/bin/macws-llvm-as"))
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args(argv)
    try:
        if not args.rootfs.is_absolute() or not 0 < args.timeout <= 600:
            raise ProvisionError("rootfs must be absolute and timeout must be in (0, 600]")
        root = args.rootfs.resolve(strict=True)
        if not root.is_dir():
            raise ProvisionError("rootfs is not a directory")
        sources, source_errors = discover(root)
        if not sources:
            if source_errors:
                return 1
            print("[INFO] no supported Office shader archives installed; no changes")
            return 0
        if not args.converter.is_file() or not all(path.is_file() and os.access(path, os.X_OK)
                for path in (args.llvm_dis, args.llvm_as)):
            raise ProvisionError("metal2metal or its packaged Apple LLVM tools are unavailable")
        office = checked_path(root, Path(*RUNTIME_BASE.parts[1:]) / "office", directories=True)
        # flock the directory itself: no persistent enable/boot/debug marker.
        fd = os.open(office, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            deadline = time.monotonic() + args.timeout
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ProvisionError("Office provisioner is busy; no changes")
                    time.sleep(0.05)
            for digest, source in sources.items():
                try:
                    provision(root, digest, source, args)
                except (ProvisionError, OSError, ValueError, plistlib.InvalidFileException,
                        subprocess.SubprocessError) as error:
                    message = f"Office shader source={digest}: {error}"
                    source_errors.append(message)
                    print(f"[ERROR] {message}", file=sys.stderr)
        finally:
            os.close(fd)
        # Healthy applications are still provisioned if another archive or
        # version failed. A partial success must not create the boot shortcut:
        # the shell caller sees failure and retries at the next preflight.
        return 1 if source_errors else 0
    except (ProvisionError, OSError, ValueError, zipfile.BadZipFile, plistlib.InvalidFileException,
            subprocess.SubprocessError) as error:
        print(f"[ERROR] Office metal2metal provisioning failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

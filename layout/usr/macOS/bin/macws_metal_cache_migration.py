"""One-time derived Metal library target revision, never a feature switch.

Only macOS root-user 31001 libraries.list/data pairs are retired. Rename
preserves bytes/inodes/permissions, and a recoverable journal permits a
partial transaction to resume. No shader cache binary format is guessed.
"""
import argparse
import ctypes
import errno
import fcntl
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import tempfile

# Kind-5 CoreUI image-filter composition preserves the request bytes/cache key
# while translating the parsed modules to Catalyst. Caches produced under v2
# can therefore retain incompatible macOS-target libraries even after the
# compiler repair. Retire them once at the existing quiescent boundary; this
# revision never enables the compatibility code and never touches live clients.
SCHEMA = "macws-macabi-image-filter-v3"
CACHE_RELATIVE = Path("private/var/folders/zz/zyxvpxvq6csfxvn_n0000000000000/C")
STATE_RELATIVE = Path("Library/Caches/MacWS/metal-library-target")
NAMES = ("libraries.list", "libraries.data")


class MigrationError(RuntimeError):
    pass


class Busy(MigrationError):
    pass


def checked(root, relative, directory=False):
    """Reject symlinks and non-directory ancestors, even inside the rootfs."""
    relative = PurePosixPath(relative)
    if relative.is_absolute() or not relative.parts or any(p in (".", "..") for p in relative.parts):
        raise MigrationError("invalid relative path: " + str(relative))
    target = root
    for index, component in enumerate(relative.parts):
        target = target / component
        try:
            info = target.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise MigrationError("symlink refused: " + str(target))
        if index < len(relative.parts) - 1 or directory:
            if not stat.S_ISDIR(info.st_mode):
                raise MigrationError("non-directory ancestor: " + str(target))
    return target


def stamp(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise MigrationError("expected one regular cache file: " + str(path))
    return {key: getattr(info, key) for key in
            ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_size", "st_mtime_ns")}


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_json(path, payload):
    descriptor, name = tempfile.mkstemp(prefix=".commit-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(payload, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path):
    if stamp(path) is None:
        return None
    if path.stat().st_size > 1024 * 1024:
        raise MigrationError("oversized cache revision record")
    with path.open() as stream:
        return json.load(stream)


def live_chroot_processes(root):
    """Actual process-root witness, not an application-name allowlist.

    Darwin libproc's versioned PROC_PIDVNODEPATHINFO ABI is 2352 bytes;
    pvi_rdir.vip_path is its last 1024 bytes (offset1328). Runtime verified
    on iPadOS16.3 against Finder/iconservices/Weather and a native control.
    This inspects metadata only; no task port, attach, suspension or signal.
    """
    library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    library.proc_listallpids.argtypes = (ctypes.c_void_p, ctypes.c_int)
    library.proc_pidinfo.argtypes = (ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
                                   ctypes.c_void_p, ctypes.c_int)
    pids = (ctypes.c_int * 8192)()
    count = library.proc_listallpids(pids, ctypes.sizeof(pids))
    if count <= 0 or count >= len(pids):
        raise MigrationError("could not obtain bounded process inventory")
    live = []
    expected = os.fsencode(root)
    for pid in pids[:count]:
        if pid <= 0 or pid == os.getpid():
            continue
        data = ctypes.create_string_buffer(2352)
        ctypes.set_errno(0)
        result = library.proc_pidinfo(pid, 9, 0, data, len(data))
        if result == len(data):
            directory = data.raw[1328:].split(b"\0", 1)[0]
            if directory == expected:
                live.append(pid)
        elif result == 0 and ctypes.get_errno() == errno.ESRCH:
            # Exited processes have no live cache mapping. Permission errors
            # are NOT equivalent to native iOS identity: fail closed below.
            continue
        else:
            raise MigrationError("unrecognized libproc root record for pid " + str(pid))
    return live


def candidates(root):
    base = checked(root, CACHE_RELATIVE, directory=True)
    if not base.exists():
        return []
    clients = [base]
    for entry in sorted(base.iterdir()):
        if entry.name.startswith(".") or entry.name == "com.apple.metal":
            continue
        if entry.is_symlink():
            raise MigrationError("symlink cache-client directory refused: " + str(entry))
        if entry.is_dir() and not entry.is_symlink():
            clients.append(entry)
            if entry.name == "WindowServer":
                for child in sorted(entry.iterdir()):
                    if child.is_symlink():
                        raise MigrationError("symlink WindowServer cache-client refused: " + str(child))
                    if child.is_dir():
                        clients.append(child)
    if len(clients) > 1024:
        raise MigrationError("cache-client inventory exceeds bound")
    result = []
    for client in clients:
        for name in NAMES:
            relative = (client / "com.apple.metal/31001" / name).relative_to(root)
            path = checked(root, relative)
            info = stamp(path)
            if info is not None:
                result.append({"path": str(relative), "stat": info})
    return result


def valid_cache_relative(value):
    relative = PurePosixPath(value)
    try:
        parts = relative.relative_to(CACHE_RELATIVE).parts
    except ValueError:
        return False
    return (len(parts) in (3, 4, 5) and parts[-3:-1] == ("com.apple.metal", "31001")
            and parts[-1] in NAMES and not any(p in (".", "..") for p in relative.parts)
            and (len(parts) != 5 or parts[0] == "WindowServer"))


def current(root):
    path = checked(root, STATE_RELATIVE / "schema.json")
    record = read_json(path)
    return record == {"schema": SCHEMA}


def journal_files(journal):
    if (not isinstance(journal, dict) or journal.get("schema") != SCHEMA
            or not isinstance(journal.get("files"), list)):
        raise MigrationError("invalid cache migration journal")
    generation = journal.get("generation", 0)
    if type(generation) is not int or not 0 <= generation <= 1024:
        raise MigrationError("invalid cache generation")
    files = journal["files"]
    if (len(files) > 2048 or not all(isinstance(entry, dict) and isinstance(entry.get("path"), str) for entry in files)
            or len({entry["path"] for entry in files}) != len(files)):
        raise MigrationError("invalid cache migration inventory")
    keys = {"st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_size", "st_mtime_ns"}
    for entry in files:
        expected = entry.get("stat")
        if (not valid_cache_relative(entry["path"]) or not isinstance(expected, dict)
                or set(expected) != keys or not all(type(value) is int and value >= 0 for value in expected.values())
                or not stat.S_ISREG(expected["st_mode"])):
            raise MigrationError("invalid cache migration target/metadata")
    return files


def archived_path(root, journal, entry):
    base = STATE_RELATIVE / "retired" / SCHEMA
    generation = journal.get("generation", 0)
    # Preserve the initial archive layout; never overwrite an old generation.
    if generation:
        base = base / "generations" / str(generation)
    return checked(root, base / entry["path"])


def refresh_generation(root, journal_path, journal):
    """A writer after an interrupted migration must not poison every restart.

    Existing archives are immutable and verified against their old receipts.
    The complete prior plan is retained before publishing a new numbered plan
    for the currently available cache generation. No missing/changed original
    file is falsely claimed to have been archived.
    """
    files = journal_files(journal)
    refresh = False
    unarchived = set()
    for entry in files:
        source = checked(root, entry["path"])
        source_info = stamp(source)
        archived_info = stamp(archived_path(root, journal, entry))
        if archived_info is not None:
            if archived_info != entry["stat"]:
                raise MigrationError("retired cache metadata changed: " + str(source))
            refresh |= source_info is not None
        else:
            unarchived.add(entry["path"])
            refresh |= source_info != entry["stat"]
    available = candidates(root)
    refresh |= bool({entry["path"] for entry in available} - unarchived)
    if not refresh:
        return journal
    generation = journal.get("generation", 0)
    if generation >= 1024:
        raise Busy("too many interrupted cache generations; preserved archives need inspection")
    history_path = checked(root, STATE_RELATIVE / (SCHEMA + ".history." + str(generation) + ".json"))
    old_history = read_json(history_path)
    if old_history is not None and old_history != journal:
        raise MigrationError("cache generation history collision")
    if old_history is None:
        atomic_json(history_path, journal)
    replacement = {"schema": SCHEMA, "generation": generation + 1,
                   "previous_generation": generation, "files": available}
    # Publishing this plan only after history is durable also makes a failure
    # between these two commits resumable without guessing a new generation.
    atomic_json(journal_path, replacement)
    return replacement


def migrate(root, process_inventory=live_chroot_processes):
    root = Path(root).resolve(strict=True)
    if root == Path("/") or not root.is_dir():
        raise MigrationError("expected a dedicated macOS rootfs")
    if current(root):
        return {"state": "current", "retired_files": 0}
    # No data/config mutation while any chroot client could have this cache
    # mapped. postinst defers; the normal GUI start calls after cleanup.
    live = process_inventory(root)
    if live:
        raise Busy("macOS clients still live: " + ",".join(map(str, live)))
    state = checked(root, STATE_RELATIVE, directory=True)
    state.mkdir(parents=True, exist_ok=True)
    lock = checked(root, STATE_RELATIVE / "migration.lock")
    descriptor = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise Busy("cache migration already running") from error
        if current(root):
            return {"state": "current", "retired_files": 0}
        live = process_inventory(root)
        if live:
            raise Busy("macOS clients appeared before cache mutation: " + ",".join(map(str, live)))
        journal_path = checked(root, STATE_RELATIVE / (SCHEMA + ".json"))
        journal = read_json(journal_path)
        if journal is None:
            journal = {"schema": SCHEMA, "files": candidates(root)}
            atomic_json(journal_path, journal)
        journal = refresh_generation(root, journal_path, journal)
        files = journal_files(journal)
        for entry in files:
            source = checked(root, entry["path"])
            archived = archived_path(root, journal, entry)
            source_info, archived_info = stamp(source), stamp(archived)
            expected = entry.get("stat")
            if archived_info is not None:
                if archived_info != expected:
                    raise MigrationError("retired cache metadata changed: " + str(source))
                if source_info is not None:
                    raise Busy("cache writer recreated a source during retirement: " + str(source))
                continue  # Resume an interrupted rename transaction.
            if source_info != expected or source_info is None:
                raise Busy("cache changed during migration; will retire its new generation: " + str(source))
            archived.parent.mkdir(parents=True, exist_ok=True)
            os.rename(source, archived)
            if stamp(archived) != expected:
                raise MigrationError("cache metadata changed while retiring: " + str(source))
            sync_directory(archived.parent)
            sync_directory(source.parent)
        # A writer unexpectedly created an unplanned pair: do not bless this
        # generation. Nothing is deleted and the journal remains recoverable.
        if candidates(root):
            raise Busy("new cache writer appeared during migration; generation remains pending")
        live = process_inventory(root)
        if live:
            raise Busy("cache retirement is recoverable but new clients defer schema commit: " +
                       ",".join(map(str, live)))
        atomic_json(checked(root, STATE_RELATIVE / "schema.json"), {"schema": SCHEMA})
        return {"state": "migrated", "retired_files": len(files)}
    finally:
        os.close(descriptor)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rootfs", required=True, type=Path)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--defer-if-running", action="store_true")
    args = parser.parse_args()
    try:
        if args.check:
            return 0 if current(args.rootfs.resolve(strict=True)) else 2
        print("METAL-CACHE " + json.dumps(migrate(args.rootfs), sort_keys=True))
        return 0
    except Busy as error:
        print("METAL-CACHE deferred: " + str(error), file=sys.stderr)
        return 0 if args.defer_if_running else 3
    except (MigrationError, OSError, ValueError, TypeError) as error:
        print("METAL-CACHE error: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

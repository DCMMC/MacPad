"""Restore existing arm64/arm64e CodeDirectories without re-signing code.

One bounded-memory reader replaces two ldid processes + shell pipelines per
image. Format reference: apple-oss-distributions/xnu/osfmk/kern/cs_blobs.h.
The kernel still validates code pages. A manifest is a hash extraction cache,
NEVER evidence of current-boot trust; membership is queried on every run.
Invoke with the iOS Python interpreter, not via a shebang or chroot.
"""
import argparse
import ctypes
import hashlib
import json
import os
import re
import sqlite3
import stat
import struct
import subprocess
import sys
import tempfile
import time

MAGICS = {b'\xce\xfa\xed\xfe', b'\xcf\xfa\xed\xfe',
          b'\xfe\xed\xfa\xce', b'\xfe\xed\xfa\xcf',
          b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca',
          b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca'}
JBCTL = '/var/jb/usr/bin/jbctl'
LDID = '/var/jb/usr/bin/ldid'
LIBJAILBREAK = '/var/jb/usr/lib/libjailbreak.dylib'
CACHE_SCHEMA = 1
MAX_CACHE_BYTES = 8 * 1024 * 1024


class InvalidMachO(ValueError):
    pass


def read_at(stream, offset, length, lower, upper):
    if offset < lower or length < 0 or offset + length > upper:
        raise InvalidMachO('out-of-range signature/load command')
    stream.seek(offset)
    data = stream.read(length)
    if len(data) != length:
        raise InvalidMachO('truncated file')
    return data


def directory_hash(stream, offset, available):
    header = read_at(stream, offset, 44, offset, offset + available)
    magic, length = struct.unpack_from('>II', header)
    if magic != 0xfade0c02 or length < 44 or length > available:
        raise InvalidMachO('invalid CodeDirectory')
    hash_size, hash_type = header[36:38]
    algorithms = {1: ('sha1', 20, 1), 2: ('sha256', 32, 3),
                  3: ('sha256', 20, 2), 4: ('sha384', 48, 4)}
    if hash_type not in algorithms or hash_size != algorithms[hash_type][1]:
        raise InvalidMachO('unsupported CodeDirectory hash type/size')
    hash_offset, ident_offset, special, slots = struct.unpack_from('>IIII', header, 16)
    if (ident_offset >= length or hash_offset > length or
            special * hash_size > hash_offset or
            hash_offset + slots * hash_size > length):
        raise InvalidMachO('invalid CodeDirectory hash table')
    digest = hashlib.new(algorithms[hash_type][0])
    # Some Electron code directories are large; never read/map the executable
    # or its signature into one allocation.
    for position in range(0, length, 65536):
        digest.update(read_at(stream, offset + position,
                              min(65536, length - position), offset, offset + length))
    return algorithms[hash_type][2], hash_type, digest.hexdigest()[:40]


def slice_hash(stream, base, size):
    top = base + size
    magic = read_at(stream, base, 4, base, top)
    if magic not in (b'\xcf\xfa\xed\xfe', b'\xfe\xed\xfa\xcf'):
        return None  # no 32-bit slice can execute in this arm64 macOS rootfs
    endian = '<' if magic == b'\xcf\xfa\xed\xfe' else '>'
    header = struct.unpack(endian + '8I', read_at(stream, base, 32, base, top))
    if header[1] != 0x0100000c:
        return None
    subtype = header[2] & 0x00ffffff
    if subtype not in (0, 1, 2):
        raise InvalidMachO('unsupported arm64 subtype')
    arch = 'arm64e' if subtype == 2 else 'arm64'
    count, command_bytes = header[4:6]
    if count > 65536 or command_bytes > 4 * 1024 * 1024 or base + 32 + command_bytes > top:
        raise InvalidMachO('invalid load command table')
    position, commands_end = base + 32, base + 32 + command_bytes
    signature = None
    for _ in range(count):
        command, length = struct.unpack(endian + 'II',
            read_at(stream, position, 8, base + 32, commands_end))
        if length < 8 or length % 4 or position + length > commands_end:
            raise InvalidMachO('invalid load command length')
        if command == 0x1d:  # LC_CODE_SIGNATURE, slice-relative file offset
            if length != 16 or signature is not None:
                raise InvalidMachO('invalid/duplicate LC_CODE_SIGNATURE')
            offset, sig_size = struct.unpack(endian + 'II',
                read_at(stream, position + 8, 8, base, commands_end))
            if offset < 32 + command_bytes or offset + sig_size > size:
                raise InvalidMachO('signature outside slice')
            signature = base + offset, sig_size
        position += length
    if position != commands_end:
        raise InvalidMachO('load command count/size mismatch')
    if signature is None:
        return None  # preserve old ldid -h behavior for unsigned optional code
    offset, sig_size = signature
    magic, length, count = struct.unpack('>III',
        read_at(stream, offset, 12, offset, offset + sig_size))
    if magic != 0xfade0cc0 or length > sig_size or count > 64 or length < 12 + count * 8:
        raise InvalidMachO('invalid embedded signature SuperBlob')
    best = None
    for index in range(count):
        slot, relative = struct.unpack('>II', read_at(stream,
            offset + 12 + index * 8, 8, offset, offset + length))
        if slot != 0 and not 0x1000 <= slot < 0x1005:
            continue
        if relative < 12 + count * 8 or relative >= length:
            raise InvalidMachO('CodeDirectory overlaps index/outside signature')
        candidate = directory_hash(stream, offset + relative, length - relative)
        if best is None or candidate[0] > best[0]:
            best = candidate
    if best is None:
        raise InvalidMachO('signature contains no CodeDirectory')
    return {'arch': arch, 'type': best[1], 'hash': best[2]}


def code_hashes(stream, size):
    magic = stream.read(4)
    if magic not in MAGICS:
        return []
    if magic in (b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca',
                 b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca'):
        endian = '>' if magic[:2] == b'\xca\xfe' else '<'
        wide = magic in (b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca')
        count, = struct.unpack(endian + 'I', read_at(stream, 4, 4, 0, size))
        entry_size = 32 if wide else 20
        if count > 64 or 8 + count * entry_size > size:
            raise InvalidMachO('invalid fat table')
        result = []
        for index in range(count):
            fields = struct.unpack(endian + ('IIQQII' if wide else 'IIIII'),
                read_at(stream, 8 + index * entry_size, entry_size, 0, size))
            cpu, subtype, offset, length = fields[:4]
            if offset < 8 + count * entry_size or offset + length > size:
                raise InvalidMachO('fat slice outside file')
            if cpu == 0x0100000c:
                record = slice_hash(stream, offset, length)
                if record:
                    expected = 'arm64e' if subtype & 0x00ffffff == 2 else 'arm64'
                    if record['arch'] != expected:
                        raise InvalidMachO('fat architecture/header mismatch')
                    result.append(record)
        return result
    record = slice_hash(stream, 0, size)
    return [record] if record else []


def identity(info):
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


class ResourceIndex:
    """Disk-backed negative cache; never keep 194,000 resource paths in RAM.

    Every file still gets a fresh stat (including ctime); changed resources
    becoming code are parsed normally. SQLite caches 2 MiB of pages. The
    separate positive manifest remains small and readable for trust auditing.
    """
    def __init__(self, path, readonly=False):
        self.connection = None
        self.pending = 0
        self.readonly = readonly
        if not path or (readonly and not os.path.exists(path)):
            return
        directory = os.path.dirname(os.path.abspath(path))
        if not readonly:
            os.makedirs(directory, mode=0o700, exist_ok=True)
        info = os.stat(directory)
        if info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise ValueError('resource index directory must be private to caller')
        if os.path.lexists(path):
            info = os.lstat(path)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
                raise ValueError('unsafe resource index')
        self.connection = sqlite3.connect(PathURI(path, readonly), uri=True)
        self.connection.execute('PRAGMA cache_size=-2048')
        self.connection.execute('PRAGMA mmap_size=0')
        if not readonly:
            os.chmod(path, 0o600)
            self.connection.execute('CREATE TABLE IF NOT EXISTS resource ('
                'path TEXT PRIMARY KEY, identity BLOB NOT NULL) WITHOUT ROWID')

    def unchanged(self, path, key):
        if not self.connection:
            return False
        row = self.connection.execute('SELECT identity FROM resource WHERE path=?', (path,)).fetchone()
        return row is not None and row[0] == struct.pack('>QQQqq', *key)

    def remember(self, path, key):
        if not self.connection or self.readonly:
            return
        self.connection.execute('INSERT OR REPLACE INTO resource VALUES (?,?)',
                                (path, struct.pack('>QQQqq', *key)))
        self.pending += 1
        if self.pending >= 10000:
            self.connection.commit()
            self.pending = 0

    def close(self):
        if self.connection:
            if not self.readonly:
                self.connection.commit()
            self.connection.close()


def PathURI(path, readonly):
    # Path.as_uri also escapes spaces, # and ?, unlike hand-built SQLite URIs.
    from pathlib import Path
    return Path(path).absolute().as_uri() + ('?mode=ro' if readonly else '?mode=rwc')


def load_cache(path):
    if not path:
        return {}
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'r') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or
                    info.st_mode & 0o022 or info.st_size > MAX_CACHE_BYTES):
                return {}
            value = json.load(stream)
        if value.get('schema') == CACHE_SCHEMA and isinstance(value.get('files'), dict):
            return value['files']
    except (OSError, ValueError, AttributeError):
        pass
    return {}


def save_cache(path, records):
    if not path:
        return
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, mode=0o700, exist_ok=True)
    info = os.stat(directory)
    if info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise ValueError('manifest directory must be owned by caller and not writable by others')
    payload = json.dumps({'schema': CACHE_SCHEMA, 'files': records}, separators=(',', ':'))
    if len(payload.encode()) > MAX_CACHE_BYTES:
        raise ValueError('hash manifest budget exceeded')
    fd, temporary = tempfile.mkstemp(prefix='.boot-trust-', dir=directory)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def files_in(paths):
    def fail(error):
        raise error

    for path in paths:
        if not os.path.exists(path):
            continue  # optional bundles, same contract as old shell loop
        if os.path.isdir(path):
            # os.walk does not follow directory symlinks. Regular-file aliases
            # inside bundles are skipped; explicit prerequisite paths resolve.
            for directory, _, names in os.walk(path, onerror=fail):
                for name in names:
                    item = os.path.join(directory, name)
                    info = os.lstat(item)
                    if stat.S_ISREG(info.st_mode):
                        yield item, info
        else:
            info = os.stat(path)
            if stat.S_ISREG(info.st_mode):
                yield path, info


def scan(paths, cache, verify_ldid=False, resources=None, progress=None):
    records, hashes, seen = {}, set(), set()
    scanned = cached = images = resource_hits = 0
    checkpoint = time.monotonic()
    for path, info in files_in(paths):
        if path in seen:
            continue
        scanned += 1
        if progress and scanned % 512 == 0 and time.monotonic() - checkpoint >= 10:
            progress(scanned, images)
            checkpoint = time.monotonic()
        key = identity(info)
        previous = cache.get(path)
        if isinstance(previous, dict) and previous.get('identity') == key:
            codes = previous.get('codes')
            if not (isinstance(codes, list) and codes and all(
                    isinstance(code, dict) and code.get('arch') in ('arm64', 'arm64e') and
                    code.get('type') in (1, 2, 3, 4) and
                    re.fullmatch('[0-9a-f]{40}', str(code.get('hash', ''))) for code in codes)):
                codes = None
            if codes is not None:
                cached += 1
        else:
            codes = None
        if codes is None:
            if resources and resources.unchanged(path, key):
                resource_hits += 1
                continue
            with open(path, 'rb') as stream:
                before = os.fstat(stream.fileno())
                try:
                    codes = code_hashes(stream, before.st_size)
                except InvalidMachO as error:
                    raise InvalidMachO(path + ': ' + str(error)) from error
                if identity(os.fstat(stream.fileno())) != key or identity(before) != key:
                    raise ValueError('file changed during signature read: ' + path)
        if not codes:
            if resources:
                resources.remember(path, key)
            continue
        seen.add(path)
        images += 1
        if verify_ldid:
            for code in codes:
                result = subprocess.run([LDID, '-arch', code['arch'], '-h', path],
                                        capture_output=True, text=True, timeout=30)
                match = re.search(r'^CDHash=([0-9a-fA-F]{40})$', result.stdout, re.M)
                if not match or match[1].lower() != code['hash']:
                    raise ValueError('ldid hash mismatch: ' + path + ' ' + code['arch'])
        records[path] = {'identity': key, 'codes': codes}
        hashes.update(code['hash'] for code in codes)
    return records, hashes, {'files': scanned, 'images': images, 'cached': cached,
                             'resource_hits': resource_hits}


def trusted_hashes():
    result = subprocess.run([JBCTL, 'trustcache', 'info'], capture_output=True,
                            text=True, timeout=30, check=True)
    if 'Trustcache' not in result.stdout:
        raise ValueError('unrecognized trustcache response')
    return {value.lower() for value in re.findall(r'\b[0-9a-fA-F]{40}\b', result.stdout)}


def restore(hashes, readd=False):
    present = trusted_hashes()
    missing = sorted(hashes if readd else hashes - present)
    if not missing:
        return 0, 'already-trusted'
    # RE-confirmed on the device libjailbreak: +0xaefc forwards x0(pointer)
    # and x1(size) to xpc_dictionary_set_data, returns the reply's int result.
    # Same API used by misc/loadtc. Never clear or replace the live trustcache.
    backend = 'libjailbreak'
    try:
        library = ctypes.CDLL(LIBJAILBREAK)
        add = library.jbclient_root_trustcache_add_cdhash
        add.argtypes = [ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t]
        add.restype = ctypes.c_int
    except (OSError, AttributeError):
        # Other jailbreak versions can keep the supported jbctl boundary.
        # Slower registration is acceptable; skipping registration is not.
        add = None
        backend = 'jbctl'
    for value in missing:
        if add is None:
            subprocess.run([JBCTL, 'trustcache', 'add', value],
                           capture_output=True, timeout=15, check=True)
            continue
        buffer = (ctypes.c_ubyte * 20).from_buffer_copy(bytes.fromhex(value))
        result = add(buffer, 20)
        if result != 0:
            raise ValueError('trustcache add failed: result=' + str(result))
    if hashes - trusted_hashes():
        raise ValueError('trustcache verification failed after restoration')
    return len(missing), backend


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest')
    parser.add_argument('--resource-index')
    parser.add_argument('--thermal-tool', help='production thermal admission/checkpoint helper')
    parser.add_argument('--dry-run', action='store_true', help='read-only, no manifest or trust writes')
    parser.add_argument('--verify-ldid', action='store_true', help='bounded validation run, compare every selected slice')
    parser.add_argument('--readd', action='store_true', help='replay real registration without clearing live trust')
    parser.add_argument('--hash', action='append', default=[], dest='extra_hashes')
    parser.add_argument('paths', nargs='+')
    args = parser.parse_args()
    began = time.monotonic()
    def progress(files, images):
        if args.thermal_tool:
            result = subprocess.run([args.thermal_tool], capture_output=True,
                                    text=True, timeout=5, check=True)
            if not re.search(r'(^|\s)thermal-state=nominal(\s|$)', result.stdout):
                raise ValueError('thermal pause, index checkpoint preserved: ' + result.stdout.strip())
        print(f'BOOT-TRUST progress files={files} images={images}', flush=True)
    if args.thermal_tool:
        progress(0, 0)
    resources = ResourceIndex(args.resource_index, readonly=args.dry_run)
    try:
        records, hashes, counts = scan(args.paths, load_cache(args.manifest),
                                      args.verify_ldid, resources, progress)
    finally:
        resources.close()
    for value in args.extra_hashes:
        if not re.fullmatch('[0-9a-fA-F]{40}', value):
            raise ValueError('invalid required hash')
        hashes.add(value.lower())
    scanned_at = time.monotonic()
    # Catch updater replacement between the metadata scan and trust admission.
    for path, record in records.items():
        if identity(os.stat(path)) != record['identity']:
            raise ValueError('file changed before trust admission: ' + path)
    added, backend = (0, 'dry-run') if args.dry_run else restore(hashes, args.readd)
    if not args.dry_run:
        save_cache(args.manifest, records)
    print('BOOT-TRUST ' + json.dumps(dict(counts, hashes=len(hashes), added=added,
        backend=backend, scan_seconds=round(scanned_at - began, 3),
        total_seconds=round(time.monotonic() - began, 3)), sort_keys=True), flush=True)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError) as error:
        print('BOOT-TRUST FAILED: ' + str(error), file=sys.stderr, flush=True)
        raise SystemExit(1)

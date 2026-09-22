"""Read-only, bounded on-disk dependency closure for first-party admission.

Only load commands are read; shared caches and framework resource trees are
never extracted/scanned. The returned files retain their original signatures.
Mach-O layouts: apple-oss-distributions/xnu EXTERNAL_HEADERS/mach-o/loader.h.
This is admission, not a substitute dyld: missing/cache-only imports remain
dyld's responsibility, and no search path or image is injected into the app.
"""
import os
import stat
import struct

import macws_boot_trust as trust

MAX_IMAGES = 512
MAX_DEPTH = 32
DYLIB_COMMANDS = {0x0c, 0x18, 0x1f, 0x20, 0x23}


def native_slices(stream, size):
    magic = trust.read_at(stream, 0, 4, 0, size)
    if magic in (b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca',
                 b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca'):
        endian = '>' if magic[:2] == b'\xca\xfe' else '<'
        wide = magic in (b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca')
        count, = struct.unpack(endian + 'I', trust.read_at(stream, 4, 4, 0, size))
        width = 32 if wide else 20
        if count > 64 or 8 + count * width > size:
            raise trust.InvalidMachO('invalid fat dependency table')
        for index in range(count):
            cpu, _, offset, length, *_ = struct.unpack(
                endian + ('IIQQII' if wide else 'IIIII'),
                trust.read_at(stream, 8 + index * width, width, 0, size))
            if offset < 8 + count * width or offset + length > size:
                raise trust.InvalidMachO('dependency slice outside file')
            if cpu == 0x0100000c:
                yield offset, length
    else:
        yield 0, size


def load_commands(stream, size):
    loads, rpaths = [], []
    for base, length in native_slices(stream, size):
        header = trust.read_at(stream, base, 32, base, base + length)
        if header[:4] not in (b'\xcf\xfa\xed\xfe', b'\xfe\xed\xfa\xcf'):
            continue
        endian = '<' if header[:4] == b'\xcf\xfa\xed\xfe' else '>'
        _, cpu, _, _, count, command_bytes, _, _ = struct.unpack(endian + '8I', header)
        if cpu != 0x0100000c:
            continue
        cursor, end = base + 32, base + 32 + command_bytes
        if count > 65536 or command_bytes > 4 * 1024 * 1024 or end > base + length:
            raise trust.InvalidMachO('invalid dependency load table')
        for _ in range(count):
            command, width = struct.unpack(endian + 'II',
                trust.read_at(stream, cursor, 8, base + 32, end))
            if width < 8 or width % 4 or cursor + width > end:
                raise trust.InvalidMachO('invalid dependency load command')
            command &= 0x7fffffff
            if command in DYLIB_COMMANDS or command == 0x1c:
                minimum = 12 if command == 0x1c else 24
                offset, = struct.unpack(endian + 'I',
                    trust.read_at(stream, cursor + 8, 4, cursor, cursor + width))
                if width < minimum or not minimum <= offset < width:
                    raise trust.InvalidMachO('invalid dependency name offset')
                data = trust.read_at(stream, cursor + offset, min(width - offset, 4096),
                                     cursor, cursor + width)
                if b'\0' not in data:
                    raise trust.InvalidMachO('unterminated/oversized dependency name')
                name = data.split(b'\0', 1)[0].decode('utf-8')
                destination = rpaths if command == 0x1c else loads
                if name not in destination:
                    destination.append(name)
            cursor += width
        if cursor != end:
            raise trust.InvalidMachO('dependency command count/size mismatch')
    return {'loads': loads, 'rpaths': rpaths}


def closure(executable, seeds, root, cache=None):
    """Follow explicit native imports and inherited rpaths, bounded by inode.

    Resolve aliases afresh even on metadata cache hits. Cached file identity
    includes ctime; a warm cache is never treated as current-boot trust.
    """
    root = os.path.realpath(root)
    executable_dir = os.path.dirname(os.path.realpath(executable))
    cached, metadata, ordered, visited = cache or {}, {}, [], set()

    def contained(path):
        canonical = os.path.realpath(path)
        if not canonical.startswith(root + '/'):
            raise ValueError('dependency escapes rootfs: ' + path)
        return canonical

    def expand(name, loader):
        if name.startswith('@loader_path/'):
            return os.path.join(loader, name[13:])
        if name.startswith('@executable_path/'):
            return os.path.join(executable_dir, name[17:])
        if name.startswith('/'):
            return root + name
        return None

    def visit(path, inherited, depth):
        path = contained(path)
        info = os.stat(path)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('dependency is not a regular file: ' + path)
        inode = (info.st_dev, info.st_ino)
        if inode in visited:
            return
        if depth > MAX_DEPTH or len(visited) >= MAX_IMAGES:
            raise ValueError('dependency graph budget exceeded')
        visited.add(inode)
        ordered.append(path)
        identity = trust.identity(info)
        previous = cached.get(path)
        if (isinstance(previous, dict) and previous.get('identity') == identity and
                all(isinstance(previous.get(key), list) and
                    len(previous[key]) <= 65536 and
                    all(isinstance(value, str) and len(value) < 4096 for value in previous[key])
                    for key in ('loads', 'rpaths'))):
            commands = previous
        else:
            with open(path, 'rb') as stream:
                commands = load_commands(stream, info.st_size)
                if trust.identity(os.fstat(stream.fileno())) != identity:
                    raise ValueError('dependency changed during metadata read')
        metadata[path] = dict(commands, identity=identity)
        loader = os.path.dirname(path)
        rpaths = [expand(value, loader) for value in commands['rpaths']]
        rpaths = list(dict.fromkeys(value for value in rpaths + inherited if value))
        for name in commands['loads']:
            candidates = ([os.path.join(value, name[7:]) for value in rpaths]
                          if name.startswith('@rpath/') else [expand(name, loader)])
            for candidate in candidates:
                if not candidate:
                    continue
                candidate = contained(candidate)
                if os.path.isfile(candidate):
                    visit(candidate, rpaths, depth + 1)
                    break

    visit(executable, [], 0)
    main = metadata[os.path.realpath(executable)]
    inherited = [expand(value, executable_dir) for value in main['rpaths']]
    for seed in seeds:
        visit(seed, [value for value in inherited if value], 0)
    return ordered, metadata

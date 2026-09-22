"""Read-only bounded inventory; never descend into an app's resource tree."""
import argparse
import json
import os
import plistlib
import subprocess

ROOTS = ('/Applications', '/System/Applications',
         '/System/Library/CoreServices/Applications')


def inventory(root):
    canonical = os.path.realpath(root)
    processes = subprocess.run(['/bin/ps', '-ax', '-o', 'pid=,comm='],
                               capture_output=True, text=True, check=True).stdout
    running = {}
    for line in processes.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2:
            running.setdefault(parts[1], []).append(int(parts[0]))
    found = {}

    def visit(directory, depth):
        if depth > 3 or not os.path.isdir(directory):
            return
        for entry in sorted(os.scandir(directory), key=lambda item: item.name):
            if not entry.is_dir():
                continue
            if not entry.name.endswith('.app'):
                if not entry.name.startswith('.') and not entry.is_symlink():
                    visit(entry.path, depth + 1)
                continue
            bundle = os.path.realpath(entry.path)
            if bundle in found or not bundle.startswith(canonical + '/'):
                continue
            record = {'name': entry.name[:-4], 'path': bundle[len(canonical):],
                      'alias': entry.path[len(root):]}
            try:
                with open(bundle + '/Contents/Info.plist', 'rb') as stream:
                    info = plistlib.load(stream)
                executable = info.get('CFBundleExecutable', '')
                path = record['path'] + '/Contents/MacOS/' + executable
                record.update(identifier=info.get('CFBundleIdentifier'),
                              executable=path, executable_exists=os.path.isfile(root + path),
                              ui_agent=bool(info.get('LSUIElement', False)),
                              background_only=bool(info.get('LSBackgroundOnly', False)),
                              running=running.get(path, []) + running.get(canonical + path, []))
            except (OSError, ValueError) as error:
                record['error'] = str(error)
            found[bundle] = record

    for folder in ROOTS:
        visit(root + folder, 0)
    return sorted(found.values(), key=lambda item: item['name'].casefold())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='/var/mnt/rootfs')
    args = parser.parse_args()
    print(json.dumps(inventory(args.root), ensure_ascii=False, indent=2))

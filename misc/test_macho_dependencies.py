import importlib.util
import io
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_boot_trust import ROOT, fat, trust

sys.modules['macws_boot_trust'] = trust
SPEC = importlib.util.spec_from_file_location('macws_macho_dependencies',
    ROOT / 'layout/usr/macOS/bin/macws_macho_dependencies.py')
deps = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = deps
SPEC.loader.exec_module(deps)


def image(loads=(), rpaths=(), cpu=0x0100000c):
    commands = b''
    for command, names, offset in ((0xc, loads, 24), (0x8000001c, rpaths, 12)):
        for name in names:
            name = name.encode() + b'\0'
            width = (offset + len(name) + 7) & ~7
            commands += (struct.pack('<III', command, width, offset) +
                         bytes(offset - 12) + name + bytes(width - offset - len(name)))
    return struct.pack('<8I', 0xfeedfacf, cpu, 0, 2, len(loads) + len(rpaths),
                       len(commands), 0, 0) + commands


class DependencyTests(unittest.TestCase):
    def parse(self, data):
        return deps.load_commands(io.BytesIO(data), len(data))

    def test_native_only_and_fat_tables(self):
        data = image(['/A', '@rpath/B'], ['@loader_path/../Frameworks'])
        for wide in (False, True):
            for endian in ('>', '<'):
                result = self.parse(fat([(0, data)], wide, endian))
                self.assertEqual(result['loads'], ['/A', '@rpath/B'])
        self.assertEqual(self.parse(image(['/x86'], cpu=0x01000007))['loads'], [])

    def test_does_not_treat_install_name_as_dependency(self):
        data = bytearray(image(['/self']))
        struct.pack_into('<I', data, 32, 0xd)
        self.assertEqual(self.parse(data)['loads'], [])

    def test_invalid_commands_and_unterminated_names(self):
        for offset, value in ((16, 99999), (20, 0xffffffff), (36, 4), (40, 8)):
            data = bytearray(image(['/A']))
            struct.pack_into('<I', data, offset, value)
            with self.assertRaises(trust.InvalidMachO):
                self.parse(data)
        data = bytearray(image(['/A']))
        data[56:] = b'x' * len(data[56:])
        with self.assertRaises(trust.InvalidMachO):
            self.parse(data)

    def test_absolute_relative_rpath_inheritance_and_cycles(self):
        with tempfile.TemporaryDirectory() as root:
            root = os.path.realpath(root)
            main = Path(root, 'Main')
            main.write_bytes(image(['/A', '@rpath/B', '/cache-only'], ['/Libraries']))
            Path(root, 'A').write_bytes(image(['@loader_path/Main']))
            Path(root, 'Libraries').mkdir()
            Path(root, 'Libraries/B').write_bytes(image(['@rpath/C']))
            Path(root, 'Libraries/C').write_bytes(image(['@executable_path/Main']))
            paths, metadata = deps.closure(str(main), [], root)
            self.assertEqual(set(paths), {str(main), root + '/A',
                                        root + '/Libraries/B', root + '/Libraries/C'})
            with patch.object(deps, 'load_commands', side_effect=AssertionError('cache miss')):
                self.assertEqual(deps.closure(str(main), [], root, metadata)[0], paths)

    def test_changed_image_and_alias_are_not_hidden_by_cache(self):
        with tempfile.TemporaryDirectory() as root:
            root = os.path.realpath(root)
            main = Path(root, 'Main')
            main.write_bytes(image(['/Alias']))
            Path(root, 'A').write_bytes(image())
            Path(root, 'B').write_bytes(image())
            alias = Path(root, 'Alias')
            alias.symlink_to('A')
            _, metadata = deps.closure(str(main), [], root)
            alias.unlink()
            alias.symlink_to('B')
            paths, _ = deps.closure(str(main), [], root, metadata)
            self.assertIn(root + '/B', paths)
            self.assertNotIn(root + '/A', paths)
            main.write_bytes(image(['/A']))
            paths, _ = deps.closure(str(main), [], root, metadata)
            self.assertIn(root + '/A', paths)

    def test_escaping_alias_and_graph_budget_fail_closed(self):
        with tempfile.TemporaryDirectory() as root:
            main = Path(root, 'Main')
            main.write_bytes(image(['/A']))
            alias = Path(root, 'A')
            alias.symlink_to('/usr/lib')
            with self.assertRaisesRegex(ValueError, 'escapes rootfs'):
                deps.closure(str(main), [], root)
            alias.unlink()
            alias.write_bytes(image())
            with patch.object(deps, 'MAX_IMAGES', 1), \
                    self.assertRaisesRegex(ValueError, 'budget exceeded'):
                deps.closure(str(main), [], root)

    def test_read_size_stays_bounded(self):
        class Bounded(io.BytesIO):
            def read(self, size=-1):
                self.assert_size(size)
                return super().read(size)

            def assert_size(self, size):
                assert 0 <= size <= 4096

        data = image(['/A'])
        self.assertEqual(deps.load_commands(Bounded(data), len(data))['loads'], ['/A'])


if __name__ == '__main__':
    unittest.main()

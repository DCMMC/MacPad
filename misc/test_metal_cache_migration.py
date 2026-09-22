"""Executable filesystem tests for recoverable, flag-free cache migration."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("migration", ROOT / "layout/usr/macOS/bin/macws_metal_cache_migration.py")
migration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migration)


class MetalCacheMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="macws-cache-test-")
        self.root = Path(self.temporary.name).resolve()

    def tearDown(self):
        self.temporary.cleanup()

    def cache(self, client="com.apple.finder", names=migration.NAMES):
        base = self.root / migration.CACHE_RELATIVE / client / "com.apple.metal/31001"
        base.mkdir(parents=True, exist_ok=True)
        for name in names:
            (base / name).write_bytes(("derived-" + name).encode())
        return base

    def migrate(self):
        return migration.migrate(self.root, process_inventory=lambda root: [])

    def test_fresh_root_missing_revision_needs_no_optin(self):
        self.assertFalse(migration.current(self.root))
        self.assertEqual(self.migrate(), {"state": "migrated", "retired_files": 0})
        self.assertTrue(migration.current(self.root))

    def test_pair_preserves_bytes_inode_permissions_and_warm_cache(self):
        cache = self.cache()
        original = {name: ((cache / name).read_bytes(), migration.stamp(cache / name)) for name in migration.NAMES}
        self.assertEqual(self.migrate()["retired_files"], 2)
        for name in migration.NAMES:
            archived = self.root / migration.STATE_RELATIVE / "retired" / migration.SCHEMA / (cache / name).relative_to(self.root)
            self.assertEqual((archived.read_bytes(), migration.stamp(archived)), original[name])
            self.assertFalse((cache / name).exists())
        (cache / "libraries.data").write_bytes(b"new-valid-compiled-data")
        self.assertEqual(self.migrate(), {"state": "current", "retired_files": 0})
        self.assertEqual((cache / "libraries.data").read_bytes(), b"new-valid-compiled-data")

    def test_single_missing_pair_member_and_unrelated_data(self):
        cache = self.cache(names=("libraries.data",))
        (cache / "functions.data").write_bytes(b"unrelated")
        self.assertEqual(self.migrate()["retired_files"], 1)
        self.assertEqual((cache / "functions.data").read_bytes(), b"unrelated")

    def test_generic_and_windowserver_client_paths(self):
        self.cache("")
        self.cache("WindowServer/com.apple.WindowServer")
        self.assertEqual(self.migrate()["retired_files"], 4)

    def test_live_clients_defer_without_even_creating_metadata(self):
        cache = self.cache()
        with self.assertRaises(migration.Busy):
            migration.migrate(self.root, process_inventory=lambda root: [321])
        self.assertTrue((cache / "libraries.data").exists())
        self.assertFalse((self.root / migration.STATE_RELATIVE).exists())

    def test_completed_revision_needs_no_repeated_process_scan(self):
        self.migrate()
        with mock.patch.object(migration, "live_chroot_processes", side_effect=AssertionError("scan")):
            result = migration.migrate(self.root, process_inventory=lambda root: [321])
        self.assertEqual(result["state"], "current")

    def test_v2_upgrade_retires_new_pair_and_preserves_previous_archive(self):
        old_schema = "macws-macabi-dag-v2"
        cache = self.cache("com.microsoft.Word")
        with mock.patch.object(migration, "SCHEMA", old_schema):
            self.assertEqual(self.migrate()["retired_files"], 2)
        old_archive = self.root / migration.STATE_RELATIVE / "retired" / old_schema
        old_files = {path: (path.read_bytes(), migration.stamp(path))
                     for path in old_archive.rglob("libraries.*")}
        old_journal = self.root / migration.STATE_RELATIVE / (old_schema + ".json")
        old_journal_bytes = old_journal.read_bytes()
        self.cache("com.microsoft.Word")
        for name in migration.NAMES:
            (cache / name).write_bytes(b"v2-coreui-wrong-target-" + name.encode())
        pending = {name: ((cache / name).read_bytes(), migration.stamp(cache / name))
                   for name in migration.NAMES}
        self.assertFalse(migration.current(self.root))
        self.assertEqual(self.migrate(), {"state": "migrated", "retired_files": 2})
        self.assertEqual(migration.read_json(self.root / migration.STATE_RELATIVE / "schema.json"),
                         {"schema": "macws-macabi-image-filter-v3"})
        for name in migration.NAMES:
            archived = (self.root / migration.STATE_RELATIVE / "retired" / migration.SCHEMA /
                        (cache / name).relative_to(self.root))
            self.assertEqual((archived.read_bytes(), migration.stamp(archived)), pending[name])
            self.assertFalse((cache / name).exists())
        self.assertEqual(old_journal.read_bytes(), old_journal_bytes)
        for path, original in old_files.items():
            self.assertEqual((path.read_bytes(), migration.stamp(path)), original)

    def test_v3_warm_pair_is_unchanged_even_with_live_clients(self):
        self.migrate()
        cache = self.cache("com.microsoft.Word")
        before = {path: (path.read_bytes(), migration.stamp(path)) for path in cache.iterdir()}
        inventory = mock.Mock(side_effect=AssertionError("warm cache must not scan/retire"))
        self.assertEqual(migration.migrate(self.root, process_inventory=inventory),
                         {"state": "current", "retired_files": 0})
        inventory.assert_not_called()
        for path, original in before.items():
            self.assertEqual((path.read_bytes(), migration.stamp(path)), original)

    def test_live_word_defers_v2_upgrade_without_updating_old_record(self):
        old_schema = "macws-macabi-dag-v2"
        self.cache("com.microsoft.Word")
        with mock.patch.object(migration, "SCHEMA", old_schema):
            self.migrate()
        self.cache("com.microsoft.Word")
        # Include the committed v2 schema, old journal/archive, and current
        # mmap-able pair: not even metadata is changed while a client lives.
        before = {path: (path.read_bytes(), migration.stamp(path))
                  for path in self.root.rglob("*") if path.is_file()}
        with self.assertRaises(migration.Busy):
            migration.migrate(self.root, process_inventory=lambda root: [321])
        self.assertFalse(migration.current(self.root))
        after_paths = {path for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(after_paths, set(before))
        for path, original in before.items():
            self.assertEqual((path.read_bytes(), migration.stamp(path)), original)
        self.assertEqual(migration.read_json(self.root / migration.STATE_RELATIVE / "schema.json"),
                         {"schema": old_schema})

    def test_partial_rename_failure_is_resumable_without_false_completion(self):
        self.cache()
        original = migration.os.rename
        calls = []
        def fail_second(source, target):
            calls.append(source)
            if len(calls) == 2:
                raise OSError("injected storage failure")
            return original(source, target)
        with mock.patch.object(migration.os, "rename", side_effect=fail_second):
            with self.assertRaises(OSError):
                self.migrate()
        self.assertFalse(migration.current(self.root))
        self.assertEqual(self.migrate()["retired_files"], 2)
        self.assertTrue(migration.current(self.root))

    def test_schema_commit_failure_does_not_destroy_recoverable_archive(self):
        self.cache()
        original = migration.atomic_json
        def fail_schema(path, value):
            if path.name == "schema.json":
                raise OSError("injected commit failure")
            return original(path, value)
        with mock.patch.object(migration, "atomic_json", side_effect=fail_schema):
            with self.assertRaises(OSError):
                self.migrate()
        self.assertFalse(migration.current(self.root))
        self.assertEqual(self.migrate()["retired_files"], 2)

    def test_symlink_and_path_escape_fail_closed(self):
        cache = self.cache()
        (cache / "libraries.data").unlink()
        outside = self.root / "unrelated-document"
        outside.write_bytes(b"do-not-touch")
        (cache / "libraries.data").symlink_to(outside)
        with self.assertRaises(migration.MigrationError):
            self.migrate()
        self.assertEqual(outside.read_bytes(), b"do-not-touch")
        self.assertFalse(migration.current(self.root))
        self.assertFalse(migration.valid_cache_relative(str(migration.CACHE_RELATIVE / "../secret/com.apple.metal/31001/libraries.data")))
        self.assertFalse(migration.valid_cache_relative("/etc/passwd"))

    def test_symlink_client_and_state_directories_rejected(self):
        base = self.root / migration.CACHE_RELATIVE
        base.mkdir(parents=True)
        (base / "com.apple.finder").symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(migration.MigrationError):
            self.migrate()
        (base / "com.apple.finder").unlink()
        state = self.root / migration.STATE_RELATIVE
        state.parent.mkdir(parents=True, exist_ok=True)
        if state.exists():
            # The aborted transaction may have created its journal directory.
            for child in state.iterdir(): child.unlink()
            state.rmdir()
        state.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(migration.MigrationError):
            self.migrate()

    def test_malformed_or_traversing_journal_never_marks_complete(self):
        state = self.root / migration.STATE_RELATIVE
        state.mkdir(parents=True)
        journal = state / (migration.SCHEMA + ".json")
        for value in ({"schema": migration.SCHEMA, "files": ["bad"]},
                      {"schema": migration.SCHEMA, "files": [{"path": "../document", "stat": {}}]}):
            journal.write_text(json.dumps(value))
            with self.assertRaises(migration.MigrationError):
                self.migrate()
            self.assertFalse(migration.current(self.root))

    def test_changed_source_during_resume_is_not_overwritten(self):
        cache = self.cache()
        with mock.patch.object(migration.os, "rename", side_effect=OSError("before rename")):
            with self.assertRaises(OSError): self.migrate()
        (cache / "libraries.data").write_bytes(b"unexpected-new-generation")
        self.assertEqual(self.migrate()["retired_files"], 2)
        journal = migration.read_json(self.root / migration.STATE_RELATIVE / (migration.SCHEMA + ".json"))
        self.assertEqual(journal["generation"], 1)
        data = next(entry for entry in journal["files"] if entry["path"].endswith("libraries.data"))
        self.assertEqual(migration.archived_path(self.root, journal, data).read_bytes(), b"unexpected-new-generation")

    def test_recreated_pair_keeps_every_archived_generation(self):
        cache = self.cache()
        originals = {name: (cache / name).read_bytes() for name in migration.NAMES}
        actual_json = migration.atomic_json
        def fail_commit(path, value):
            if path.name == "schema.json": raise OSError("power loss before epoch")
            return actual_json(path, value)
        with mock.patch.object(migration, "atomic_json", side_effect=fail_commit):
            with self.assertRaises(OSError): self.migrate()
        old_journal = migration.read_json(self.root / migration.STATE_RELATIVE / (migration.SCHEMA + ".json"))
        for name in migration.NAMES: (cache / name).write_bytes(b"second-" + name.encode())
        with mock.patch.object(migration, "atomic_json", side_effect=fail_commit):
            with self.assertRaises(OSError): self.migrate()
        second_journal = migration.read_json(self.root / migration.STATE_RELATIVE / (migration.SCHEMA + ".json"))
        for name in migration.NAMES: (cache / name).write_bytes(b"third-" + name.encode())
        self.assertEqual(self.migrate()["retired_files"], 2)
        latest = migration.read_json(self.root / migration.STATE_RELATIVE / (migration.SCHEMA + ".json"))
        self.assertEqual(latest["generation"], 2)
        for journal, prefix in ((old_journal, None), (second_journal, b"second-"), (latest, b"third-")):
            for entry in journal["files"]:
                name = Path(entry["path"]).name
                expected = originals[name] if prefix is None else prefix + name.encode()
                self.assertEqual(migration.archived_path(self.root, journal, entry).read_bytes(), expected)
                self.assertEqual(migration.stamp(migration.archived_path(self.root, journal, entry)), entry["stat"])
        self.assertTrue(migration.current(self.root))

    def test_failure_after_history_commit_resumes_same_new_generation(self):
        cache = self.cache()
        actual_json = migration.atomic_json
        def fail_schema(path, value):
            if path.name == "schema.json": raise OSError("stop before epoch")
            return actual_json(path, value)
        with mock.patch.object(migration, "atomic_json", side_effect=fail_schema):
            with self.assertRaises(OSError): self.migrate()
        self.cache()
        def fail_new_plan(path, value):
            if value.get("generation") == 1: raise OSError("stop after history")
            return actual_json(path, value)
        with mock.patch.object(migration, "atomic_json", side_effect=fail_new_plan):
            with self.assertRaises(OSError): self.migrate()
        self.assertFalse(migration.current(self.root))
        self.assertTrue((cache / "libraries.data").exists())
        self.assertEqual(self.migrate()["retired_files"], 2)

    def test_writer_after_only_first_pair_member_was_retired(self):
        cache = self.cache()
        old_list = (cache / "libraries.list").read_bytes()
        old_data = (cache / "libraries.data").read_bytes()
        rename = migration.os.rename
        def fail_data(source, target):
            if source.name == "libraries.data": raise OSError("interrupted between pair")
            return rename(source, target)
        with mock.patch.object(migration.os, "rename", side_effect=fail_data):
            with self.assertRaises(OSError): self.migrate()
        prior = migration.read_json(self.root / migration.STATE_RELATIVE / (migration.SCHEMA + ".json"))
        (cache / "libraries.list").write_bytes(b"new-generation-index")
        self.assertEqual(self.migrate()["retired_files"], 2)
        latest = migration.read_json(self.root / migration.STATE_RELATIVE / (migration.SCHEMA + ".json"))
        original_list = next(entry for entry in prior["files"] if entry["path"].endswith("libraries.list"))
        self.assertEqual(migration.archived_path(self.root, prior, original_list).read_bytes(), old_list)
        for entry in latest["files"]:
            expected = old_data if entry["path"].endswith("libraries.data") else b"new-generation-index"
            self.assertEqual(migration.archived_path(self.root, latest, entry).read_bytes(), expected)

    def test_changed_archive_still_fails_closed_without_overwrite(self):
        self.cache()
        actual_json = migration.atomic_json
        def fail_schema(path, value):
            if path.name == "schema.json": raise OSError("stop before epoch")
            return actual_json(path, value)
        with mock.patch.object(migration, "atomic_json", side_effect=fail_schema):
            with self.assertRaises(OSError): self.migrate()
        journal = migration.read_json(self.root / migration.STATE_RELATIVE / (migration.SCHEMA + ".json"))
        archive = migration.archived_path(self.root, journal, journal["files"][0])
        archive.write_bytes(b"changed archive, not trusted")
        self.cache()
        with self.assertRaises(migration.MigrationError): self.migrate()
        self.assertEqual(archive.read_bytes(), b"changed archive, not trusted")
        self.assertFalse(migration.current(self.root))

    def test_client_appearing_after_lock_prevents_first_rename(self):
        cache = self.cache()
        inventory = mock.Mock(side_effect=[[], [321]])
        with self.assertRaises(migration.Busy):
            migration.migrate(self.root, process_inventory=inventory)
        self.assertTrue((cache / "libraries.data").exists())
        self.assertFalse(migration.current(self.root))

    def test_client_before_commit_keeps_recoverable_journal_without_epoch(self):
        cache = self.cache()
        inventory = mock.Mock(side_effect=[[], [], [321]])
        with self.assertRaises(migration.Busy):
            migration.migrate(self.root, process_inventory=inventory)
        self.assertFalse((cache / "libraries.data").exists())
        self.assertFalse(migration.current(self.root))
        self.assertEqual(self.migrate()["retired_files"], 2)

    def test_rename_is_durable_before_schema_commit(self):
        cache = self.cache()
        events = []
        original_sync, original_json = migration.sync_directory, migration.atomic_json
        def record_sync(path):
            events.append(("sync", path))
            return original_sync(path)
        def record_json(path, value):
            events.append(("json", path))
            return original_json(path, value)
        with mock.patch.object(migration, "sync_directory", side_effect=record_sync), \
                mock.patch.object(migration, "atomic_json", side_effect=record_json):
            self.migrate()
        commit = next(i for i, event in enumerate(events) if event[0] == "json" and event[1].name == "schema.json")
        self.assertEqual(sum(event == ("sync", cache) for event in events[:commit]), 2)
        self.assertEqual(sum(event[0] == "sync" and "retired" in event[1].parts for event in events[:commit]), 2)


if __name__ == "__main__":
    unittest.main()

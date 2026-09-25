import io
import multiprocessing
import os
import sqlite3
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from catalog import CatalogError, CatalogStore


def abandon_ingest(root, now):
    class ExitReader:
        def read(self, size=-1):
            os._exit(0)

    CatalogStore(Path(root), clock=lambda: now).ingest(
        ExitReader(), 'abandoned.bin', lambda path, name: {'success': True}
    )


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.now = 1_800_000_000.0
        self.root = Path(self.directory.name)
        self.store = CatalogStore(self.root, clock=lambda: self.now)
        self.store.initialize()

    def ingest(self, content=b'log', filename='flight.bin'):
        return self.store.ingest(
            io.BytesIO(content), filename, lambda path, name: {'success': True}
        )[1]

    def test_saved_original_survives_expiry_and_restart(self):
        analysis, temporary = self.store.ingest(
            io.BytesIO(b'original bytes'), '../../flight.bin',
            lambda path, name: {'success': True, 'filename': name},
        )
        self.assertEqual(analysis['filename'], '../../flight.bin')
        saved = self.store.save(temporary['id'], {
            'display_name': 'Hover test', 'folder_id': 1,
            'tags': [' Hover ', 'hover'], 'notes': 'Check landing',
        })
        self.assertEqual(saved['tags'], ['hover'])
        self.now += 7200
        restarted = CatalogStore(self.root, clock=lambda: self.now)
        restarted.initialize()
        restarted.cleanup()
        self.assertEqual(restarted.get(saved['id'])['notes'], 'Check landing')
        with restarted.use_file(saved['id']) as (path, metadata):
            self.assertEqual(path.read_bytes(), b'original bytes')
            self.assertEqual(metadata['id'], saved['id'])

    def test_save_at_deadline_is_rejected_before_sweep(self):
        _, temporary = self.store.ingest(
            io.BytesIO(b'log'), 'flight.bin',
            lambda path, name: {'success': True},
        )
        self.now += 3600
        with self.assertRaises(CatalogError) as error:
            self.store.save(temporary['id'], {'display_name': 'Too late'})
        self.assertEqual(error.exception.status_code, 410)

    def test_repeated_save_returns_original_metadata_unchanged(self):
        temporary = self.ingest()
        saved = self.store.save(temporary['id'], {
            'display_name': 'First', 'tags': ['One'], 'notes': 'original',
        })

        repeated = self.store.save(temporary['id'], {
            'display_name': '', 'folder_id': False, 'tags': 'invalid',
            'notes': 42,
        })

        self.assertEqual(repeated, saved)

    def test_folder_lifecycle_enforces_parentage_uniqueness_and_root_rules(self):
        child = self.store.create_folder(' Aircraft ')
        lowercase = self.store.create_folder('aircraft')
        nested = self.store.create_folder('Tests', child['id'])
        renamed = self.store.rename_folder(nested['id'], ' Flights ')
        self.assertEqual(renamed['name'], 'Flights')
        with self.assertRaises(CatalogError) as duplicate:
            self.store.create_folder('Aircraft')
        self.assertEqual(duplicate.exception.status_code, 409)
        self.assertEqual(
            [(folder['id'], folder['parent_id'], folder['name'])
             for folder in self.store.list_folders()],
            [(1, None, 'Logs'), (child['id'], 1, 'Aircraft'),
             (lowercase['id'], 1, 'aircraft'),
             (nested['id'], child['id'], 'Flights')],
        )
        self.assertEqual(
            self.store.list_folders()[0]['created_at'], '2027-01-15T08:00:00Z'
        )

        with self.assertRaises(CatalogError) as root_error:
            self.store.rename_folder(1, 'Changed')
        self.assertEqual(root_error.exception.status_code, 409)
        with self.assertRaises(CatalogError) as root_delete:
            self.store.delete_folder(1)
        self.assertEqual(root_delete.exception.status_code, 409)
        with self.assertRaises(CatalogError) as nonempty_error:
            self.store.delete_folder(child['id'])
        self.assertEqual(nonempty_error.exception.status_code, 409)
        self.store.delete_folder(nested['id'])
        self.store.delete_folder(child['id'])
        self.store.delete_folder(lowercase['id'])
        self.assertEqual([folder['name'] for folder in self.store.list_folders()], ['Logs'])

    def test_edit_and_list_filters_use_public_values_and_and_semantics(self):
        folder = self.store.create_folder('Flights')
        first = self.store.save(self.ingest()['id'], {
            'display_name': 'Hover 100%_A', 'tags': [' TEST ', 'test'],
        })
        self.now += 1
        second = self.store.save(self.ingest()['id'], {
            'display_name': 'Hover ordinary', 'folder_id': folder['id'],
            'tags': ['test', 'night'], 'notes': 'before',
        })

        edited = self.store.edit(second['id'], {
            'folder_id': 1, 'notes': 'after', 'tags': [' Night ', 'NIGHT'],
        })

        self.assertEqual(edited['display_name'], 'Hover ordinary')
        self.assertEqual(edited['notes'], 'after')
        self.assertEqual(edited['tags'], ['night'])
        self.assertEqual(
            [item['id'] for item in self.store.list_logs(folder_id=1)],
            [second['id'], first['id']],
        )
        self.assertEqual(
            [item['id'] for item in self.store.list_logs(name='%_')],
            [first['id']],
        )
        self.assertEqual(
            [item['id'] for item in self.store.list_logs(name='Hover', tag=' NIGHT ')],
            [second['id']],
        )
        self.assertEqual(self.store.list_tags(), ['night', 'test'])
        self.assertEqual(
            set(self.store.get(first['id'])),
            {'id', 'status', 'original_filename', 'size_bytes', 'created_at',
             'expires_at', 'saved_at', 'updated_at', 'display_name',
             'folder_id', 'tags', 'notes'},
        )
        self.store.delete_folder(folder['id'])

    def test_use_file_prevents_delete_until_context_closes(self):
        saved = self.store.save(self.ingest(content=b'locked')['id'], {
            'display_name': 'Locked',
        })

        with self.store.use_file(saved['id']) as (path, metadata):
            with self.assertRaises(CatalogError) as error:
                self.store.delete(saved['id'])
            self.assertEqual(error.exception.status_code, 409)
            self.assertEqual(path.read_bytes(), b'locked')
            self.assertEqual(metadata['id'], saved['id'])

        self.store.delete(saved['id'])
        self.assertFalse(path.exists())
        with self.assertRaises(CatalogError) as missing:
            self.store.get(saved['id'])
        self.assertEqual(missing.exception.status_code, 404)

    def test_ingest_failures_remove_database_rows_and_files(self):
        class BrokenSource(io.BytesIO):
            def read(self, size=-1):
                raise OSError('read failed')

        with self.assertRaises(OSError):
            self.store.ingest(
                BrokenSource(b'ignored'), 'read.bin',
                lambda path, name: {'success': True},
            )
        with self.assertRaises(RuntimeError):
            self.store.ingest(
                io.BytesIO(b'copied'), 'analyze.bin',
                lambda path, name: (_ for _ in ()).throw(RuntimeError('bad log')),
            )

        self.assertEqual(list((self.root / 'files').iterdir()), [])
        self.assertEqual(self.store.cleanup(), {'removed': 0, 'skipped': 0, 'failed': 0})

    def test_ingest_unlink_failure_is_logged_and_retried(self):
        with mock.patch.object(Path, 'unlink', side_effect=OSError('injected')):
            with self.assertLogs('catalog', level='ERROR'):
                with self.assertRaises(RuntimeError):
                    self.store.ingest(
                        io.BytesIO(b'copied'), 'retry.bin',
                        lambda path, name: (_ for _ in ()).throw(RuntimeError('bad log')),
                    )

        self.assertEqual(
            self.store.cleanup(), {'removed': 1, 'skipped': 0, 'failed': 0}
        )

    def test_same_original_filename_uses_distinct_private_files(self):
        first = self.store.save(self.ingest(b'first', 'same.bin')['id'], {
            'display_name': 'First',
        })
        second = self.store.save(self.ingest(b'second', 'same.bin')['id'], {
            'display_name': 'Second',
        })

        with self.store.use_file(first['id']) as (first_path, _):
            with self.store.use_file(second['id']) as (second_path, _):
                self.assertNotEqual(first_path, second_path)
                self.assertEqual(first_path.read_bytes(), b'first')
                self.assertEqual(second_path.read_bytes(), b'second')

    def test_invalid_save_preserves_original_deadline(self):
        temporary = self.ingest()
        invalid_values = [
            {'display_name': 3},
            {'display_name': 'x', 'folder_id': True},
            {'display_name': 'x', 'tags': 'tag'},
            {'display_name': 'x', 'notes': 3},
            {'display_name': 'bad/name'},
            {'display_name': 'x', 'tags': ['bad\x00tag']},
        ]
        for metadata in invalid_values:
            with self.subTest(metadata=metadata):
                with self.assertRaises(CatalogError) as error:
                    self.store.save(temporary['id'], metadata)
                self.assertEqual(error.exception.status_code, 400)

        self.now += 3600
        with self.assertRaises(CatalogError) as expired:
            self.store.save(temporary['id'], {'display_name': 'Valid now'})
        self.assertEqual(expired.exception.status_code, 410)

    def test_save_rejects_non_json_metadata_and_plain_text_limits(self):
        invalid_values = [
            [],
            {'display_name': 'Valid', 'extra': 'field'},
            {'display_name': 'x' * 201},
            {'display_name': 'Valid', 'tags': ['x'] * 21},
            {'display_name': 'Valid', 'tags': ['x' * 41]},
            {'display_name': 'Valid', 'notes': 'x' * 10_001},
        ]
        for metadata in invalid_values:
            with self.subTest(metadata_type=type(metadata), keys=getattr(metadata, 'keys', lambda: [])()):
                temporary = self.ingest()
                with self.assertRaises(CatalogError) as error:
                    self.store.save(temporary['id'], metadata)
                self.assertEqual(error.exception.status_code, 400)

        with self.assertRaises(CatalogError) as name_filter:
            self.store.list_logs(name='x' * 201)
        self.assertEqual(name_filter.exception.status_code, 400)
        with self.assertRaises(CatalogError) as folder_name:
            self.store.create_folder('x' * 121)
        self.assertEqual(folder_name.exception.status_code, 400)

    def test_private_paths_and_files_have_owner_only_permissions(self):
        temporary = self.ingest()
        file_path = next((self.root / 'files').iterdir())
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.root / 'files').stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.root / 'catalog.sqlite3').stat().st_mode & 0o777, 0o600)
        self.assertEqual(file_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(temporary['original_filename'], 'flight.bin')

    def test_cleanup_removes_expired_and_old_uuid_staging_only(self):
        temporary = self.ingest(content=b'expired')
        old_staging = self.root / 'files' / f'{uuid.uuid4()}.bin'
        young_staging = self.root / 'files' / f'{uuid.uuid4()}.bin'
        unrelated = self.root / 'files' / 'keep-me.bin'
        for path in (old_staging, young_staging, unrelated):
            path.write_bytes(b'staging')
            path.chmod(0o600)
        os.utime(old_staging, (self.now - 3601, self.now - 3601))
        os.utime(young_staging, (self.now, self.now))
        os.utime(unrelated, (self.now - 7200, self.now - 7200))
        self.now += 3600

        result = self.store.cleanup()

        self.assertEqual(result, {'removed': 2, 'skipped': 0, 'failed': 0})
        self.assertFalse(old_staging.exists())
        self.assertTrue(young_staging.exists())
        self.assertTrue(unrelated.exists())
        with self.assertRaises(CatalogError) as expired:
            self.store.save(temporary['id'], {'display_name': 'Too late'})
        self.assertEqual(expired.exception.status_code, 404)

    def test_cleanup_removes_processing_row_abandoned_by_child(self):
        process = multiprocessing.get_context('fork').Process(
            target=abandon_ingest, args=(self.root, self.now)
        )
        process.start()
        process.join(5)
        self.assertEqual(process.exitcode, 0)
        self.assertEqual(len(list((self.root / 'files').iterdir())), 1)

        self.assertEqual(
            self.store.cleanup(), {'removed': 1, 'skipped': 0, 'failed': 0}
        )
        self.assertEqual(list((self.root / 'files').iterdir()), [])

    def test_cleanup_skips_active_processing_file(self):
        started = threading.Event()
        release = threading.Event()

        class PausingSource(io.BytesIO):
            def read(self, size=-1):
                started.set()
                release.wait(5)
                return super().read(size)

        with ThreadPoolExecutor(max_workers=1) as executor:
            ingest = executor.submit(
                self.store.ingest,
                PausingSource(b'active'),
                'active.bin',
                lambda path, name: {'success': True},
            )
            self.assertTrue(started.wait(5))
            self.assertEqual(
                self.store.cleanup(), {'removed': 0, 'skipped': 1, 'failed': 0}
            )
            release.set()
            _, temporary = ingest.result(5)
        self.assertEqual(temporary['status'], 'temporary')

    def test_delete_unlink_failure_is_retried_by_cleanup(self):
        saved = self.store.save(self.ingest(content=b'retry')['id'], {
            'display_name': 'Retry',
        })
        with self.store.use_file(saved['id']) as (path, _):
            pass

        with mock.patch.object(Path, 'unlink', side_effect=OSError('injected')):
            with self.assertLogs('catalog', level='ERROR'):
                with self.assertRaises(CatalogError) as error:
                    self.store.delete(saved['id'])
        self.assertEqual(error.exception.status_code, 500)
        self.assertTrue(path.exists())
        self.assertEqual(
            self.store.cleanup(), {'removed': 1, 'skipped': 0, 'failed': 0}
        )
        self.assertFalse(path.exists())

    def test_cleanup_reports_unlink_failure_and_retries_deleting_row(self):
        self.ingest(content=b'retry cleanup')
        self.now += 3600

        with mock.patch.object(Path, 'unlink', side_effect=OSError('injected')):
            with self.assertLogs('catalog', level='ERROR'):
                result = self.store.cleanup()
        self.assertEqual(result, {'removed': 0, 'skipped': 0, 'failed': 1})
        self.assertEqual(
            self.store.cleanup(), {'removed': 1, 'skipped': 0, 'failed': 0}
        )

    def test_independent_stores_cannot_save_at_cleanup_deadline(self):
        temporary = self.ingest()
        self.now += 3600
        other = CatalogStore(self.root, clock=lambda: self.now)
        barrier = threading.Barrier(2)

        def save_at_deadline():
            barrier.wait()
            try:
                self.store.save(temporary['id'], {'display_name': 'Race'})
            except CatalogError as error:
                return error.status_code
            return 200

        def clean_at_deadline():
            barrier.wait()
            return other.cleanup()

        with ThreadPoolExecutor(max_workers=2) as executor:
            save_result = executor.submit(save_at_deadline)
            cleanup_result = executor.submit(clean_at_deadline)
            status = save_result.result(5)
            cleanup_result.result(5)

        self.assertIn(status, {404, 409, 410})
        self.assertNotEqual(status, 200)
        other.cleanup()
        with self.assertRaises(CatalogError):
            self.store.get(temporary['id'])

    def test_initialize_rejects_future_schema_versions(self):
        future_root = self.root / 'future'
        future_root.mkdir()
        database = future_root / 'catalog.sqlite3'
        with sqlite3.connect(database) as connection:
            connection.execute('PRAGMA user_version = 2')

        with self.assertRaises(CatalogError) as error:
            CatalogStore(future_root, clock=lambda: self.now).initialize()
        self.assertEqual(error.exception.status_code, 500)

    def test_schema_rejects_invalid_log_state_combinations(self):
        invalid_rows = [
            ('processing', self.now + 1, None, None, None),
            ('temporary', None, None, None, None),
            ('saved', None, self.now, 1, None),
            ('saved', None, self.now, 1, ''),
        ]
        with sqlite3.connect(self.root / 'catalog.sqlite3') as connection:
            for index, (status, expires_at, saved_at, folder_id, display_name) in enumerate(invalid_rows):
                with self.subTest(status=status):
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(
                            '''INSERT INTO logs(
                                   id, file_key, original_filename, size_bytes,
                                   created_at, status, expires_at, saved_at,
                                   updated_at, folder_id, display_name, notes
                               ) VALUES (?, ?, 'x.bin', 0, ?, ?, ?, ?, ?, ?, ?, '')''',
                            (str(index), f'{index}.bin', self.now, status,
                             expires_at, saved_at, self.now, folder_id, display_name),
                        )

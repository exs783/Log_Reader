import io
import os
import sqlite3
import subprocess
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from catalog import CatalogError, CatalogStore


class CatalogDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(prefix='catalog-deployment-tests-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / 'catalog'

    def _store_with_expired_and_saved(self) -> tuple[CatalogStore, str, str]:
        old_now = time.time() - 7200
        store = CatalogStore(self.root, clock=lambda: old_now)
        store.initialize()
        _, expired = store.ingest(
            io.BytesIO(b'expired'), 'expired.bin',
            lambda path, name: {'success': True},
        )
        _, temporary = store.ingest(
            io.BytesIO(b'keep'), 'saved.bin',
            lambda path, name: {'success': True},
        )
        saved = store.save(temporary['id'], {'display_name': 'Keep'})
        return store, expired['id'], saved['id']

    @staticmethod
    def _catalog_script() -> Path:
        return Path(__file__).with_name('catalog.py')

    def _wsgi_environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        environment['CATALOG_DIR'] = str(self.root)
        environment['PUBLIC_ORIGIN'] = 'http://127.0.0.1:18765'
        environment['PYTHONDONTWRITEBYTECODE'] = '1'
        return environment

    def test_cleanup_cli_removes_expired_data_and_preserves_saved_data(self):
        store, expired_id, saved_id = self._store_with_expired_and_saved()

        completed = subprocess.run(
            [
                sys.executable,
                str(self._catalog_script()),
                'cleanup',
                '--data-dir',
                str(self.root),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, 'removed=1 skipped=0 failed=0\n')
        reopened = CatalogStore(self.root)
        reopened.initialize()
        self.assertEqual(reopened.get(saved_id)['display_name'], 'Keep')
        with self.assertRaises(CatalogError):
            reopened.get(expired_id)
        self.assertEqual([path.read_bytes() for path in reopened.files.iterdir()], [b'keep'])

    def test_cleanup_cli_returns_nonzero_and_counts_failure(self):
        invalid_root = Path(self.directory.name) / 'not-a-directory'
        invalid_root.write_text('file', encoding='utf-8')

        completed = subprocess.run(
            [
                sys.executable,
                str(self._catalog_script()),
                'cleanup',
                '--data-dir',
                str(invalid_root),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, 'removed=0 skipped=0 failed=1\n')
        self.assertEqual(completed.stderr, '')

    def test_wsgi_startup_sweeps_before_serving_a_request(self):
        _, expired_id, saved_id = self._store_with_expired_and_saved()
        completed = subprocess.run(
            [
                sys.executable,
                '-c',
                "import wsgi; assert wsgi.app.test_client().get('/api/health').status_code == 200",
            ],
            cwd=Path(__file__).parent,
            env=self._wsgi_environment(),
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        reopened = CatalogStore(self.root)
        reopened.initialize()
        self.assertEqual(reopened.get(saved_id)['display_name'], 'Keep')
        with self.assertRaises(CatalogError):
            reopened.get(expired_id)
        self.assertEqual([path.read_bytes() for path in reopened.files.iterdir()], [b'keep'])

    def test_wsgi_startup_stops_on_incompatible_schema(self):
        store = CatalogStore(self.root)
        store.initialize()
        with sqlite3.connect(store.database) as connection:
            connection.execute('PRAGMA user_version = 2')

        completed = subprocess.run(
            [sys.executable, '-c', 'import wsgi'],
            cwd=Path(__file__).parent,
            env=self._wsgi_environment(),
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn('Unsupported catalog schema version', completed.stderr)


if __name__ == '__main__':
    unittest.main()

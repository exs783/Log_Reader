import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from catalog import CatalogStore
from catalog_routes import init_catalog
import python_backend


class CatalogAPITests(unittest.TestCase):
    def setUp(self):
        self.backend = python_backend
        self.now = [1_800_000_000.0]
        self.sandbox = TemporaryDirectory(prefix='catalog-api-tests-')
        self.addCleanup(self.sandbox.cleanup)

        app = self.backend.app
        old_config = {
            'CATALOG_DIR': app.config.get('CATALOG_DIR'),
            'PUBLIC_ORIGIN': app.config.get('PUBLIC_ORIGIN'),
            'TESTING': app.config.get('TESTING'),
        }
        old_catalog = app.extensions.get('catalog')

        def restore_app() -> None:
            app.config.update(old_config)
            if old_catalog is None:
                app.extensions.pop('catalog', None)
            else:
                app.extensions['catalog'] = old_catalog

        self.addCleanup(restore_app)
        app.config.update(TESTING=True, PUBLIC_ORIGIN='http://localhost')
        self.store = CatalogStore(
            Path(self.sandbox.name) / 'catalog', clock=lambda: self.now[0]
        )
        init_catalog(app, store=self.store)
        self.client = app.test_client()

    @staticmethod
    def _analysis(filename: str = 'flight.bin') -> dict:
        return {
            'success': True,
            'filename': filename,
            'summary': {},
            'analyses': {},
            'frame': {},
        }

    def _upload(self, content: bytes = b'flight bytes', filename: str = 'flight.bin'):
        with patch.object(
            self.backend, 'run_analysis', return_value=self._analysis(filename)
        ):
            return self.client.post(
                '/api/upload',
                headers={'Origin': 'http://localhost'},
                data={'file': (io.BytesIO(content), filename)},
            )

    def _save(
        self,
        *,
        display_name: str = 'First hover',
        folder_id: int = 1,
        tags: list[str] | None = None,
        notes: str = 'Watch yaw',
    ) -> dict:
        temporary = self._upload().get_json()
        response = self.client.post(
            f"/api/logs/{temporary['log_id']}/save",
            headers={'Origin': 'http://localhost'},
            json={
                'display_name': display_name,
                'folder_id': folder_id,
                'tags': ['hover'] if tags is None else tags,
                'notes': notes,
            },
        )
        self.assertEqual(response.status_code, 200)
        return response.get_json()['log']

    def test_upload_save_and_original_download(self):
        with patch.object(self.backend, 'run_analysis', return_value={
            'success': True, 'filename': 'flight.bin',
            'summary': {}, 'analyses': {}, 'frame': {},
        }):
            uploaded = self.client.post(
                '/api/upload',
                headers={'Origin': 'http://localhost'},
                data={'file': (io.BytesIO(b'flight bytes'), 'flight.bin')},
            )
        self.assertEqual(uploaded.status_code, 200)
        temporary = uploaded.get_json()
        self.assertIn('expires_at', temporary)
        response = self.client.post(
            f"/api/logs/{temporary['log_id']}/save",
            headers={'Origin': 'http://localhost'},
            json={
                'display_name': 'First hover', 'folder_id': 1,
                'tags': ['hover'], 'notes': 'Watch yaw',
            },
        )
        self.assertEqual(response.status_code, 200)
        log_id = response.get_json()['log']['id']
        download = self.client.get(f'/api/logs/{log_id}/download')
        self.assertEqual(download.data, b'flight bytes')
        self.assertIn('attachment', download.headers['Content-Disposition'])
        self.assertEqual(download.headers['Cache-Control'], 'no-store')
        self.assertEqual(download.headers['X-Content-Type-Options'], 'nosniff')
        download.close()

    def test_log_folder_crud_and_filters(self):
        folder_response = self.client.post(
            '/api/folders',
            headers={'Origin': 'http://localhost'},
            json={'name': 'Tests', 'parent_id': 1},
        )
        self.assertEqual(folder_response.status_code, 201)
        folder = folder_response.get_json()['folder']
        saved = self._save(
            display_name='Yaw hover', folder_id=folder['id'], tags=['Yaw', 'hover']
        )

        fetched = self.client.get(f"/api/logs/{saved['id']}")
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.get_json()['log']['display_name'], 'Yaw hover')

        for query in (
            f"folder_id={folder['id']}",
            'name=Yaw',
            'tag=yaw',
        ):
            listed = self.client.get(f'/api/logs?{query}')
            self.assertEqual(listed.status_code, 200)
            self.assertEqual([item['id'] for item in listed.get_json()['logs']], [saved['id']])
        self.assertEqual(self.client.get('/api/logs?name=missing').get_json()['logs'], [])

        edited = self.client.patch(
            f"/api/logs/{saved['id']}",
            headers={'Origin': 'http://localhost'},
            json={'display_name': 'Yaw retest', 'tags': ['Retest'], 'notes': ''},
        )
        self.assertEqual(edited.status_code, 200)
        self.assertEqual(edited.get_json()['log']['tags'], ['retest'])
        self.assertEqual(self.client.get('/api/tags').get_json()['tags'], ['retest'])

        renamed = self.client.patch(
            f"/api/folders/{folder['id']}",
            headers={'Origin': 'http://localhost'},
            json={'name': 'Retests'},
        )
        self.assertEqual(renamed.status_code, 200)
        self.assertEqual(renamed.get_json()['folder']['name'], 'Retests')
        folder_names = [item['name'] for item in self.client.get('/api/folders').get_json()['folders']]
        self.assertEqual(folder_names, ['Logs', 'Retests'])

        deleted = self.client.delete(
            f"/api/logs/{saved['id']}", headers={'Origin': 'http://localhost'}
        )
        self.assertEqual(deleted.get_json(), {'success': True})
        folder_deleted = self.client.delete(
            f"/api/folders/{folder['id']}", headers={'Origin': 'http://localhost'}
        )
        self.assertEqual(folder_deleted.get_json(), {'success': True})

    def test_invalid_requests_do_not_partially_mutate(self):
        root_folders = self.store.list_folders()
        for body in (None, [], {'name': 'Extra', 'unknown': True}):
            kwargs = {'data': ''} if body is None else {'json': body}
            response = self.client.post(
                '/api/folders', headers={'Origin': 'http://localhost'}, **kwargs
            )
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.store.list_folders(), root_folders)

        saved = self._save()
        original = self.store.get(saved['id'])
        invalid_edits = (
            {},
            {'unknown': 'value'},
            {'folder_id': '1'},
            {'tags': 'hover'},
        )
        for changes in invalid_edits:
            response = self.client.patch(
                f"/api/logs/{saved['id']}",
                headers={'Origin': 'http://localhost'},
                json=changes,
            )
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.store.get(saved['id']), original)

        for query in ('folder_id=nope', 'folder_id=', 'unknown=value'):
            response = self.client.get(f'/api/logs?{query}')
            self.assertEqual(response.status_code, 400)

    def test_missing_and_expired_records_return_expected_statuses(self):
        missing = '00000000-0000-4000-8000-000000000000'
        self.assertEqual(self.client.get(f'/api/logs/{missing}').status_code, 404)
        self.assertEqual(
            self.client.delete(
                f'/api/logs/{missing}', headers={'Origin': 'http://localhost'}
            ).status_code,
            404,
        )

        temporary = self._upload().get_json()
        self.now[0] += 3601
        expired = self.client.post(
            f"/api/logs/{temporary['log_id']}/save",
            headers={'Origin': 'http://localhost'},
            json={'display_name': 'Too late'},
        )
        self.assertEqual(expired.status_code, 410)

    def test_duplicate_save_validates_metadata_then_remains_idempotent(self):
        saved = self._save()
        files_before = sorted(self.store.files.iterdir())

        for invalid in (
            {'display_name': 'First hover', 'unknown': True},
            {'display_name': 'First hover', 'folder_id': '1'},
        ):
            response = self.client.post(
                f"/api/logs/{saved['id']}/save",
                headers={'Origin': 'http://localhost'},
                json=invalid,
            )
            self.assertEqual(response.status_code, 400)

        duplicate = self.client.post(
            f"/api/logs/{saved['id']}/save",
            headers={'Origin': 'http://localhost'},
            json={
                'display_name': 'First hover',
                'folder_id': 1,
                'tags': ['hover'],
                'notes': 'Watch yaw',
            },
        )
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(duplicate.get_json()['log'], saved)
        self.assertEqual(sorted(self.store.files.iterdir()), files_before)

    def test_write_requests_require_same_origin(self):
        for headers in (
            {'Origin': 'https://foreign.example'},
            {'Origin': 'null'},
            {},
            {'Sec-Fetch-Site': 'cross-site'},
        ):
            response = self.client.post('/api/folders', headers=headers, json={'name': 'Blocked'})
            self.assertEqual(response.status_code, 403)
        self.assertEqual(len(self.store.list_folders()), 1)

        allowed = self.client.post(
            '/api/folders',
            headers={'Sec-Fetch-Site': 'same-origin'},
            json={'name': 'Allowed'},
        )
        self.assertEqual(allowed.status_code, 201)

    def test_reanalysis_failure_preserves_saved_bytes(self):
        saved = self._save()
        with self.assertLogs(self.backend.app.logger.name, level='ERROR'):
            with patch.object(
                self.backend, 'run_analysis', side_effect=RuntimeError('parser failed')
            ):
                response = self.client.post(
                    f"/api/logs/{saved['id']}/analyze",
                    headers={'Origin': 'http://localhost'},
                )
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json(), {'error': 'Catalog request failed'})
        download = self.client.get(f"/api/logs/{saved['id']}/download")
        self.assertEqual(download.data, b'flight bytes')
        download.close()

    def test_unbuffered_download_holds_file_until_close(self):
        saved = self._save()
        download = self.client.get(
            f"/api/logs/{saved['id']}/download", buffered=False
        )
        blocked = self.client.delete(
            f"/api/logs/{saved['id']}", headers={'Origin': 'http://localhost'}
        )
        self.assertEqual(blocked.status_code, 409)

        download.close()
        deleted = self.client.delete(
            f"/api/logs/{saved['id']}", headers={'Origin': 'http://localhost'}
        )
        self.assertEqual(deleted.status_code, 200)


if __name__ == '__main__':
    unittest.main()

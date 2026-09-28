import io
import json
import subprocess
import sys
import sysconfig
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from werkzeug.datastructures import MultiDict
from werkzeug.test import TestResponse

from catalog import CatalogStore
from catalog_routes import init_catalog
import python_backend


DATAFLASH_TEXT = (
    b'FMT, 128, 89, FMT, BBnNZ, Type,Length,Name,Format,Columns\n'
    b'FMT, 129, 15, TEST, Qf, TimeUS,Value\n'
    b'TEST, 1000000, 42.5\n'
)


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
            'INGEST_API_KEY': app.config.get('INGEST_API_KEY'),
            'MAX_CONTENT_LENGTH': app.config.get('MAX_CONTENT_LENGTH'),
        }
        old_catalog = app.extensions.get('catalog')

        def restore_app() -> None:
            app.config.update(old_config)
            if old_catalog is None:
                app.extensions.pop('catalog', None)
            else:
                app.extensions['catalog'] = old_catalog

        self.addCleanup(restore_app)
        app.config.update(TESTING=True, PUBLIC_ORIGIN='http://localhost',
                          INGEST_API_KEY='test-ingest-key')
        self.store = CatalogStore(
            Path(self.sandbox.name) / 'catalog', clock=lambda: self.now[0]
        )
        init_catalog(app, store=self.store)
        self.client = app.test_client()

    def _device_upload(
        self, content: bytes = b'device bytes', filename: str = 'device.BIN', **fields: str,
    ) -> TestResponse:
        return self.client.post(
            '/api/ingest', headers={'Authorization': 'Bearer test-ingest-key'},
            data={'file': (io.BytesIO(content), filename), **fields},
        )

    def test_device_upload_receipt_and_existing_actions(self):
        with patch.object(self.backend, 'run_analysis', side_effect=AssertionError('No analysis')):
            with patch('catalog_routes.open_text_dump', side_effect=AssertionError('No dump')):
                first = self._device_upload(
                    DATAFLASH_TEXT, 'device.log', display_name='Field test',
                    tags='["Hover", "hover"]', notes='Watch yaw',
                )
        self.assertEqual(first.status_code, 201)
        receipt = first.get_json()
        self.assertEqual(set(receipt), {'success', 'log_id', 'duplicate'})
        self.assertTrue(receipt['success'])
        self.assertFalse(receipt['duplicate'])
        log_id = receipt['log_id']
        log = self.store.get(log_id)
        self.assertEqual((log['display_name'], log['tags'], log['notes']),
                         ('Field test', ['hover'], 'Watch yaw'))
        retry = self._device_upload(DATAFLASH_TEXT, 'renamed.BIN', notes='Replace')
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.get_json(), {**receipt, 'duplicate': True})
        self.assertEqual(self.store.get(log_id), log)
        self.assertEqual(self.client.get('/api/logs').get_json()['logs'], [log])
        download = self.client.get(f'/api/logs/{log_id}/download')
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.data, DATAFLASH_TEXT)
        download.close()
        with patch.object(self.backend, 'run_analysis', return_value=self._analysis('device.log')):
            analyzed = self.client.post(f'/api/logs/{log_id}/analyze',
                                       headers={'Origin': 'http://localhost'})
        self.assertEqual(analyzed.status_code, 200)
        self.assertEqual(analyzed.get_json()['log_id'], log_id)
        text = self.client.get(f'/api/logs/{log_id}/text?download=1')
        self.assertEqual(text.status_code, 200)
        self.assertIn(b'TEST', text.data)
        self.assertEqual(text.mimetype, 'text/plain')
        text.close()

    def test_device_upload_key_required_before_body(self):
        self.backend.app.config['MAX_CONTENT_LENGTH'] = 256
        with patch('flask.wrappers.Request._load_form_data',
                   side_effect=AssertionError('Unauthorized body was parsed')):
            for authorization in ('', 'Bearer wrong', 'Basic test-ingest-key',
                                  'Bearer ', 'test-ingest-key', 'Bearer t\u00e9st'):
                with self.subTest(authorization=authorization):
                    response = self.client.post(
                        '/api/ingest',
                        headers={'Origin': 'http://localhost', 'Authorization': authorization},
                        data={'file': (io.BytesIO(b'x' * 1024), 'flight.bin')},
                    )
                    self.assertEqual(response.status_code, 401)
            for key in ('', None):
                self.backend.app.config['INGEST_API_KEY'] = key
                self.assertEqual(self._device_upload().status_code, 503)
            self.backend.app.config.pop('INGEST_API_KEY')
            self.assertEqual(self._device_upload().status_code, 503)
        self.assertEqual(self.store.list_logs(), [])
        self.assertEqual(list(self.store.files.iterdir()), [])

    def test_device_key_does_not_bypass_browser_origin(self):
        receipt = self._device_upload().get_json()
        self.assertIn('log_id', receipt)
        for route in ('/api/upload', '/api/folders', f"/api/logs/{receipt['log_id']}/save",
                      '/api/ingest/other'):
            with self.subTest(route=route):
                response = self.client.post(route, headers={'Authorization': 'Bearer test-ingest-key'})
                self.assertEqual(response.status_code, 403)
        browser = self._save()
        self.assertEqual(browser['status'], 'saved')
        self.assertNotEqual(browser['id'], receipt['log_id'])

    def test_device_upload_validation_and_size(self):
        self.assertEqual(self.backend.app.config['MAX_CONTENT_LENGTH'], 500 * 1024 * 1024)
        receipt = self._device_upload().get_json()
        self.assertIn('log_id', receipt)
        before = self.store.get(receipt['log_id'])
        invalid_fields = [
            {'display_name': ''}, {'display_name': 'x' * 201}, {'display_name': '../flight'},
            {'tags': '['}, {'tags': 'null'}, {'tags': '{}'},
            {'tags': json.dumps(['x'] * 21)}, {'tags': json.dumps(['x' * 41])},
            {'notes': 'x' * 10001}, {'folder_id': '1'},
        ]
        for fields in invalid_fields:
            with self.subTest(fields=list(fields)):
                response = self._device_upload(**fields)
                self.assertEqual(response.status_code, 400)
                self.assertIn('error', response.get_json())
        for filename in ('flight.zip', ''):
            self.assertEqual(self._device_upload(filename=filename).status_code, 400)
        for data in ({}, MultiDict([
            ('file', (io.BytesIO(b'first'), 'first.bin')),
            ('file', (io.BytesIO(b'second'), 'second.bin')),
        ]), MultiDict([
            ('file', (io.BytesIO(b'first'), 'first.bin')),
            ('notes', 'first'), ('notes', 'second'),
        ])):
            response = self.client.post('/api/ingest', data=data,
                                        headers={'Authorization': 'Bearer test-ingest-key'})
            self.assertEqual(response.status_code, 400)
        self.backend.app.config['MAX_CONTENT_LENGTH'] = 256
        oversized = self._device_upload(b'x' * 1024)
        self.assertEqual(oversized.status_code, 413)
        self.assertIn('error', oversized.get_json())
        self.assertEqual(self.store.list_logs(), [before])
        self.assertEqual(len(list(self.store.files.iterdir())), 1)

    def test_device_upload_storage_error_is_generic(self):
        with patch.object(self.store, 'ingest_saved', side_effect=OSError('/private/path')):
            with self.assertLogs(self.backend.app.logger.name, level='ERROR'):
                response = self._device_upload()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json(), {'error': 'Catalog request failed'})
        self.assertNotIn(b'test-ingest-key', response.data)
        self.assertEqual(self.store.list_logs(), [])
        malformed = self.client.post(
            '/api/ingest', headers={'Authorization': 'Bearer test-ingest-key'},
            content_type='multipart/form-data; boundary=fixture', data=b'--fixture',
            environ_overrides={'CONTENT_LENGTH': '100'},
        )
        self.assertEqual(malformed.status_code, 400)
        self.assertIn('error', malformed.get_json())

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

    def _save_text(self, display_name: str = 'Text sample') -> dict:
        _, temporary = self.store.ingest(
            io.BytesIO(DATAFLASH_TEXT), 'sample.log', lambda path, name: {'success': True}
        )
        return self.store.save(temporary['id'], {'display_name': display_name})

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

    def test_view_is_plain_text_and_download_has_same_contents(self):
        saved = self._save_text()
        route = f"/api/logs/{saved['id']}/text"

        view = self.client.get(route)
        download = self.client.get(route + '?download=1')

        self.assertEqual(view.status_code, 200)
        self.assertEqual(view.mimetype, 'text/plain')
        self.assertEqual(view.headers['X-Content-Type-Options'], 'nosniff')
        self.assertIn(b'TEST', view.data)
        self.assertEqual(view.data, download.data)
        self.assertIn('inline', view.headers['Content-Disposition'])
        self.assertIn('attachment', download.headers['Content-Disposition'])
        with self.store.use_file(saved['id']) as (path, _):
            converter = Path(sysconfig.get_path('scripts')) / 'mavlogdump.py'
            expected = subprocess.check_output(
                [sys.executable, '-u', str(converter), str(path)],
                stderr=subprocess.DEVNULL,
            )
        self.assertEqual(view.data, expected)
        view.close()
        download.close()

    def test_text_stream_releases_file_on_exhaustion_and_early_close(self):
        exhausted = self._save_text('Exhausted')
        response = self.client.get(f"/api/logs/{exhausted['id']}/text")
        self.assertIn(b'TEST', response.data)
        deleted = self.client.delete(
            f"/api/logs/{exhausted['id']}", headers={'Origin': 'http://localhost'}
        )
        self.assertEqual(deleted.status_code, 200)
        response.close()

        cancelled = self._save_text('Cancelled')
        response = self.client.get(
            f"/api/logs/{cancelled['id']}/text", buffered=False
        )
        blocked = self.client.delete(
            f"/api/logs/{cancelled['id']}", headers={'Origin': 'http://localhost'}
        )
        self.assertEqual(blocked.status_code, 409)
        response.close()
        deleted = self.client.delete(
            f"/api/logs/{cancelled['id']}", headers={'Origin': 'http://localhost'}
        )
        self.assertEqual(deleted.status_code, 200)

    def test_text_conversion_failure_returns_500_and_preserves_source(self):
        saved = self._save_text()
        process = Mock()
        process.stdout = io.BytesIO()
        process.wait.return_value = 2
        process.poll.return_value = 2

        with self.assertLogs(self.backend.app.logger.name, level='ERROR'):
            with patch('log_text.subprocess.Popen', return_value=process):
                response = self.client.get(f"/api/logs/{saved['id']}/text")

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json(), {'error': 'Catalog request failed'})
        download = self.client.get(f"/api/logs/{saved['id']}/download")
        self.assertEqual(download.data, DATAFLASH_TEXT)
        download.close()

    def test_failed_text_tail_and_html_looking_output_remain_plain_bytes(self):
        saved = self._save_text()
        process = Mock()
        process.stdout.read.side_effect = [b'<script>alert(1)</script>', b'']
        process.wait.return_value = 3
        process.poll.return_value = 3

        with self.assertLogs('log_text', level='ERROR'):
            with patch('log_text.subprocess.Popen', return_value=process):
                response = self.client.get(f"/api/logs/{saved['id']}/text")
                payload = response.data

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'text/plain')
        self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(response.headers['X-Accel-Buffering'], 'no')
        self.assertEqual(
            payload,
            b'<script>alert(1)</script>'
            b'\n[TEXT DUMP INCOMPLETE: conversion failed]\n',
        )
        response.close()
        download = self.client.get(f"/api/logs/{saved['id']}/download")
        self.assertEqual(download.data, DATAFLASH_TEXT)
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

    def test_normalized_tag_limit_applies_to_save_edit_and_filter_without_mutation(self):
        boundary = 'ß' * 20
        too_long_after_casefold = 'ß' * 21
        normalized_boundary = 'ss' * 20
        temporary = self._upload().get_json()

        invalid_save = self.client.post(
            f"/api/logs/{temporary['log_id']}/save",
            headers={'Origin': 'http://localhost'},
            json={'display_name': 'Boundary tag', 'tags': [too_long_after_casefold]},
        )
        self.assertEqual(invalid_save.status_code, 400)

        valid_save = self.client.post(
            f"/api/logs/{temporary['log_id']}/save",
            headers={'Origin': 'http://localhost'},
            json={'display_name': 'Boundary tag', 'tags': [boundary]},
        )
        self.assertEqual(valid_save.status_code, 200)
        saved = valid_save.get_json()['log']
        self.assertEqual(saved['tags'], [normalized_boundary])
        filtered = self.client.get('/api/logs', query_string={'tag': boundary})
        self.assertEqual(
            [log['id'] for log in filtered.get_json()['logs']], [saved['id']]
        )

        invalid_edit = self.client.patch(
            f"/api/logs/{saved['id']}",
            headers={'Origin': 'http://localhost'},
            json={'tags': [too_long_after_casefold]},
        )
        self.assertEqual(invalid_edit.status_code, 400)
        self.assertEqual(self.store.get(saved['id']), saved)
        invalid_filter = self.client.get(
            '/api/logs', query_string={'tag': too_long_after_casefold}
        )
        self.assertEqual(invalid_filter.status_code, 400)
        self.assertEqual(self.store.get(saved['id']), saved)

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

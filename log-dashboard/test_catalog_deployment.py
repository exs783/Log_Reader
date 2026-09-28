import base64
import hashlib
import http.client
import io
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from werkzeug.serving import WSGIRequestHandler, make_server

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
        environment['INGEST_API_KEY'] = ''
        return environment

    def test_wsgi_ingest_key_configuration(self):
        script = '''
import io, json, wsgi
response = wsgi.app.test_client().post(
    '/api/ingest', headers={'Authorization': 'Bearer test-ingest-key'},
    data={'file': (io.BytesIO(b'fixture'), 'fixture.bin')},
)
print(json.dumps({'status': response.status_code, 'body': response.get_json()}))
'''
        for key, expected_status in [('test-ingest-key', 201), ('', 503), (None, 503)]:
            with self.subTest(configured=bool(key)):
                environment = self._wsgi_environment()
                if key is None:
                    environment.pop('INGEST_API_KEY')
                else:
                    environment['INGEST_API_KEY'] = key
                completed = subprocess.run(
                    [sys.executable, '-c', script], cwd=Path(__file__).parent,
                    env=environment, capture_output=True, text=True, check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                response = json.loads(completed.stdout)
                self.assertEqual(response['status'], expected_status)
                if expected_status == 201:
                    self.assertTrue(response['body']['success'])
                    self.assertFalse(response['body']['duplicate'])

    @unittest.skipUnless(shutil.which('docker') and shutil.which('nginx'),
                         'Docker Compose and nginx required for deployment check')
    def test_ingest_compose_and_nginx_configuration(self):
        project = Path(__file__).resolve().parents[1]
        completed = subprocess.run(
            ['docker', 'compose', 'config', '--format', 'json'], cwd=project,
            env={**os.environ, 'INGEST_API_KEY': 'test-ingest-key'},
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        service = json.loads(completed.stdout)['services']['log-reader']
        self.assertEqual(service['environment'].get('INGEST_API_KEY'), 'test-ingest-key')
        self.assertEqual(service['environment']['CATALOG_DIR'], '/app/catalog-data')
        self.assertEqual(service['ports'][0]['host_ip'], '127.0.0.1')
        self.assertEqual(str(service['ports'][0]['published']), '18765')
        self.assertIn('/app/catalog-data', [volume['target'] for volume in service['volumes']])
        self._assert_nginx_auth_scope(project)

    def _assert_nginx_auth_scope(self, project):
        import python_backend

        class QuietHandler(WSGIRequestHandler):
            def log_request(self, code='-', size='-'):
                pass

        store = CatalogStore(self.root)
        store.initialize()
        sandbox = Path(self.directory.name)
        passwords = sandbox / 'fixture.htpasswd'
        encoded = base64.b64encode(hashlib.sha1(b'fixture-password').digest()).decode()
        passwords.write_text(f'team:{{SHA}}{encoded}\n')
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            proxy_port = listener.getsockname()[1]
        with patch.dict(python_backend.app.config, {'INGEST_API_KEY': 'test-ingest-key'}), \
                patch.dict(python_backend.app.extensions, {'catalog': store}), \
                make_server('127.0.0.1', 0, python_backend.app,
                            request_handler=QuietHandler) as upstream:
            upstream_url = f'http://127.0.0.1:{upstream.server_port}'
            snippet = (project / 'deploy/catalog-nginx.conf').read_text()
            snippet = snippet.replace('/etc/nginx/log-catalog.htpasswd', str(passwords))
            snippet = snippet.replace('http://127.0.0.1:18765', upstream_url)
            # Refuse to run a fixture that could reach the production upstream.
            self.assertTrue(all(url == upstream_url for url in re.findall(r'proxy_pass\s+([^;]+);', snippet)))
            self.assertNotIn('/etc/nginx/', snippet)
            config = sandbox / 'nginx.conf'
            temp_paths = '\n'.join(f'{kind}_temp_path {sandbox}/{kind};'
                                   for kind in ('client_body', 'proxy', 'fastcgi', 'uwsgi', 'scgi'))
            config.write_text(f'''
daemon off;
master_process off;
pid {sandbox}/nginx.pid;
error_log {sandbox}/nginx-error.log;
events {{ worker_connections 32; }}
http {{
    access_log off;
    {temp_paths}
    server {{
        listen 127.0.0.1:{proxy_port};
        client_max_body_size 500M;
        {snippet}
        location / {{ proxy_pass {upstream_url}; }}
    }}
}}
''')
            worker = threading.Thread(target=upstream.serve_forever, daemon=True)
            worker.start()
            try:
                with subprocess.Popen(
                    [shutil.which('nginx'), '-p', str(sandbox), '-c', str(config)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                ) as proxy:
                    try:
                        deadline = time.monotonic() + 5
                        while True:
                            self.assertIsNone(proxy.poll(), 'Isolated nginx failed to start')
                            try:
                                with socket.create_connection(('127.0.0.1', proxy_port), timeout=0.1):
                                    break
                            except OSError:
                                if time.monotonic() >= deadline:
                                    self.fail('Isolated nginx did not listen within 5 seconds')
                                time.sleep(0.02)

                        def request(path, authorization='', *, upload=False):
                            connection = http.client.HTTPConnection('127.0.0.1', proxy_port, timeout=5)
                            try:
                                headers = {'Authorization': authorization}
                                body = None
                                if upload:
                                    headers['Content-Type'] = 'multipart/form-data; boundary=fixture'
                                    body = (b'--fixture\r\nContent-Disposition: form-data; name="file"; '
                                            b'filename="fixture.bin"\r\n\r\nnginx-fixture\r\n--fixture--\r\n')
                                connection.request('POST' if upload else 'GET', path, body, headers)
                                response = connection.getresponse()
                                return response.status, response.read()
                            finally:
                                connection.close()

                        bearer = 'Bearer test-ingest-key'
                        for path in ('/', '/catalog.js', '/api/logs', '/api/ingest/other'):
                            self.assertEqual(request(path)[0], 401)
                            self.assertEqual(request(path, bearer)[0], 401)
                        self.assertEqual(request('/api/ingest', upload=True)[0], 401)
                        self.assertEqual(request('/api/ingest', 'Bearer wrong', upload=True)[0], 401)
                        first_status, first = request('/api/ingest', bearer, upload=True)
                        retry_status, retry = request('/api/ingest', bearer, upload=True)
                        self.assertEqual((first_status, retry_status), (201, 200))
                        self.assertEqual(json.loads(first)['log_id'], json.loads(retry)['log_id'])
                        basic = 'Basic ' + base64.b64encode(b'team:fixture-password').decode()
                        status, catalog = request('/api/logs', basic)
                        self.assertEqual(status, 200)
                        self.assertEqual(json.loads(catalog)['logs'][0]['id'], json.loads(first)['log_id'])
                    finally:
                        proxy.terminate()
                        proxy.wait(timeout=5)
            finally:
                upstream.shutdown()
                worker.join(timeout=5)

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
            connection.execute('PRAGMA user_version = 3')

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

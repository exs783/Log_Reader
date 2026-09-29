import io
import logging
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from catalog import CatalogStore
from catalog_routes import init_catalog
from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

import python_backend


ANALYSIS = {
    'success': True,
    'filename': 'flight.bin',
    'summary': {
        'timestamp': '2026-09-25T14:32:18Z',
        'flight_quality_score': 100,
        'verdict': 'Excellent',
        'issues': [],
        'messages_loaded': {'TEST': 1},
        'frame': {
            'name': 'QUAD',
            'motor_count': 4,
            'layout': 'X',
            'source': 'fixture',
        },
    },
    'analyses': {
        'imu': {
            'timestamps': [0, 1_000_000],
            'accX': [0.1, 0.2], 'accY': [0.2, 0.3], 'accZ': [9.7, 9.8],
            'gyrX': [0.01, 0.02], 'gyrY': [0.02, 0.03], 'gyrZ': [0.03, 0.04],
        },
        'vibration': {
            'timestamps': [0, 1_000_000],
            'vibration_magnitude': [9.7, 9.8],
        },
        'attitude': {
            'timestamps': [0, 1_000_000],
            'desRoll': [1.0, 2.0], 'roll': [0.9, 1.9],
            'desPitch': [2.0, 3.0], 'pitch': [1.9, 2.9],
        },
        'pid': {
            'timestamps': [0, 1_000_000],
            'desRoll': [1.0, 2.0], 'roll': [0.9, 1.9],
            'desPitch': [2.0, 3.0], 'pitch': [1.9, 2.9],
        },
        'battery': {
            'timestamps': [0, 1_000_000],
            'voltage': [24.0, 23.8], 'current': [4.0, 5.0],
        },
        'motors': {
            'timestamps': [0, 1_000_000], 'motor_count': 2,
            'motor1': [1200, 1300], 'motor2': [1210, 1310],
        },
        'magnetometer': {
            'timestamps': [0, 1_000_000], 'magnitude': [410.0, 412.0],
        },
        'barometer': {
            'timestamps': [0, 1_000_000], 'altitude': [10.0, 11.0],
        },
        'gps': {
            'timestamps': [0, 1_000_000],
            'altitude': [100.0, 101.0], 'num_satellites': [12, 13],
        },
        'events': {
            'events': [
                {'type': 'MODE_CHANGE', 'value': '<img src=x onerror=alert(1)>',
                 'timestamp': 1_000_000},
            ],
        },
    },
    'frame': {'name': 'QUAD', 'motor_count': 4, 'layout': 'X', 'source': 'fixture'},
}

DATAFLASH_TEXT = (
    b'FMT, 128, 89, FMT, BBnNZ, Type,Length,Name,Format,Columns\n'
    b'FMT, 129, 15, TEST, Qf, TimeUS,Value\n'
    b'TEST, 1000000, 42.5\n'
)


class CatalogBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sandbox = TemporaryDirectory(prefix='catalog-browser-tests-')
        app = python_backend.app
        cls.old_config = {
            'CATALOG_DIR': app.config.get('CATALOG_DIR'),
            'PUBLIC_ORIGIN': app.config.get('PUBLIC_ORIGIN'),
            'TESTING': app.config.get('TESTING'),
        }
        cls.old_catalog = app.extensions.get('catalog')
        cls.store = CatalogStore(Path(cls.sandbox.name) / 'catalog')
        init_catalog(app, store=cls.store)
        cls.server = make_server('127.0.0.1', 0, app, threaded=True)
        cls.base_url = f'http://127.0.0.1:{cls.server.server_port}'
        app.config.update(TESTING=True, PUBLIC_ORIGIN=cls.base_url)
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.analysis_patch = patch.object(
            python_backend, 'run_analysis', return_value=ANALYSIS
        )
        cls.analysis_mock = cls.analysis_patch.start()
        cls.server_thread.start()
        cls.werkzeug_logger = logging.getLogger('werkzeug')
        cls.old_werkzeug_disabled = cls.werkzeug_logger.disabled
        cls.werkzeug_logger.disabled = True

        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()
        cls.server_thread.join(timeout=5)
        cls.analysis_patch.stop()
        cls.werkzeug_logger.disabled = cls.old_werkzeug_disabled
        app = python_backend.app
        app.config.update(cls.old_config)
        if cls.old_catalog is None:
            app.extensions.pop('catalog', None)
        else:
            app.extensions['catalog'] = cls.old_catalog
        cls.sandbox.cleanup()

    def setUp(self):
        self.now = [time.time()]
        self.store = CatalogStore(
            Path(self.sandbox.name) / self._testMethodName,
            clock=lambda: self.now[0],
        )
        init_catalog(python_backend.app, store=self.store)
        self.analysis_mock.reset_mock(return_value=True, side_effect=True)
        self.analysis_mock.return_value = ANALYSIS
        self.context = self.browser.new_context()
        self.addCleanup(self.context.close)
        self.context.add_init_script(
            "window.__plotCalls = [];"
            "window.Plotly = {newPlot: (id) => {"
            "window.__plotCalls.push(id); return Promise.resolve();}};"
        )
        self.page = self.context.new_page()
        self.page.route(
            '**/*',
            lambda route: route.abort()
            if not route.request.url.startswith(self.base_url)
            else route.continue_(),
        )

    def upload(self, *, name='flight.bin', contents=b'fixture log'):
        self.page.locator('#log-file').set_input_files({
            'name': name,
            'mimeType': 'application/octet-stream',
            'buffer': contents,
        })

    def test_save_prompts_then_catalog_displays_inert_metadata(self):
        page = self.page
        page.goto(self.base_url)
        self.upload()
        page.get_by_role('button', name='Save to catalog', exact=True).click()
        name = page.get_by_label('Log name', exact=True)
        self.assertRegex(
            name.input_value(),
            r'^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}Z-[0-9a-f]{8}$',
        )
        name.fill('Hover <img src=x onerror=alert(1)>')
        page.get_by_label('Tags', exact=True).fill('hover, landing')
        page.get_by_label('Notes', exact=True).fill('Check yaw after landing')
        page.get_by_role('button', name='Save log', exact=True).click()
        page.get_by_role('button', name='Catalog', exact=True).click()
        self.assertEqual(page.locator('#catalog-list img').count(), 0)
        self.assertIn('Hover <img', page.locator('#catalog-list').inner_text())

    def test_catalog_mode_hides_analysis_and_root_controls_stay_unavailable(self):
        page = self.page
        page.goto(self.base_url)
        page.get_by_role('button', name='Catalog', exact=True).click()
        expect(page.locator('#catalog-panel')).to_be_visible()
        expect(page.locator('.upload-section')).to_be_hidden()
        expect(page.locator('nav')).to_be_hidden()
        expect(page.locator('.container')).to_be_hidden()
        self.assertEqual(page.request.get(f'{self.base_url}/catalog-data/catalog.sqlite3').status, 404)
        page.locator('#catalog-folder-filter').select_option('1')
        expect(page.get_by_role('button', name='Rename folder', exact=True)).to_be_disabled()
        expect(page.get_by_role('button', name='Delete folder', exact=True)).to_be_disabled()

    def test_upload_and_save_pending_states_cancel_repeat_and_failure_identity(self):
        page = self.page
        release_upload = threading.Event()

        def delayed_analysis(_path, _name):
            release_upload.wait(timeout=5)
            return ANALYSIS

        self.analysis_mock.side_effect = delayed_analysis
        page.goto(self.base_url)
        try:
            self.upload()
            expect(page.locator('#upload-status')).to_contain_text('Uploading flight.bin')
            expect(page.get_by_role('button', name='Save to catalog', exact=True)).to_be_disabled()
        finally:
            release_upload.set()
        expect(page.get_by_role('button', name='Save to catalog', exact=True)).to_be_enabled()
        self.analysis_mock.side_effect = None
        self.analysis_mock.return_value = ANALYSIS

        page.get_by_role('button', name='Save to catalog', exact=True).click()
        page.get_by_label('Log name', exact=True).fill('Canceled name')
        page.get_by_role('button', name='Cancel', exact=True).click()
        self.assertEqual(self.store.list_logs(), [])

        page.get_by_role('button', name='Save to catalog', exact=True).click()
        page.get_by_label('Log name', exact=True).fill('Saved once')
        release_save = threading.Event()
        original_save = self.store.save

        def delayed_save(*args, **kwargs):
            release_save.wait(timeout=5)
            return original_save(*args, **kwargs)

        self.store.save = delayed_save
        try:
            page.get_by_role('button', name='Save log', exact=True).click()
            expect(page.locator('#save-to-catalog')).to_be_disabled()
            expect(page.get_by_role('button', name='Save log', exact=True)).to_be_disabled()
        finally:
            release_save.set()
        expect(page.locator('#save-to-catalog')).to_have_text('Open in catalog')
        self.store.save = original_save

        page.locator('#save-to-catalog').click()
        page.get_by_role('button', name='Analyzer', exact=True).click()
        page.locator('#save-to-catalog').click()
        self.assertEqual(len(self.store.list_logs()), 1)

        self.analysis_mock.side_effect = RuntimeError('fixture failure')
        page.get_by_role('button', name='Analyzer', exact=True).click()
        with patch.object(python_backend.app.logger, 'exception'):
            self.upload(name='broken.bin')
            expect(page.locator('#upload-alert')).to_contain_text('Upload error')
        expect(page.locator('#save-to-catalog')).to_be_disabled()
        expect(page.locator('#save-to-catalog')).to_have_text('Save to catalog')

    def test_expired_temporary_prompts_reupload_and_refresh_does_not_restore_it(self):
        page = self.page
        page.goto(self.base_url)
        self.upload()
        expect(page.locator('#save-to-catalog')).to_be_enabled()
        self.now[0] += 3601
        page.locator('#save-to-catalog').click()
        page.get_by_label('Log name', exact=True).fill('Expired log')
        page.get_by_role('button', name='Save log', exact=True).click()
        expect(page.locator('#log-form-error')).to_contain_text('Re-upload')
        expect(page.locator('#save-to-catalog')).to_be_disabled()
        page.reload()
        expect(page.locator('#save-to-catalog')).to_be_disabled()
        self.assertEqual(self.store.list_logs(), [])

    def test_editing_saved_log_keeps_new_temporary_save_identity(self):
        page = self.page
        page.goto(self.base_url)
        self.upload(name='saved-a.bin', contents=b'saved A')
        page.locator('#save-to-catalog').click()
        page.get_by_label('Log name', exact=True).fill('Saved A')
        page.get_by_role('button', name='Save log', exact=True).click()

        with page.expect_response(lambda response: response.url.endswith('/api/upload')) as upload:
            self.upload(name='temporary-b.bin', contents=b'temporary B')
        temporary_b_id = upload.value.json()['log_id']

        page.get_by_role('button', name='Catalog', exact=True).click()
        page.locator('.catalog-log', has_text='Saved A').get_by_role(
            'button', name='Edit log', exact=True
        ).click()
        page.get_by_label('Log name', exact=True).fill('Edited A')
        page.get_by_role('button', name='Save changes', exact=True).click()

        page.get_by_role('button', name='Analyzer', exact=True).click()
        expect(page.locator('#save-to-catalog')).to_be_enabled()
        expect(page.locator('#save-to-catalog')).to_have_text('Save to catalog')
        page.locator('#save-to-catalog').click()
        page.get_by_label('Log name', exact=True).fill('Saved B')
        page.get_by_role('button', name='Save log', exact=True).click()
        expect(page.locator('#save-to-catalog')).to_have_text('Open in catalog')

        self.assertEqual(self.store.get(temporary_b_id)['display_name'], 'Saved B')
        self.assertEqual(
            {log['display_name'] for log in self.store.list_logs()},
            {'Edited A', 'Saved B'},
        )

    def test_saved_log_reanalysis_renders_tabs_and_failure_preserves_catalog_log(self):
        page = self.page
        page_errors = []
        page.on('pageerror', lambda error: page_errors.append(str(error)))
        page.goto(self.base_url)
        self.upload()
        page.locator('#save-to-catalog').click()
        page.get_by_label('Log name', exact=True).fill('Reanalysis sample')
        page.get_by_label('Tags', exact=True).fill('<img onerror=alert(1)>')
        page.get_by_label('Notes', exact=True).fill('<script>alert(1)</script>')
        page.get_by_role('button', name='Save log', exact=True).click()
        page.get_by_role('button', name='Catalog', exact=True).click()
        expect(page.locator('#catalog-list')).to_contain_text('<img onerror=alert(1)>')
        expect(page.locator('#catalog-list')).to_contain_text('<script>alert(1)</script>')
        self.assertEqual(page.locator('#catalog-list img, #catalog-list script').count(), 0)
        card = page.locator('.catalog-log', has_text='Reanalysis sample')
        card.get_by_role('button', name='Analyze', exact=True).click()
        expect(page.get_by_role('button', name='Analyzer', exact=True)).to_have_class('mode-btn active')
        expect(page.locator('#overview')).to_contain_text('Flight Quality Score')

        plot_tabs = (
            ('IMU Analysis', 'imu', 'imu-plot'),
            ('Vibration', 'vibration', 'vibration-plot'),
            ('Attitude Control', 'attitude', 'attitude-plot'),
            ('PID Tuning', 'pid', 'pid-plot'),
            ('Battery Health', 'battery', 'battery-plot'),
            ('Motor Performance', 'motors', 'motors-plot'),
            ('Magnetometer', 'magnetometer', 'magnetometer-plot'),
            ('Barometer', 'barometer', 'barometer-plot'),
            ('GPS & Navigation', 'gps', 'gps-plot'),
        )
        for tab_name, page_id, plot_id in plot_tabs:
            page.get_by_role('tab', name=tab_name, exact=True).click()
            expect(page.locator(f'#{page_id}')).to_be_visible()
            self.assertIn(plot_id, page.evaluate('window.__plotCalls'))

        page.get_by_role('tab', name='Events', exact=True).click()
        expect(page.locator('#events-list')).to_contain_text('<img src=x onerror=alert(1)>')
        self.assertEqual(page.locator('#events-list img').count(), 0)
        page.get_by_role('tab', name='Custom Plot', exact=True).click()
        expect(page.locator('#customplot-plot')).to_contain_text('Select variables')
        self.assertGreater(page.locator('.customplot-checkbox').count(), 0)
        self.assertEqual(page_errors, [])

        self.analysis_mock.side_effect = RuntimeError('fixture failure')
        page.get_by_role('button', name='Catalog', exact=True).click()
        card = page.locator('.catalog-log', has_text='Reanalysis sample')
        with patch.object(python_backend.app.logger, 'exception'):
            card.get_by_role('button', name='Analyze', exact=True).click()
            expect(page.locator('#catalog-alert')).to_contain_text('Catalog request failed')
        expect(page.locator('#catalog-list')).to_contain_text('Reanalysis sample')
        self.assertEqual(len(self.store.list_logs()), 1)

    def test_catalog_edits_moves_filters_and_enforces_folder_deletion(self):
        page = self.page
        page.goto(self.base_url)
        page.get_by_role('button', name='Catalog', exact=True).click()

        page.get_by_role('button', name='Create folder', exact=True).click()
        page.get_by_label('Folder name', exact=True).fill('Test flights')
        page.get_by_role('button', name='Save folder', exact=True).click()

        page.get_by_role('button', name='Analyzer', exact=True).click()
        self.upload()
        page.get_by_role('button', name='Save to catalog', exact=True).click()
        page.get_by_label('Log name', exact=True).fill('Hover run')
        page.get_by_label('Tags', exact=True).fill('hover, test')
        page.get_by_role('button', name='Save log', exact=True).click()
        page.get_by_role('button', name='Catalog', exact=True).click()

        page.get_by_role('button', name='Edit log', exact=True).click()
        log_dialog = page.locator('#log-dialog')
        self.assertTrue(
            log_dialog.evaluate('(element) => element.open'),
            page.evaluate("""() => ({
                dialogCount: document.querySelectorAll('#log-dialog').length,
                folderCount: document.querySelectorAll('#log-folder').length,
                title: document.querySelector('#log-dialog-title')?.textContent,
                catalog: document.querySelector('#catalog-list')?.innerText,
            })"""),
        )
        log_dialog.get_by_label('Log name', exact=True).fill('Landing run')
        page.locator('#log-folder').select_option(label='Logs / Test flights')
        log_dialog.get_by_label('Tags', exact=True).fill('landing')
        page.get_by_role('button', name='Save changes', exact=True).click()

        page.get_by_role('button', name='Analyzer', exact=True).click()
        self.upload(name='hover.bin')
        page.locator('#save-to-catalog').click()
        page.get_by_label('Log name', exact=True).fill('Hover run')
        page.get_by_label('Tags', exact=True).fill('hover')
        page.get_by_role('button', name='Save log', exact=True).click()
        page.get_by_role('button', name='Catalog', exact=True).click()

        page.locator('#catalog-name-filter').fill('missing')
        expect(page.locator('#catalog-list')).to_contain_text('No saved logs')
        page.locator('#catalog-name-filter').fill('Landing')
        expect(page.locator('#catalog-list')).to_contain_text('Landing run')
        expect(page.locator('#catalog-list')).not_to_contain_text('Hover run')
        page.locator('#catalog-name-filter').fill('')
        page.locator('#catalog-tag-filter').select_option('landing')
        expect(page.locator('#catalog-list')).to_contain_text('Landing run')
        expect(page.locator('#catalog-list')).not_to_contain_text('Hover run')
        page.locator('#catalog-folder-filter').select_option(label='Logs / Test flights')
        expect(page.locator('#catalog-list')).to_contain_text('Landing run')
        expect(page.locator('#catalog-breadcrumb')).to_have_text('Logs / Test flights')

        page.get_by_role('button', name='Rename folder', exact=True).click()
        page.get_by_label('Folder name', exact=True).fill('Landings')
        page.get_by_role('button', name='Save folder', exact=True).click()
        expect(page.locator('#catalog-breadcrumb')).to_contain_text('Landings')

        page.once('dialog', lambda dialog: dialog.accept())
        page.get_by_role('button', name='Delete folder', exact=True).click()
        expect(page.locator('#catalog-alert')).to_contain_text('Folder is not empty')

        page.once('dialog', lambda dialog: dialog.accept())
        page.get_by_role('button', name='Delete log', exact=True).click()
        page.once('dialog', lambda dialog: dialog.accept())
        page.get_by_role('button', name='Delete folder', exact=True).click()
        expect(page.locator('#catalog-folder-filter')).not_to_contain_text('Landings')

    def test_duplicate_folder_names_show_paths_and_select_the_intended_ids(self):
        aircraft_a = self.store.create_folder('Aircraft A')
        flights_a = self.store.create_folder('Flights', aircraft_a['id'])
        aircraft_b = self.store.create_folder('Aircraft B')
        flights_b = self.store.create_folder('Flights', aircraft_b['id'])
        page = self.page
        page.goto(self.base_url)
        page.get_by_role('button', name='Catalog', exact=True).click()

        folder_filter = page.locator('#catalog-folder-filter')
        folder_filter.select_option(label='Logs / Aircraft B / Flights', timeout=1000)
        self.assertEqual(folder_filter.input_value(), str(flights_b['id']))

        page.get_by_role('button', name='Create folder', exact=True).click()
        parent = page.locator('#folder-parent')
        parent.select_option(label='Logs / Aircraft B / Flights', timeout=1000)
        self.assertEqual(parent.input_value(), str(flights_b['id']))
        page.get_by_role('button', name='Cancel', exact=True).click()

        page.get_by_role('button', name='Analyzer', exact=True).click()
        self.upload(name='nested.bin')
        page.locator('#save-to-catalog').click()
        log_folder = page.locator('#log-folder')
        log_folder.select_option(label='Logs / Aircraft B / Flights', timeout=1000)
        self.assertEqual(log_folder.input_value(), str(flights_b['id']))
        page.get_by_label('Log name', exact=True).fill('Nested flight')
        page.get_by_role('button', name='Save log', exact=True).click()
        expect(page.locator('#save-to-catalog')).to_have_text('Open in catalog')
        saved = self.store.list_logs()[0]
        self.assertEqual(saved['folder_id'], flights_b['id'])

        page.get_by_role('button', name='Catalog', exact=True).click()
        page.get_by_role('button', name='Edit log', exact=True).click()
        log_folder.select_option(label='Logs / Aircraft A / Flights', timeout=1000)
        self.assertEqual(log_folder.input_value(), str(flights_a['id']))
        page.get_by_role('button', name='Save changes', exact=True).click()
        expect(page.locator('#catalog-list')).to_contain_text('Nested flight')
        self.assertEqual(self.store.get(saved['id'])['folder_id'], flights_a['id'])

    def test_open_in_catalog_clears_filters_and_selects_the_log_folder(self):
        target_folder = self.store.create_folder('Target flights')
        other_folder = self.store.create_folder('Other flights')
        other_temporary = self.store.ingest(
            io.BytesIO(b'other'), 'other.bin', lambda path, name: {'success': True}
        )[1]
        self.store.save(other_temporary['id'], {
            'display_name': 'Other log',
            'folder_id': other_folder['id'],
            'tags': ['other'],
        })
        page = self.page
        page.goto(self.base_url)
        self.upload(name='target.bin', contents=b'target')
        page.locator('#save-to-catalog').click()
        page.get_by_label('Log name', exact=True).fill('Target log')
        page.get_by_label('Tags', exact=True).fill('target')
        page.locator('#log-folder').select_option(label='Logs / Target flights')
        page.get_by_role('button', name='Save log', exact=True).click()
        expect(page.locator('#save-to-catalog')).to_have_text('Open in catalog')

        page.get_by_role('button', name='Catalog', exact=True).click()
        page.locator('#catalog-name-filter').fill('missing')
        page.locator('#catalog-tag-filter').select_option('other')
        page.locator('#catalog-folder-filter').select_option(str(other_folder['id']))
        expect(page.locator('#catalog-list')).to_contain_text('No saved logs')

        page.get_by_role('button', name='Analyzer', exact=True).click()
        page.get_by_role('button', name='Open in catalog', exact=True).click()
        expect(page.locator('#catalog-name-filter')).to_have_value('')
        expect(page.locator('#catalog-tag-filter')).to_have_value('')
        expect(page.locator('#catalog-folder-filter')).to_have_value(
            str(target_folder['id'])
        )
        expect(page.locator('#catalog-breadcrumb')).to_have_text('Logs / Target flights')
        expect(page.locator('#catalog-list')).to_contain_text('Target log')

        page.locator('#catalog-name-filter').fill('missing')
        expect(page.locator('#catalog-list')).to_contain_text('No saved logs')
        page.get_by_role('button', name='Analyzer', exact=True).click()
        page.get_by_role('button', name='Catalog', exact=True).click()
        expect(page.locator('#catalog-name-filter')).to_have_value('missing')
        expect(page.locator('#catalog-list')).to_contain_text('No saved logs')

    def test_view_text_warns_then_opens_searchable_plain_text_tab(self):
        page = self.page
        text_requests = []
        self.context.on(
            'request',
            lambda request: text_requests.append(request.resource_type)
            if '/text' in request.url else None,
        )
        page.goto(self.base_url)
        self.upload(name='sample.log', contents=DATAFLASH_TEXT)
        page.get_by_role('button', name='Save to catalog', exact=True).click()
        page.get_by_label('Log name', exact=True).fill('Text sample')
        page.get_by_role('button', name='Save log', exact=True).click()
        page.get_by_role('button', name='Catalog', exact=True).click()

        card = page.locator('.catalog-log', has_text='Text sample')
        log_id = self.store.list_logs()[0]['id']
        original = card.get_by_role('link', name='Download original')
        download_text = card.get_by_role('link', name='Download text')
        self.assertEqual(original.get_attribute('href'), f'/api/logs/{log_id}/download')
        self.assertEqual(download_text.get_attribute('href'), f'/api/logs/{log_id}/text?download=1')
        view = card.get_by_role('link', name='View text')
        self.assertEqual(view.get_attribute('href'), f'/api/logs/{log_id}/text')
        self.assertEqual(view.get_attribute('target'), '_blank')
        self.assertEqual(view.get_attribute('rel'), 'noopener')

        dialogs = []
        page.once('dialog', lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
        view.click()
        self.assertEqual(dialogs, [
            'Large text dumps can exhaust browser memory. Open anyway? '
            'Wait for loading to finish before Ctrl+F or Cmd+F.'
        ])
        self.assertEqual(len(self.context.pages), 1)

        page.once('dialog', lambda dialog: dialog.accept())
        with self.context.expect_page() as popup_info:
            view.click()
        popup = popup_info.value
        popup.wait_for_load_state()
        self.assertEqual(popup.locator('pre').count(), 1)
        self.assertTrue(popup.evaluate("window.find('42.5')"))
        self.assertEqual(text_requests, ['document'])


if __name__ == '__main__':
    unittest.main()

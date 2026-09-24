# Private team log catalog implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the team an explicitly saved, organized log catalog with original downloads, text downloads, and native browser text search.

**Architecture:** Keep the current Flask analyzer and HTML dashboard. A SQLite-backed catalog module owns metadata, private files, locks, and cleanup; Flask routes and a cleanup command share it. Text export streams the installed converter's output without storing a second permanent file.

**Tech Stack:** Python 3.12, Flask, SQLite, Linux file locks, pymavlink, existing vanilla JavaScript, Docker, nginx, host cron. Tests use unittest and a real Chromium browser through development-only Playwright; mypy checks new Python modules.

**Spec:** `docs/superpowers/specs/2026-09-25-log-catalog-design.md`

**Status:** Awaiting written-plan review. The user selected subagent-driven execution and requested implementation, review, and commits on `feat/log-catalog`. No product implementation has started.

## Global Constraints

- One shared password protects the dashboard, catalog, APIs, and downloads.
- Keep the 500 MB request limit and in-memory analysis. Hold the analyzed file temporarily on the server for one hour; Save retains that same copy without another upload. Sweep expired files every five minutes and at startup.
- Saved logs remain until explicitly deleted.
- Use plain-text limits of 200 characters for display names, 120 for folder names, 40 per tag with at most 20 tags per log, and 10,000 for notes.
- Render names, folders, tags, notes, and log content as text. Preserve the Events XSS fix.

Additional binding scope: keep existing analysis output and legacy upload files; no accounts, full-text index, text cache, job queue, automatic backups, CD, or unrelated refactoring. Names and folders never become storage paths. No credentials or AI attribution in artifacts. Use `apply_patch` for edits and commit only explicit task files.

## Review Focus

1. Save at the expiry boundary while cleanup runs: exactly one outcome, never deletion of a saved file. Task 1 tests independent store instances and controlled time.
2. Client disconnect or deletion during a download: close the converter and release locks without deleting the saved original. Tasks 2 and 3 test closing streamed responses.
3. Untrusted metadata, messages, or filenames: no executable HTML, path traversal, or header injection. Tasks 1, 2, and 4 test these at their public seams.
4. A failed upload after a previous success: the browser must not save the old log under the impression it is the new upload. Task 4 tests identity reset and repeat-save behavior.
5. A long conversion behind Gunicorn/nginx: no silent timeout-truncated output or full JavaScript copy. Tasks 3 and 5 check streaming, failure markers, and aligned request timeouts.

## Execution scope and verification seams

This plan's approval also confirms the TDD seams below. Write one failing behavioral test, implement that slice, then repeat. Do not bulk-write speculative tests or inspect SQL tables to verify public behavior.

| Seam | What tests observe |
|---|---|
| Catalog store | Temporary-to-saved lifecycle, organization, public metadata, byte-identical files, cleanup and locking using a real temporary SQLite database/directory |
| Flask HTTP | Upload response, validation, same-origin writes, catalog actions, response headers and stream closure through `app.test_client()` |
| Browser | Save dialog, folders/tags/notes, identity changes, native text-tab navigation, inert hostile strings, existing analysis rendering |
| Deployment | Disposable Docker/nginx smoke checks, configuration validation, startup cleanup, persistence across recreation, and documented manual activation |

Proposed workspace: work in place on the requested current feature branch, `feat/log-catalog`. Plan approval confirms this in-place preference under using-git-worktrees; do not create a different branch or switch the main checkout. The four existing untracked `.bin` files under `log-dashboard/uploads` are user data, not test fixtures to remove or commit.

The current repository has no configured static type checker. Add a development requirements file with `-r requirements.txt`, `mypy`, and `playwright`; install it only into the project virtual environment. Use `mypy --follow-imports=silent --ignore-missing-imports --check-untyped-defs` on new Python modules after each task. Do not call `py_compile` a type check. No production dependency is needed for SQLite or text conversion.

Run focused test files during each task. Run the complete dashboard suite once after integration, then only affected tests for review fixes. The unrelated mission-model applications are outside this feature's test scope. The baseline command `.venv/bin/python -m unittest discover -s log-dashboard -p test_uploads.py` passed all 8 tests on 2026-09-25.

Live activation is separate from code implementation. Do not reload the live proxy, choose/change the shared password, alter host cron, replace the running container, merge, or push as part of these task commits. Task 5 supplies and tests deployment support locally. Activation requires the user's authorization; update `/home/willis/SETUP_REPLICATION.md` in that activation task, alongside the actual machine changes, without secret values. Record any pending live acceptance checks explicitly rather than reporting them passed.

## File map

| File | Responsibility |
|---|---|
| `log-dashboard/catalog.py` | SQLite migrations, validation, UUID private files, lifecycle, locks, organization, cleanup CLI |
| `log-dashboard/catalog_routes.py` | HTTP catalog interface, initialization, same-origin write guard, original/text response ownership |
| `log-dashboard/log_text.py` | Closeable streaming adapter for the installed `mavlogdump.py` process |
| `log-dashboard/python_backend.py` | Existing analyzer and legacy routes, revised upload lifecycle, narrowly served catalog JavaScript |
| `log-dashboard/catalog.js` | Catalog controls, dialogs, navigation, rendering, and interaction with existing analysis UI |
| `log-dashboard/log_dashboard.html` | Existing UI plus catalog markup/styles and analysis-selection hooks |
| `log-dashboard/test_catalog.py`, `test_catalog_api.py`, `test_log_text.py`, `test_catalog_browser.py` | Behavioral tests at the agreed seams |
| `log-dashboard/test_uploads.py` | Existing upload regressions updated for one-hour retention |
| `requirements-dev.txt` | Local verification tools, excluded from production installation |
| `compose.yaml`, `Dockerfile`, `log-dashboard/wsgi.py` | Private volume, configuration, startup initialization, finite request timeout |
| `deploy/catalog-nginx.conf`, `deploy/log-catalog.cron`, `README.md` | Manual deployment inputs and operator instructions, no automatic live activation |
| `.gitignore`, `.dockerignore` | Exclude private catalog/test data before any is written |

---

### Task 1: Catalog storage, organization, and cleanup

**Files:** Create `log-dashboard/catalog.py`, `log-dashboard/test_catalog.py`, and `requirements-dev.txt`; modify `.gitignore` and `.dockerignore`.

**Interface:** `CatalogStore(root: Path, *, clock: Callable[[], float] = time.time)` owns its directory, schema, and file lifecycle. `initialize()` creates/migrates the store. `CatalogError(message, status_code)` is the public exception for validation, missing/expired records, and conflicts. Use these methods and return types:

| Method | Behavior/return value |
|---|---|
| `ingest(source: BinaryIO, original_filename: str, analyze: Callable[[str, str], dict]) -> tuple[dict, dict]` | Copy stream into one locked UUID file; run analyzer on its path; return analysis and temporary metadata; remove caught failures |
| `save(log_id: str, metadata: dict) -> dict` | Atomically make an unexpired temporary log saved; repeated Save returns existing saved metadata unchanged |
| `get(log_id: str) -> dict` | Public metadata for a saved log; no internal file key/path |
| `list_logs(*, folder_id: int | None = None, name: str = '', tag: str = '') -> list[dict]` | Only saved logs, newest first; filters combine with AND; omitted folder searches all folders |
| `edit(log_id: str, changes: dict) -> dict` | Update only supplied display_name/folder_id/tags/notes fields, last-save-wins |
| `delete(log_id: str) -> None` | Claim, unlink, and remove one saved log; return conflict if an active operation holds a lock |
| `use_file(log_id: str) -> ContextManager[tuple[Path, dict]]` | Hold a shared lock on an existing saved original and return its path and metadata; release on exit |
| `cleanup() -> dict` | Remove expired, abandoned, or retryable files; return removed/skipped/failed counts; never remove active or saved files |
| `list_folders() -> list[dict]` | Flat parent-linked folder rows, including root ID 1 |
| `create_folder(name: str, parent_id: int = 1) -> dict` | Validate and insert a folder |
| `rename_folder(folder_id: int, name: str) -> dict` | Rename a non-root folder |
| `delete_folder(folder_id: int) -> None` | Delete a non-root empty folder only |
| `list_tags() -> list[str]` | Sorted normalized tags associated with saved logs |

Metadata inputs use `display_name`, `folder_id`, `tags`, and `notes`. Save defaults folder to 1 and optional tags/notes to empty values but requires a valid display name. Public log metadata keys are `id`, `status`, `original_filename`, `size_bytes`, `created_at`, `expires_at`, `saved_at`, `updated_at`, `display_name`, `folder_id`, `tags`, `notes`. Public timestamps are ISO 8601 UTC strings ending in `Z`, or null. Store UTC epoch seconds internally. Folder dictionaries use `id`, `parent_id`, `name`, `created_at`.

- [ ] **Step 1: Prepare ignored storage and verification tools.** Add `log-dashboard/catalog-data/`, `.catalog-test/`, and project-local browser downloads to both ignore files before creating them. Preserve existing patterns. Install development requirements into `.venv`, not system Python.

```text
-r requirements.txt
mypy
playwright
```

- [ ] **Step 2: Start the store seam with a failing save/retention test.** Use a real temporary directory and a supplied clock. The analyzer callback is this module's external input, not a patched internal method. Extend one behavior at a time after each red/green cycle.

```python
import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from catalog import CatalogError, CatalogStore

class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.now = 1_800_000_000.0
        self.root = Path(self.directory.name)
        self.store = CatalogStore(self.root, clock=lambda: self.now)
        self.store.initialize()

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
```

Run `.venv/bin/python -m unittest discover -s log-dashboard -p test_catalog.py`; first expect an import failure because the module does not exist. Then add only the behavior needed for each test.

- [ ] **Step 3: Implement the store and schema, with focused red/green slices.** Use `sqlite3`, `PRAGMA foreign_keys = ON` per connection, a 5-second busy timeout, short `BEGIN IMMEDIATE` write transactions, and transactional schema-version migration. Future unknown schema versions fail initialization. Private directories use 0700 and data files use 0600. Never chmod or import the legacy uploads folder.

The first migration is equivalent to this SQL, plus the required state CHECK constraints described immediately below:

```sql
CREATE TABLE folders (
  id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES folders(id) ON DELETE RESTRICT,
  name TEXT NOT NULL, created_at REAL NOT NULL,
  UNIQUE(parent_id, name),
  CHECK ((id = 1 AND parent_id IS NULL) OR (id <> 1 AND parent_id IS NOT NULL))
);
CREATE TABLE logs (
  id TEXT PRIMARY KEY, file_key TEXT NOT NULL UNIQUE,
  original_filename TEXT NOT NULL, size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
  created_at REAL NOT NULL, status TEXT NOT NULL,
  expires_at REAL, saved_at REAL, updated_at REAL NOT NULL,
  folder_id INTEGER REFERENCES folders(id) ON DELETE RESTRICT,
  display_name TEXT, notes TEXT NOT NULL DEFAULT '',
  CHECK (status IN ('processing', 'temporary', 'saved', 'deleting'))
);
CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE log_tags (
  log_id TEXT NOT NULL REFERENCES logs(id) ON DELETE CASCADE,
  tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  PRIMARY KEY(log_id, tag_id)
);
CREATE INDEX logs_expiry ON logs(status, expires_at);
CREATE INDEX logs_folder_date ON logs(folder_id, saved_at);
CREATE INDEX log_tags_tag ON log_tags(tag_id, log_id);
PRAGMA user_version = 1;
```

Insert root ID 1 with name `Logs`. Add CHECK constraints: processing has no expiry, saved timestamp, folder, or display name; temporary has an expiry and no saved timestamp/folder/display name; saved requires saved timestamp/folder/nonempty display name and null expiry; deleting may retain prior metadata. Application operations prevent tag associations before Save. Use only parameterized statements, including filters. Name search treats `%` and `_` literally, not as undocumented wildcard syntax. Sibling folder uniqueness is case-sensitive; tags use trimmed case-folded uniqueness.

Create `files/<uuid>.<validated-extension>` with exclusive creation, acquire an exclusive nonblocking `fcntl.flock`, and only then publish `processing`. Copy from the source stream, flush, call the analyzer, and transition to temporary with `expires_at = clock() + 3600` at successful completion. Do not hold a database transaction during parsing. An exception claims deletion and removes the file immediately; a failed unlink retains a deleting row for retry and logs the failure.

For cleanup/delete, acquire a nonblocking exclusive lock before a short transaction rechecks state. Save-versus-cleanup uses a conditional state/expiry update in a write transaction. If unlink succeeds, remove its deleting row and tag links. Retry deleting rows whose files are already gone. Cleanup ignores lock contention, removes abandoned unlocked processing rows, and removes untracked UUID staging files only after one hour and after locking them. Do not scan unrelated directories. A saved row is never a cleanup candidate.

Validate JSON-equivalent types as well as lengths: names/tags must be strings, tags a list of strings, folder IDs real integers rather than booleans, notes a string. Trim names, reject empties, `/`, `\\`, and Unicode control characters in names/tags. Allow line breaks in notes. Missing referenced folders return 404, invalid metadata 400, locked/nonempty/root-operation conflicts 409. Keep file/header sanitization separate from display names.

- [ ] **Step 4: Add the remaining behavioral slices and run the focused file/type check.** Cover idempotent Save, invalid Save preserving its original deadline, same-name uploads not overwriting, literal-name and normalized-tag filtering, folder rename/move/empty deletion, immutable root, errors during source read/analyzer, abandoned processing after a child exits, deletion retry after an injected filesystem unlink error, and independent store instances competing at the deadline. Use real locks and temporary SQLite; inject only time/filesystem failures. Verify active `use_file()` prevents deletion and that closing it permits deletion.

```bash
.venv/bin/python -m unittest discover -s log-dashboard -p test_catalog.py
.venv/bin/python -m mypy --follow-imports=silent --ignore-missing-imports --check-untyped-defs log-dashboard/catalog.py
```

- [ ] **Step 5: Commit only the Task 1 files**, using `feat: add private catalog storage and cleanup`. Report tests with commands/results and the public method contract to the controller. Do not dispatch a reviewer yourself.

### Task 2: Upload lifecycle and catalog HTTP routes

**Files:** Create `log-dashboard/catalog_routes.py` and `log-dashboard/test_catalog_api.py`; modify `log-dashboard/python_backend.py` and `log-dashboard/test_uploads.py`.

**Consumes:** Task 1's `CatalogStore`, `CatalogError`, metadata shapes, and `use_file()` lock context. Existing `python_backend.run_analysis(filepath, filename)` returns `success`, `filename`, `summary`, `analyses`, `frame`.

**Produces:** `init_catalog(app: Flask, *, store: CatalogStore | None = None) -> CatalogStore`, `get_catalog() -> CatalogStore`, and `register_catalog_routes(app: Flask, analyze: Callable[[str, str], dict]) -> None`. Store instances live under `app.extensions['catalog']`. Tests initialize an injected temporary store; production startup invokes `init_catalog(app)`. Initialization migrates then sweeps before serving. A lazy call from `get_catalog()` is a fallback for the direct development entrypoint, not a per-request sweep.

`app.config['CATALOG_DIR']` defaults to `BASE_DIR/catalog-data` and may read the same environment variable. `PUBLIC_ORIGIN` is an optional app/environment configuration value; use it when deployed, otherwise the current request's origin for loopback development. Do not create default catalog data at module import, before tests can configure it.

Register catalog routes with an analyzer callback that resolves the backend function at call time: `register_catalog_routes(app, lambda path, name: run_analysis(path, name))`. This keeps both upload and saved-log reanalysis on the same existing analyzer seam.

- [ ] **Step 1: Write the first HTTP lifecycle test and observe red.** Configure a temporary store and an explicit test origin. Keep the existing upload test's analysis fake at the analyzer seam; use the real store and HTTP handlers.

```python
import io
from unittest.mock import patch

def test_upload_save_and_original_download(self):
    with patch.object(self.backend, 'run_analysis', return_value={
        'success': True, 'filename': 'flight.bin',
        'summary': {}, 'analyses': {}, 'frame': {},
    }):
        uploaded = self.client.post('/api/upload',
            headers={'Origin': 'http://localhost'},
            data={'file': (io.BytesIO(b'flight bytes'), 'flight.bin')})
    self.assertEqual(uploaded.status_code, 200)
    temporary = uploaded.get_json()
    self.assertIn('expires_at', temporary)
    response = self.client.post(f"/api/logs/{temporary['log_id']}/save",
        headers={'Origin': 'http://localhost'}, json={
            'display_name': 'First hover', 'folder_id': 1,
            'tags': ['hover'], 'notes': 'Watch yaw',
        })
    self.assertEqual(response.status_code, 200)
    log_id = response.get_json()['log']['id']
    download = self.client.get(f'/api/logs/{log_id}/download')
    self.assertEqual(download.data, b'flight bytes')
    self.assertIn('attachment', download.headers['Content-Disposition'])
    download.close()
```

Put this method in `CatalogAPITests(unittest.TestCase)`. In `setUp`, use `TemporaryDirectory`, `CatalogStore`, and `init_catalog(backend.app, store=store)`; preserve/restore modified config/extensions in cleanup. Set `self.backend`, `self.client`, and `self.store`. Do not write catalog fixtures to the legacy uploads directory.

- [ ] **Step 2: Implement the upload replacement and JSON routes.** Keep the existing upload route in `python_backend.py` so current mocks/hooks remain valid. Replace the TemporaryDirectory block with:

```python
analysis, temporary = get_catalog().ingest(
    file.stream, file.filename, run_analysis,
)
return jsonify({**analysis, 'log_id': temporary['id'],
                'expires_at': temporary['expires_at']}), 200
```

Preserve no-file/type checks, the 500 * 1024 * 1024 request limit, and analysis output. Re-raise Werkzeug HTTP exceptions before the broad upload handler so oversized requests reach the 413 handler instead of becoming 500. Convert catalog exceptions to `{'error': message}` and their status; log unexpected exceptions and return a non-path-bearing 500 message.

| Route | Success response |
|---|---|
| `POST /api/logs/<id>/save` | 200 `{'success': true, 'log': metadata}` |
| `GET /api/logs?folder_id=&name=&tag=` | 200 object with `success: true`, `logs: list[log metadata]`; omitted folder searches all |
| `GET /api/logs/<id>` | 200 `{'success': true, 'log': metadata}` |
| `PATCH /api/logs/<id>` | 200 `{'success': true, 'log': metadata}` |
| `DELETE /api/logs/<id>` | 200 `{'success': true}` |
| `POST /api/logs/<id>/analyze` | Existing analysis shape plus `log_id`; saved source preserved on failure |
| `GET /api/logs/<id>/download` | Original bytes as an attachment |
| `GET /api/folders` | 200 object with `success: true`, `folders: list[folder metadata]` |
| `POST /api/folders` | 201 object with `success: true`, `folder: folder metadata`; body `name`, `parent_id` |
| `PATCH /api/folders/<int:id>` | 200 object with `success: true`, `folder: folder metadata`; body `name` |
| `DELETE /api/folders/<int:id>` | 200 `{'success': true}` |
| `GET /api/tags` | 200 object with `success: true`, `tags: list[str]` |

Empty or non-object JSON, unknown metadata keys, malformed filters, and wrong types return 400 without partial mutations. The root folder is ID 1. Missing records return 404; identifiable expired Save returns 410. Both 404 and 410 will mean re-upload in the Save UI.

- [ ] **Step 3: Add same-origin protection and lifetime-safe original downloads.** Remove unrestricted `CORS(app)` and its unused backend import. For POST/PATCH/DELETE/PUT API requests, compare a present Origin exactly with `PUBLIC_ORIGIN` or `request.host_url.rstrip('/')`. Reject null/foreign origins. If Origin is absent, require `Sec-Fetch-Site: same-origin`; absent or cross-site metadata is rejected. CLI examples must send the configured Origin. No new application login/session system: nginx supplies the shared password gate in Task 5.

Hold `use_file()` through the complete original response, including client cancellation. Safe response ownership follows this pattern:

```python
from contextlib import ExitStack
from flask import send_file
from werkzeug.utils import secure_filename

stack = ExitStack()
try:
    path, metadata = stack.enter_context(get_catalog().use_file(log_id))
    stem = secure_filename(metadata['display_name']) or f'log-{log_id}'
    response = send_file(path, as_attachment=True,
                         download_name=stem + path.suffix,
                         mimetype='application/octet-stream')
    response.direct_passthrough = False
    response.call_on_close(stack.close)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response
except BaseException:
    stack.close()
    raise
```

New routes expose no storage filenames/paths. Keep `/api/files` and `/api/analyze/<filename>` behavior for legacy files; do not add an uploads/static directory route. Verify the legacy filename lookup remains constrained to `UPLOAD_FOLDER`.

- [ ] **Step 4: Adapt regressions and verify focused files/typechecking.** Successful-upload tests now expect UUID files retained for one hour; remove only the old immediate-success deletion assertions and TemporaryDirectory monkeypatch. Retain original-name display, traversal/sentinel, duplicate filename, failure cleanup, allowlist, and parser-close tests. Adapt partial-write injection to the stream/filesystem seam because ingest no longer calls `FileStorage.save`. Test actual 413 behavior with a temporarily reduced test limit, not a 500 MB fixture. Add CRUD/error/filter tests, rejected foreign/null/missing Origin, permitted same-origin requests, reanalysis failure preserving saved bytes, and unbuffered download close permitting deletion.

```bash
.venv/bin/python -m unittest discover -s log-dashboard -p test_uploads.py
.venv/bin/python -m unittest discover -s log-dashboard -p test_catalog_api.py
.venv/bin/python -m mypy --follow-imports=silent --ignore-missing-imports --check-untyped-defs log-dashboard/catalog.py log-dashboard/catalog_routes.py
```

- [ ] **Step 5: Commit only Task 2 files**, using `feat: expose saved-log catalog and upload lifecycle`. Report schema/response deviations before returning DONE; do not silently change Task 1's interface.

### Task 3: On-demand text download and browser response

**Files:** Create `log-dashboard/log_text.py` and `log-dashboard/test_log_text.py`; modify `log-dashboard/catalog_routes.py` and `log-dashboard/test_catalog_api.py`.

**Consumes:** `CatalogStore.use_file()`, the Task 2 error/response conventions, and the installed `mavlogdump.py` script. Original data never passes through filtered analysis DataFrames.

**Produces:** `open_text_dump(path: Path) -> TextDump`, a closeable byte iterator. It starts and primes conversion before returning, raises `TextDumpError` if conversion fails before output, yields a clear error marker if failure follows output, and terminates/reaps its child on `close()`. `TextDump` exposes iteration and `close()`, not process details. `GET /api/logs/<id>/text` returns inline text; `?download=1` changes disposition only. Both require an existing saved log.

- [ ] **Step 1: Write and fail a route/format test using a real converter input.** Use a tiny DataFlash text fixture whose custom `TEST` message is not in the analyzer's target list:

```python
DATAFLASH_TEXT = (
    b'FMT, 128, 89, FMT, BBnNZ, Type,Length,Name,Format,Columns\n'
    b'FMT, 129, 15, TEST, Qf, TimeUS,Value\n'
    b'TEST, 1000000, 42.5\n'
)

def test_view_is_plain_text_and_download_has_same_contents(self):
    _, temporary = self.store.ingest(
        io.BytesIO(DATAFLASH_TEXT), 'sample.log',
        lambda path, name: {'success': True},
    )
    saved = self.store.save(temporary['id'], {'display_name': 'Text sample'})
    route = f"/api/logs/{saved['id']}/text"
    view = self.client.get(route)
    download = self.client.get(route + '?download=1')
    self.assertEqual(view.status_code, 200)
    self.assertEqual(view.mimetype, 'text/plain')
    self.assertEqual(view.headers['X-Content-Type-Options'], 'nosniff')
    self.assertIn('TEST', view.get_data(as_text=True))
    self.assertEqual(view.data, download.data)
    self.assertIn('inline', view.headers['Content-Disposition'])
    self.assertIn('attachment', download.headers['Content-Disposition'])
    view.close()
    download.close()
```

Use the Task 2 HTTP fixture, with a temporary real store. Compare this small output with invoking the installed converter directly on the same stored original under the same timezone, using `use_file()` to obtain its path; do not derive expected dump text from the new adapter itself.

- [ ] **Step 2: Implement the external-process adapter.** Locate the script in `sysconfig.get_path('scripts')`, with an explicit missing-converter error. Start Python unbuffered with fixed arguments, stdout piped, and no shell:

```python
command = [sys.executable, '-u', str(converter_path), str(path)]
process = subprocess.Popen(command, stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL)
```

Read bounded 64 KiB byte chunks. Prime the first read before returning a success response. If no bytes and the child exits nonzero, raise an error mapped to 500. If any bytes were emitted and the exit code is nonzero, append `\n[TEXT DUMP INCOMPLETE: conversion failed]\n`, log the failure, and close. Never collect a whole dump or write a permanent text file. In `close()`, close stdout, terminate a still-running child, wait up to 2 seconds, then kill/reap only that child if needed. Cleanup also runs on exceptions and response cancellation.

- [ ] **Step 3: Bind response ownership and headers.** Acquire `use_file()` and open the converter in an `ExitStack` before returning the response; register stack closure for normal end and early disconnect, including closing a response before consuming it. Attach `text/plain; charset=utf-8`, `X-Content-Type-Options: nosniff`, `Cache-Control: no-store`, and `X-Accel-Buffering: no`. Use Werkzeug's header helpers for inline/attachment dispositions and the same sanitized-name policy as original downloads, with `.txt` extension. Never interpolate dump bytes into HTML.

```python
response = Response(dump, content_type='text/plain; charset=utf-8')
response.headers['Content-Disposition'] = disposition_header
response.headers['X-Content-Type-Options'] = 'nosniff'
response.headers['Cache-Control'] = 'no-store'
response.headers['X-Accel-Buffering'] = 'no'
response.call_on_close(stack.close)
```

`dump` is the `TextDump` iterator registered with `stack.callback(dump.close)`; `disposition_header` is created using Werkzeug header serialization, not string concatenation of raw display names. Arrange cleanup so natural iterator exhaustion also closes the lock, not only an explicitly closed test response.

- [ ] **Step 4: Test cancellation, failure, and hostile text at the process/HTTP seams.** Inject fake process behavior only at the subprocess boundary to exercise failure before first output, failure after a chunk, and termination escalation. Add an actual small-process cancellation check to ensure the child exits; assert through completion, not private adapter fields. Confirm a saved source remains readable after all failures, early response closure allows deletion, and HTML-looking log strings remain literal response bytes with plain-text headers. Reading the first chunk must not consume or assemble the entire remaining iterator.

```bash
.venv/bin/python -m unittest discover -s log-dashboard -p test_log_text.py
.venv/bin/python -m unittest discover -s log-dashboard -p test_catalog_api.py
.venv/bin/python -m mypy --follow-imports=silent --ignore-missing-imports --check-untyped-defs log-dashboard/log_text.py log-dashboard/catalog_routes.py
```

- [ ] **Step 5: Commit the Task 3 files**, using `feat: stream log text downloads and browser views`. Record any converter fixture format corrections with the observed CLI output; do not replace the converter with a filtered approximation.

### Task 4: Catalog UI and native browser workflow

**Files:** Create `log-dashboard/catalog.js` and `log-dashboard/test_catalog_browser.py`; modify `log-dashboard/log_dashboard.html` and `log-dashboard/python_backend.py` only to serve `/catalog.js` from its fixed path.

**Consumes:** Task 2's exact JSON shapes and Task 3's direct text URLs. The existing dashboard has `currentData`, `currentSummary`, `showOverviewPage()`, `showPage(pageId, evt)`, and an anonymous `#log-file` change handler.

**Produces:** A top-level Analyzer/Catalog selector, explicit Save dialog, catalog listing/editor/folder controls, and the three per-log file actions. `window.logCatalog` exposes `uploaded(result)`, `cleared()`, and `opened(metadata)` for the existing analyzer code. A small `displayAnalysis(result)` function in the HTML owns the existing successful-analysis render sequence, shared by upload and catalog reanalysis. Do not rewrite plot functions or the Events renderer.

- [ ] **Step 1: Add one failing browser Save test.** Install Chromium into a project-ignored browser-cache directory using Playwright, not system packages. The Python unittest starts the Flask application on an ephemeral loopback port using a temporary real catalog and stops it during cleanup. Inject a fixed result at the existing analyzer seam for upload/reanalysis; keep browser requests, the upload stream, catalog HTTP, and SQLite real.

```python
def test_save_prompts_then_catalog_displays_inert_metadata(self):
    page = self.page
    page.goto(self.base_url)
    page.locator('#log-file').set_input_files({
        'name': 'flight.bin', 'mimeType': 'application/octet-stream',
        'buffer': b'fixture log',
    })
    page.get_by_role('button', name='Save to catalog', exact=True).click()
    name = page.get_by_label('Log name', exact=True)
    self.assertRegex(name.input_value(),
                     r'^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}Z-[0-9a-f]{8}$')
    name.fill('Hover <img src=x onerror=alert(1)>')
    page.get_by_label('Tags', exact=True).fill('hover, landing')
    page.get_by_label('Notes', exact=True).fill('Check yaw after landing')
    page.get_by_role('button', name='Save log', exact=True).click()
    page.get_by_role('button', name='Catalog', exact=True).click()
    self.assertEqual(page.locator('#catalog-list img').count(), 0)
    self.assertIn('Hover <img', page.locator('#catalog-list').inner_text())
```

Configure the upload fixture through the existing analyzer seam so the upload really creates a temporary record. Block external Plotly loading or replace its browser global with a minimal test adapter so the smoke test does not depend on a CDN. Keep a fixture with the summary/analysis fields the unchanged overview renderer needs.

- [ ] **Step 2: Implement the UI and its analysis-selection hooks.** Change the existing API constant to `'/api'`. Add selector buttons outside `.nav-btn` so analysis gating does not disable the catalog. Use native `<dialog>` elements and ordinary form controls. Add Save/expiry controls inside the upload area and a catalog panel independent of the analysis `.page` list.

```javascript
function displayAnalysis(result) {
    currentData = result.analyses;
    currentSummary = result.summary;
    document.querySelectorAll('.nav-btn').forEach(button => {
        button.disabled = false;
        button.classList.remove('active');
        button.setAttribute('aria-selected', 'false');
    });
    document.querySelectorAll('.page').forEach(page => page.classList.remove('active'));
    document.getElementById('overview').classList.add('active');
    const overviewTab = document.getElementById('tab-overview');
    overviewTab.classList.add('active');
    overviewTab.setAttribute('aria-selected', 'true');
    hideAlert('global-alert');
    showOverviewPage();
}
```

Call `window.logCatalog.cleared()` when a new upload starts and when it fails; disable Save while upload or Save is pending. On success call `displayAnalysis(data)` then `window.logCatalog.uploaded(data)`. A successful Save calls `opened(savedMetadata)` and replaces the action with Open in catalog. Repeat clicks cannot create a second saved entry. Canceling the dialog changes nothing. A failed Save keeps edits for retry unless expired/missing, in which case prompt re-upload. Server deadlines are authoritative.

Default names use current UTC date/time plus the first 8 hex characters of the UUID, for example `2026-09-25_14-32-18Z-a1b2c3d4`. Populate folder choices from the flat parent-linked folder response and show a breadcrumb. Tags are comma-separated input, trimmed before submission; backend normalization remains authoritative. Save and Edit share the form; Edit uses PATCH. Refresh does not resurrect temporary selections or extend deadlines.

Render a simple list with name search, one tag filter, current-folder filter and an All folders option. Offer create/rename/delete-empty folder, rename/edit/move log, analyze, and confirmed log deletion. Root rename/delete controls are unavailable. Reanalysis switches to Analyzer and calls `displayAnalysis(result)`; a failure leaves the catalog original untouched and reports an error.

- [ ] **Step 3: Add direct file links and safe rendering.** Use `createElement`, `textContent`, and properties for all returned strings. Do not build catalog HTML with interpolated innerHTML. File URLs contain only encoded server log IDs; new tabs use `rel="noopener"`.

```javascript
const textLink = document.createElement('a');
textLink.textContent = 'View text';
textLink.href = `/api/logs/${encodeURIComponent(log.id)}/text`;
textLink.target = '_blank';
textLink.rel = 'noopener';
textLink.addEventListener('click', event => {
    if (!window.confirm('Large text dumps can exhaust browser memory. '
        + 'Open anyway? Wait for loading to finish before Ctrl+F or Cmd+F.')) {
        event.preventDefault();
    }
});
```

Download original uses `/api/logs/<id>/download`; Download text uses `/api/logs/<id>/text?download=1`. These are normal links, never `fetch().text()`, a Blob, or a custom viewer. Label actions exactly Download original, Download text, View text. Serve only `/catalog.js`, not the whole `BASE_DIR`, so `catalog-data` cannot become a static directory.

- [ ] **Step 4: Run browser slices and JavaScript syntax checking.** Cover Save cancellation, repeated Save, failed upload clearing prior identity, expiry/re-upload, editing/moving a log, folder restrictions, tag/name filtering, deletion confirmation, and hostile names/tags/notes/events. Test View text's warning and new plain-text tab; use `window.find()` to verify the loaded known text and manually verify Ctrl+F in the acceptance run. Check text/raw links remain available and no request retrieves a dump into dashboard JavaScript. Confirm existing analysis tabs still render with the saved-log reanalysis fixture.

```bash
node --check log-dashboard/catalog.js
.venv/bin/python -m unittest discover -s log-dashboard -p test_catalog_browser.py
.venv/bin/python -m mypy --follow-imports=silent --ignore-missing-imports --check-untyped-defs log-dashboard/catalog.py log-dashboard/catalog_routes.py log-dashboard/log_text.py
```

Also syntax-check the HTML's inline script with Node by extracting it in a temporary test process; do not add a bundler or frontend framework. If Chromium cannot start without system installation, report that exact limitation; do not claim browser verification from a string-only DOM mock.

- [ ] **Step 5: Commit only Task 4 files**, using `feat: add catalog save and browser log workflow`. Report actual browser checks separately from any still-manual native Ctrl+F check.

### Task 5: Docker startup, private deployment support, and end-to-end verification

**Files:** Modify `compose.yaml`, `Dockerfile`, `log-dashboard/wsgi.py`, `log-dashboard/catalog.py`, and `README.md`; create `deploy/catalog-nginx.conf`, `deploy/log-catalog.cron`, and `log-dashboard/test_catalog_deployment.py`.

**Consumes:** `init_catalog(app)` from Task 2, `CatalogStore.cleanup()` from Task 1, and the file endpoints from Tasks 2/3. No task changes production credentials or services automatically.

**Produces:** Persistent private mount, startup migrations/sweep, `python catalog.py cleanup --data-dir PATH` operator command, and reviewed manual auth/cron/backup instructions. CLI exits 0 after a healthy sweep and nonzero for cleanup failures, prints only removed/skipped/failed counts, and shares the same store implementation rather than SQL duplication.

- [ ] **Step 1: Add failing CLI/startup tests.** In a temporary store create an expired temporary upload and a saved original. Run the CLI in a subprocess and reopen the store to verify only saved data remains. Add a test that startup initialization invokes a sweep before a request and that a failed/incompatible migration prevents startup. Observe red before adding the entrypoint.

```python
completed = subprocess.run([
    sys.executable, str(Path(__file__).with_name('catalog.py')),
    'cleanup', '--data-dir', str(self.root),
], capture_output=True, text=True, check=False)
self.assertEqual(completed.returncode, 0, completed.stderr)
self.assertEqual(self.store.get(self.saved_id)['display_name'], 'Keep')
```

Construct expired data using a store clock more than 3600 seconds before real time, not SQL mutation. Saved data is created through ingest/save. Tests use process-local arguments/environment, not live host cron.

- [ ] **Step 2: Add startup and Compose configuration.** Initialize before Gunicorn accepts requests:

```python
# log-dashboard/wsgi.py
from python_backend import app
from catalog_routes import init_catalog

init_catalog(app)

if __name__ == '__main__':
    app.run()
```

Preserve `127.0.0.1:18765:8000`, the existing uploads mount, and healthcheck. Add the private mount and configured public origin:

```yaml
environment:
  CATALOG_DIR: /app/catalog-data
  PUBLIC_ORIGIN: ${PUBLIC_ORIGIN:-http://localhost:18765}
volumes:
  - ./log-dashboard/uploads:/app/uploads
  - ./log-dashboard/catalog-data:/app/catalog-data
```

Document setting `PUBLIC_ORIGIN=https://vtollogs.williserdman.com` for the live deployment; never infer origin from arbitrary forwarded headers. Add local `.env` to Git/Docker ignores if using that documented configuration file. Environment variables here configure the application, not Codex.

Use a single Gunicorn worker with a finite 600-second request timeout, matching the existing nginx timeout. Long-running conversion still occupies that worker; concurrency/background processing is deferred. Verify this limitation in README and do not claim streaming alone makes unlimited conversions safe. Keep health probes local and unauthenticated behind loopback.

```dockerfile
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--timeout", "600", "wsgi:app"]
```

- [ ] **Step 3: Write deployable proxy/cron inputs and exact operator steps.** The nginx file is an include snippet for the existing HTTPS server, not a replacement for certificate configuration:

```nginx
auth_basic "Team log catalog";
auth_basic_user_file /etc/nginx/log-catalog.htpasswd;
```

The server-level auth applies to the existing catch-all location, dashboard, scripts, legacy APIs, catalog APIs, and downloads. No public health bypass. Keep the current HTTP-to-HTTPS redirect. Use the Task 3 `X-Accel-Buffering: no` response for text streaming; do not proxy-cache private downloads.

The cron template is a user-crontab line, without a username field:

```cron
*/5 * * * * cd /home/willis/projects/log_reading_summer_26 && /usr/bin/docker compose exec -T log-reader python catalog.py cleanup --data-dir /app/catalog-data # log-catalog-cleanup
```

Document verifying the Docker binary path and replacing only an existing `log-catalog-cleanup` marker, preserving every unrelated job. Document password creation with `sudo htpasswd -B -c /etc/nginx/log-catalog.htpasswd team` only when the file does not exist; omit `-c` for later password updates. Run it interactively, never pass the password as a command argument. Use root/www-data ownership and mode 0640 for the hash file. Never include a sample credential or password hash in Git.

Activation order is explicit: obtain authorization and a team password; install the hash/auth snippet; run `nginx -t`; reload nginx and prove unauthenticated requests are denied; then enable the private data mount, deploy the branch, install the named cron entry, and verify authenticated actions. No code task executes those live steps. At activation, update `/home/willis/SETUP_REPLICATION.md` with actual paths, environment-variable names, date, checks, and backup/deployment commands.

README replaces the immediate-success-deletion description with the one-hour temporary lifecycle and indefinite saved retention. Explain the three file actions, browser-memory warning, manual deployment, no CD, expected 404/410 Save expiry, and how to run focused tests/typechecking. Describe a consistent manual backup: disable the named cleanup job, stop this application, archive the entire catalog-data directory with permissions, restart the application, restore the named cleanup job, and verify. Restore the database and files together while stopped; never overwrite live catalog data without separate confirmation. Persistence is not backup.

- [ ] **Step 4: Verify the complete local feature, then run the full dashboard suite once.** Use a disposable Docker project or `docker run` with a temporary data volume and an ephemeral loopback port; never use `docker compose up` against the live project during implementation. Set `PUBLIC_ORIGIN` to the disposable browser/proxy origin, including its assigned port. Use a temporary nginx instance with a generated test-only credential outside Git to verify 401 before login and success after login, the catch-all auth coverage, and text response headers. Do not print the credential. Recreate the disposable application container with the same private volume and verify saved records/bytes survive and startup removes expired uploads. Execute cleanup with no browser traffic.

Upload the tracked `log-dashboard/uploads/00000005.BIN` through this disposable app, Save with edited metadata, reanalyze, compare original-download bytes, and compare the streamed dump against CLI output. The existing larger untracked sample may be read for a representative streaming test but must not be modified or committed. Verify the proxy completes the conversion within the configured limit and does not report a truncated output as complete. Stop/remove only test-owned containers and recoverable temporary artifacts.

```bash
.venv/bin/python -m unittest discover -s log-dashboard -p test_catalog_deployment.py
.venv/bin/python -m mypy --follow-imports=silent --ignore-missing-imports --check-untyped-defs log-dashboard/catalog.py log-dashboard/catalog_routes.py log-dashboard/log_text.py
node --check log-dashboard/catalog.js
docker compose config --quiet
.venv/bin/python -m unittest discover -s log-dashboard -p 'test_*.py'
git diff --check
```

The full dashboard suite includes the browser file and requires its local browser/runtime setup. Keep that test visible if unavailable; do not silently skip it and report full verification. Do not rerun the entire suite during each review cycle; run files covering changed code.

- [ ] **Step 5: Commit the Task 5 files**, using `feat: prepare persistent private catalog deployment`. Report local evidence and explicitly pending live activation checks. Do not claim nginx, cron, or the live website changed.

## Review and finish

For each task, the controller records BASE before dispatch, generates its brief and review package with the SDD scripts, and obtains both spec and quality verdicts. Resume the original implementer for scoped fixes and preserve the ledger across compaction. Do not run implementers concurrently in the shared checkout. Read-only independent exploration and the final review axes may run in parallel.

At completion use the requested `code-review` skill with `df2ea40` as the fixed point and the linked catalog spec as authority. This explicitly pins the review range for that skill. Run its Standards and Spec axes in parallel as the final whole-branch review, using SDD's broad review rubric and diff package, rather than adding a second duplicate broad review cycle. There is no issue tracker in scope; the on-disk spec is the supplied work item. Carry deferred findings/rulings into the review. After review fixes, run the affected tests and commit them on the same branch.

Use the finishing-a-development-branch skill without treating this request as permission to merge, push, publish, or deploy. Preserve user data and report any activation work still awaiting authorization. Hand off the branch, passing verification evidence, limitations, and all SDD rulings.

## Plan self-check

The store task covers schema, metadata, file identity, lifecycle, and cleanup races. Routes cover the existing analyzer, original download ownership, validation and origin checks. Text export covers full unfiltered conversion, stream closure, and inert text. Browser work covers Save/organize/reopen/download/view without a custom search subsystem. Deployment support covers private persistence, startup, auth, scheduling, consistent backup, and a separate authorized live activation. Every Review Focus item has a named owning test step. Cross-task identifiers are `log_id` in analysis/upload responses and `id` inside catalog metadata, with `display_name` consistently used for edits.

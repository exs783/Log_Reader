# Device ingestion implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. The user must approve this plan and choose execution first. Respect their instruction not to initiate code-review cycles.

**Goal:** Let a device submit a log once, or retry it, and receive the ID of one permanently saved catalog entry.

**Architecture:** Extend the existing SQLite/file store with direct saved ingestion and content-hash deduplication. Add one multipart Flask endpoint with upload-only Bearer authentication, plus an exact nginx Basic Auth exception. Keep browser uploads and analysis unchanged.

**Tech stack:** Existing Python 3.12, Flask/Werkzeug, SQLite, Linux file locks, unittest, Docker Compose, and nginx. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-28-device-ingest-design.md`

## Global constraints

- `POST /api/ingest`; one `INGEST_API_KEY`; unset or empty disables ingestion. Authenticate before reading form/file data. Browser Basic Auth and same-origin protection remain on other routes.
- Accept `.bin` and `.log`, case-insensitively. Keep `500 * 1024 * 1024` as the whole-request limit. Stream storage; never analyze, generate text, or decompress during ingestion.
- Optional `display_name`, `tags`, and `notes`; limits are 200 characters, 20 tags of 40 characters, and 10,000 characters. Tags are a JSON array encoded in a form string. No device folder selection.
- Default to root-level `Device uploads` and a UTC timestamp plus short ID name. `201` means new; `200` means duplicate. Both return only `success`, `log_id`, and `duplicate`. Existing metadata never changes on retry.
- Deduplicate saved device imports only. Preserve current files, browser uploads, and schema-v1 records. No client/UI changes, live deployment, key provisioning, review cycles, or broad refactoring in this execution scope.

## Review focus

These are focused test cases, not authorization for a separate review cycle.

1. Two devices finish identical uploads together: one saved record and file, one ID. Task 1.
2. An existing version-1 catalog is opened: migrate without losing metadata or bytes; reject version 3. Task 1.
3. A user moves a log or renames the inbox: retries leave it alone; new content finds or recreates the default folder. Task 1.
4. A bad key is accompanied by a valid Origin or an oversized body: reject before multipart parsing, never accept browser headers as authentication. Task 2.
5. Werkzeug raises an HTTP error inside the catalog error wrapper: preserve `400`/`413`, do not turn them into `500`. Task 2. Nginx auth isolation is also an explicit activation check in Task 3.

## Files and sequence

| Files | Responsibility |
|---|---|
| `log-dashboard/catalog.py` | Schema migration, direct saved ingestion, hash uniqueness, cleanup integration. |
| `log-dashboard/catalog_routes.py`, `log-dashboard/python_backend.py` | Bearer guard, exact origin exemption, request/response contract, environment loading. |
| `log-dashboard/test_catalog.py`, new `log-dashboard/test_fixtures/catalog_v1.sql` | Store tests and a frozen genuine version-1 schema. |
| `log-dashboard/test_catalog_api.py`, `log-dashboard/test_catalog_deployment.py` | Endpoint, WSGI, migration compatibility, and deployment configuration tests. |
| `compose.yaml`, `deploy/catalog-nginx.conf`, `README.md` | Runtime key wiring, nginx exception, uploader contract and manual activation instructions. |

Execute Tasks 1, 2, and 3 in order. At execution start, read the spec and use the
worktree skill to isolate implementation from `feat/log-catalog`; include the
approved spec and this plan. Leave the four untracked files in
`log-dashboard/uploads` untouched. Do not stage those files or copy production
catalog data into tests. Reuse the existing root checkout's `.venv` when working
in a new worktree; installing dependencies is unnecessary.

Test commands below run from the implementation checkout's `log-dashboard`
directory. Set `INGEST_TEST_PYTHON` to the existing interpreter without modifying
Codex or machine configuration:

```bash
INGEST_TEST_PYTHON=/home/willis/projects/log_reading_summer_26/.venv/bin/python
```

## Task 1: Save and deduplicate device imports

**Files:** Modify `catalog.py`, `test_catalog.py`, and the future-schema test in
`test_catalog_deployment.py`. Create `test_fixtures/catalog_v1.sql`, all under
`log-dashboard/`.

**Interfaces:** Consume existing `_name`, `_tags`, file-lock and processing/deleting
cleanup helpers. Produce
`CatalogStore.ingest_saved(self, source: BinaryIO, original_filename: str, metadata: dict) -> tuple[str, bool]`,
returning `(log_id, duplicate)`. `metadata` accepts only `display_name`, `tags`,
and `notes`; validation raises `CatalogError` with status 400. Keep `initialize()`,
`ingest()`, `save()`, and public metadata interfaces unchanged.

- [ ] **Step 1: Write focused failing store tests.** Use `CatalogTests` and its
  temporary store/fixed clock. Freeze the pre-change version-1 DDL, root folder,
  and `PRAGMA user_version = 1` in the SQL fixture. Seed a saved file, tags, and
  notes directly in that fixture database before invoking the new initializer.
  Do not manufacture v1 by changing only the version of a new v2 database.

  | Test | Required assertions |
  |---|---|
  | `test_device_ingest_saved_and_retry_unchanged` | First receipt is `(id, False)`; retry is `(id, True)` even with a different filename. `assertEqual(store.get(id), before_retry)`, including edits and timestamps. `assertIsNone(saved['expires_at'])`; assert normalized tags, default timestamp name, private UUID path, original bytes, and persistence after reopening/cleanup. Browser ingestion of the same bytes remains a separate record. |
  | `test_device_ingest_concurrent_duplicates` | Use two store instances and synchronized readers with `ThreadPoolExecutor`. `assertEqual(len({r[0] for r in receipts}), 1)`; `assertEqual(sorted(r[1] for r in receipts), [False, True])`; exactly one saved row and file. Changing bytes produces a different ID. |
  | `test_device_ingest_folder_and_delete_lifecycle` | Rename the default folder, move the first log, then retry: unchanged ID/folder. New content recreates root-level `Device uploads`. Delete a saved import, resend its contents, and `assertNotEqual(new_id, deleted_id)`. |
  | `test_device_ingest_failure_cleanup` | Inject a read failure: no saved row or partial file. Extend existing active-upload, unlink-failure, and child-abandonment tests to cover `ingest_saved`; cleanup skips the locked upload and later removes abandoned work. After invalid metadata, `assertEqual(store.list_logs(), before)`. |
  | `test_initialize_migrates_v1_without_data_loss` | `assertEqual(version, 2)`; metadata and original bytes match their pre-migration values; `ingest_sha256` is null on old records; repeat initialization succeeds. Update both existing future-schema fixtures from version 2 to 3. |

- [ ] **Step 2: Run the store tests and confirm the expected failure.**

  ```bash
  "$INGEST_TEST_PYTHON" -m unittest test_catalog test_catalog_deployment
  ```

  Expected failure: missing `ingest_saved` or missing schema-v2 migration, not a
  broken interpreter or fixture. Existing unrelated tests remain unchanged.

- [ ] **Step 3: Implement schema v2 and `ingest_saved`.** Keep initial v1 creation,
  then apply an explicit transaction that adds nullable `ingest_sha256 TEXT`,
  creates the index below, and sets `PRAGMA user_version = 2`. Reject versions
  greater than 2. No backfill or rewrites of existing log records.

  ```sql
  CREATE UNIQUE INDEX logs_ingest_sha256 ON logs(ingest_sha256)
  WHERE ingest_sha256 IS NOT NULL AND status = 'saved';
  ```

  Validate metadata before staging. Default names follow
  `YYYY-MM-DD_HH-MM-SSZ-<first-eight-ID-characters>`. Reuse the existing locked
  `processing` lifecycle; stream in bounded chunks and update SHA-256 as bytes
  are copied. Flush the completed file before publishing. Never hold SQLite's
  write lock while reading the source.

  Use `BEGIN IMMEDIATE` for the final hash lookup and saved-state transaction.
  If a saved hash exists, discard only the new staging record/file. Otherwise
  find or insert the root-level `Device uploads` folder, then commit size, hash,
  metadata, tags, and saved timestamps together. Use existing deletion/cleanup
  helpers on failures; retain retryable cleanup state if unlink fails. Do not
  return a deleted, processing, or temporary entry as a duplicate.
  End the transaction before calling cleanup helpers that open their own SQLite
  connections; keep the staging file locked until that cleanup finishes.

- [ ] **Step 4: Rerun the Step 2 command.** Expected result: all store and
  deployment tests pass, including the existing browser-storage lifecycle tests.

- [ ] **Step 5: Commit only Task 1 files.** Commit message:
  `feat: store and deduplicate device log imports`.

## Task 2: Add the authenticated multipart endpoint

**Files:** Modify `log-dashboard/catalog_routes.py`,
`log-dashboard/python_backend.py`, and `log-dashboard/test_catalog_api.py`.

**Interfaces:** Consume Task 1's `ingest_saved`. Add
`_require_ingest_key() -> None` in `catalog_routes.py`, raising `CatalogError`
on failure. Register `ingest_log() -> ResponseReturnValue` for
`POST /api/ingest` within `register_catalog_routes`. Load `INGEST_API_KEY` from
the environment into Flask config once, alongside existing runtime settings.

- [ ] **Step 1: Write failing endpoint tests.** Extend `CatalogAPITests` config
  save/restore with `INGEST_API_KEY` and `MAX_CONTENT_LENGTH`; use the public
  fixture value `test-ingest-key`, never a real key. Add test helper
  `_device_upload(self, content: bytes = b'device bytes', filename: str = 'device.BIN', **fields: str) -> TestResponse`
  that posts multipart with Bearer auth and no Origin. Import `TestResponse`
  from `werkzeug.test`. Close download/text responses to release file locks.

  | Test | Required assertions |
  |---|---|
  | `test_device_upload_receipt_and_existing_actions` | `assertEqual(first.status_code, 201)`; response keys exactly `{'success', 'log_id', 'duplicate'}`; retry status 200 and same ID. Patch `run_analysis` and `open_text_dump` during ingest and assert neither is called. Afterwards verify catalog listing, byte-identical original download, existing analyze routing, and text download using `DATAFLASH_TEXT`. |
  | `test_device_upload_key_required_before_body` | Missing, wrong, malformed, or Basic credentials return 401 even with valid Origin. Empty/unset config returns 503. With `MAX_CONTENT_LENGTH=256` and a 1,024-byte file, wrong key still returns 401; patch multipart loading to assert it is not called. No saved entries appear. |
  | `test_device_key_does_not_bypass_browser_origin` | Bearer-only POSTs to `/api/upload`, `/api/folders`, and `/api/logs/<id>/save` without Origin return 403. Existing `_upload`/`_save` still work with their browser Origin. Do not claim these Flask tests prove nginx Basic Auth. |
  | `test_device_upload_validation_and_size` | Assert the normal configured limit is `500 * 1024 * 1024`. Valid-key request with the reduced limit returns JSON 413. Missing file, `.zip`, malformed/non-array tags, unknown `folder_id`, blank/over-200 name, over-20/over-40 tags, and over-10,000 notes return 400. Invalid duplicate metadata also returns 400 without mutation. |
  | `test_device_upload_storage_error_is_generic` | Inject storage failure: `assertEqual(response.status_code, 500)`; JSON error contains neither injected private path nor fixture token; no success receipt. Malformed multipart errors retain JSON 400 rather than becoming 500. |

- [ ] **Step 2: Run the API tests and confirm expected failures.**

  ```bash
  "$INGEST_TEST_PYTHON" -m unittest test_catalog_api
  ```

  Expected failures: endpoint missing or blocked by the current origin guard.

- [ ] **Step 3: Implement the route and key guard.** Read only
  `app.config.get('INGEST_API_KEY')` in the guard so an empty config cannot fall back
  to an ambient secret. Use `secrets.compare_digest` on encoded tokens. Exempt
  only exact `POST /api/ingest` from `require_same_origin`; its route must call
  the guard before accessing `request.files` or `request.form`.

  Accept exactly one `file` and the three optional fields. Reject unexpected or
  repeated fields. Decode `tags` with `json.loads`; pass the resulting metadata
  to `ingest_saved`. Validate the supported filename extension without treating
  it as a filesystem path. Map `(id, duplicate)` to the specified receipt and
  200/201. Update `_catalog_errors` to preserve Werkzeug `HTTPException` status
  codes with JSON errors, including 400 and 413. Keep generic storage errors
  generic and never log request authorization headers or form bodies.

- [ ] **Step 4: Run focused API and browser-upload regression tests.**

  ```bash
  "$INGEST_TEST_PYTHON" -m unittest test_catalog_api test_uploads
  ```

  Expected result: all tests pass; browser uploads still analyze and expire
  rather than automatically saving or deduplicating.

- [ ] **Step 5: Commit only Task 2 files.** Commit message:
  `feat: add upload-only device ingestion endpoint`.

## Task 3: Wire deployment configuration and document the uploader contract

**Files:** Modify `compose.yaml`, `deploy/catalog-nginx.conf`, `README.md`, and
`log-dashboard/test_catalog_deployment.py`. No live files or services change.

**Interfaces:** Compose provides `INGEST_API_KEY: ${INGEST_API_KEY:-}` to Flask.
The nginx server include retains browser Basic Auth and adds only
`location = /api/ingest` with `auth_basic off`. The device uses the Task 2 contract.

- [ ] **Step 1: Write failing configuration tests.** In
  `CatalogDeploymentTests`, override `INGEST_API_KEY` in subprocess environments
  with a fixture key or empty string, never inherit a production value. Add
  `test_wsgi_ingest_key_configuration`: a temporary WSGI catalog accepts the
  fixture key and returns 503 when unset. Add
  `test_ingest_compose_and_nginx_configuration`: inspect Compose's JSON output
  with a dummy key, and assert `environment['INGEST_API_KEY'] == 'test-ingest-key'`,
  unchanged storage mounts and loopback port. Inspect the snippet and assert
  exactly one `location = /api/ingest`, one `auth_basic off`, unchanged outer
  Basic Auth directives, and no new `location /`.

- [ ] **Step 2: Run deployment tests and confirm expected failures.**

  ```bash
  "$INGEST_TEST_PYTHON" -m unittest test_catalog_deployment
  ```

  Expected failure: missing Compose key and nginx exception. Configuration
  tests are not a substitute for the live auth checks documented below.

- [ ] **Step 3: Add the runtime wiring and operator documentation.** In the exact
  nginx location preserve `proxy_pass http://127.0.0.1:18765`, HTTP/1.1, Host and
  existing forwarded headers, and 600-second read/send timeouts. Explicitly
  forward `Authorization`; disable proxy caching. Retain the server's existing
  `client_max_body_size 500M`; do not add a conflicting server-level directive.
  All other paths retain inherited Basic Auth.

  Document a minimal multipart request with a non-secret key placeholder,
  optional fields, 201/200 receipts, 400/401/413/500/503 handling, and safe retries.
  Explain device-only deduplication and that successful ingestion does not
  validate log contents. Update the old README claim that the snippet contains
  only Basic Auth directives.

  Document private key provisioning outside the checkout at
  `/home/willis/.config/log-reader/ingest.env`, mode 0600, passed via Compose
  `--env-file`; no real value in history, examples, output, or git. Preserve the
  explicit production `PUBLIC_ORIGIN`. Recreating the container rotates the key.
  No file is provisioned during implementation.

  The activation checklist must require separate deployment authorization,
  pause only the existing cleanup job, stop the application, and make a
  consistent database/files backup before migration. Restore the cleanup job
  after activation. Note that image-only rollback cannot open schema v2 and
  that restoring old data requires confirmation and preserving newer data.
  Validate/reload nginx, deploy the image with private configuration, and prove
  unauthorized browser paths remain 401 while valid-key ingest returns 201/200.
  Also test that the ingest key cannot read `/api/logs` through nginx. Check
  persistence across restart using an owned disposable fixture, then remove
  only that fixture. Update `/home/willis/SETUP_REPLICATION.md` during activation,
  not code preparation. Use the wizard skill only if human credential/sudo steps
  are needed. Never use Docker privileges to bypass missing sudo access.

- [ ] **Step 4: Run the final focused checks.** From `log-dashboard`:

  ```bash
  "$INGEST_TEST_PYTHON" -m unittest test_catalog test_catalog_api test_uploads test_catalog_deployment
  "$INGEST_TEST_PYTHON" -m mypy --follow-imports=silent --ignore-missing-imports --check-untyped-defs catalog.py catalog_routes.py
  ```

  From the implementation checkout root:

  ```bash
  docker compose config --quiet
  git diff --check
  ```

  Expected: all focused tests pass and all commands exit 0. No Playwright
  installs, broad test expansion, production uploads, or secret dumps. Report
  live deployment/auth verification as pending, not completed.

- [ ] **Step 5: Commit only Task 3 files and hand off.** Commit message:
  `feat: wire and document device ingestion deployment`. Report tested local
  behavior, commit IDs, and the separate deployment prerequisites. Do not merge,
  push, open a PR, or deploy unless requested for this feature.

## Deferred work

Keep the spec's deferrals: browser-wide deduplication, per-device keys, device
folder selection, resumable transfers, queues, quotas, and automated backups.
Existing worker/proxy limits remain. Do not add review agents unless the user
explicitly asks for a review.

## Execution choice

Recommend native execution for three sequential tasks that share the same
store and route interfaces. Subagent-driven execution remains available if
the user chooses it; preserve the no-unrequested-review constraint either way.
Wait for approval of this written plan and the execution choice before coding.

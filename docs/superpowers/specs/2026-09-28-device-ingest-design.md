# Device uploads into the log catalog

Status: written spec approved on 2026-09-28. Implementation plan pending approval.

## Purpose and scope

A team-owned device already pulls logs from a Pixhawk and normally has internet.
Its uploader is handled separately. Define and implement only the receiving
backend so one request adds a saved log to the existing private team catalog.

Success means a device can upload without the browser password, safely retry a
request, and get a catalog ID. Team members can then open, annotate, move,
analyze, and download the log through the existing browser workflow.

Use a dedicated ingestion endpoint. Reusing browser Upload followed by Save
would require two requests and run analysis before saving. A queue or separate
ingestion service adds infrastructure this flow does not need.

## HTTP contract

`POST /api/ingest` over HTTPS, with `Authorization: Bearer <key>` and a
`multipart/form-data` body. No browser password or Origin header is required.

| Form field | Contract |
|---|---|
| `file` | Required single file. Case-insensitive `.bin` or `.log` extension. |
| `display_name` | Optional text. Use the catalog's existing name validation and 200-character limit. |
| `tags` | Optional JSON array encoded as a form string, for example `["pixhawk","field-test"]`. Existing normalization and limits apply: 20 tags, 40 characters each. |
| `notes` | Optional plain text, at most 10,000 characters. |

Reject unknown fields and invalid metadata. Omitted tags and notes default to
an empty list and empty string. Omitted display names use server UTC time plus
a short log-ID suffix, matching the browser's timestamp-based naming pattern.
An explicitly empty name is invalid.

Retain the existing 500 MB request limit, currently `500 * 1024 * 1024` bytes.
This includes multipart overhead, not just file bytes. Accept only the two
listed extensions and never decompress uploads. Do not run analysis or generate
text during ingestion.
A successful receipt confirms storage, not that the bytes parse as a valid log.

Responses contain only the receipt, not other catalog metadata:

```json
{"success": true, "log_id": "<uuid>", "duplicate": false}
```

A new entry returns `201`. Identical contents return `200`, the existing
`log_id`, and `duplicate: true`. Validate the request's metadata even on retries,
but never apply it to an existing entry. A lost success response is safe to retry.

Use `400` for malformed requests, unsupported extensions, or invalid metadata;
`401` for missing or incorrect keys; `413` for oversized requests; and `503`
when ingestion is not configured. Unexpected storage failures return `500`
without internal paths or credentials. Application errors use `{"error": "..."}`.
Proxy-generated errors may have non-JSON bodies. Devices may retry transport
failures and server errors; unchanged invalid requests will not succeed.

## Authentication and deployment

Configure one shared, upload-only `INGEST_API_KEY`. An unset or empty key
disables ingestion. Check the Bearer token with a constant-time comparison
before the application reads the multipart body. Never log the token, put it in
a URL, commit it, or include it in API responses.

Nginx currently applies browser Basic Auth to the whole HTTPS server. Add an
exact-match location for `/api/ingest` that disables only that Basic Auth gate
and forwards the Authorization header to Flask. Preserve the existing proxy
target, request size limit, and timeouts. All other paths keep Basic Auth.

Flask exempts only `POST /api/ingest` from the browser same-origin check,
and that handler always requires the upload key. Possessing this key does not
authorize listing, reading, editing, deleting, analysis, or downloads. Do not
add a global API-key bypass, browser key storage, or cross-origin browser access.

Pass `INGEST_API_KEY` through Compose from private deployment configuration.
Keep the current `PUBLIC_ORIGIN` and persistent `CATALOG_DIR` bind mount.
Document key replacement by changing the configured value and recreating the
container. No key-management UI or per-device credentials in this MVP.

## Storage, deduplication, and organization

Extend `CatalogStore` with a device-ingest operation. Reuse private UUID-based
storage filenames, existing file locks, metadata validation, and cleanup.
Original filenames, display names, and virtual folder names never become paths.

1. Create and lock a private file and register a `processing` record using the
   existing lifecycle. Stream bytes to it in bounded chunks while computing
   SHA-256. Do not hold a database write transaction during the transfer.
2. After the complete file is written, use a short transaction to check its
   hash against saved device imports. A match wins regardless of filename,
   tags, or current folder. Remove the new staging file and processing record,
   and return the existing ID without updating any existing metadata.
3. For new content, find or create `Device uploads` directly under the catalog
   root. Commit the file's hash, validated metadata, tags, and `saved` state
   together, with no expiry. Return success only after storage and commit finish.

Add nullable `ingest_sha256` to `logs` in schema version 2, with a unique index
for non-null hashes on saved rows. Transactional duplicate detection plus this
constraint must prevent simultaneous identical imports from creating two saved
entries. Upgrade the existing version-1 database without replacing its records
or files. Fresh databases reach the same schema. Browser uploads leave this
column null; do not backfill or deduplicate historical or browser uploads.

The device cannot choose a folder. Users can move imported logs normally, and
retries do not move them back. Resolve the default folder by its root-level name
for each new entry. If it is renamed or removed, the next new import recreates
`Device uploads`; existing entries remain where users put them.

Saved imports persist until explicit deletion. After deletion, uploading the
same contents creates a new entry. Retrying an import cannot overwrite an
existing name, tags, notes, timestamps, or folder.

Caught upload failures remove their partial files and processing rows. Reuse
the existing retryable cleanup behavior if removal fails or a process stops.
Cleanup must skip active uploads and never expire saved imports. No changes to
the browser's one-hour temporary-upload lifecycle are needed.

## Implementation boundaries and verification

Keep changes in the existing Flask configuration/routes and `CatalogStore`,
plus Compose, the nginx deployment snippet, focused tests, and API/setup docs.
No frontend changes are required. Existing analysis and original/text download
routes operate on the new saved records without special device handling.

Focused verification must cover:

1. A valid keyed upload without Origin returns `201`, appears in the default
   folder with expected metadata, survives reopening storage, and downloads
   byte-for-byte. Ingestion does not call analysis.
2. Repeated and simultaneous identical uploads return one saved ID and retain
   metadata after browser edits or moves. Different contents create another
   entry; re-upload after deletion creates a new ID.
3. Missing, incorrect, and disabled keys fail. The ingestion key cannot bypass
   browser authentication or the origin guard on other routes. Verify the exact
   nginx exception when deploying.
4. Invalid fields/extensions and oversized uploads fail. Interrupted writes do
   not publish saved entries; cleanup removes abandoned files without deleting
   active or saved files.
5. A version-1 fixture migrates without data loss. Existing browser upload/save
   behavior still works. Use temporary fixtures, not production user records.

Deployment later requires a consistent database/files backup before migration,
private key provisioning, nginx validation/reload, and container recreation.
The old application rejects schema version 2, so image-only rollback is not
supported. Update `/home/willis/SETUP_REPLICATION.md` with configuration names
and verification, never secret values. This spec does not deploy anything or
modify machine configuration.

Deferred work includes browser-wide deduplication, folder selection by devices,
per-device key management, resumable transfers, queues, quotas, and automated
backups. Persistent storage is not a backup. Existing proxy limits and request
worker concurrency still apply; no throughput or maximum-size transfer guarantee
is added by this endpoint.

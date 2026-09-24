# Private team log catalog

Status: catalog and text-dump design approved through the request to implement
using subagent-driven development. The implementation plan awaits review.

## Purpose and agreed scope

Give the team an archive of flight logs they can find, annotate, analyze again,
and download. Keep the existing Flask dashboard and make the smallest useful
addition to it.

The agreed requirements are:

1. One shared password protects the dashboard, catalog, APIs, and downloads.
2. Analysis alone does not permanently save a log. An explicit Save action
   opens a naming dialog with an editable timestamp-based default.
3. Saved logs support freely named folders and subfolders, tags, and plain-text
   notes. Team members can rename and move logs and edit their metadata.
4. Team members can reopen saved logs for analysis, download their original
   bytes or a derived text dump, and view the text in a browser using Ctrl+F.
   Saved logs remain until explicitly deleted.
5. Keep the 500 MB request limit and in-memory analysis. Hold the analyzed file
   temporarily on the server for one hour; Save retains that same copy without
   another upload. Sweep expired files every five minutes and at startup.

SQLite will have an explicit relational schema. Original files remain on disk.
Normal upload or analysis failures remove their files immediately. Cleanup
also handles abandoned work after process termination.

## User flow

The hosted dashboard has an Analyzer view and a Catalog view. The existing
analysis tabs remain in the Analyzer. A simple folder listing and breadcrumbs
are sufficient for the catalog; a drag-and-drop file manager is unnecessary.

After a successful upload and analysis, the UI retains the returned log ID and
expiration time and enables Save to catalog. It displays how long the file is
available. A new upload replaces that selection, but the previous temporary
file keeps its original deadline. Refreshing or closing the page does not save
anything or extend the deadline.

Save immediately opens a dialog containing the proposed name, folder picker,
tags, and notes. The default name uses the current UTC timestamp and a short
suffix from the log ID, for example `2026-09-25_14-32-18Z-a1b2c3d4`.
It is an editable display name, not the storage filename. Users may leave tags
and notes empty and choose the catalog root. Cancel leaves the file temporary.
Confirmation saves the metadata and changes the existing record to saved.
The UI then offers Open in catalog instead of creating another saved copy.

The catalog lists saved logs by folder, with name search and tag filtering.
Each entry supports opening its analysis, editing metadata, moving to another
folder, downloading, and deleting. Opening reruns the existing analysis;
analysis results are not persistently cached. Deletion requires confirmation.
Original downloads use the display name plus the original supported extension
and return the original bytes as an attachment. Text downloads and browser
viewing are associated with that same catalog entry, as described below.

Folders can be created under the root or another folder and renamed. Only
empty folders can be deleted. Moving whole folder trees is deferred; moving a
log between folders is included. The fixed catalog root cannot be renamed or
deleted.

## Text dump and browser viewing

Include this small addition in the catalog MVP. Each saved log offers:

1. Download original, returning the unchanged uploaded file.
2. Download text, returning a `.txt` dump of that log.
3. View text, opening the dump in a new browser tab for native Ctrl+F or Cmd+F.

Use the installed `mavlogdump` converter's normal text output without
message-type filtering. Read the original log, not the analysis DataFrames,
which omit message types. Both text actions use the same conversion path and
log ID; the text is a derived representation, not another catalog entry.
No schema changes, upload-time conversion, permanent text copies, or cache
are needed. Renaming the log changes the suggested download name; deleting
the original removes the ability to generate its dump.

Generate and stream text on request without assembling the whole dump in
server memory or a JavaScript Blob. View text navigates directly to a
same-origin endpoint serving `text/plain; charset=utf-8`, with inline
disposition and `X-Content-Type-Options: nosniff`. Download text uses attachment
disposition with a sanitized `.txt` filename. Both use `Cache-Control: no-store`
and the same password gate as original downloads. Never interpret dump content
as HTML or insert it into the dashboard DOM.

Keep the original file locked for the lifetime of conversion, release it on
completion or disconnect, and stop any converter process when the response
closes. Use fixed converter arguments and the internal file path, never a
user-supplied shell command. Conversion failure preserves the saved log.
Reject failures before streaming with an error response; a failure after
streaming starts must clearly mark the output incomplete rather than silently
presenting it as a complete dump.

Native browser search is the entire search feature for this MVP. It searches
loaded text, so users should wait for the dump to finish loading for complete
results. Show a warning before View text: the dump may be larger than its
500 MB source limit, and opening or searching it may exhaust browser memory.
Downloading remains available without opening a text tab. This is an accepted
MVP limitation, not a guarantee that every allowed log is comfortable to view.
Conversion also consumes server CPU and occupies a request worker; no job
queue, custom text viewer, server-side search, or full-text index is included.

## Temporary-file lifecycle

The application uses one private file per upload, named with its generated
UUID and validated `.bin` or `.log` suffix. Display names and virtual folder
names never become disk paths.

| State | File and record behavior |
|---|---|
| `processing` | Upload/analysis owns the file. It is absent from catalog listings. |
| `temporary` | Analysis succeeded. `expires_at` is completion time plus 3,600 seconds. |
| `saved` | Save committed the name, folder, tags, and notes. Expiration is cleared. |
| `deleting` | Removal has been claimed; Save is no longer allowed. Cleanup can retry it. |

Save rejects a temporary log at or after its deadline using server time, even
if the next cleanup sweep has not run. An expired or removed upload produces
an expiration response, and the UI
asks the user to upload again. Invalid metadata leaves the temporary file and
its original deadline intact. Repeating a successful Save returns the existing
saved record; later metadata changes use the edit operation.

SQLite transactions make the Save-versus-expiration decision atomic. A cleanup
operation must recheck state and deadline before claiming removal; it cannot
delete a record that Save has already made permanent. Save changes metadata
only and does not move or duplicate the raw file.

Active file operations hold an advisory file lock on the Linux deployment.
Cleanup skips files still in use. File operations acquire their lock before
opening a short write transaction; parsing never holds a database write lock.
Manual deletion of a file in active use returns a conflict without removing it.
A process crash releases its file lock. The next sweep can then remove an
abandoned `processing` record and retry `deleting` records. Untracked staging
files are removed only when older than one hour and not locked by an active
upload. Create and lock a staging file before publishing its processing record.

Caught upload/analysis errors remove their files immediately. If removal itself
fails, retain a retryable deletion record and log the failure. Deletion uses
the same claim, unlink, and record-removal path for manual and timed cleanup.

The cleanup command runs at application startup and every five minutes through
the host's existing cron service. A healthy scheduler therefore removes an
unused temporary upload within roughly 60 to 65 minutes after analysis.
Downtime can delay physical removal, but an expired file cannot be saved.
Startup runs the sweep before serving requests. Scheduled cleanup runs even
when nobody visits the dashboard.

## Relational schema

Use Python's SQLite support, parameterized SQL, explicit migrations, and
foreign-key enforcement on every connection. Keep write transactions short
and use a bounded busy timeout. The application fails clearly if a migration
cannot complete; it must not silently serve against an incompatible schema.

| Table | Columns and relationships |
|---|---|
| `folders` | `id`, `parent_id`, `name`, `created_at`. Parent references `folders`. A fixed root row is the only row with no parent. Sibling names are unique. |
| `logs` | UUID `id`, unique `file_key`, `original_filename`, `size_bytes`, `created_at`, `status`, `expires_at`, `saved_at`, `updated_at`, `folder_id`, `display_name`, `notes`. Folder references `folders`; saved records require a folder and display name. |
| `tags` | `id`, unique normalized `name`. Trim and case-fold tag names so `Hover` and `hover` identify one tag. |
| `log_tags` | `log_id`, `tag_id`, with a composite primary key and foreign keys to both tables. Removing a log removes its tag associations. |

Use UTC epoch timestamps internally and return UTC timestamps in API responses.
Processing records have no save deadline; temporary records require one;
saved records require `saved_at` and no deadline. Deleting records preserve
enough metadata to finish removal after a crash. Temporary records have no
folder, tags, or saved display name.

Index temporary expiration lookup, saved-log folder/date listing, and reverse
tag lookup. Folder deletion is restricted while it has children or logs.
Display names are editable and need not be unique; UUIDs protect file identity
and prevent overwrites. Use versioned SQL migrations recorded with SQLite's
schema version. No user or ownership tables are needed for the shared login.

Metadata edits use last-save-wins semantics in this MVP. Revision history and
simultaneous-edit conflict resolution are deferred.

## Code and API boundaries

Keep the existing parser and analysis functions. Put catalog persistence and
file lifecycle operations in one small module, separate from Flask handlers.
It owns migrations, IDs, metadata queries, state transitions, file locking,
downloads' file lookup, and cleanup. Both the web handlers and the cleanup
command use that module. The UI receives IDs and metadata, never storage paths.

The HTTP contract is:

1. `POST /api/upload` keeps the existing analysis response and adds the temporary
   log ID and expiry. `POST /api/logs/<id>/save` accepts name, folder, tags, and
   notes without a second file upload.
2. `GET /api/logs` lists only saved entries, with optional folder, name, and tag
   filters. `GET /api/logs/<id>` returns a saved entry's metadata.
3. `POST /api/logs/<id>/analyze` reruns analysis on a saved file.
   `GET /api/logs/<id>/download` returns its original bytes as an attachment.
   `GET /api/logs/<id>/text` streams the text dump inline; `?download=1`
   switches only the disposition to a text attachment. Both require a saved log.
4. `PATCH /api/logs/<id>` edits metadata or changes folder.
   `DELETE /api/logs/<id>` removes the file and its catalog record.
5. `GET/POST /api/folders` lists or creates folders;
   `PATCH/DELETE /api/folders/<id>` renames or deletes an empty folder.
   `GET /api/tags` supplies available tags; Save/Edit creates new tags as needed.

Return validation errors for invalid metadata, 404 for unknown records, 410 for
expired temporary records when still identifiable, and 413 for oversized
requests. An expired record already removed by cleanup can return 404; both
404 and 410 on Save tell the UI that re-upload is needed. Parser failures must
not leave catalog entries. Failed reanalysis of a saved log preserves that
saved file and its metadata.

Legacy filename-based endpoints remain password-protected for existing files.
The new catalog uses only ID-based routes. Existing files under
`log-dashboard/uploads` are left untouched and are not automatically imported.
Team members can upload and explicitly save them through the new flow.

## Private access and rendering

Use HTTP Basic authentication at the existing nginx HTTPS proxy, with fixed
username `team` and one shared password. This uses the browser's login prompt
and needs no account-management UI. All team members have the same catalog
permissions. Store only the password hash in a protected host file outside the
repository; never embed credentials in JavaScript, URLs, documentation, or Git.

The HTTPS proxy must gate the entire dashboard and every API/download route.
Docker remains bound to host loopback. Its internal healthcheck stays local
and does not need the team password. Enable the password gate before exposing
any persistent catalog data.

Serve the dashboard and API from the same origin and use relative `/api` URLs.
Remove unrestricted CORS. Require same-origin browser writes so another site
cannot mutate the catalog through cached Basic-auth credentials. Cross-origin
standalone dashboard copies are outside this MVP.

Render names, folders, tags, notes, and log content as text. Preserve the Events
XSS fix. Use plain-text limits of 200 characters for display names, 120 for
folder names, 40 per tag with at most 20 tags per log, and 10,000 for notes.
Trim names; reject empty names, path separators, and control characters.
Notes allow line breaks and have no HTML or Markdown rendering.
File names in download headers must be sanitized independently of
the unchanged original file bytes. Do not expose the file directory through a
static-file route. Files and the database need private filesystem permissions.

## Persistence and deployment

Add a private host directory at `log-dashboard/catalog-data`, bind-mounted as
`/app/catalog-data`. It contains `catalog.sqlite3` and a flat `files/` directory
holding UUID-named files. The existing uploads mount stays in place for legacy
files. Exclude the new data directory from Git and Docker build context before
writing any catalog data.

Run migrations and startup cleanup before accepting requests. Add one named
five-minute cron entry that invokes the cleanup command in the running
container; preserve existing cron jobs. Keep the existing single application
worker for this MVP. No queue, additional database service, or background
thread inside Flask is required.

Document a manual backup procedure that stops the application and cleanup
job, copies the entire private data directory, and restarts both. Restoring the
database and files must use the same backup. Automated backups are deferred.
The deployment instructions must distinguish persistence from backup.

Implementation will update the README and `/home/willis/SETUP_REPLICATION.md`
with the authentication file location, data directory, cleanup schedule,
backup procedure, deployment commands, audit date, and verification results.
Record no secret values. Deployment remains a manual Docker rebuild; this
feature does not add continuous deployment.

## Focused acceptance checks

1. Upload a real log, analyze it, then Save with an edited name, folder, tags,
   and notes. Confirm one raw file and one saved record; retrying Save creates
   no duplicates. Reopen it and download byte-identical original contents.
   Confirm text view and download match the converter's normal output,
   including a message type outside the analysis selection, without retaining
   text files. Check conversion failure and cancellation release resources
   without deleting the saved log.
2. Exercise expiration with a controlled clock, Save at the deadline, and
   cleanup competing with Save. Saved files survive; expired unsaved files,
   abandoned processing work, and deletion retries are removed safely.
3. Confirm folder navigation, name/tag filtering, metadata edits, moving logs,
   empty-folder restrictions, and explicit file deletion. Existing legacy logs
   remain untouched.
4. Check that unauthenticated public browsing, API access, and downloads are
   denied; authenticated requests succeed. Reject cross-origin writes and
   render hostile metadata, event messages, and dump contents as inert text in
   a real browser. Verify View text opens separately, native find locates a
   known record after loading, and a representative large dump streams through
   the deployed proxy without silently truncating.
5. Retain upload size/type and unsafe-filename checks. Restart/rebuild Docker
   and verify saved records and files remain, startup cleanup works, the cron
   sweep runs without site traffic, and healthchecks still pass.

## Deferred work

Individual accounts, roles, audit trails, threaded comments, automatic log
naming from flight contents, batch uploads, duplicate-content detection,
analysis or text-dump caching, custom text viewers, server-side text search,
full-text indexing, drag-and-drop organization, folder-tree moves, automated
backups, storage quotas, and continuous deployment are outside this MVP.

This spec is the design-review artifact. After written-spec approval, prepare
an implementation plan and have the user select its execution method before
starting implementation.

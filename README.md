# CWRU VTOL Log Reader

Flask dashboard for inspecting ArduPilot `.BIN` and `.log` files.

## Local setup

Create and activate a virtual environment, then install the dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Run the application directly:

```bash
cd log-dashboard
PUBLIC_ORIGIN=http://localhost:8000 gunicorn --bind 127.0.0.1:8000 --workers 1 --timeout 600 wsgi:app
```

Or run it with Docker Compose:

```bash
docker compose up -d --build
```

Open `http://localhost:18765`. The Compose port is loopback-only. Existing legacy
files in `log-dashboard/uploads` stay available through the legacy API. Compose
also starts the `cleanup` service after `log-reader` is healthy. It uses the same
image and catalog-data bind mount. It immediately runs
`python catalog.py cleanup --data-dir /app/catalog-data`, then repeats every five
minutes with an `unless-stopped` restart policy. It exposes no port and does not
receive the ingestion key.

## Catalog behavior

Uploads accept `.bin` and `.log` files up to 500 MB. Analysis remains in memory,
so the browser and application process need enough memory for the uploaded log and
its analysis result. An analyzed upload stays in `log-dashboard/catalog-data` for
one hour. Saving it retains the same server copy indefinitely. Unsaved uploads are
removed by the startup sweep or the five-minute cleanup job after expiry. A Save
attempt can return 404 if cleanup already removed the upload, or 410 if it expired
before cleanup claimed it. Upload the file again in either case.

Saved catalog entries can be renamed, tagged, annotated, moved between folders,
reanalyzed, or explicitly deleted. Their three file actions are:

- **Download original** returns the unchanged uploaded bytes.
- **Download text** streams a converted `.txt` attachment.
- **View text** opens the same plain-text stream in a new tab for browser find.

Text conversion is streamed from the server, but Gunicorn has one worker and a
600-second request timeout. A conversion occupies that worker until it finishes.
The text dump can be much larger than the uploaded file, and **View text** can make
the browser hold that full page in memory. Concurrent processing and background
jobs are not implemented.

Catalog persistence is not a backup. The database and stored files share one
directory and must be copied together while the application is stopped.

## Device ingestion

`POST /api/ingest` accepts one multipart `file` and an
`Authorization: Bearer <ingest-key>` header. The device needs neither the shared
browser password nor an Origin header. The separate `INGEST_API_KEY` permits
ingestion only; it does not grant catalog browsing, editing, deletion, or downloads.
An unset or empty key disables ingestion. Existing browser uploads are unchanged.

| Multipart field | Value |
|---|---|
| `file` | One `.bin` or `.log` file, case-insensitive extension. |
| `display_name` | Optional name, up to 200 characters; omitted generates a UTC timestamp plus short ID. |
| `tags` | Optional JSON-array string, for example `["pixhawk","field-test"]`; at most 20 tags of 40 characters each. |
| `notes` | Optional plain text, up to 10,000 characters. |

The entire request must fit within `500 * 1024 * 1024` bytes, including multipart
overhead. Unknown/repeated fields, blank supplied names, and unsupported extensions
are rejected. Uploads are never decompressed. Ingestion stores bytes without
analysis or text conversion, so a receipt does not guarantee a parseable log.

New content returns HTTP 201:

```json
{"success": true, "log_id": "<uuid>", "duplicate": false}
```

Identical bytes return HTTP 200 with the existing `log_id` and `duplicate: true`,
even if the filename differs. Retrying never changes that entry's metadata or
folder. This applies only to saved device imports, not historical/browser uploads.
After deletion, submitting the same bytes creates a new entry.

New imports go into `Device uploads` under the catalog root and remain until
explicitly deleted. Users can organize them normally. If that folder is renamed
or removed, the next new import recreates it. Open, analyze, original download,
and text download use the existing catalog actions.

Errors use `{"error": "..."}`: 400 for invalid requests, 401 for missing/wrong keys,
413 for oversized requests, 503 when ingestion is disabled, and 500 for storage
failures. Retry transport failures and server errors with a delay; do not change
the original bytes. A lost success response is safe to retry. Proxy errors may
have non-JSON bodies. Correct invalid requests or configuration before retrying
400/401/413/503 indefinitely. Existing proxy limits and single-worker concurrency
still apply; large transfers can encounter tighter upstream limits.

For a manual request, put the following header in a private curl config outside
the checkout, with mode 0600. The token shown is a placeholder, not a real key:

```text
header = "Authorization: Bearer <ingest-key>"
```

Then send the file without placing the credential in command arguments or history:

```bash
curl --config /home/willis/.config/log-reader/ingest-curl.conf \
  --fail-with-body \
  --form 'file=@flight.BIN' \
  --form 'display_name=Field flight' \
  --form 'tags=["pixhawk","field-test"]' \
  --form 'notes=First hover' \
  https://vtollogs.williserdman.com/api/ingest
```

Do not use shell tracing or `curl --verbose` with credentials. Keep keys out of
application/browser logs, URLs, git, and client-side dashboard code.

### Manual activation of device ingestion

These steps require separate deployment approval. Code preparation does not
provision a key, migrate production data, or reload any service.

1. Generate a cryptographically random key, such as 32 random bytes encoded as
   hex. Privately provision it to the device and to
   `/home/willis/.config/log-reader/ingest.env`, owned by the deployment operator,
   mode 0600, containing `INGEST_API_KEY=<private-value>`. Do not use a real value
   in command history or commit the file. No per-device key management is provided.
2. Follow the backup procedure below before the first deployment. Stop both
   Compose services, then archive the database and files together. The new
   application upgrades schema version 1 to 2 without
   changing existing entries. The old image rejects version 2: image-only rollback
   is unsupported. Restoring a backup needs confirmation and must preserve newer
   data separately.
3. From the deployment checkout, build and recreate the container:

   ```bash
   PUBLIC_ORIGIN=https://vtollogs.williserdman.com \
   docker compose --env-file /home/willis/.config/log-reader/ingest.env \
     up -d --build --wait --wait-timeout 60
   ```

   Copy the updated `deploy/catalog-nginx.conf` into the existing nginx snippet,
   run `sudo nginx -t`, then reload nginx. Keep the existing catch-all proxy,
   500 MB body limit, TLS configuration, and Basic Auth file. Every later Compose
   recreation must include the private env file; omitting it disables ingestion.
4. Through HTTPS, verify missing/wrong keys fail, a valid upload returns 201,
   and its repeat returns 200 with the same ID. Ordinary routes still require
   the browser password, including `/api/logs` when given only the ingestion key.
   Verify original bytes, browser actions, and persistence after a container
   restart. Remove only the disposable test entry. Repeat checks through the
   public proxy, not just loopback.
5. Update `/home/willis/SETUP_REPLICATION.md` with activation date, configuration
   names/paths, and verification results, never key values. To rotate the key,
   replace the value privately, update devices, and recreate the container with
   the same env-file command. The old key stops working after recreation.

## Private manual deployment

There is no continuous deployment. These are operator steps and must not run until
the deployment owner authorizes activation and provides the shared team password.
The host needs Docker Compose, nginx, and the `htpasswd` utility. The checked-in
nginx file is an include for the existing HTTPS server. It does not configure TLS
or replace the existing HTTP-to-HTTPS redirect.

1. Create the password file interactively. Use `-c` only when the file does not
   exist. Never put the password on the command line.

   ```bash
   sudo htpasswd -B -c /etc/nginx/log-catalog.htpasswd team
   sudo chown root:www-data /etc/nginx/log-catalog.htpasswd
   sudo chmod 0640 /etc/nginx/log-catalog.htpasswd
   ```

   For later password changes, run `sudo htpasswd -B
   /etc/nginx/log-catalog.htpasswd team` without `-c`.

2. Copy `deploy/catalog-nginx.conf` to
   `/etc/nginx/snippets/log-catalog.conf`, owned by root with mode 0644. Add this
   literal line inside the existing HTTPS `server` block in
   `/etc/nginx/sites-available/vtollogs.williserdman.com`:

   ```nginx
   include /etc/nginx/snippets/log-catalog.conf;
   ```

   The snippet contains server-level Basic Auth plus an exact `/api/ingest`
   location authenticated by Flask's ingestion key. Do not add a second
   `location /` block or a second server-level `client_max_body_size` directive.
   Preserve the existing catch-all proxy to `127.0.0.1:18765`, its 500 MB request
   limit, and its 600-second read/send timeouts. Confirm proxy caching is off for
   private downloads; add `proxy_cache off` to the existing location only if a
   higher-level cache is configured. The server-level auth protects `/`,
   `/catalog.js`, legacy APIs, catalog APIs other than `/api/ingest`, original
   downloads, and text downloads.
   Do not add a public health exception. The container health probe reaches the
   loopback application directly and remains unauthenticated. Keep the existing
   port 80 redirect.

3. Run `sudo nginx -t`, then reload nginx. Before deploying catalog data, prove an
   unauthenticated request to each representative path returns 401: `/`,
   `/catalog.js`, `/api/health`, `/api/files`, `/api/logs`,
   `/api/logs/not-found/download`, and `/api/logs/not-found/text`. Repeat with the
   shared credential and confirm requests reach the application rather than nginx
   returning 401. Do not place the password in shell history; let `curl -u team`
   prompt interactively.

4. Create `log-dashboard/catalog-data` with access restricted to the deployment
   operator. Deploy this branch manually with the public origin set explicitly:

   ```bash
   PUBLIC_ORIGIN=https://vtollogs.williserdman.com docker compose up -d --build
   ```

   This leaves device ingestion disabled. If enabling ingestion or preserving
   an existing ingestion key, use the private `--env-file` command above instead.

   The application never trusts arbitrary forwarded headers as its write origin.
   Confirm `docker compose ps` reports healthy before continuing.

5. If the deployment user's crontab has a legacy line ending in
   `# log-catalog-cleanup`, use `crontab -e` to remove only that line. Preserve
   every unrelated entry. Do not install a replacement cron entry; the Compose
   `cleanup` service owns the schedule. Confirm `docker compose ps` reports both
   `log-reader` and `cleanup` running and `log-reader` healthy.

6. Through authenticated HTTPS, upload, save, edit, reanalyze, download the
   original, view/download text, and delete a disposable test log. Confirm a saved
   entry and its original bytes survive `docker compose restart log-reader`. Check
   nginx and application logs for errors.

7. Update `/home/willis/SETUP_REPLICATION.md` with the activation date, actual
   paths, environment-variable names, verification results, and the deployment,
   cleanup, backup, and restore commands. Do this during activation, not during
   code preparation.

`X-Accel-Buffering: no` on text responses tells nginx not to buffer those streams.
The proxy does not cache private downloads. Its read/send timeouts match the
600-second Gunicorn timeout, and its request limit matches the application's
500 MB limit.

## Backup and restore

Use one consistent snapshot procedure:

1. Stop cleanup before the application so neither process can change catalog data:

   ```bash
   docker compose stop cleanup log-reader
   ```

2. Archive the entire `log-dashboard/catalog-data` directory, including ownership
   and permissions, to a new restricted backup file. Set `CATALOG_BACKUP_ARCHIVE`
   to an absolute path on backup storage, confirm it does not exist, then run from
   the repository root:

   ```bash
   umask 077
   if [ -z "${CATALOG_BACKUP_ARCHIVE:-}" ] ||
      [ "${CATALOG_BACKUP_ARCHIVE#/}" = "$CATALOG_BACKUP_ARCHIVE" ]; then
       echo "Set CATALOG_BACKUP_ARCHIVE to an absolute path." >&2
       false
   elif [ -e "$CATALOG_BACKUP_ARCHIVE" ]; then
       echo "Refusing to overwrite existing archive: $CATALOG_BACKUP_ARCHIVE" >&2
       false
   else
       sudo tar --numeric-owner -czpf "$CATALOG_BACKUP_ARCHIVE" -C log-dashboard catalog-data &&
       sudo tar -tzpf "$CATALOG_BACKUP_ARCHIVE"
   fi
   ```

3. Start the application, wait until it is healthy, then start cleanup:

   ```bash
   docker compose start log-reader
   ```

   Wait for `docker compose ps` to report `log-reader` healthy, then run
   `docker compose start cleanup`. If Compose must recreate containers with device ingestion
   enabled, use the private `--env-file` deployment command instead. Do not
   recreate the application without that file.
4. Verify an authenticated catalog listing and one saved original download.

To restore, get separate confirmation before changing live catalog data. Run
`docker compose stop cleanup log-reader` first. Restore the database and the
`files` directory together from one archive; never overwrite a running catalog.
Move the stopped current directory to a separately named recovery location before
extracting the archive. Run `sudo tar --numeric-owner -xzpf
"$CATALOG_BACKUP_ARCHIVE" -C log-dashboard`, then start `log-reader`. After it is
healthy, start `cleanup`. Verify metadata plus original-byte hashes. Compose never
restores a backup automatically.

## Checks

Focused deployment checks:

```bash
.venv/bin/python -m unittest discover -s log-dashboard -p test_catalog_deployment.py
.venv/bin/python -m mypy --follow-imports=silent --ignore-missing-imports --check-untyped-defs log-dashboard/catalog.py log-dashboard/catalog_routes.py log-dashboard/log_text.py
node --check log-dashboard/catalog.js
docker compose config --quiet
```

The full suite includes a real Chromium test and needs the matching Playwright
runtime described by the development environment. Run it with:

```bash
.venv/bin/python -m unittest discover -s log-dashboard -p 'test_*.py'
```

On this Ubuntu host, use the matching Playwright Docker image to run browser
checks without installing host browser libraries. From the repository root, with
the existing Python 3.12 virtual environment and development dependencies:

```bash
docker run --rm --network none \
  -e PYTHONDONTWRITEBYTECODE=1 \
  -e PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
  -v "$PWD:$PWD:ro" -w "$PWD" \
  mcr.microsoft.com/playwright/python:v1.63.0-noble \
  .venv/bin/python -m unittest discover -s log-dashboard -p 'test_*.py'
```

The repository is read-only in this container; tests create their own temporary
catalogs. Match the image version to `.venv/bin/python -m playwright --version`
when upgrading Playwright.

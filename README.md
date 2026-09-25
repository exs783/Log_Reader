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
files in `log-dashboard/uploads` stay available through the legacy API.

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

   The snippet contains only the server-level Basic Auth directives. Do not
   add a second `location /` block or a second `client_max_body_size` directive.
   Preserve the existing catch-all proxy to `127.0.0.1:18765`, its 500 MB request
   limit, and its 600-second read/send timeouts. Confirm proxy caching is off for
   private downloads; add `proxy_cache off` to the existing location only if a
   higher-level cache is configured. The server-level auth protects `/`,
   `/catalog.js`, legacy APIs, catalog APIs, original downloads, and text downloads.
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

   The application never trusts arbitrary forwarded headers as its write origin.
   Confirm `docker compose ps` reports healthy before continuing.

5. Verify the Docker path with `command -v docker`. The supplied cron line assumes
   `/usr/bin/docker`. Edit the deployment user's crontab and replace only a line
   ending in `# log-catalog-cleanup`; preserve every unrelated entry. Install the
   line from `deploy/log-catalog.cron`. It has no username field because it is for
   a user crontab. Run its cleanup command once with no browser traffic and confirm
   it reports only `removed`, `skipped`, and `failed` counts.

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

1. Save the current crontab, then use `crontab -e` to remove only the line marked
   `log-catalog-cleanup` from the installed crontab:

   ```bash
   crontab -l > /tmp/log-catalog.crontab.before-backup
   ```

2. Run `docker compose stop log-reader`.
3. Archive the entire `log-dashboard/catalog-data` directory, including ownership
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

4. Run `docker compose start log-reader`, restore the one marked cron line without
   changing unrelated jobs, and confirm the container is healthy.
5. Verify an authenticated catalog listing and one saved original download.

To restore, get separate confirmation before changing live catalog data. Disable
the named cleanup job and stop this application first. Restore the database and
the `files` directory together from one archive; never overwrite a running catalog.
Move the stopped current directory to a separately named recovery location before
extracting the archive. Run `sudo tar --numeric-owner -xzpf
"$CATALOG_BACKUP_ARCHIVE" -C log-dashboard`, restart, restore the named cron line,
and verify metadata plus original-byte hashes.

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

## CWRU VTOL Log Reader
> Author: Ethan Simon

This is a html/flask based webpage designed to allow for easy log inspection.

```
log-dashboard
├── log_dashboard.html
├── models
│   └── flight_path_good.html
├── python_backend.py
└── uploads
    └── somelog.BIN

```


###  running instructions

create venv with python or uv 
`uv venv`
or 
`python -m venv .venv`

activate venv
`source .venv/bin/activate`

install requirments
`uv pip install -r requirements.txt`
or 
`pip install -r requirements.txt`

run the program
`uv run gunicorn --bind 0.0.0.0:8000 wsgi:app`
or 
`gunicorn --bind 0.0.0.0:8000 wsgi:app`

this should serve the flask app on your local network, you may upload logs via the webpage or place them directly into the uploads folder

### Docker

Build and start the service in the background:

`docker compose up -d --build`

Open `http://localhost:18765`.

Web uploads accept `.bin` and `.log` files with a 500 MB request limit. Each
upload uses a separate temporary directory and a fixed filename; the original
filename is display text only. The parser closes the log, and the temporary
copy is deleted after analysis, including on errors. Dashboard results remain
in browser memory until refresh, when the log must be uploaded again.

Existing files and logs manually placed in `log-dashboard/uploads` still
persist on the host and require manual removal. A forced process termination
can leave temporary files behind; automatic cleanup after crashes is deferred.

View logs with `docker compose logs -f` and stop with `docker compose down`.
The container restarts automatically unless it is explicitly stopped.

**future work**
- deploy the Dockerized service to an SBC
- auto upload pipeline after flight 
- mission model?

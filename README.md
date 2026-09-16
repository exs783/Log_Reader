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

Uploaded logs persist on the host in `log-dashboard/uploads`. Uploading a file
with an existing filename replaces that file. The application does not
automatically delete uploaded logs.

View logs with `docker compose logs -f` and stop with `docker compose down`.
The container restarts automatically unless it is explicitly stopped.

**future work**
- deploy the Dockerized service to an SBC
- auto upload pipeline after flight 
- mission model?

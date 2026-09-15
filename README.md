## CWRU VTOL Log Reader
> Author: Ethan Simon

This is a html/flask based webpage designed to allow for easy log inspection.

```
log-dashboard
├── log_dashboard.html
├── models
│   └── Flight Path good.html
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
`uv run log-dashboard/python_backend.py`
or 
`python3 log-dashboard/python_backend.py`

this should serve the flask app on your local network, you may upload logs via the webpage or place them directly into the uploads folder

** future work **
- we may dockerize this and deploy it to a sbc
- auto upload pipeline after flight 
- mission model?

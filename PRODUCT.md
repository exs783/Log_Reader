# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Small team of drone operators/engineers who fly Pixhawk/ArduPilot-based aircraft and need to diagnose flight logs after a flight (vibration, tuning, GPS, power issues).

## Product Purpose

Upload a `.bin`/`.log` dataflash file from an ArduPilot flight controller; the Flask backend (`python backend.py`) parses it and returns per-subsystem analysis (IMU, vibration FFT, attitude/PID tracking, battery, motors, GPS, barometer, magnetometer, events) plus an overall flight-quality score. The dashboard (`log_dashboard.html`) renders that analysis as interactive Plotly charts across tabs so the team can spot mechanical or tuning problems without digging through raw logs.

## Positioning

Purpose-built, ArduPilot-aware log reader (MAVExplorer-style analysis, proper Welch/FFT vibration spectrum with real Hz frequencies and ArduPilot vibration-severity thresholds) served as a single static page by the team's own Flask backend — not a generic file viewer.

## Operating Context

Local/internal tool: user picks a log file in the browser, it POSTs to `${API_URL}/upload` on the Flask backend, and the response (`data.analyses`, `data.summary`) drives every tab. No auth, no multi-user accounts — shared by the team informally.

## Capabilities and Constraints

- Frontend must stay a single static HTML file (no build step) — Flask serves it directly via `send_from_directory`.
- Frontend DOM ids/classes (`#log-file`, `#upload-status`, `.nav-btn`, `.page`, `#imu-plot`, `#vibration-stats`, etc.) are read/written directly by inline JS and must be preserved exactly.
- Two backend/frontend pairs exist in this repo: `python backend.py` (Welch/FFT vibration analysis in Hz) pairs with `log_dashboard.html`; `python backend_V1.py` (older bin-index FFT) pairs with `log_dashboard_V1.html`. Only the `log_dashboard.html` / `python backend.py` pair is in active use.

## Evidence on Hand

Working previous implementation of both dashboard versions and both backend versions in this repo; no other product docs existed before this file.

## Product Principles

- Preserve the exact API contract and DOM hooks between `log_dashboard.html` and `python backend.py` — this is an internal tool, not a rebrand.
- Favor scanability and low cognitive load over expressive visual flourish (Operate surface, shared by a small team, used to diagnose real problems quickly).
- Real telemetry data drives every chart; never fabricate sample data or claims.

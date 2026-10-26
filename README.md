# Fight Karhutla

Verified wildfire triage for Indonesia. Live at https://karhutla.thariq.xyz.

Fight Karhutla turns satellite hotspots and citizen reports about *karhutla* (forest and land fires) into incidents whose evidence can be traced, whose burned area is measured, and whose action brief is ready for a human verifier in minutes.

> Not an official emergency channel. A human verifier always decides. See [docs/SAFETY.md](docs/SAFETY.md).

## Status

Work in progress for the Meta AI Hackathon (kickoff 2026-10-26). Built so far:

- FIRMS, Open-Meteo, and OpenStreetMap clients with file cache, bounded retry, and fixture mode
- Hotspot clustering into incidents (2 km, 24 h) and SQLite storage of hotspots, incidents, evidence, and verdicts
- Evidence gate: schema check, evidence existence, number matching, burn area recomputed from pixels, location check, content rules
- Dashboard with map, incident list, evidence panel, and verifier approve or reject
- 33 tests that run without a network

Not built yet: the Muse Spark triage agent, SAM 3.1 segmentation, Muse Voice Transcribe, WhatsApp intake, the web report form, Sentinel-2 dNBR, replay mode, evaluation. The Meta Model API key is pending credit approval.

## Meta models

| Model | Role |
| --- | --- |
| Muse Spark 1.3 | Triage agent with tool calling and structured output; critique pass |
| SAM 3.1 | Smoke, flame, and burn scar masks; area in hectares computed by code from pixels |
| Muse Voice Transcribe | Transcribes citizen voice notes |

## Run locally

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt   # Windows; use .venv/bin on Linux and macOS
.venv/Scripts/python -m pytest -q
.venv/Scripts/python -m uvicorn backend.main:app --port 8000
```

`FIXTURE_MODE=true` (the default) uses saved real responses in `fixtures/` and marks incidents as SIMULASI. Copy `.env.example` to `.env` for live keys.

## Built during the hackathon

This repository started on 2026-10-27 with only a LICENSE. Everything else was built during the hackathon; the commit history shows each step. The hosting project for `karhutla.thariq.xyz` was created at kickoff and held no application before this repository.

Development used a Code Assistant for acceleration and debugging.

## Data attribution

- NASA FIRMS (VIIRS and MODIS NRT)
- Weather data by Open-Meteo.com (CC BY 4.0)
- Map data (c) OpenStreetMap contributors, ODbL

## License

MIT

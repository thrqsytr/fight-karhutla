# Fight Karhutla

Verified wildfire triage for Indonesia. Live at https://karhutla.thariq.xyz.

Fight Karhutla turns satellite hotspots and citizen reports about *karhutla* (forest and land fires) into incidents whose evidence can be traced, whose burned area is measured, and whose action brief is ready for a human verifier in minutes.

> Not an official emergency channel. A human verifier always decides. See [docs/SAFETY.md](docs/SAFETY.md).

## Status

Work in progress for the Meta AI Hackathon (kickoff 2026-10-26). Built so far:

- FIRMS, Open-Meteo, and OpenStreetMap clients with file cache, bounded retry, and fixture mode
- Hotspot clustering into incidents (2 km, 24 h) and SQLite storage of hotspots, incidents, evidence, and verdicts
- Evidence gate: schema check, evidence existence, number matching, burn area recomputed from pixels, location check, content rules
- Evidence per incident: hotspot cluster summary, Open-Meteo weather, nearest settlement, road, river, lake, and fire station from OSM, with quota budgets and failures shown as unknown
- Citizen report form at `/lapor` (location, up to 3 photos, voice note, text) with EXIF stripped and media visible to verifiers only
- Triage agent: Muse Spark 1.3 calls incident-bound tools, answers in a strict schema, passes the evidence gate, and gets a critique pass from Muse Spark Open on Baseten (public evidence only; claims on citizen evidence are reviewed on Meta Model API)
- Intake processing: citizen voice notes transcribed with Muse Voice Transcribe (Indonesian), photos read by Muse Spark, SAM 3.1 segmentation available to the agent
- Sentinel-2 burn scar evidence: a clear before and after pair from Earth Search, dNBR computed in code, burned hectares counted from 20 m pixels at dNBR 0.27 or more, no hectares under heavy haze, before, after, and burn scar images in the incident view and on the map
- Drone survey simulator (SIMULASI): a Blender flyover of a real incident built on its Sentinel-2 burn mask, planned by the app's mission planner, filtered at the ground station by Muse Glimmer on Modal, measured with SAM 3.1 and georeferenced to hectares by code, compared with render truth. Shown beside the incident, never used in triage
- Drone survey proposals: the agent's `plan_drone_survey` tool or a verifier plans a lawnmower flight over the hotspots; only a verifier can approve it, and no drone flies by itself
- Offline OpenStreetMap fallback from a dated Geofabrik extract, used when every Overpass server is down
- Admin panel at `/admin/` (verifier token to view, execution code to save): FIRMS fetch interval from 30 minutes to 2 days, enrichment budgets, Sentinel-2 reuse window, triage caps and schedule, spend, recent runs, and source health
- Metered cost per triage from the usage of each call (first production run: $0.055, 8 of 8 claims grounded)
- Dashboard with map, incident list, evidence panel, claims linked to evidence, gate details, and verifier approve or reject
- 108 tests that run without a network

Not built yet: WhatsApp intake (no Business API credentials), replay mode, evaluation. Triage runs only on purpose: verifiers select incidents in the list or by drawing an area on the map (at most 5 per selection) and confirm with an execution code; a daily cap and a daily or hourly schedule over citizen reports bound the spend.

## Meta models

| Model | Role |
| --- | --- |
| Muse Spark 1.3 (Meta Model API) | Triage agent with tool calling and structured output; reads citizen photos; reviews claims that rest on citizen evidence |
| Muse Spark Open (Baseten) | Critique pass over claims and briefs built on public evidence only |
| SAM 3.1 (Meta Model API) | Masks burned ground and objects on photos, with concepts chosen by Muse Spark. In our tests it found no smoke on photos and nothing on a satellite image, so hectares come only from nadir drone frames with known ground pixel size |
| Muse Voice Transcribe (Meta Model API) | Transcribes Indonesian voice notes |

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

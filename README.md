# Fight Karhutla

Verified forest and land fire (*karhutla*) triage for Indonesia, built on Meta AI. Live: **https://karhutla.thariq.xyz**

Satellite hotspots and citizen reports become incidents with numbered evidence, a measured burn scar, and a suppression outlook. Meta models read every source and draft a bilingual brief whose every claim cites evidence; code checks every number; a human verifier decides.

> Not an official emergency channel: call 112. No medical advice. A human verifier always decides. See [docs/SAFETY.md](docs/SAFETY.md).

## How it works

1. **Detect.** NASA FIRMS hotspots for Indonesia, clustered into incidents (2 km, 24 h). Citizen reports from the Report page and WhatsApp join the same incident.
2. **Evidence, collected by code.** Weather (Open-Meteo); nearest roads, water, settlements, fire stations, and offices (OpenStreetMap and Overture Maps); Sentinel-2 before and after images with burned hectares from dNBR. Each item gets an evidence ID.
3. **Meta AI analyses and drafts.** Muse Spark 1.3 calls incident-bound tools and writes status, claims, risk, and actions in English and Indonesian, each claim citing evidence IDs.
4. **Evidence gate.** Code checks schema, cited IDs, numbers against tool results, area from real masks, location, and content rules; unsupported claims are dropped and the status lowered. A critic model disputes weak claims. Code computes the suppression outlook.
5. **People decide.** Every incident ends waiting for a verifier. Nothing is sent to anyone automatically.

## Meta models

| Model | Role |
| --- | --- |
| Muse Spark 1.3 (Meta Model API) | Triage agent: tool calling, structured bilingual output, reads citizen photos, reviews claims on citizen evidence |
| Muse Voice Transcribe (Meta Model API) | Indonesian voice notes to text |
| SAM 3.1 (Meta Model API) | Burned ground masks on drone frames and objects on photos; hectares computed in code |
| Muse Spark Open (Baseten) | Critique of claims built on public evidence; second evaluation run |
| Muse Glimmer (open weights, Modal) | Ground station frame filter in the drone simulation |

Inference only; no model is trained on user data. Metered cost: $0.058 per incident in the evaluation (English output), about $0.10 per incident bilingual in production (6 completed runs).

## Features

- Map with incident list, filters, NASA GIBS smoke and aerosol layers, map or world basemap, and the state of every source.
- Incident view in the five stages above, and a card on the selected map point with its actions.
- Suppression outlook: likely spread and approach side, access, nearest water, fire station and offices, dryness, size, with thresholds stated.
- Report page (up to 3 photos, voice, text, location; EXIF stripped) and WhatsApp intake; public WhatsApp report log.
- Persistent heat sources: anyone flags a mine, flare, factory, or volcano; a verifier accepts it as a zone.
- Verifier's desk: verification queue, source health, coverage, spend, operator jobs, settings log, CSV export.
- Drone survey (simulated): plan, verifier approval, simulated pilot, 3D view with a data console.
- Replay of three recorded 2025 incidents, Indonesian and English, How it works overlay.
- Plain-language overview in Indonesian: [docs/WHITEPAPER.md](docs/WHITEPAPER.md).

## Evaluation

30 hotspot clusters from the 2025 dry season, 15 burned and 15 not, labelled from Sentinel-2 dNBR and checked by eye. The agent sees hotspots, weather, and OSM only (Sentinel-2 is the label source, so it is withheld).

| Muse Spark 1.3, n=30 | Precision | Recall | F1 | Abstained |
|---|---|---|---|---|
| Rule: any nominal or high hotspot | 52% | 100% | 68% | 0 |
| Agent after gate and critique | 100% | 13% | 24% | 27 |

- Grounding 100% (160 of 160 claims cite stored evidence); the gate corrected 3 numbers.
- **Weakness:** without independent evidence the agent mostly answers needs_more_info (27 of 30) and found 2 of 15 burns, with no false alarm. It adds no detection over the rule on these inputs; detection comes from Sentinel-2, citizen photos, and the drone.
- Drone simulation, against render truth: SAM 3.1 measured 7.69 ha against 8.70 ha (-11.6%) on maskable frames.

Full tables, the Muse Spark Open run, method, and how to rerun: [docs/EVALUATION.md](docs/EVALUATION.md).

## Limits

- Clouds and haze hide Sentinel-2; then the burned area is unknown, not zero.
- The drone is simulated; no drone is connected.
- WhatsApp is one way: reports arrive, but Meta refuses outgoing messages until business verification; only registered test numbers can report.
- OpenStreetMap and Overture Maps are incomplete.

## Privacy

Citizen photos, voice, and text are visible to verifiers only; photo metadata is removed; WhatsApp numbers are kept only as a salted hash in evidence. A verifier can blur parts of a photo and delete citizen data per incident. Retention: report data is deleted after the demo period ends (date to be set before submission).

## Built during the hackathon

This repository started on 2026-10-27 with only a LICENSE; everything else was built during the hackathon, and the commit history shows each step. The hosting project for `karhutla.thariq.xyz` was created at kickoff and held no application before. Development used a Code Assistant for acceleration and debugging. Decisions and their reasons: [docs/DECISIONS.md](docs/DECISIONS.md).

## Run locally

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt   # Windows; use .venv/bin on Linux and macOS
.venv/Scripts/python -m pytest -q                   # 158 tests, no network
.venv/Scripts/python -m uvicorn backend.main:app --port 8000
```

`FIXTURE_MODE=true` (the default) uses saved real responses in `fixtures/` and marks incidents SIMULASI. Copy `.env.example` to `.env` for live keys. Data sources and limits: [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md).

## Data attribution

- NASA FIRMS (VIIRS and MODIS NRT); NASA GIBS / Worldview layers
- Contains modified Copernicus Sentinel data (Sentinel-2 L2A via Earth Search)
- Weather data by Open-Meteo.com (CC BY 4.0)
- Map data (c) OpenStreetMap contributors, ODbL; light basemap by OpenFreeMap
- Places (c) Overture Maps Foundation and its sources (Meta, Microsoft, Foursquare, AllThePlaces, and others), CDLA Permissive 2.0, Apache 2.0, CC0
- World imagery: Esri, Vantor, Earthstar Geographics, and the GIS User Community

## License

MIT

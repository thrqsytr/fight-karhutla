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
- WhatsApp intake (Cloud API, test number): photos, voice notes, text, and a location pin in any order become the same report as the form; the webhook checks Meta's signature, the reporter gets the incident number and later the verifier's decision, verifiers get a message with a link for every new citizen report (form or WhatsApp). Evidence keeps a salted hash of the number, never the number
- WhatsApp report log at `/whatsapp/`: per report, what the resident sent, what Muse Voice Transcribe and Muse Spark found, each processing step, and every bot reply with its delivery result. Real reports from production; verifiers see all and choose which ones the public sees
- Triage agent: Muse Spark 1.3 calls incident-bound tools, answers in a strict schema, passes the evidence gate, and gets a critique pass from Muse Spark Open on Baseten (public evidence only; claims on citizen evidence are reviewed on Meta Model API)
- Intake processing: citizen voice notes transcribed with Muse Voice Transcribe (Indonesian), photos read by Muse Spark, SAM 3.1 segmentation available to the agent
- Sentinel-2 burn scar evidence: a clear before and after pair from Earth Search, dNBR computed in code, burned hectares counted from 20 m pixels at dNBR 0.27 or more, no hectares under heavy haze, before, after, and burn scar images in the incident view and on the map
- Drone survey simulator (SIMULASI): a Blender flyover of a real incident built on its Sentinel-2 burn mask, planned by the app's mission planner, filtered at the ground station by Muse Glimmer on Modal, measured with SAM 3.1 and georeferenced to hectares by code, compared with render truth. Shown beside the incident, never used in triage
- Drone survey proposals: the agent's `plan_drone_survey` tool or a verifier plans a lawnmower flight over the hotspots; only a verifier can approve it, and no drone flies by itself
- Simulated pilot: an approved plan is flown on the map with telemetry simulated from the plan and the incident's wind (battery packs, ground speed, frames), on a shared sim clock; abort, land, and replay
- 3D flight view (three.js): the drone flies the plan on autopilot from the same telemetry, over the incident's Sentinel-2 image with its burn scar (or OpenStreetMap), with a chase camera the reader can orbit and an inset of the drone's nadir camera. The ground is flat and the drone is drawn 30 times larger than life; the view says so
- Offline OpenStreetMap fallback from a dated Geofabrik extract, used when every Overpass server is down
- Share any public incident: a link with a WhatsApp and Facebook preview (summary plus a static map image with the hotspots)
- Admin panel at `/admin/` (verifier token to view, execution code to save): FIRMS fetch interval from 30 minutes to 2 days, enrichment budgets, Sentinel-2 reuse window, triage caps and schedule, spend, recent runs, and source health
- Metered cost per triage from the usage of each call (first production run: $0.055, 8 of 8 claims grounded)
- Dashboard with map, incident list, evidence panel, claims linked to evidence, gate details, and verifier approve or reject
- Map basemap switch under the zoom controls: a light map, or world satellite imagery with the same boundaries and labels (display only, never evidence)
- Replay mode: three recorded incidents from the 2025 dry season (clearly burned, false positive, needs more info) load as SIMULASI from the admin panel, with no network and no model call
- Evaluation on 30 historical clusters labelled with Sentinel-2 dNBR, three baselines, results below
- 140 tests that run without a network

WhatsApp works one way for now: reports come in (tested in production on 2026-10-29: text, photo, voice note, and a pin became one incident with transcript, photo reading, weather, and OSM), but Meta blocks every outgoing message (error 131031, account locked) because the business account is a person, not a verified business. Replies and notifications are still attempted and recorded with that reason. It runs on Meta's test number, so only registered tester numbers can report. Triage runs only on purpose: verifiers select incidents in the list or by drawing an area on the map (at most 5 per selection) and confirm with an execution code; a daily cap and a daily or hourly schedule over citizen reports bound the spend.

## Meta models

| Model | Role |
| --- | --- |
| Muse Spark 1.3 (Meta Model API) | Triage agent with tool calling and structured output; reads citizen photos; reviews claims that rest on citizen evidence |
| Muse Spark Open (Baseten) | Critique pass over claims and briefs built on public evidence only; the agent in a second evaluation run at volume |
| SAM 3.1 (Meta Model API) | Masks burned ground and objects on photos, with concepts chosen by Muse Spark. In our tests it found no smoke on photos and nothing on a satellite image, so hectares come only from nadir drone frames with known ground pixel size |
| Muse Voice Transcribe (Meta Model API) | Transcribes Indonesian voice notes |

## Evaluation

Thirty hotspot clusters from the 2025 dry season (FIRMS VIIRS S-NPP archive, Kalimantan Barat, Kalimantan Tengah, Riau, Sumatera Selatan and Jambi), 15 burned and 15 not burned. A label is burned when Sentinel-2 dNBR shows at least 2 ha at 0.27 or more within 500 m of a hotspot, in a clear pair before and within 40 days after the first detection, and not burned under 0.5 ha with at least 60% of that ground clear. Every label was then checked by eye on the before, after, and burn mask images; 4 are doubtful (cloud or haze under the mask, open mining) and the second table leaves them out. Labels: [eval/labels.csv](eval/labels.csv), images in `eval/dataset/sheets/`.

Each cluster runs through the app's own ingest, enrichment, agent, gate, and critique in a throwaway database. The agent may use hotspots, weather (Open-Meteo archive at the time), and OSM only: Sentinel-2 is the label source, so it is withheld. Positive means `likely_fire` or `possible_fire`. The same 30 were run on both models.

**Muse Spark 1.3 (Meta Model API, the production model)**

| meta (muse-spark-1.3), n=30 | Precision | Recall | F1 | Accuracy | Abstained |
|---|---|---|---|---|---|
| (a) Rule: any nominal or high hotspot | 52% | 100% | 68% | 53% | 0 |
| (b) Agent before gate | 100% | 13% | 24% | 57% | 27 |
| (c) Agent after gate and critique | 100% | 13% | 24% | 57% | 27 |

Only the 26 clusters whose label also passed a visual check:

| Baseline | Precision | Recall | F1 | Accuracy | Abstained |
|---|---|---|---|---|---|
| (a) Rule | 44% | 100% | 61% | 46% | 0 |
| (b) Agent before gate | 100% | 18% | 31% | 65% | 23 |
| (c) Agent after gate and critique | 100% | 18% | 31% | 65% | 23 |

Grounding rate 100% (160 of 160 claims kept), 3 corrections, 0 downgrades, 10 disputed by the critic, 1 errors, 0 schema failures. Median 94.3 s per incident (p90 149.1 s), about 29847 tokens per incident, $1.7274 in total ($0.0576 per incident).

**Muse Spark Open (Baseten)**

| baseten (meta-models/muse-spark-open-1026), n=30 | Precision | Recall | F1 | Accuracy | Abstained |
|---|---|---|---|---|---|
| (a) Rule: any nominal or high hotspot | 52% | 100% | 68% | 53% | 0 |
| (b) Agent before gate | 0% | 0% | n/a | 47% | 27 |
| (c) Agent after gate and critique | n/a | 0% | n/a | 50% | 28 |

Only the 26 clusters whose label also passed a visual check:

| Baseline | Precision | Recall | F1 | Accuracy | Abstained |
|---|---|---|---|---|---|
| (a) Rule | 44% | 100% | 61% | 46% | 0 |
| (b) Agent before gate | 0% | 0% | n/a | 54% | 23 |
| (c) Agent after gate and critique | n/a | 0% | n/a | 58% | 24 |

Grounding rate 100% (172 of 172 claims kept), 26 corrections, 0 downgrades, 7 disputed by the critic, 2 errors, 5 schema failures. Median 76.2 s per incident (p90 131.8 s), about 34194 tokens per incident, cost not metered (Baseten credit).

What this shows:

- The rule calls nearly every cluster a fire (29 of 30 have a nominal or high hotspot), so its precision is about the share of burned clusters in the set. Hotspot confidence alone does not separate the clusters that left a scar from those that did not.
- Without an independent source, both models mostly decline to call it. Muse Spark 1.3 returned `needs_more_info` for 27 of 30 and listed what it needs (a citizen photo or report, a clear satellite pass); the 2 clusters it called `possible_fire` were both burned, so it raised no false alarm, but it found 2 of 15 burns. Muse Spark Open never committed. On hotspot, weather, and OSM data alone, neither adds detection over the rule: the value is a grounded brief and routing to a verifier with a request for data, and detection comes from the independent sources (Sentinel-2, citizen photos, the drone), which this evaluation withholds on purpose.
- Every claim that reached the output cited stored evidence. The gate replaced numbers with the tool values (3 on Muse Spark 1.3, 26 on Muse Spark Open), and the critic disputed 10 and 7 claims. Muse Spark Open failed the schema twice in 5 runs (confidence above 1) and broke off mid JSON in 2; Muse Spark 1.3 returned one empty answer. All count against the agent above.
- 4 Muse Spark 1.3 runs first failed with a transient API error ("referenced response not found") and were run again after the agent learned to retry that error (decision 49).
- Burned area error is not reported: the labels are dNBR areas, so comparing dNBR hectares with them would grade the label against itself.
- Muse Spark 1.3 cost $1.73 for the 30, $0.058 per incident, from the usage of each call. Baseten runs on hackathon credit that is not metered per token.

Run it: `eval/build_dataset.py` (or `eval/dataset_modal.py` next to the data), then `eval/run_eval.py --provider meta` (or `baseten`). Raw results: [eval/results/](eval/results/).

## Related research

The drone survey is a simulation, built the way recent work builds synthetic wildfire imagery: a 3D scene derived from real data, with exact truth for every frame.

- Chen, Sun, Li, et al. *A Simulation-Grounded Agentic VLM Framework for Wildfire Monitoring and Reporting.* arXiv:2610.02451 (2026). Turns 2D fire simulations into Blender proxy scenes with labels and uses them as memory for VLM wildfire reports. Our survey also renders in Blender from real ground (a Sentinel-2 burn mask) and measures against the render truth.
- Sun, Lo, Hu. *WildfireX-SLAM: A Large-scale Low-altitude RGB-D Dataset for Wildfire SLAM and Beyond.* arXiv:2510.27133 (MMM 2026). Synthetic low-altitude forest and wildfire imagery from Unreal Engine 5, because real forest data is costly to collect.
- Schuck, Rath, Hua, et al. *Crazyflow: An Accurate, GPU-Accelerated, Differentiable Drone Simulator in JAX.* arXiv:2606.01478 (2026), and Tayal. *MuJoCo-Drones-Gym: A GPU-Accelerated Multi-Drone Simulator for Control and Reinforcement Learning.* arXiv:2606.08039 (2026). Physics simulators for control and learning. Our flight telemetry is a deliberately simple kinematic model (cruise speed, wind along each leg, battery drain); a physics simulator like these would be the next step for flight dynamics, and is not used here.

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
- NASA GIBS / Worldview map layers (VIIRS true color, MODIS AOD, OMPS aerosol index)
- Contains modified Copernicus Sentinel data (Sentinel-2 L2A via Earth Search)
- Weather data by Open-Meteo.com (CC BY 4.0)
- Map data (c) OpenStreetMap contributors, ODbL; light basemap by OpenFreeMap
- World imagery: Esri, Vantor, Earthstar Geographics, and the GIS User Community

## License

MIT

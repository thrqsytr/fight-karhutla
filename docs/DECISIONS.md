# Decisions

Every deviation from the execution spec is recorded here with its reason.

## 2026-10-27 (Day 1)

| # | Decision | Reason |
| --- | --- | --- |
| 1 | Folders are named by role: `backend/` (spec: `app/`) and `frontend/` (spec: `web/`). | Owner preference for role-based names. |
| 2 | Hosting is the owner's Dokploy VPS, project `karhutla`, not a Compute Engine VM. | The owner already runs Dokploy; Traefik there provides HTTPS and domains. |
| 3 | Caddy is dropped. | Dokploy's Traefik terminates TLS with Let's Encrypt. |
| 4 | SQLite lives on a Dokploy named volume mounted at `/data`. | Persistent disk without a managed database. |
| 5 | Citizen channel is WhatsApp Cloud API plus a web form at `/lapor`, not Telegram. | WhatsApp is part of the Meta ecosystem. The web form lets judges try reporting, since a WhatsApp test number only reaches up to 5 registered recipients. |
| 6 | Provider switches `LLM_PROVIDER` and `ASR_PROVIDER` (`meta` or `assemblyai`). | The Meta Model API credit is pending approval. AssemblyAI is an interim and fallback provider only. Demo, video, and evaluation run on Meta models. |
| 7 | Map starts on MapLibre with OpenStreetMap tiles. The Google Maps adapter is not built yet. | No Google Maps key yet. `MAP_PROVIDER=google` currently shows a visible note and falls back to MapLibre. |
| 8 | Fixture mode is the default and marks every incident as `simulation` (SIMULASI badge). | Day 1 builds without the Meta key. Fixture data is real FIRMS, Open-Meteo, and Overpass responses captured on 2026-10-26 for a cluster near -0.02, 116.45 (East Kalimantan). |
| 9 | The evidence gate (`backend/gate.py`) is built on Day 1 instead of Day 6. | It is deterministic code that needs no model, so it can be tested now. The critique pass (second Muse Spark call) still lands on Day 6. |
| 10 | Dashboard UI text is English; dispatch briefs are Indonesian and English. | Judges are global. Open to change. |
| 11 | Basemap is OpenFreeMap Positron (vector, no key) instead of raw OpenStreetMap raster tiles. | A muted basemap keeps fire data legible and looks professional. It also avoids leaning on the OSMF tile servers. |
| 12 | Map severity comes from measured FIRMS values: color follows fire radiative power (MW), opacity follows detection confidence. | No model-made severity score, consistent with spec principle 3. |
| 13 | A drone survey simulator is added (Blender 3D flyover, Muse Glimmer frame filter on the drone, SAM 3.1 on the server), planned for Days 5 and 6. Google Maps, the peat layer, and Muse Wonder are cut to make room. | Lets the agent close its own uncertainty by requesting a survey. Blender renders give exact ground-truth masks for measuring area error. All drone output is labelled SIMULASI. |

## 2026-10-27 (Day 2)

| # | Decision | Reason |
| --- | --- | --- |
| 14 | OSM is fetched per 0.2 degree tile (padded 0.1 degree, 0.3 for fire stations), cached 7 days, with at most 12 uncached tiles per run and three Overpass endpoints tried in order. | On 2026-10-27 all four public Overpass endpoints tried returned 502, 504, or timed out, and overpass-api.de states it is meant for human-operated use. One tile serves every incident inside it. |
| 15 | Automatic enrichment covers citizen reports and clusters with 3+ hotspots or max FRP 20 MW and above; a verifier can collect any incident on request. | Open-Meteo allows under 10,000 calls a day and Overpass is shared. Most clusters are single hotspots. |
| 16 | A failed or deferred source is stored as `failed: reason` and shown in the UI as UNKNOWN, then retried; retries never duplicate evidence. | Spec principle 5 and global rule 18. |
| 17 | In fixture mode, weather and OSM are recorded as unknown outside 15 km of the fixture point. | Fixture values from one place must not be presented as facts about another. |
| 18 | Evidence type `report` is added for the citizen's typed description. | The spec lists seven types and none fits free text. |
| 19 | `/lapor` is in Indonesian; the dashboard stays in English. | Reporters are Indonesian residents; dashboard readers include global judges. |
| 20 | Verifier endpoint `DELETE /api/incidents/{id}/citizen-data` removes citizen text, photos, voice, and files. | Needed for the retention promise in SAFETY.md and to remove test reports from production. |
| 21 | Triage runs only on purpose. A verifier ticks incidents or draws an area on the map (the box keeps the highest-FRP incidents up to 5 per selection) and confirms with an execution code. Wrong codes lock a client out after 5 attempts for 15 minutes. A server-side cap of 30 runs per WITA day covers manual and scheduled runs. The schedule is stored in the database: daily by default, hourly as high intensity, or off, and it only triages citizen-reported incidents. | Each triage spends about $0.05 of Meta credit (measured). Automatic triage over all 400+ eligible clusters would have used most of the credit before judging. |
| 22 | The interface is bilingual, Indonesian by default, with an ID or EN switch on every page. Dashboard text is written in English with an Indonesian dictionary; the report page is written in Indonesian with an English one. Server-generated text (evidence summaries, gate reasons, claims written by the model) stays in English, and report errors from the server stay in Indonesian. | Operators and residents read Indonesian; judges read English. |
| 23 | Three NASA GIBS daily layers are available on the map: VIIRS true color, MODIS aerosol optical depth, and the OMPS UV aerosol index, with NASA's own color legends. They are display only and are not used as evidence. | GIBS serves colored image tiles, not values, so the gate cannot check numbers from them. Clouds also hide smoke. |

## Day 1 spike results

Run on 2026-10-27 against the live Meta Model API, one call per capability. The key lists `muse-spark-1.3`, `sam-3.1`, `muse-voice-transcribe-1.0`, `muse-wonder-1.0`, and others. Test media: a CC BY 2.0 ground photo of a burned peatland in Indonesia and a public domain NASA satellite image of Borneo smoke (2002), used for testing only and not committed. Spike scripts and raw outputs are kept outside the repo.

| Spike | Result | Decision |
| --- | --- | --- |
| (a) Muse Spark reads a smoke photo | Passed. Structured output: burned ground and background smoke visible, no flames, which matches the photo on inspection. It proposed concepts to segment: "smoke", "burned ground", "charred tree stump". 5.0 s, 578 input and 722 output tokens. | Proceed. Muse Spark picks the SAM concepts from the photo. |
| (b) SAM 3.1 on a ground photo | Mixed. "burned ground" gave one mask over 51% of the frame that follows the ground and excludes stumps and logs precisely (checked visually). "tree stump" found 7 objects (positive control). "smoke", "flames", and "burn scar" returned zero matches; "haze" masked the sky, not the smoke. 1.9 to 3.2 s per call. | Use SAM on photos for burned ground and objects, with concepts chosen by Muse Spark. Do not treat SAM as a smoke detector. An oblique photo has no ground pixel size, so photo masks give a frame fraction, never hectares. |
| (b) SAM 3.1 on a satellite image | Failed. "smoke", "smoke plume", and "cloud" returned zero matches on a MODIS true color image of Borneo smoke. Sentinel-2 is not tested yet (Day 4). | Spec branch applies: burned area from satellite uses dNBR. Hectares from SAM come only from nadir frames with known ground sampling distance (drone survey). |
| (c) Voice Transcribe with Indonesian audio | Passed. Indonesian is one of the 25 documented languages. A 14.5 s note gave word error rate 0% (30 words, ignoring case and punctuation) from clean WAV and 3.3% after an Opus round trip like a browser recording, in 4.3 s. The audio was synthesized with the Windows Indonesian voice, so field recordings with wind and accents will likely do worse. | Proceed with Muse Voice Transcribe. The endpoint accepts only mono 16-bit PCM WAV at 16 or 24 kHz, so the server converts browser audio with ffmpeg. AssemblyAI is no longer needed as a fallback. |
| (d) Tool calling and structured output | Passed. Muse Spark called `get_weather` and `get_nearby_assets` with the right coordinates, kept `possible_fire` for a hotspot-only incident, cited real evidence IDs in all 5 claims, copied every number from tool results, and wrote an 86-word Indonesian brief. 49.6 s at reasoning effort medium; 3,162 input tokens (753 cached) and 9,048 output tokens (7,831 reasoning). | Proceed. Measured cost is about $0.04 per incident at list prices, mostly reasoning tokens. Re-measure on the real agent. |
| Baseten, Muse Spark Open (`meta-models/muse-spark-open-1026`, hackathon credit) | Passed. Chat in 2.7 s with a correct Indonesian answer; tool calling in 1.5 s with the right arguments; JSON schema output in 9.0 s. As a critic over three claims it caught the planted error ("spreading north" from wind blowing from 58 degrees, which points southwest) and also disputed two claims whose wording went beyond the shortened evidence text ("VIIRS", "nearest"). | Candidate for the critique pass and for evaluation runs, keeping Meta Model API credit for triage. Strict by default, so critique prompts must pass full evidence summaries. Baseten data handling is not checked yet, so only public evidence (satellite, weather, OSM) goes there until it is. |
| FIRMS area API | Passed: key active, 5000 transactions per 10 minutes, 3243 VIIRS SNPP hotspots over Indonesia for 2 days | Proceed |

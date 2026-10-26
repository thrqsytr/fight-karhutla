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

## Day 1 spike results

| Spike | Result | Decision |
| --- | --- | --- |
| (a) Muse Spark reads a smoke photo | Not run: Meta Model API key pending | Run when the key arrives |
| (b) SAM 3.1 on photo and Sentinel-2 | Not run: Meta Model API key pending | Run when the key arrives; dNBR stays the fallback for burn area |
| (c) Voice Transcribe with Indonesian audio | Not run: Meta Model API key pending | Run when the key arrives; AssemblyAI is the fallback |
| (d) Tool calling and structured output | Not run: Meta Model API key pending | Run when the key arrives |
| FIRMS area API | Passed: key active, 5000 transactions per 10 minutes, 3243 VIIRS SNPP hotspots over Indonesia for 2 days | Proceed |

# Data sources

Each endpoint below was verified with a live call on 2026-10-26 (UTC). Saved responses live in `fixtures/`.

| Source | Endpoint | Auth | License and attribution | Module |
| --- | --- | --- | --- | --- |
| NASA FIRMS | `GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/{MAP_KEY}/{SOURCE}/{west,south,east,north}/{days 1-5}` | Free `MAP_KEY`; limit 5000 transactions per 10 minutes (checked at `/mapserver/mapkey_status/`) | NASA open data; cite "NASA FIRMS" | `backend/sources/firms.py` |
| Open-Meteo | `GET https://api.open-meteo.com/v1/forecast?latitude&longitude&hourly=wind_speed_10m,wind_direction_10m,precipitation,relative_humidity_2m&past_days=1&forecast_days=1&wind_speed_unit=ms&timezone=UTC` | None for non-commercial use | CC BY 4.0; "Weather data by Open-Meteo.com" | `backend/sources/weather.py` |
| OpenStreetMap via Overpass | `POST https://overpass-api.de/api/interpreter` with an `around:` query for places, main roads, rivers, and fire stations, `out geom` | None; results cached 7 days to spare the public server | ODbL; "(c) OpenStreetMap contributors" | `backend/sources/osm.py` |
| OpenStreetMap tiles | `https://tile.openstreetmap.org/{z}/{x}/{y}.png` (display only) | None; light use under the OSMF tile usage policy | ODbL | `frontend/app.js` |

FIRMS NRT sources available on 2026-10-26: `VIIRS_SNPP_NRT`, `VIIRS_NOAA20_NRT`, `VIIRS_NOAA21_NRT`, `MODIS_NRT`, `LANDSAT_NRT`. Archive (`*_SP`) covers earlier dates for evaluation.

## Behavior shared by all clients

- 10 second timeout per request.
- Retry only on timeouts, transport errors, HTTP 429, and HTTP 5xx: at most 2 retries, 1 s then 3 s, each logged.
- HTTP 4xx other than 429 is an answer and is not retried.
- File cache keyed by a SHA-256 of source and parameters. Empty bodies are never cached.
- `FIXTURE_MODE=true` reads `fixtures/` and makes no network call. Tests block the network outright.

## Not yet integrated

- Sentinel-2 pairs for dNBR (Day 4).
- Peat layer (optional, only if an open layer is found within 30 minutes).

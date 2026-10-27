# Data sources

Each endpoint below was verified with a live call on 2026-10-26 (UTC). Saved responses live in `fixtures/`.

| Source | Endpoint | Auth | License and attribution | Module |
| --- | --- | --- | --- | --- |
| NASA FIRMS | `GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/{MAP_KEY}/{SOURCE}/{west,south,east,north}/{days 1-5}` | Free `MAP_KEY`; limit 5000 transactions per 10 minutes (checked at `/mapserver/mapkey_status/`) | NASA open data; cite "NASA FIRMS" | `backend/sources/firms.py` |
| Open-Meteo | `GET https://api.open-meteo.com/v1/forecast?latitude&longitude&hourly=wind_speed_10m,wind_direction_10m,precipitation,relative_humidity_2m&past_days=1&forecast_days=1&wind_speed_unit=ms&timezone=UTC` | None for non-commercial use | CC BY 4.0; "Weather data by Open-Meteo.com" | `backend/sources/weather.py` |
| OpenStreetMap via Overpass | `POST https://overpass-api.de/api/interpreter` with an `around:` query for places, main roads, rivers, and fire stations, `out geom` | None; results cached 7 days to spare the public server | ODbL; "(c) OpenStreetMap contributors" | `backend/sources/osm.py` |
| NASA GIBS (display only) | `https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/{layer}/default/default/{TileMatrixSet}/{z}/{y}/{x}.{ext}` for `VIIRS_SNPP_CorrectedReflectance_TrueColor` (Level9, jpg), `MODIS_Combined_Value_Added_AOD` and `OMPS_Aerosol_Index` (Level6, png); legends at `/legends/{layer}_H.svg`. Verified 2026-10-28, CORS open. | None | NASA open data; "Imagery: NASA GIBS / Worldview" | `frontend/app.js` |
| Copernicus Sentinel-2 L2A via Earth Search | `POST https://earth-search.aws.element84.com/v1/search` (collection `sentinel-2-l2a`), then windowed reads of the `nir`, `swir22`, `scl`, `aot`, and `visual` COGs on `sentinel-cogs.s3.us-west-2.amazonaws.com`. Reflectance = DN x 0.0001 - 0.1 (from `raster:bands`). Verified 2026-10-28. | None | Copernicus data, free and open; "Contains modified Copernicus Sentinel data 2026" | `backend/sources/sentinel.py` |
| OpenFreeMap basemap (Positron style) | `https://tiles.openfreemap.org/styles/positron` (display only, vector tiles) | None, no key | OpenMapTiles and OpenStreetMap, attribution shown by MapLibre | `frontend/app.js` |

FIRMS NRT sources available on 2026-10-26: `VIIRS_SNPP_NRT`, `VIIRS_NOAA20_NRT`, `VIIRS_NOAA21_NRT`, `MODIS_NRT`, `LANDSAT_NRT`. Archive (`*_SP`) covers earlier dates for evaluation.

## Usage limits checked on 2026-10-27

- Open-Meteo terms: fewer than 10,000 API calls per day, 5,000 per hour, and 600 per minute for non-commercial use.
- Overpass (overpass-api.de): replied 502 "at capacity" and says the public endpoint is meant for browser-based, human-operated tools; automated or high-volume use should run its own instance. The OSM wiki asks for under 10,000 queries and under 1 GB per day. maps.mail.ru returned 504; overpass.private.coffee and overpass.kumi.systems timed out at 30 s.
- Our use: one query per 0.2 degree tile, cached 7 days, at most 12 uncached tiles per scheduler run (every 30 minutes).
- If Overpass stays unreliable, the fallback is a one-time Geofabrik Indonesia extract processed locally. Not built.

## Behavior shared by all clients

- 10 second timeout per request.
- Retry only on timeouts, transport errors, HTTP 429, and HTTP 5xx: at most 2 retries, 1 s then 3 s, each logged.
- HTTP 4xx other than 429 is an answer and is not retried.
- File cache keyed by a SHA-256 of source and parameters. Empty bodies are never cached.
- `FIXTURE_MODE=true` reads `fixtures/` and makes no network call. Tests block the network outright.

## Not yet integrated

- Peat layer (optional, only if an open layer is found within 30 minutes).

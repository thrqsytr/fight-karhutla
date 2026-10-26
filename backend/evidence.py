"""Evidence collection for incidents. Every stored item is a measured fact with an ID.

Shared sources have quotas (Open-Meteo: under 10,000 calls a day; Overpass: shared public servers),
so each run enriches a bounded number of incidents, highest priority first. A source that fails is
recorded as failed with its reason and retried on a later run; it never blocks the others.
"""

import json
import logging
import time
from datetime import datetime

from . import db
from .config import Settings
from .geo import haversine_m
from .sources import osm, weather
from .sources.http import SourceError, _cache_path

log = logging.getLogger(__name__)

WEATHER_BUDGET_PER_RUN = 40
OSM_BUDGET_PER_RUN = 12  # tiles, not incidents: one tile serves every incident inside it
# Satellite clusters below both thresholds are not enriched automatically; a verifier can still ask.
MIN_HOTSPOTS = 3
MIN_MAX_FRP = 20.0
# Fixtures were captured around one East Kalimantan cluster. Using them elsewhere would invent facts,
# so outside this radius a fixture-mode source is recorded as unknown instead.
FIXTURE_CENTER = (-0.02, 116.45)
FIXTURE_RADIUS_M = 15_000


def _outside_fixture(settings: Settings, lat: float, lon: float) -> bool:
    return settings.fixture_mode and haversine_m(lat, lon, *FIXTURE_CENTER) > FIXTURE_RADIUS_M


def hotspot_evidence(conn, incident_id: str) -> tuple[str, dict] | None:
    rows = conn.execute(
        "SELECT id, lat, lon, acquired_at, satellite, confidence, frp FROM hotspots WHERE incident_id=? ORDER BY acquired_at", (incident_id,)
    ).fetchall()
    if not rows:
        return None
    frps = [r["frp"] for r in rows if r["frp"] is not None]
    lat = sum(r["lat"] for r in rows) / len(rows)
    lon = sum(r["lon"] for r in rows) / len(rows)
    conf = {c: sum(1 for r in rows if r["confidence"] == c) for c in sorted({r["confidence"] for r in rows})}
    payload = {
        "lat": round(lat, 5),
        "lon": round(lon, 5),
        "count": len(rows),
        "hotspot_ids": [r["id"] for r in rows],
        "first_acquired": rows[0]["acquired_at"],
        "last_acquired": rows[-1]["acquired_at"],
        "max_frp_mw": max(frps) if frps else None,
        "confidence_counts": conf,
        "satellites": sorted({r["satellite"] for r in rows}),
    }
    names = {"l": "low", "n": "nominal", "h": "high"}
    conf_text = ", ".join(f"{n} {names.get(c, c)}" for c, n in conf.items())
    summary = f"{len(rows)} FIRMS hotspot(s), max FRP {payload['max_frp_mw']} MW, confidence: {conf_text}"
    return summary, payload


def weather_summary_text(w: dict) -> str:
    rain = "unknown" if w["rain_24h_mm"] is None else f"{w['rain_24h_mm']} mm"
    return f"Wind {w['wind_speed_ms']} m/s from {w['wind_direction_deg']} deg, humidity {w['relative_humidity_pct']}%, rain 24 h {rain}"


def osm_summary_text(a: dict) -> str:
    parts = []
    for cat, label in (("settlement", "settlement"), ("road", "road"), ("river", "river"), ("lake", "lake or pond"), ("fire_station", "fire station")):
        n = a["nearest"][cat]
        radius = a["fire_station_search_radius_m"] if cat == "fire_station" else a["search_radius_m"]
        parts.append(f"{label} {n['distance_m'] / 1000:.1f} km" + (f" ({n['name']})" if n.get("name") else "") if n else f"no {label} within {radius // 1000} km")
    return "Nearest: " + ", ".join(parts)


def image_payload(
    *,
    media_id: str,
    sha256: str,
    width: int,
    height: int,
    lat: float,
    lon: float,
    captured_at: str,
    capture_source: str,
    location_source: str,
    camera: dict | None = None,
) -> dict:
    """One shape for citizen photos and drone frames, so a drone tool can add evidence without schema changes.

    camera (drone frames only): altitude_m, heading_deg, pitch_deg, hfov_deg. From that geometry, code
    derives the ground pixel size. Segmentation later adds pixel_count and pixel_size_m, which is what
    the gate needs to compute hectares. Oblique citizen photos have no pixel size, so they never yield
    an area.
    """
    return {
        "media_id": media_id,
        "sha256": sha256,
        "width": width,
        "height": height,
        "lat": lat,
        "lon": lon,
        "captured_at": captured_at,
        "capture_source": capture_source,  # citizen | drone
        "location_source": location_source,  # gps | map_pick | telemetry
        "camera": camera,
        "segmentation": None,
    }


def _priority_candidates(conn, limit: int) -> list:
    return conn.execute(
        """SELECT i.id, i.lat, i.lon, i.trigger, i.hotspot_count, i.last_seen, i.enrichment_json,
                  (SELECT MAX(frp) FROM hotspots h WHERE h.incident_id = i.id) AS max_frp
           FROM incidents i
           WHERE i.enriched_at IS NULL OR i.enrichment_json LIKE '%failed%'
           -- Citizen reports first, then never-enriched before retries, so a failing source cannot starve new incidents.
           ORDER BY (i.trigger != 'hotspot_cluster') DESC, (i.enriched_at IS NOT NULL), i.hotspot_count DESC, max_frp DESC, i.id
           LIMIT ?""",
        (limit,),
    ).fetchall()


def enrich_incident(settings: Settings, incident_id: str, *, osm_tiles_left: list[int] | None = None) -> dict:
    """Add hotspot, weather, and OSM evidence that the incident does not have yet. Returns per-source status.

    Network calls can take minutes when Overpass is overloaded, so they run outside any transaction:
    read in one short session, fetch, then write in another. Holding SQLite's write lock across a
    network call blocked citizen reports in production ("database is locked").
    """
    with db.session(settings.db_path) as conn:
        inc = conn.execute("SELECT * FROM incidents WHERE id=?", (incident_id,)).fetchone()
        if inc is None:
            raise KeyError(incident_id)
        inc = dict(inc)
        have = {r["type"] for r in conn.execute("SELECT type FROM evidence WHERE incident_id=?", (incident_id,))}
        hs = hotspot_evidence(conn, incident_id) if "hotspot" not in have and inc["hotspot_count"] else None
    status = json.loads(inc["enrichment_json"]) if inc["enrichment_json"] else {}
    at = datetime.fromisoformat(inc["last_seen"])
    new: list[tuple[str, str, str, dict]] = []  # (type, source, summary, payload)

    if hs:
        new.append(("hotspot", "NASA FIRMS", hs[0], hs[1]))
        status["hotspot"] = "ok"

    if "weather" not in have and _outside_fixture(settings, inc["lat"], inc["lon"]):
        status["weather"] = "failed: outside fixture coverage (fixture mode)"
    elif "weather" not in have:
        try:
            w = weather.get_weather(
                round(inc["lat"], 2), round(inc["lon"], 2), at=None if settings.fixture_mode else at,
                cache_dir=settings.cache_dir, fixtures_dir=settings.fixtures_dir, fixture=settings.fixture_mode,
            )
            w["lat"], w["lon"] = inc["lat"], inc["lon"]
            new.append(("weather", "Open-Meteo", weather_summary_text(w), w))
            status["weather"] = "ok"
        except (SourceError, ValueError, KeyError) as exc:
            log.warning("weather failed for %s: %s", incident_id, exc)
            status["weather"] = f"failed: {exc}"

    if "osm" not in have and _outside_fixture(settings, inc["lat"], inc["lon"]):
        status["osm"] = "failed: outside fixture coverage (fixture mode)"
    elif "osm" not in have:
        tile = None if settings.fixture_mode else osm.tile_of(inc["lat"], inc["lon"])
        # Only a real Overpass call spends the budget: fixtures and cached tiles are free.
        cached = tile is None or _tile_cached(settings, tile)
        if osm_tiles_left is not None and not cached and osm_tiles_left[0] <= 0:
            status["osm"] = "failed: deferred, Overpass budget for this run is used"
        else:
            if osm_tiles_left is not None and not cached:
                osm_tiles_left[0] -= 1
            try:
                a = osm.get_nearby_assets(inc["lat"], inc["lon"], cache_dir=settings.cache_dir, fixtures_dir=settings.fixtures_dir, fixture=settings.fixture_mode)
                new.append(("osm", "OpenStreetMap (Overpass)", osm_summary_text(a), a))
                status["osm"] = "ok"
            except SourceError as exc:
                log.warning("osm failed for %s: %s", incident_id, exc)
                status["osm"] = f"failed: {str(exc)[:300]}"
                if osm_tiles_left is not None:
                    # Circuit breaker: when every endpoint just failed, more tiles this run would only wait
                    # on the same overloaded servers. The rest are deferred to the next run.
                    osm_tiles_left[0] = 0

    with db.session(settings.db_path) as conn:
        if conn.execute("SELECT 1 FROM incidents WHERE id=?", (incident_id,)).fetchone() is None:
            return status  # deleted while sources were being fetched
        # Re-check inside the write: a concurrent run may have stored the same type meanwhile.
        have_now = {r["type"] for r in conn.execute("SELECT type FROM evidence WHERE incident_id=?", (incident_id,))}
        for type_, source, summary, payload in new:
            if type_ not in have_now:
                db.add_evidence(conn, incident_id, type_, source, summary, payload)
        conn.execute(
            "UPDATE incidents SET enriched_at=?, enrichment_json=?, updated_at=? WHERE id=?",
            (db.utcnow(), json.dumps(status), db.utcnow(), incident_id),
        )
    return status


def _tile_cached(settings: Settings, tile: tuple[int, int]) -> bool:
    p = _cache_path(settings.cache_dir, "overpass", {"q": osm.build_tile_query(tile)})
    return p.exists() and time.time() - p.stat().st_mtime < osm.TTL_S


def run_enrichment(settings: Settings, *, max_incidents: int = WEATHER_BUDGET_PER_RUN) -> dict:
    """Enrich the highest-priority incidents that are new or had a failed source."""
    tiles_left = [OSM_BUDGET_PER_RUN]
    done, failed = [], []
    with db.session(settings.db_path) as conn:
        candidates = [dict(r) for r in _priority_candidates(conn, max_incidents * 3)]
    for row in candidates:
        if len(done) + len(failed) >= max_incidents:
            break
        eligible = row["trigger"] != "hotspot_cluster" or row["hotspot_count"] >= MIN_HOTSPOTS or (row["max_frp"] or 0) >= MIN_MAX_FRP
        if not eligible:
            continue
        try:
            st = enrich_incident(settings, row["id"], osm_tiles_left=tiles_left)
        except KeyError:
            continue  # deleted since the candidate list was read
        (failed if any(str(v).startswith("failed") for v in st.values()) else done).append(row["id"])
    log.info("enrichment: complete=%d partial=%d osm_tiles_left=%d", len(done), len(failed), tiles_left[0])
    return {"complete": done, "partial": failed, "osm_tiles_left": tiles_left[0]}

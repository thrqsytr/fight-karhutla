"""Hotspot ingestion: FIRMS to stored hotspots to clustered incidents (2 km, 24 h)."""

import logging
from datetime import datetime, timedelta, timezone

from . import db, source_status
from .config import Settings
from .geo import Point, centroid, cluster_points, haversine_m
from .sources import firms

log = logging.getLogger(__name__)

INDONESIA_FIRMS_BBOX = (95.0, -11.0, 141.0, 6.0)
CLUSTER_RADIUS_M = 2000
CLUSTER_WINDOW = timedelta(hours=24)
LOOKBACK = timedelta(hours=48)


def store_hotspots(conn, hotspots: list[firms.Hotspot]) -> int:
    now = db.utcnow()
    before = conn.total_changes
    conn.executemany(
        """INSERT OR IGNORE INTO hotspots
           (id, source, lat, lon, acquired_at, satellite, instrument, confidence, frp, daynight, ingested_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        [
            (h.id, h.source, h.lat, h.lon, h.acquired_at, h.satellite, h.instrument, h.confidence, h.frp, h.daynight, now)
            for h in hotspots
        ],
    )
    return conn.total_changes - before


def _citizen_incident_near(conn, lat: float, lon: float, at: datetime) -> str | None:
    rows = conn.execute(
        "SELECT id, lat, lon FROM incidents WHERE trigger = 'citizen_report' AND last_seen >= ?", ((at - CLUSTER_WINDOW).isoformat(),)
    ).fetchall()
    near = sorted((haversine_m(lat, lon, r["lat"], r["lon"]), r["id"]) for r in rows)
    return near[0][1] if near and near[0][0] <= CLUSTER_RADIUS_M else None


def cluster_into_incidents(conn, *, now: datetime, simulation: bool = False) -> dict:
    """Group recent hotspots. Clusters touching an existing incident extend it; the rest open new incidents."""
    since = (now - LOOKBACK).isoformat()
    rows = conn.execute("SELECT * FROM hotspots WHERE acquired_at >= ? ORDER BY acquired_at, id", (since,)).fetchall()
    by_id = {r["id"]: r for r in rows}
    points = [Point(r["id"], r["lat"], r["lon"], datetime.fromisoformat(r["acquired_at"])) for r in rows]
    created, extended = [], []
    for group in cluster_points(points, CLUSTER_RADIUS_M, CLUSTER_WINDOW):
        existing = sorted({by_id[p.id]["incident_id"] for p in group if by_id[p.id]["incident_id"]})
        unassigned = [p for p in group if not by_id[p.id]["incident_id"]]
        if not unassigned:
            continue
        lat, lon = centroid(group)
        first, last = group[0].at.isoformat(), group[-1].at.isoformat()
        if not existing:
            # A new cluster near a recent citizen report joins that report's incident (spec trigger c).
            reported = _citizen_incident_near(conn, lat, lon, group[0].at)
            if reported:
                existing = [reported]
                # enriched_at is cleared so the next run adds the hotspot evidence this incident now has.
                conn.execute("UPDATE incidents SET trigger='merged', first_seen=MIN(first_seen, ?), enriched_at=NULL WHERE id=?", (first, reported))
        if existing:
            # Merging two prior incidents is a human call; attach to the oldest and leave the others intact.
            inc_id = existing[0]
            # enriched_at is cleared so the next enrichment run stores a fresh hotspot snapshot.
            conn.execute(
                "UPDATE incidents SET lat=?, lon=?, last_seen=MAX(last_seen, ?), hotspot_count=hotspot_count+?, enriched_at=NULL, updated_at=? WHERE id=?",
                (lat, lon, last, len(unassigned), db.utcnow(), inc_id),
            )
            extended.append(inc_id)
        else:
            inc_id = db.next_incident_id(conn, group[0].at)
            ts = db.utcnow()
            conn.execute(
                """INSERT INTO incidents (id, created_at, updated_at, trigger, lat, lon, first_seen, last_seen, hotspot_count, simulation)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (inc_id, ts, ts, "hotspot_cluster", lat, lon, first, last, len(group), int(simulation)),
            )
            created.append(inc_id)
        conn.executemany("UPDATE hotspots SET incident_id=? WHERE id=?", [(inc_id, p.id) for p in unassigned])
    return {"created": created, "extended": extended}


def run_once(settings: Settings, *, now: datetime | None = None, days: int = 2) -> dict:
    now = now or datetime.now(timezone.utc)
    try:
        hotspots = firms.get_area(
            INDONESIA_FIRMS_BBOX,
            days,
            map_key=settings.firms_map_key,
            cache_dir=settings.cache_dir,
            fixtures_dir=settings.fixtures_dir,
            fixture=settings.fixture_mode,
        )
    except Exception as exc:
        if not settings.fixture_mode:
            source_status.record_firms(settings, False, str(exc))
        raise
    if not settings.fixture_mode:
        source_status.record_firms(settings, True, f"{len(hotspots)} hotspots fetched")
    if settings.fixture_mode:
        # Fixture data is frozen; anchor the lookback on its newest hotspot so clustering is reproducible.
        now = max(datetime.fromisoformat(h.acquired_at) for h in hotspots) if hotspots else now
    with db.session(settings.db_path) as conn:
        new = store_hotspots(conn, hotspots)
        result = cluster_into_incidents(conn, now=now, simulation=settings.fixture_mode)
    log.info("ingest: fetched=%d new=%d created=%d extended=%d", len(hotspots), new, len(result["created"]), len(result["extended"]))
    return {"fetched": len(hotspots), "new_hotspots": new, **result}

from backend import db, evidence, ingest
from backend.sources import http as source_http
from backend.sources.http import SourceError


def _setup(settings):
    db.init(settings.db_path)
    ingest.run_once(settings)


def test_enrichment_adds_hotspot_weather_osm_for_priority_incidents(settings):
    _setup(settings)
    r = evidence.run_enrichment(settings)
    assert r["complete"]
    with db.session(settings.db_path) as conn:
        # Incidents outside the fixture area get unknown weather and OSM, never borrowed values.
        for pid in r["partial"]:
            st = conn.execute("SELECT enrichment_json FROM incidents WHERE id=?", (pid,)).fetchone()[0]
            assert "outside fixture coverage" in st
            assert [e["type"] for e in db.list_evidence(conn, pid)] == ["hotspot"]
        inc_id = r["complete"][0]
        ev = db.list_evidence(conn, inc_id)
        types = [e["type"] for e in ev]
        assert types == ["hotspot", "weather", "osm"]
        assert [e["id"] for e in ev] == ["EV-001", "EV-002", "EV-003"]
        hs = ev[0]["payload"]
        n = conn.execute("SELECT hotspot_count FROM incidents WHERE id=?", (inc_id,)).fetchone()[0]
        assert hs["count"] == n == len(hs["hotspot_ids"])
        assert "m/s" in ev[1]["summary"] and isinstance(ev[1]["payload"]["wind_speed_ms"], (int, float))
        assert "no fire station within 30 km" in ev[2]["summary"]


def test_low_signal_clusters_are_not_enriched_automatically(settings):
    _setup(settings)
    evidence.run_enrichment(settings)
    with db.session(settings.db_path) as conn:
        rows = conn.execute(
            """SELECT i.id, i.hotspot_count, i.enriched_at, (SELECT MAX(frp) FROM hotspots h WHERE h.incident_id=i.id) AS f
               FROM incidents i"""
        ).fetchall()
    for r in rows:
        eligible = r["hotspot_count"] >= evidence.MIN_HOTSPOTS or (r["f"] or 0) >= evidence.MIN_MAX_FRP
        if not eligible:
            assert r["enriched_at"] is None


def test_enrichment_is_idempotent(settings):
    _setup(settings)
    first = evidence.run_enrichment(settings)
    second = evidence.run_enrichment(settings)
    assert second["complete"] == []
    assert set(second["partial"]) == set(first["partial"])  # unknown sources are retried, not dropped
    with db.session(settings.db_path) as conn:
        assert len(db.list_evidence(conn, first["complete"][0])) == 3
        for pid in first["partial"]:
            assert len(db.list_evidence(conn, pid)) == 1  # retries never duplicate evidence


def test_failed_source_is_recorded_and_others_still_stored(settings, monkeypatch):
    _setup(settings)
    from backend.sources import osm

    def boom(*a, **k):
        raise SourceError("all Overpass endpoints failed: 502")

    monkeypatch.setattr(osm, "get_nearby_assets", boom)
    r = evidence.run_enrichment(settings, max_incidents=1)
    inc_id = r["partial"][0]
    with db.session(settings.db_path) as conn:
        row = conn.execute("SELECT enrichment_json FROM incidents WHERE id=?", (inc_id,)).fetchone()
        assert '"osm": "failed: all Overpass endpoints failed: 502"' in row[0]
        assert [e["type"] for e in db.list_evidence(conn, inc_id)] == ["hotspot", "weather"]
    monkeypatch.undo()
    monkeypatch.setattr(source_http.httpx, "request", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    # The retry picks up only the missing source.
    st = evidence.enrich_incident(settings, inc_id)
    assert st["osm"] == "ok"
    with db.session(settings.db_path) as conn:
        assert [e["type"] for e in db.list_evidence(conn, inc_id)] == ["hotspot", "weather", "osm"]


def test_enrich_endpoint_requires_token(settings):
    from fastapi.testclient import TestClient
    from backend.main import app

    with TestClient(app) as c:
        inc_id = c.get("/api/incidents").json()["items"][0]["id"]
        assert c.post(f"/api/incidents/{inc_id}/enrich").status_code == 401
        r = c.post(f"/api/incidents/{inc_id}/enrich", headers={"Authorization": "Bearer test-token"})
        assert r.status_code == 200 and set(r.json()["sources"]) >= {"weather", "osm"}


def test_live_mode_spends_overpass_budget_only_on_uncached_tiles(settings, monkeypatch):
    _setup(settings)
    from backend.sources import osm, weather

    live = settings.model_copy(update={"fixture_mode": False})
    calls = []
    monkeypatch.setattr(weather, "get_weather", lambda *a, **k: {"wind_speed_ms": 2.0, "wind_direction_deg": 90, "relative_humidity_pct": 70, "rain_24h_mm": 0.0})
    monkeypatch.setattr(osm, "get_nearby_assets", lambda lat, lon, **k: calls.append((lat, lon)) or {
        "lat": lat, "lon": lon, "search_radius_m": 10000, "fire_station_search_radius_m": 30000,
        "nearest": {c: None for c in osm.CATEGORIES}, "osm_timestamp": None, "tile": None, "endpoint": "test"})
    monkeypatch.setattr(evidence, "_tile_cached", lambda s, t: False)
    monkeypatch.setattr(evidence, "OSM_BUDGET_PER_RUN", 2)
    r = evidence.run_enrichment(live, max_incidents=5)
    assert len(calls) == 2
    assert len(r["partial"]) == 3  # deferred, not silently skipped
    with db.session(settings.db_path) as conn:
        st = conn.execute("SELECT enrichment_json FROM incidents WHERE id=?", (r["partial"][0],)).fetchone()[0]
        assert "deferred" in st


def test_no_write_lock_is_held_while_a_source_is_slow(settings, monkeypatch):
    """Regression: a slow Overpass call inside the transaction made citizen reports fail with 'database is locked'."""
    import threading

    from backend import reports
    from backend.sources import osm

    _setup(settings)
    entered, release = threading.Event(), threading.Event()

    def slow(*a, **k):
        entered.set()
        release.wait(10)
        raise SourceError("timeout")

    monkeypatch.setattr(osm, "get_nearby_assets", slow)
    t = threading.Thread(target=evidence.run_enrichment, args=(settings,), kwargs={"max_incidents": 1})
    t.start()
    assert entered.wait(5)
    try:
        # While the source hangs, another writer must get the lock at once.
        import sqlite3

        conn = sqlite3.connect(settings.db_path, timeout=0.5)
        conn.row_factory = sqlite3.Row
        try:
            reports.submit(conn, settings.data_dir, lat=-3.0, lon=104.7, accuracy_m=5, location_source="gps",
                           text="asap", photos=[], audio=None)
            conn.commit()
        finally:
            conn.close()
    finally:
        release.set()
        t.join(10)


def test_overpass_failure_stops_further_osm_calls_this_run(settings, monkeypatch):
    _setup(settings)
    from backend.sources import osm, weather

    live = settings.model_copy(update={"fixture_mode": False})
    calls = []
    monkeypatch.setattr(weather, "get_weather", lambda *a, **k: {"wind_speed_ms": 2.0, "wind_direction_deg": 90, "relative_humidity_pct": 70, "rain_24h_mm": 0.0})

    def down(lat, lon, **k):
        calls.append((lat, lon))
        raise SourceError("all Overpass endpoints failed: 502")

    monkeypatch.setattr(osm, "get_nearby_assets", down)
    monkeypatch.setattr(evidence, "_tile_cached", lambda s, t: False)
    r = evidence.run_enrichment(live, max_incidents=5)
    assert len(calls) == 1
    with db.session(settings.db_path) as conn:
        sts = [conn.execute("SELECT enrichment_json FROM incidents WHERE id=?", (i,)).fetchone()[0] for i in r["partial"]]
    assert sum("all Overpass endpoints failed" in s for s in sts) == 1
    assert sum("deferred" in s for s in sts) == len(sts) - 1


def test_hotspot_snapshot_is_refreshed_when_the_cluster_grows(settings):
    _setup(settings)
    r = evidence.run_enrichment(settings)
    inc_id = r["complete"][0]
    with db.session(settings.db_path) as conn:
        n = conn.execute("SELECT hotspot_count FROM incidents WHERE id=?", (inc_id,)).fetchone()[0]
        # Simulate two new detections joining the cluster after the first snapshot.
        h = conn.execute("SELECT * FROM hotspots WHERE incident_id=? LIMIT 1", (inc_id,)).fetchone()
        for k in (1, 2):
            conn.execute("INSERT INTO hotspots (id, source, lat, lon, acquired_at, satellite, instrument, confidence, frp, daynight, incident_id, ingested_at) "
                         "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (f"{h['id']}-new{k}", h["source"], h["lat"], h["lon"], h["acquired_at"], h["satellite"],
                                                             h["instrument"], "h", 50.0, h["daynight"], inc_id, h["ingested_at"]))
        conn.execute("UPDATE incidents SET hotspot_count=hotspot_count+2, enriched_at=NULL WHERE id=?", (inc_id,))
    evidence.enrich_incident(settings, inc_id)
    with db.session(settings.db_path) as conn:
        snaps = [e for e in db.list_evidence(conn, inc_id) if e["type"] == "hotspot"]
    assert [s["payload"]["count"] for s in snaps] == [n, n + 2]
    assert snaps[-1]["payload"]["max_frp_mw"] == 50.0
    # Unchanged cluster: no duplicate snapshot.
    evidence.enrich_incident(settings, inc_id)
    with db.session(settings.db_path) as conn:
        assert sum(1 for e in db.list_evidence(conn, inc_id) if e["type"] == "hotspot") == 2

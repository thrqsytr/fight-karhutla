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
    with db.session(settings.db_path) as conn:
        st = evidence.enrich_incident(conn, settings, inc_id)
        assert st["osm"] == "ok"
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

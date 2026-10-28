from fastapi.testclient import TestClient


def client(settings):
    from backend.main import app

    return TestClient(app)


AUTH = {"Authorization": "Bearer test-token"}


def test_health_and_fixture_ingest_on_startup(settings):
    with client(settings) as c:
        h = c.get("/health").json()
        assert h["status"] == "ok" and h["fixture_mode"] is True
        assert h["incidents"] > 0
        inc = c.get("/api/incidents").json()
        assert inc["count"] == h["incidents"] == len(inc["items"])
        assert all(i["simulation"] for i in inc["items"])


def test_public_coordinates_are_rounded(settings):
    with client(settings) as c:
        pub = c.get("/api/incidents").json()["items"][0]
        priv = next(i for i in c.get("/api/incidents", headers=AUTH).json()["items"] if i["id"] == pub["id"])
        assert pub["lat"] == round(priv["lat"], 3)


def test_verdict_requires_token_and_is_read_back(settings):
    with client(settings) as c:
        inc_id = c.get("/api/incidents").json()["items"][0]["id"]
        assert c.post(f"/api/incidents/{inc_id}/verdict", json={"decision": "approve"}).status_code == 401
        r = c.post(f"/api/incidents/{inc_id}/verdict", json={"decision": "approve"}, headers=AUTH)
        assert r.status_code == 200 and r.json()["decision"] == "approve"
        detail = c.get(f"/api/incidents/{inc_id}").json()
        assert detail["verdicts"][0]["decision"] == "approve"
        assert detail["hotspot_count"] == len(detail["hotspots"])


def test_unknown_incident_404(settings):
    with client(settings) as c:
        assert c.get("/api/incidents/INC-00000000-0000").status_code == 404


def test_config_js_hides_google_key_when_maplibre(settings):
    with client(settings) as c:
        body = c.get("/config.js").text
        assert '"mapProvider": "maplibre"' in body and '"googleMapsKey": ""' in body


def test_incidents_carry_measured_severity(settings):
    with client(settings) as c:
        items = c.get("/api/incidents").json()["items"]
        assert all("max_frp" in i and "high_conf_count" in i for i in items)
        top = max(items, key=lambda i: i["max_frp"] or 0)
        detail = c.get(f"/api/incidents/{top['id']}").json()
        assert detail["max_frp"] == max(h["frp"] for h in detail["hotspots"])


def test_stats_counts_match_lists(settings):
    with client(settings) as c:
        s = c.get("/api/stats").json()
        assert s["incidents"] == c.get("/api/incidents", headers=AUTH).json()["count"]
        assert s["hotspots"] == c.get("/api/hotspots").json()["count"]
        assert 0 < s["hotspots_last_24h"] <= s["hotspots"]


def test_large_lists_are_gzipped(settings):
    with client(settings) as c:
        r = c.get("/api/hotspots", headers={"Accept-Encoding": "gzip"})
        assert r.headers.get("content-encoding") == "gzip"


def test_unchanged_list_returns_304_on_revalidation(settings):
    with client(settings) as c:
        first = c.get("/api/incidents")
        tag = first.headers["etag"]
        again = c.get("/api/incidents", headers={"If-None-Match": tag})
        assert again.status_code == 304 and again.content == b""
        assert c.get("/api/incidents", headers={"If-None-Match": '"stale"'}).status_code == 200


def test_citizen_report_is_public_only_after_approval(settings):
    from backend import db

    with client(settings) as c:
        with db.session(settings.db_path) as conn:
            ts = db.utcnow()
            conn.execute(
                "INSERT INTO incidents (id, created_at, updated_at, trigger, lat, lon, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?)",
                ("INC-20261027-9001", ts, ts, "citizen_report", -0.5, 116.0, ts, ts),
            )
        ids = lambda h=None: {i["id"] for i in c.get("/api/incidents", headers=h or {}).json()["items"]}
        assert "INC-20261027-9001" not in ids()
        assert "INC-20261027-9001" in ids(AUTH)
        assert c.get("/api/incidents/INC-20261027-9001").status_code == 404
        c.post("/api/incidents/INC-20261027-9001/verdict", json={"decision": "reject"}, headers=AUTH)
        assert "INC-20261027-9001" not in ids()
        # Changing a decision means revoking it first; both steps stay on record.
        assert c.post("/api/incidents/INC-20261027-9001/verdict", json={"decision": "revoke"}, headers=AUTH).status_code == 200
        c.post("/api/incidents/INC-20261027-9001/verdict", json={"decision": "approve"}, headers=AUTH)
        assert "INC-20261027-9001" in ids()
        assert c.get("/api/incidents/INC-20261027-9001").status_code == 200


def test_static_files_are_revalidated_not_heuristically_cached(settings):
    with client(settings) as c:
        r = c.get("/app.js")
        assert r.status_code == 200 and r.headers["cache-control"] == "no-cache" and r.headers.get("etag")
        assert c.get("/app.js", headers={"If-None-Match": r.headers["etag"]}).status_code == 304


def test_source_status_reports_last_real_result(settings):
    import json

    from backend import db, source_status

    with client(settings) as c:
        s = c.get("/api/sources").json()
        assert s["firms"]["state"] == "sim" and s["firms"]["newest_acquisition"]
        assert s["weather"]["state"] == "sim"  # fixture answers never read as live
        with db.session(settings.db_path) as conn:
            ids = [r[0] for r in conn.execute("SELECT id FROM incidents ORDER BY id LIMIT 2")]
            # The newest result is a skipped call, so the one before it decides: a real Overpass failure.
            conn.execute("UPDATE incidents SET enriched_at='2099-01-01T00:00:02+00:00', enrichment_json=? WHERE id=?",
                         (json.dumps({"osm": "failed: deferred, Overpass budget for this run is used"}), ids[0]))
            conn.execute("UPDATE incidents SET enriched_at='2099-01-01T00:00:01+00:00', enrichment_json=? WHERE id=?",
                         (json.dumps({"osm": "failed: all Overpass endpoints timed out"}), ids[1]))
        osm = c.get("/api/sources").json()["osm"]
        assert osm["state"] == "error" and "timed out" in osm["detail"]
    settings.fixture_mode = False
    source_status.record(settings, "firms", False, "HTTP 503")
    assert source_status.snapshot(settings)["firms"]["state"] == "error"


def test_information_pages_are_served_in_both_languages(settings):
    with client(settings) as c:
        for slug in ("about", "sources", "privacy", "terms", "license"):
            r = c.get(f"/{slug}/")
            assert r.status_code == 200, slug
            assert 'article class="card doc-body" lang="id"' in r.text and 'lang="en"' in r.text


def test_a_decision_can_be_revoked_and_stays_in_history(settings):
    with client(settings) as c:
        items = c.get("/api/incidents", headers=AUTH).json()["items"]
        inc_id = items[0]["id"]
        post = lambda d: c.post(f"/api/incidents/{inc_id}/verdict", json={"decision": d, "note": "test"}, headers=AUTH)
        assert post("revoke").status_code == 409  # nothing to revoke yet
        assert post("approve").status_code == 200
        assert post("approve").status_code == 409  # one standing decision at a time
        listed = next(i for i in c.get("/api/incidents", headers=AUTH).json()["items"] if i["id"] == inc_id)
        assert listed["latest_verdict"] == "approve"
        assert post("revoke").status_code == 200
        listed = next(i for i in c.get("/api/incidents", headers=AUTH).json()["items"] if i["id"] == inc_id)
        assert listed["latest_verdict"] is None
        assert [v["decision"] for v in c.get(f"/api/incidents/{inc_id}", headers=AUTH).json()["verdicts"]] == ["approve", "revoke"]
        assert post("reject").status_code == 200


def test_old_verdict_table_is_migrated_with_its_rows(tmp_path):
    import sqlite3

    from backend import db

    path = tmp_path / "old.sqlite3"
    old = db.SCHEMA.replace("CHECK (decision IN ('approve', 'reject', 'revoke'))", "CHECK (decision IN ('approve', 'reject'))")
    conn = sqlite3.connect(path)
    conn.executescript(old)
    conn.execute("INSERT INTO incidents (id, trigger, status, lat, lon, first_seen, last_seen, hotspot_count, simulation, created_at, updated_at) VALUES ('INC-1','hotspot_cluster','pending_triage',0,116,'x','x',1,0,'x','x')")
    conn.execute("INSERT INTO verdicts (incident_id, verifier_hash, decision, channel, created_at) VALUES ('INC-1','h','approve','dashboard','t')")
    conn.commit()
    conn.close()
    db.init(path)
    with db.session(path) as c:
        assert [tuple(r) for r in c.execute("SELECT id, decision FROM verdicts")] == [(1, "approve")]
        c.execute("INSERT INTO verdicts (incident_id, verifier_hash, decision, channel, created_at) VALUES ('INC-1','h','revoke','dashboard','t')")

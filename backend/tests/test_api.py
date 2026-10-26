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


def test_replay_not_built_says_so(settings):
    with client(settings) as c:
        assert c.post("/api/replay/start", headers=AUTH).status_code == 501


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

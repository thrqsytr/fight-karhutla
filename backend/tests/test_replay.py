"""Replay mode: recorded cases load as SIMULASI without network, once, and are removed cleanly."""

import json

import pytest
from fastapi.testclient import TestClient

from backend import db, replay
from backend.config import REPO_ROOT, get_settings

AUTH = {"Authorization": "Bearer test-token"}


def write_case(root, slug, role, first_seen="2025-08-28T06:00:00+00:00"):
    folder = root / "incidents" / slug
    (folder / "satellite").mkdir(parents=True)
    (folder / "satellite" / "20251007T000000_overlay.png").write_bytes(b"\x89PNG fake")
    triage = {"incident_id": "{INC}", "status": "possible_fire", "claims": [{"text": "one hotspot", "evidence_ids": ["EV-001"]}]}
    case = {
        "slug": slug, "role": role, "title": {"id": slug, "en": slug}, "note": {"id": "", "en": ""}, "recorded_with": "test",
        "incident": {"trigger": "hotspot_cluster", "lat": -1.1, "lon": 110.4, "first_seen": first_seen, "last_seen": first_seen,
                     "hotspot_count": 1, "status": "possible_fire", "confidence": 0.5, "risk_score": 40,
                     "triage_json": json.dumps(triage), "gate_json": json.dumps({"claims_total": 1, "claims_passed": 1})},
        "hotspots": [{"id": "{INC}-H000", "source": "VIIRS_SNPP_SP", "lat": -1.1, "lon": 110.4, "acquired_at": first_seen,
                      "satellite": "N", "instrument": "VIIRS", "confidence": "n", "frp": 12.0, "daynight": ""}],
        "evidence": [{"id": "EV-001", "type": "hotspot", "source": "NASA FIRMS", "retrieved_at": first_seen, "summary": "1 FIRMS hotspot",
                      "payload": {"count": 1, "hotspot_ids": ["{INC}-H000"]}}],
        "runs": [{"started_at": first_seen, "finished_at": first_seen, "outcome": "ok", "seconds": 30.0, "cost_usd": 0.05, "detail": {"tools": []}}],
    }
    (folder / "case.json").write_text(json.dumps(case), encoding="utf-8")


@pytest.fixture
def replayed(settings, tmp_path, monkeypatch):
    root = tmp_path / "replay-src"
    for slug, role in (("burned", "burned"), ("false-positive", "false_positive"), ("needs-more-info", "needs_more_info")):
        write_case(root, slug, role)
    monkeypatch.setenv("REPLAY_DIR", str(root))
    get_settings.cache_clear()
    s = get_settings()
    db.init(s.db_path)
    return s


def test_start_loads_three_simulated_incidents_once(replayed):
    from backend.main import app

    with TestClient(app) as c:
        r = c.post("/api/replay/start", headers=AUTH)
        assert r.status_code == 200 and r.json()["started"] is True
        ids = r.json()["incidents"]
        assert ids == ["INC-20250828-0001", "INC-20250828-0002", "INC-20250828-0003"]
        again = c.post("/api/replay/start", headers=AUTH).json()
        assert again == {"started": False, "incidents": ids}

        inc = c.get(f"/api/incidents/{ids[0]}", headers=AUTH).json()
        assert inc["simulation"] is True and inc["status"] == "possible_fire"
        assert inc["triage"]["incident_id"] == ids[0]  # the {INC} token is filled in
        assert inc["evidence"][0]["id"] == "EV-001" and inc["evidence"][0]["payload"]["hotspot_ids"] == [f"{ids[0]}-H000"]
        assert inc["hotspots"][0]["id"] == f"{ids[0]}-H000"
        assert (replayed.data_dir / "satellite" / ids[0] / "20251007T000000_overlay.png").exists()

        overview = c.get("/api/admin/overview", headers=AUTH).json()
        assert overview["replay"]["loaded"] == ids and [c["role"] for c in overview["replay"]["cases"]] == list(replay.ROLES)
        assert overview["spend_total"]["runs"] == 0  # recorded runs are not spend on this server


def test_stop_removes_only_replayed_incidents(replayed):
    from backend.main import app

    with db.session(replayed.db_path) as conn:
        conn.execute("""INSERT INTO incidents (id, created_at, updated_at, trigger, lat, lon, first_seen, last_seen, hotspot_count)
                        VALUES ('INC-20261028-0001', 'x', 'x', 'hotspot_cluster', 0, 0, 'x', 'x', 1)""")
    with TestClient(app) as c:
        ids = c.post("/api/replay/start", headers=AUTH).json()["incidents"]
        # A stored list that names a real incident must not delete it.
        with db.session(replayed.db_path) as conn:
            conn.execute("UPDATE app_state SET value=? WHERE key=?", (json.dumps(ids + ["INC-20261028-0001"]), replay.STATE_KEY))
        r = c.delete("/api/replay", headers=AUTH)
        assert r.status_code == 200
        with db.session(replayed.db_path) as conn:
            left = [x["id"] for x in conn.execute("SELECT id FROM incidents")]
            assert left == ["INC-20261028-0001"]
            for table in ("evidence", "hotspots", "agent_runs"):
                assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert not (replayed.data_dir / "satellite" / ids[0]).exists()
        assert c.get("/api/replay", headers=AUTH).json()["loaded"] == []


def test_replay_needs_a_verifier(replayed):
    from backend.main import app

    with TestClient(app) as c:
        assert c.post("/api/replay/start").status_code == 401
        assert c.delete("/api/replay").status_code == 401


def test_recorded_cases_in_the_repo_load(settings, monkeypatch):
    """The shipped cases: one of each role, each with stored triage and gate results."""
    found = replay.cases(REPO_ROOT / "replay")
    if not found:
        pytest.skip("no recorded cases yet")
    assert sorted(c["role"] for c in found) == sorted(replay.ROLES)
    monkeypatch.setenv("REPLAY_DIR", str(REPO_ROOT / "replay"))
    get_settings.cache_clear()
    s = get_settings()
    db.init(s.db_path)
    ids = replay.start(s)["incidents"]
    with db.session(s.db_path) as conn:
        for i in ids:
            row = conn.execute("SELECT simulation, triage_json, gate_json FROM incidents WHERE id=?", (i,)).fetchone()
            assert row["simulation"] == 1 and json.loads(row["triage_json"])["incident_id"] == i and row["gate_json"]


def test_sync_refreshes_replay_text_and_keeps_what_verifiers_added(replayed, tmp_path):
    from backend.main import app

    with TestClient(app) as c:
        ids = c.post("/api/replay/start", headers=AUTH).json()["incidents"]
        with db.session(replayed.db_path) as conn:
            conn.execute("INSERT INTO verdicts (incident_id, verifier_hash, decision, note, channel, created_at) VALUES (?,?,?,?,?,?)",
                         (ids[0], "v", "approve", None, "dashboard", "2026-10-29T00:00:00+00:00"))
    # The case file gains an Indonesian twin after the replay was loaded.
    path = tmp_path / "replay-src" / "incidents" / "burned" / "case.json"
    case = json.loads(path.read_text(encoding="utf-8"))
    tri = json.loads(case["incident"]["triage_json"])
    tri["claims"][0]["text_id"] = "satu titik panas di {INC}"
    case["incident"]["triage_json"] = json.dumps(tri)
    path.write_text(json.dumps(case), encoding="utf-8")

    assert replay.sync_text(replayed) == [ids[0]]
    assert replay.sync_text(replayed) == []  # nothing left to change
    with db.session(replayed.db_path) as conn:
        stored = json.loads(conn.execute("SELECT triage_json FROM incidents WHERE id=?", (ids[0],)).fetchone()[0])
        verdicts = conn.execute("SELECT COUNT(*) FROM verdicts WHERE incident_id=?", (ids[0],)).fetchone()[0]
    assert stored["claims"][0]["text_id"] == f"satu titik panas di {ids[0]}" and verdicts == 1

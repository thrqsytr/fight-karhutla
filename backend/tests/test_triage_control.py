from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend import db, triage_control

AUTH = {"Authorization": "Bearer test-token"}
CODE = "K4RHUTLA-77"


@pytest.fixture(autouse=True)
def reset_attempts():
    triage_control._failed.clear()
    yield
    triage_control._failed.clear()


@pytest.fixture
def live(settings, monkeypatch):
    s = settings.model_copy(update={"model_api_key": "test", "execution_code": CODE, "triage_batch_limit": 3, "triage_daily_cap": 4})
    from backend.config import get_settings
    from backend.main import app

    app.dependency_overrides[get_settings] = lambda: s
    ran = []
    monkeypatch.setattr("backend.main._run_triage", lambda settings, i: ran.append(i))
    with TestClient(app) as c:
        yield s, c, ran
    app.dependency_overrides.clear()


def ids(c, n):
    return [i["id"] for i in c.get("/api/incidents", headers=AUTH).json()["items"][:n]]


def test_batch_needs_token_code_and_respects_limit(live):
    s, c, ran = live
    three = ids(c, 3)
    assert c.post("/api/triage/batch", json={"incident_ids": three, "code": CODE}).status_code == 401
    r = c.post("/api/triage/batch", json={"incident_ids": three, "code": "nope"}, headers=AUTH)
    assert r.status_code == 403 and "attempts left" in r.json()["detail"]
    too_many = ids(c, 4)
    r = c.post("/api/triage/batch", json={"incident_ids": too_many, "code": CODE}, headers=AUTH)
    assert r.status_code == 422 and "at most 3" in r.json()["detail"]
    r = c.post("/api/triage/batch", json={"incident_ids": three, "code": CODE}, headers=AUTH)
    assert r.status_code == 202 and r.json()["accepted"] == three
    assert r.json()["est_cost_usd"] == round(3 * triage_control.EST_COST_USD, 3)
    assert ran == three


def test_unknown_incident_is_rejected_before_anything_runs(live):
    s, c, ran = live
    r = c.post("/api/triage/batch", json={"incident_ids": [ids(c, 1)[0], "INC-00000000-0000"], "code": CODE}, headers=AUTH)
    assert r.status_code == 404 and ran == []


def test_wrong_codes_lock_out_after_five_attempts(live):
    s, c, ran = live
    one = ids(c, 1)
    for _ in range(triage_control.CODE_ATTEMPTS):
        assert c.post("/api/triage/batch", json={"incident_ids": one, "code": "bad"}, headers=AUTH).status_code == 403
    # Even the right code is refused during the lockout window.
    assert c.post("/api/triage/batch", json={"incident_ids": one, "code": CODE}, headers=AUTH).status_code == 429
    assert ran == []


def test_daily_cap_counts_every_run_today(live):
    s, c, ran = live
    with db.session(s.db_path) as conn:
        for i in range(3):
            conn.execute("INSERT INTO agent_runs (incident_id, started_at, outcome) VALUES (?,?,?)", (f"X{i}", db.utcnow(), "ok"))
    two = ids(c, 2)
    r = c.post("/api/triage/batch", json={"incident_ids": two, "code": CODE}, headers=AUTH)
    assert r.status_code == 429 and "1 left today" in r.json()["detail"]
    assert c.post(f"/api/incidents/{two[0]}/triage", json={"code": CODE}, headers=AUTH).status_code == 202
    st = c.get("/api/triage/control", headers=AUTH).json()
    assert st["daily_cap"] == 4 and st["today"]["runs"] == 3  # the stubbed run does not insert a row


def test_mode_defaults_to_daily_and_needs_code_to_change(live):
    s, c, ran = live
    st = c.get("/api/triage/control", headers=AUTH).json()
    assert st["mode"] == "daily" and st["auto_scope"] == "citizen-reported incidents only"
    assert c.put("/api/triage/control/mode", json={"mode": "hourly", "code": "bad"}, headers=AUTH).status_code == 403
    assert c.put("/api/triage/control/mode", json={"mode": "every-minute", "code": CODE}, headers=AUTH).status_code == 422
    r = c.put("/api/triage/control/mode", json={"mode": "hourly", "code": CODE}, headers=AUTH)
    assert r.status_code == 200 and r.json()["mode"] == "hourly"


def test_schedule_runs_once_per_interval_on_citizen_incidents_only(live):
    s, c, ran = live
    from backend import reports
    from backend.main import auto_triage

    with db.session(s.db_path) as conn:
        r = reports.submit(conn, s.data_dir, lat=-3.0, lon=104.7, accuracy_m=10, location_source="gps", text="asap", photos=[], audio=None)
    t0 = datetime.now(timezone.utc)
    assert auto_triage(s) == [r["incident_id"]]  # first pass is due; hotspot-only incidents are not touched
    assert auto_triage(s) == []  # daily: not due again
    with db.session(s.db_path) as conn:
        assert triage_control.due_auto_run(conn, t0 + timedelta(hours=23)) is False
        assert triage_control.due_auto_run(conn, t0 + timedelta(hours=24, minutes=1)) is True
        triage_control.set_mode(conn, "hourly")
        assert triage_control.due_auto_run(conn, t0 + timedelta(minutes=61)) is True
        triage_control.set_mode(conn, "off")
        assert triage_control.due_auto_run(conn, t0 + timedelta(days=3)) is False
    assert ran == [r["incident_id"]]


def test_no_execution_code_configured_means_no_runs(settings):
    s = settings.model_copy(update={"model_api_key": "test", "execution_code": ""})
    with pytest.raises(triage_control.ControlError) as e:
        triage_control.check_code(s, "anything", "1.2.3.4")
    assert e.value.status == 503


def test_day_boundary_is_wita():
    # 2026-10-28 15:30 UTC is 23:30 WITA, so the day started at 2026-10-27 16:00 UTC.
    assert triage_control.day_start_utc(datetime(2026, 10, 28, 15, 30, tzinfo=timezone.utc)) == datetime(2026, 10, 27, 16, 0, tzinfo=timezone.utc)

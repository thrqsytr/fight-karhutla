"""Admin settings: bounded values, token plus execution code to change, and their effect."""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from backend import admin_settings, db, triage_control

AUTH = {"Authorization": "Bearer test-token"}


def client(settings, code="exec-code"):
    settings.execution_code = code
    from backend.main import app

    return TestClient(app)


def test_overview_needs_the_verifier_token(settings):
    with client(settings) as c:
        assert c.get("/api/admin/overview").status_code == 401
        assert c.get("/api/auth/check").status_code == 401
        assert c.get("/api/auth/check", headers=AUTH).json() == {"ok": True}
        o = c.get("/api/admin/overview", headers=AUTH).json()
        keys = {s["key"] for s in o["settings"]}
        assert {"firms_interval_minutes", "triage_daily_cap", "satellite_fresh_hours"} <= keys
        assert o["triage"]["daily_cap"] == settings.triage_daily_cap  # the environment gives the default


def test_change_needs_the_code_and_valid_values(settings):
    with client(settings) as c:
        body = {"values": {"firms_interval_minutes": 1440}, "code": "wrong"}
        assert c.put("/api/admin/settings", json=body, headers=AUTH).status_code == 403
        assert c.put("/api/admin/settings", json={**body, "code": "exec-code"}).status_code == 401
        bad = c.put("/api/admin/settings", json={"values": {"firms_interval_minutes": 7}, "code": "exec-code"}, headers=AUTH)
        assert bad.status_code == 422 and "one of" in bad.json()["detail"]
        too_high = c.put("/api/admin/settings", json={"values": {"triage_daily_cap": 1000}, "code": "exec-code"}, headers=AUTH)
        assert too_high.status_code == 422
        unknown = c.put("/api/admin/settings", json={"values": {"model": 1}, "code": "exec-code"}, headers=AUTH)
        assert unknown.status_code == 422
        ok = c.put("/api/admin/settings", json={"values": {"firms_interval_minutes": 1440, "triage_batch_limit": 2}, "code": "exec-code"}, headers=AUTH)
        assert ok.status_code == 200 and ok.json()["changed"] == ["firms_interval_minutes", "triage_batch_limit"]
        assert ok.json()["triage"]["batch_limit"] == 2


def test_stored_limits_drive_triage(settings):
    with client(settings) as c:
        ids = [i["id"] for i in c.get("/api/incidents").json()["items"][:3]]
    with db.session(settings.db_path) as conn:
        admin_settings.update(settings, conn, {"triage_batch_limit": 2})
        try:
            triage_control.validate_batch(settings, conn, ids)
            raise AssertionError("expected the stored limit of 2 to refuse 3 incidents")
        except triage_control.ControlError as exc:
            assert exc.status == 422 and "at most 2" in exc.detail


def test_fetch_waits_for_the_chosen_interval(settings):
    now = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
    db.init(settings.db_path)
    with db.session(settings.db_path) as conn:
        assert admin_settings.fetch_due(settings, conn, now)  # never fetched
        admin_settings.update(settings, conn, {"firms_interval_minutes": 360})
        admin_settings.mark_fetch(conn, now)
        assert not admin_settings.fetch_due(settings, conn, now + timedelta(hours=5))
        assert admin_settings.fetch_due(settings, conn, now + timedelta(hours=6))
        assert admin_settings.schedule(settings, conn)["next_fetch"] == "2026-10-28T18:00:00+00:00"

"""Drone survey proposals: bounded plans, one open proposal per incident, human decision."""

from fastapi.testclient import TestClient

from backend import db, drone, drone_missions, tools
from backend.meta_client import Meter

AUTH = {"Authorization": "Bearer test-token"}


def test_plan_fits_the_limits_even_for_a_wide_cluster():
    wide = [(0.0, 116.40), (0.05, 116.47)]  # about 8 km across
    plan = drone_missions.plan_for(wide)
    assert plan["area_km2"] <= drone.MAX_SURVEY_KM2 and len(plan["waypoints"]) <= drone.MAX_FRAMES
    assert plan["estimate"]["flight_minutes"] > 0 and plan["estimate"]["batteries"] >= 1


def test_one_open_proposal_and_a_single_decision(settings):
    from backend.main import app

    with TestClient(app) as c:
        inc_id = c.get("/api/incidents").json()["items"][0]["id"]
        assert c.post(f"/api/incidents/{inc_id}/drone-proposals", json={"reason": "clouds"}).status_code == 401
        first = c.post(f"/api/incidents/{inc_id}/drone-proposals", json={"reason": "clouds"}, headers=AUTH).json()
        again = c.post(f"/api/incidents/{inc_id}/drone-proposals", json={"reason": "again"}, headers=AUTH).json()
        assert first["created"] and not again["created"] and again["id"] == first["id"]
        decide = lambda d: c.post(f"/api/drone-proposals/{first['id']}/decision", json={"decision": d}, headers=AUTH)
        assert decide("approve").json()["status"] == "approved"
        assert decide("reject").status_code == 409
        listed = c.get(f"/api/incidents/{inc_id}/drone-proposals", headers=AUTH).json()
        assert listed["count"] == 1 and listed["items"][0]["plan"]["waypoints"]


def test_agent_tool_returns_a_plan_not_an_observation(settings):
    with TestClient(__import__("backend.main", fromlist=["app"]).app) as c:
        inc_id = c.get("/api/incidents").json()["items"][1]["id"]
    ctx = tools.ToolContext(settings=settings, incident_id=inc_id, meter=Meter())
    out = tools.call(ctx, "plan_drone_survey", '{"reason": "no citizen photo and cloud cover"}')
    assert out["status"] == "proposed" and out["frames"] > 0 and "evidence_id" not in out
    assert "No drone flies until a human verifier approves it" in out["note"]
    with db.session(settings.db_path) as conn:
        assert drone_missions.list_for(conn, inc_id)[0]["source"] == "agent"

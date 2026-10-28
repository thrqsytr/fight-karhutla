"""Simulated pilot flights: deterministic telemetry, and the order a pilot must follow."""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from backend import db, drone_flight, drone_missions

AUTH = {"Authorization": "Bearer test-token"}


def plan():
    return drone_missions.plan_for([(-0.02, 116.45), (-0.021, 116.452)])


def test_telemetry_follows_the_plan_and_the_wind():
    p = plan()
    calm = drone_flight.simulate(p, {"speed_ms": 0.0, "from_deg": None})
    windy = drone_flight.simulate(p, {"speed_ms": 6.0, "from_deg": 0.0})  # from the north
    assert [s["seq"] for s in calm["samples"]] == [w["seq"] for w in p["waypoints"]]
    assert calm == drone_flight.simulate(p, {"speed_ms": 0.0, "from_deg": None})  # deterministic
    assert all(s["ground_speed_ms"] == drone_flight.AIRSPEED_MS for s in calm["samples"])
    # Flying south with a north wind is faster than flying north into it.
    # Only legs along a line: the first frame of each line follows an east-west crossing with no wind along it.
    legs = [s for prev, s in zip(windy["samples"], windy["samples"][1:]) if s["lon"] == prev["lon"]]
    south = [s for s in legs if s["heading_deg"] == 180.0]
    north = [s for s in legs if s["heading_deg"] == 0.0]
    assert south and north and min(s["ground_speed_ms"] for s in south) > max(s["ground_speed_ms"] for s in north)
    assert windy["samples"][-1]["battery_pct"] < calm["samples"][-1]["battery_pct"] or windy["packs"] > calm["packs"]
    assert calm["events"][0]["kind"] == "takeoff" and calm["events"][-1]["kind"] == "landing"


def test_a_long_flight_swaps_packs():
    p = plan()
    long = {**p, "waypoints": p["waypoints"] * 3}
    t = drone_flight.simulate(long, {"speed_ms": 0.0, "from_deg": None})
    swaps = [e for e in t["events"] if e["kind"] == "battery_swap"]
    assert swaps and t["packs"] == len(swaps) + 1
    assert all(s["battery_pct"] >= drone_flight.SWAP_BELOW_PCT - 5 for s in t["samples"])


def test_pilot_flow(settings):
    from backend.main import app

    with TestClient(app) as c:
        inc_id = c.get("/api/incidents").json()["items"][0]["id"]
        p = c.post(f"/api/incidents/{inc_id}/drone-proposals", json={"reason": "check"}, headers=AUTH).json()
        assert c.post(f"/api/drone-proposals/{p['id']}/flight", json={"speed": 30}, headers=AUTH).status_code == 409  # not approved yet
        c.post(f"/api/drone-proposals/{p['id']}/decision", json={"decision": "approve"}, headers=AUTH)
        assert c.post(f"/api/drone-proposals/{p['id']}/flight", json={"speed": 7}, headers=AUTH).status_code == 422
        f = c.post(f"/api/drone-proposals/{p['id']}/flight", json={"speed": 60}, headers=AUTH).json()
        assert f["state"] == "flying" and f["simulation"] and f["telemetry"]["wind"]["evidence_id"]  # fixture weather evidence
        assert c.post(f"/api/drone-proposals/{p['id']}/flight", json={"speed": 60}, headers=AUTH).status_code == 409  # one at a time
        assert c.post(f"/api/drone-proposals/{p['id']}/decision", json={"decision": "cancel"}, headers=AUTH).status_code == 409  # in the air
        finish = lambda o: c.post(f"/api/drone-flights/{f['id']}/finish", json={"outcome": o}, headers=AUTH)
        assert finish("completed").status_code == 409  # route not done
        assert finish("aborted").json()["state"] == "aborted"
        # After an abort the plan can fly again; listing shows the latest flight.
        f2 = c.post(f"/api/drone-proposals/{p['id']}/flight", json={"speed": 60}, headers=AUTH).json()
        listed = c.get(f"/api/incidents/{inc_id}/drone-proposals", headers=AUTH).json()["items"][0]
        assert listed["flight"]["id"] == f2["id"]

        # Land once the sim clock has passed the route: move the start back.
        with db.session(settings.db_path) as conn:
            early = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
            conn.execute("UPDATE drone_flights SET started_at=? WHERE id=?", (early, f2["id"]))
        done = c.post(f"/api/drone-flights/{f2['id']}/finish", json={"outcome": "completed"}, headers=AUTH).json()
        assert done["state"] == "completed" and done["route_done"] and done["frames_captured"] == len(p["plan"]["waypoints"])
        # A flown plan no longer blocks a new one.
        again = c.post(f"/api/incidents/{inc_id}/drone-proposals", json={"reason": "second pass"}, headers=AUTH).json()
        assert again["created"]
        assert c.post(f"/api/drone-proposals/{p['id']}/flight", json={"speed": 60}, headers=AUTH).status_code == 409  # already flown

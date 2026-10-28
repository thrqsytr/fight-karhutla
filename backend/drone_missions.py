"""Drone survey proposals: planned by the agent or a verifier, flown only after a human approves.

A proposal is a plan, not a flight: the area, altitude, frame count, and an estimate of flight time.
No drone is connected in this build; an approved proposal waits for a pilot. Planning uses the same
geometry as the simulator (backend/drone.py), so a real MAVLink mission could be generated from it.
"""

import json
import math

from . import db, drone

BUFFER_M = 300
SPEED_MS = 8.0
SECONDS_PER_FRAME = 2.0
BATTERY_MIN = 25  # typical usable flight time of a small mapping drone
OPEN = "proposed"
DECISIONS = {"approve": "approved", "reject": "rejected"}


class ProposalError(ValueError):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _bbox(points: list[tuple[float, float]], half_cap_m: float) -> list[float]:
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    clat, clon = (min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2
    m_lat, m_lon = 110_574.0, 111_320.0 * math.cos(math.radians(clat))
    half_n = min((max(lats) - min(lats)) / 2 * m_lat + BUFFER_M, half_cap_m)
    half_e = min((max(lons) - min(lons)) / 2 * m_lon + BUFFER_M, half_cap_m)
    return [round(clon - half_e / m_lon, 6), round(clat - half_n / m_lat, 6), round(clon + half_e / m_lon, 6), round(clat + half_n / m_lat, 6)]


def plan_for(points: list[tuple[float, float]], altitude_m: float = drone.MAX_ALTITUDE_M) -> dict:
    """Largest lawnmower over the hotspots plus a buffer that fits the area and frame limits."""
    half = 700.0
    while True:
        try:
            plan = drone.plan_survey(_bbox(points, half), altitude_m=altitude_m)
            break
        except ValueError:
            half *= 0.85  # too many frames or too large: shrink around the cluster center
            if half < 100:
                raise
    wps = plan["waypoints"]
    path_m = sum(math.dist((a["utm_x"], a["utm_y"]), (b["utm_x"], b["utm_y"])) for a, b in zip(wps, wps[1:]))
    minutes = (path_m / SPEED_MS + SECONDS_PER_FRAME * len(wps)) / 60
    plan["estimate"] = {"path_km": round(path_m / 1000, 2), "flight_minutes": round(minutes, 1),
                        "batteries": max(1, math.ceil(minutes / BATTERY_MIN)), "speed_ms": SPEED_MS}
    return plan


def _row(r) -> dict:
    d = dict(r)
    d["plan"] = json.loads(d.pop("plan_json"))
    return d


def list_for(conn, incident_id: str) -> list[dict]:
    return [_row(r) for r in conn.execute("SELECT * FROM drone_proposals WHERE incident_id=? ORDER BY id DESC", (incident_id,))]


def propose(conn, incident_id: str, *, source: str, reason: str) -> tuple[dict, bool]:
    """Returns (proposal, created). An incident has at most one open proposal; asking again returns it."""
    inc = conn.execute("SELECT lat, lon FROM incidents WHERE id=?", (incident_id,)).fetchone()
    if inc is None:
        raise ProposalError(404, "incident not found")
    open_ = conn.execute("SELECT * FROM drone_proposals WHERE incident_id=? AND status=? ORDER BY id DESC LIMIT 1", (incident_id, OPEN)).fetchone()
    if open_:
        return _row(open_), False
    points = [(r["lat"], r["lon"]) for r in conn.execute("SELECT lat, lon FROM hotspots WHERE incident_id=?", (incident_id,))] or [(inc["lat"], inc["lon"])]
    plan = plan_for(points)
    cur = conn.execute(
        "INSERT INTO drone_proposals (incident_id, source, reason, status, plan_json, created_at) VALUES (?,?,?,?,?,?)",
        (incident_id, source, (reason or "").strip()[:300], OPEN, json.dumps(plan), db.utcnow()),
    )
    return _row(conn.execute("SELECT * FROM drone_proposals WHERE id=?", (cur.lastrowid,)).fetchone()), True


def decide(conn, proposal_id: int, decision: str, verifier_hash: str, note: str | None) -> dict:
    if decision not in DECISIONS:
        raise ProposalError(422, "decision must be approve or reject")
    row = conn.execute("SELECT * FROM drone_proposals WHERE id=?", (proposal_id,)).fetchone()
    if row is None:
        raise ProposalError(404, "proposal not found")
    if row["status"] != OPEN:
        raise ProposalError(409, f"proposal is already {row['status']}")
    conn.execute("UPDATE drone_proposals SET status=?, decided_at=?, decided_by=?, note=? WHERE id=?",
                 (DECISIONS[decision], db.utcnow(), verifier_hash, (note or "").strip()[:300] or None, proposal_id))
    return _row(conn.execute("SELECT * FROM drone_proposals WHERE id=?", (proposal_id,)).fetchone())


def summary(p: dict) -> dict:
    """What the agent sees: enough to mention the plan, nothing it could mistake for a result."""
    plan = p["plan"]
    return {"proposal_id": p["id"], "status": p["status"], "frames": len(plan["waypoints"]), "area_km2": plan["area_km2"],
            "altitude_agl_m": plan["altitude_agl_m"], "gsd_m": plan["gsd_m"], "flight_minutes": plan["estimate"]["flight_minutes"],
            "note": "A plan only. No drone flies until a human verifier approves it, and no survey result exists yet."}

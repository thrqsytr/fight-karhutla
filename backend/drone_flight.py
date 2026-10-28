"""Simulated pilot flight of an approved drone plan (SIMULASI). No drone is connected in this build.

A pilot (a verifier in this prototype) starts the flight; the server simulates telemetry for the whole route
once, deterministically from the plan and the incident's own weather evidence, and keeps a sim clock: the
start time and a playback speed. Anyone opening the incident sees the same flight at the same point.
The pilot can abort at any time and can land only after the route is done. A completed flight carries no
imagery, except where a simulated survey exists for the incident (INC-20261025-0407).

Telemetry model, kept simple on purpose and stated in the payload: cruise airspeed 8 m/s, ground speed
changed by the wind component along each leg, battery drain of one pack per 25 minutes plus 4% per m/s of
wind, a pack swap (5 minutes on the ground) when a pack falls under 20%.
"""

import json
import math
from datetime import datetime, timezone

from . import db, drone_missions

AIRSPEED_MS = 8.0
MIN_GROUND_MS, MAX_GROUND_MS = 3.0, 14.0
PACK_S = drone_missions.BATTERY_MIN * 60
WIND_DRAIN_PER_MS = 0.04
SWAP_BELOW_PCT = drone_missions.RESERVE * 100
SWAP_S = 300
SPEEDS = (1, 10, 30, 60)
FLYING, DONE, ABORTED = "flying", "completed", "aborted"  # table drone_flights is in db.SCHEMA


class FlightError(ValueError):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _wind(conn, incident_id: str) -> dict:
    rows = [e for e in db.list_evidence(conn, incident_id) if e["type"] == "weather"]
    if not rows:
        return {"speed_ms": 0.0, "from_deg": None, "evidence_id": None, "note": "no weather evidence: calm air assumed"}
    p = rows[-1]["payload"]
    return {"speed_ms": float(p.get("wind_speed_ms") or 0.0), "from_deg": p.get("wind_direction_deg"), "evidence_id": rows[-1]["id"]}


def simulate(plan: dict, wind: dict) -> dict:
    """Telemetry at each frame position, plus events. Pure function of the plan and the wind."""
    wps = plan["waypoints"]
    w = wind["speed_ms"]
    to_rad = math.radians((wind["from_deg"] + 180) % 360) if wind["from_deg"] is not None else None
    wx, wy = (w * math.sin(to_rad), w * math.cos(to_rad)) if to_rad is not None else (0.0, 0.0)  # wind vector, east and north
    drain = 100.0 / PACK_S * (1 + WIND_DRAIN_PER_MS * w)
    t, battery, pack = 0.0, 100.0, 1
    samples, events = [], [{"t_s": 0.0, "kind": "takeoff", "pack": 1}]
    for i, wp in enumerate(wps):
        ground = AIRSPEED_MS
        if i:
            prev = wps[i - 1]
            dx, dy = wp["utm_x"] - prev["utm_x"], wp["utm_y"] - prev["utm_y"]
            dist = math.hypot(dx, dy)
            along = (wx * dx + wy * dy) / dist if dist else 0.0
            ground = min(MAX_GROUND_MS, max(MIN_GROUND_MS, AIRSPEED_MS + along))
            leg = dist / ground + drone_missions.SECONDS_PER_FRAME
            t += leg
            battery -= leg * drain
            if battery < SWAP_BELOW_PCT and i < len(wps) - 1:
                events.append({"t_s": round(t, 1), "kind": "battery_swap", "pack": pack + 1, "battery_pct": round(battery, 1)})
                t += SWAP_S
                battery, pack = 100.0, pack + 1
        samples.append({"seq": wp["seq"], "t_s": round(t, 1), "lat": wp["lat"], "lon": wp["lon"], "alt_m": plan["altitude_agl_m"],
                        "heading_deg": wp["heading_deg"], "ground_speed_ms": round(ground, 2), "battery_pct": round(battery, 1), "pack": pack})
    events.append({"t_s": round(t, 1), "kind": "landing", "pack": pack, "battery_pct": round(battery, 1)})
    return {"samples": samples, "events": events, "duration_s": round(t, 1), "packs": pack, "wind": wind,
            "model": {"airspeed_ms": AIRSPEED_MS, "pack_minutes": drone_missions.BATTERY_MIN, "wind_drain_per_ms": WIND_DRAIN_PER_MS,
                      "swap_below_pct": SWAP_BELOW_PCT, "swap_s": SWAP_S}}


def _sim_elapsed(row, now: datetime) -> float:
    return (now - datetime.fromisoformat(row["started_at"])).total_seconds() * row["speed"]


def _row(row, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    tel = json.loads(row["telemetry_json"])
    d = {k: row[k] for k in ("id", "proposal_id", "incident_id", "state", "speed", "started_at", "finished_at", "note")}
    d["telemetry"] = tel
    end = datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else now
    d["sim_elapsed_s"] = round(min(tel["duration_s"], _sim_elapsed(row, end)), 1)
    d["route_done"] = d["sim_elapsed_s"] >= tel["duration_s"]
    d["frames_captured"] = sum(1 for s in tel["samples"] if s["t_s"] <= d["sim_elapsed_s"])
    d["simulation"] = True
    return d


def latest_for(conn, proposal_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM drone_flights WHERE proposal_id=? ORDER BY id DESC LIMIT 1", (proposal_id,)).fetchone()
    return _row(row) if row else None


def start(conn, proposal_id: int, speed: int, pilot_hash: str) -> dict:
    if speed not in SPEEDS:
        raise FlightError(422, f"speed must be one of {SPEEDS}")
    p = conn.execute("SELECT * FROM drone_proposals WHERE id=?", (proposal_id,)).fetchone()
    if p is None:
        raise FlightError(404, "proposal not found")
    if p["status"] != "approved":
        raise FlightError(409, f"only an approved plan can fly; this one is {p['status']}")
    last = latest_for(conn, proposal_id)
    if last and last["state"] in (FLYING, DONE):
        raise FlightError(409, f"this plan already has a {last['state']} flight")
    if conn.execute("SELECT 1 FROM drone_flights WHERE incident_id=? AND state=?", (p["incident_id"], FLYING)).fetchone():
        raise FlightError(409, "another plan of this incident is in the air; one drone flies at a time")
    tel = simulate(json.loads(p["plan_json"]), _wind(conn, p["incident_id"]))
    cur = conn.execute("INSERT INTO drone_flights (proposal_id, incident_id, state, speed, started_at, pilot_hash, telemetry_json) VALUES (?,?,?,?,?,?,?)",
                       (proposal_id, p["incident_id"], FLYING, speed, db.utcnow(), pilot_hash, json.dumps(tel)))
    return _row(conn.execute("SELECT * FROM drone_flights WHERE id=?", (cur.lastrowid,)).fetchone())


def finish(conn, flight_id: int, outcome: str, note: str | None, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    row = conn.execute("SELECT * FROM drone_flights WHERE id=?", (flight_id,)).fetchone()
    if row is None:
        raise FlightError(404, "flight not found")
    if row["state"] != FLYING:
        raise FlightError(409, f"flight is already {row['state']}")
    if outcome not in (DONE, ABORTED):
        raise FlightError(422, "outcome must be completed or aborted")
    if outcome == DONE and _sim_elapsed(row, now) < json.loads(row["telemetry_json"])["duration_s"]:
        raise FlightError(409, "the route is not finished yet; abort instead, or wait for the last frame")
    conn.execute("UPDATE drone_flights SET state=?, finished_at=?, note=? WHERE id=?",
                 (outcome, now.isoformat(timespec="seconds"), (note or "").strip()[:300] or None, flight_id))
    return _row(conn.execute("SELECT * FROM drone_flights WHERE id=?", (flight_id,)).fetchone(), now)

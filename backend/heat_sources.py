"""Persistent heat sources (owner review round 3): anyone can say a hotspot cluster is not a land fire but a mine,
smelter, gas flare, factory, power plant, or volcano that keeps emitting heat. A flag changes nothing until a
verifier accepts it; then a zone (1 km by default) is stored, every incident inside it gets heat_source evidence,
new hotspots there are marked when they arrive, and the evidence gate asks for a citizen photo or voice note before
"likely fire" inside a zone. Nothing is deleted or closed automatically: a real fire can burn next to a mine.
Wording stays neutral: what the heat source is, never who runs it or whether anything is allowed."""

import hashlib
import math
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from . import db
from .geo import haversine_m

KINDS = ("mine_smelter", "gas_flare_refinery", "factory_power_plant", "volcano_geothermal", "other")
DEFAULT_RADIUS_M = 1000
MIN_RADIUS_M, MAX_RADIUS_M = 200, 5000
NOTE_MAX = 280

# One flag per incident per sender per day, and a few per hour overall; in memory, reset on restart (a demo).
_sent: dict[str, deque] = defaultdict(deque)
FLAGS_PER_HOUR = 5


class FlagError(ValueError):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def client_hash(client: str, salt: str) -> str:
    return hashlib.sha256(f"heat:{salt}:{client}".encode()).hexdigest()[:16]


def submit(conn, incident: dict, kind: str, note: str | None, sender: str, now: float | None = None) -> dict:
    if kind not in KINDS:
        raise FlagError(422, f"kind must be one of {', '.join(KINDS)}")
    note = (note or "").strip()[:NOTE_MAX] or None
    now = now or time.time()
    q = _sent[sender]
    while q and now - q[0] > 3600:
        q.popleft()
    if len(q) >= FLAGS_PER_HOUR:
        raise FlagError(429, "too many flags from this connection; try again in an hour")
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds")
    if conn.execute("SELECT 1 FROM heat_source_flags WHERE incident_id=? AND sender_hash=? AND created_at > ?", (incident["id"], sender, since)).fetchone():
        raise FlagError(409, "this incident was already flagged from this connection today")
    cur = conn.execute(
        "INSERT INTO heat_source_flags (incident_id, lat, lon, kind, note, sender_hash, created_at, status) VALUES (?,?,?,?,?,?,?, 'pending')",
        (incident["id"], incident["lat"], incident["lon"], kind, note, sender, db.utcnow()))
    q.append(now)
    return {"id": cur.lastrowid, "status": "pending"}


def flags_for(conn, incident_id: str, verifier: bool) -> dict:
    rows = [dict(r) for r in conn.execute("SELECT * FROM heat_source_flags WHERE incident_id=? ORDER BY id DESC", (incident_id,))]
    zone = zone_of(conn, incident_id)
    pending = [r for r in rows if r["status"] == "pending"]
    out = {"pending": len(pending), "kinds": sorted({r["kind"] for r in pending}), "zone": zone}
    if verifier:
        for r in rows:
            r.pop("sender_hash", None)
        out["items"] = rows
    return out


def zone_of(conn, incident_id: str) -> dict | None:
    row = conn.execute("""SELECT z.* FROM evidence e JOIN heat_source_zones z ON z.id = json_extract(e.payload_json, '$.zone_id')
                          WHERE e.incident_id=? AND e.type='heat_source' AND z.active=1 ORDER BY e.rowid DESC LIMIT 1""", (incident_id,)).fetchone()
    return dict(row) if row else None


def decide(conn, flag_id: int, decision: str, verifier_hash: str, radius_m: int | None, kind: str | None, note: str | None) -> dict:
    flag = conn.execute("SELECT * FROM heat_source_flags WHERE id=?", (flag_id,)).fetchone()
    if flag is None:
        raise FlagError(404, "flag not found")
    if flag["status"] != "pending":
        raise FlagError(409, f"already decided ({flag['status']})")
    if decision not in ("accept", "reject"):
        raise FlagError(422, "decision must be accept or reject")
    zone_id = None
    if decision == "accept":
        radius = int(radius_m or DEFAULT_RADIUS_M)
        if not MIN_RADIUS_M <= radius <= MAX_RADIUS_M:
            raise FlagError(422, f"radius must be {MIN_RADIUS_M} to {MAX_RADIUS_M} m")
        kind = kind or flag["kind"]
        if kind not in KINDS:
            raise FlagError(422, f"kind must be one of {', '.join(KINDS)}")
        zone_id = conn.execute(
            "INSERT INTO heat_source_zones (lat, lon, radius_m, kind, note, flag_id, created_at, created_by, active) VALUES (?,?,?,?,?,?,?,?,1)",
            (flag["lat"], flag["lon"], radius, kind, (note or "").strip()[:NOTE_MAX] or None, flag_id, db.utcnow(), verifier_hash)).lastrowid
    conn.execute("UPDATE heat_source_flags SET status=?, decided_at=?, decided_by=?, decision_note=?, zone_id=? WHERE id=?",
                 ("accepted" if decision == "accept" else "rejected", db.utcnow(), verifier_hash, (note or "").strip()[:NOTE_MAX] or None, zone_id, flag_id))
    # Other pending flags on the same incident are settled by the same decision.
    conn.execute("UPDATE heat_source_flags SET status=?, decided_at=?, decided_by=?, zone_id=? WHERE incident_id=? AND status='pending'",
                 ("accepted" if decision == "accept" else "rejected", db.utcnow(), verifier_hash, zone_id, flag["incident_id"]))
    marked = mark_incidents(conn) if zone_id else 0
    return {"flag_id": flag_id, "status": "accepted" if decision == "accept" else "rejected", "zone_id": zone_id, "incidents_marked": marked}


def deactivate(conn, zone_id: int) -> dict:
    """Withdraw a zone: it stops marking incidents, and each incident it marked gets a withdrawal row (evidence is
    append-only), so the gate and the view read the newest row per zone."""
    if conn.execute("UPDATE heat_source_zones SET active=0 WHERE id=? AND active=1", (zone_id,)).rowcount == 0:
        raise FlagError(404, "no active zone with this id")
    ids = [r[0] for r in conn.execute("SELECT DISTINCT incident_id FROM evidence WHERE type='heat_source' AND json_extract(payload_json, '$.zone_id')=?", (zone_id,))]
    for inc in ids:
        db.add_evidence(conn, inc, "heat_source", "Verifier", f"Persistent heat source zone {zone_id} withdrawn by a verifier", {"zone_id": zone_id, "withdrawn": True})
    return {"zone_id": zone_id, "active": False, "incidents": len(ids)}


def active_zone_evidence(evidence: list[dict]) -> list[dict]:
    """heat_source evidence whose zone is still in force: the newest row per zone, unless it is a withdrawal."""
    latest: dict = {}
    for e in evidence:
        if e["type"] == "heat_source":
            latest[e["payload"].get("zone_id")] = e
    return [e for e in latest.values() if not e["payload"].get("withdrawn")]


def zones(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT id, lat, lon, radius_m, kind, created_at FROM heat_source_zones WHERE active=1 ORDER BY id")]


def mark_incidents(conn) -> int:
    """Store heat_source evidence for every incident inside an active zone that has none for it yet."""
    n = 0
    for z in zones(conn):
        dlat = z["radius_m"] / 111_320
        dlon = dlat / max(0.1, math.cos(math.radians(z["lat"])))
        rows = conn.execute("SELECT id, lat, lon FROM incidents WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?",
                            (z["lat"] - dlat, z["lat"] + dlat, z["lon"] - dlon, z["lon"] + dlon)).fetchall()
        for r in rows:
            d = haversine_m(z["lat"], z["lon"], r["lat"], r["lon"])
            if d > z["radius_m"]:
                continue
            have = conn.execute("SELECT 1 FROM evidence WHERE incident_id=? AND type='heat_source' AND json_extract(payload_json, '$.zone_id')=?",
                                (r["id"], z["id"])).fetchone()
            if have:
                continue
            payload = {"zone_id": z["id"], "kind": z["kind"], "radius_m": z["radius_m"], "distance_m": round(d), "zone_lat": z["lat"], "zone_lon": z["lon"],
                       "approved_at": z["created_at"]}
            summary = f"Inside a persistent heat source zone ({z['kind'].replace('_', ' ')}) approved by a verifier, {round(d)} m from its centre, radius {z['radius_m']} m"
            db.add_evidence(conn, r["id"], "heat_source", "Verifier (citizen flag)", summary, payload)
            n += 1
    return n

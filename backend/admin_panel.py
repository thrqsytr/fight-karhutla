"""What the admin and verifier panel shows beyond the settings: the verification queue, recent decisions, status and
coverage counts, the offline data file, spend by outcome, drone activity, settings changes, and operator jobs
(fetch now, collect pending evidence, look for Sentinel-2 pairs) that run in the background. Owner review round 2."""

import csv
import io
import json
import logging
import threading
from datetime import datetime, timedelta, timezone

from . import admin_settings, db, evidence, ingest, satellite
from .config import Settings
from .sources import osm

log = logging.getLogger(__name__)

AI_STATUSES = ("likely_fire", "possible_fire", "unlikely", "needs_more_info")
LATEST_VERDICT = "SELECT v.incident_id, v.decision, v.created_at FROM verdicts v WHERE v.id = (SELECT MAX(id) FROM verdicts WHERE incident_id = v.incident_id)"


def queue(conn) -> dict:
    """Work waiting for a person. Decisions stay on the incident page, where the evidence is."""
    awaiting = [dict(r) for r in conn.execute(f"""
        SELECT i.id, i.status, i.confidence, i.trigger, i.hotspot_count, i.last_seen, i.simulation, i.updated_at,
               (SELECT MAX(r.finished_at) FROM agent_runs r WHERE r.incident_id = i.id AND r.outcome = 'ok') AS triaged_at
        FROM incidents i LEFT JOIN ({LATEST_VERDICT}) lv ON lv.incident_id = i.id
        WHERE i.status IN {AI_STATUSES!r} AND (lv.decision IS NULL OR lv.decision = 'revoke')
        ORDER BY i.simulation, i.updated_at DESC LIMIT 50""")]
    for a in awaiting:
        a["triaged_at"] = a["triaged_at"] or a.pop("updated_at")
        a.pop("updated_at", None)
        a["evidence"] = sorted({r[0] for r in conn.execute("SELECT DISTINCT type FROM evidence WHERE incident_id=?", (a["id"],))})
    citizen = [dict(r) for r in conn.execute("""
        SELECT i.id, i.trigger, i.status, i.created_at, i.last_seen,
               (SELECT COUNT(*) FROM evidence e WHERE e.incident_id = i.id AND e.type IN ('report', 'image', 'voice')) AS items,
               (SELECT json_extract(e.payload_json, '$.channel') FROM evidence e WHERE e.incident_id = i.id AND e.type IN ('report', 'image', 'voice') ORDER BY e.rowid LIMIT 1) AS channel
        FROM incidents i WHERE i.trigger != 'hotspot_cluster' AND i.status IN ('pending_triage', 'triaging')
        ORDER BY i.created_at DESC LIMIT 50""")]
    drone = [dict(r) for r in conn.execute("""
        SELECT p.id, p.incident_id, p.source, p.created_at, p.reason FROM drone_proposals p WHERE p.status = 'proposed' ORDER BY p.created_at DESC LIMIT 50""")]
    wa = [dict(r) for r in conn.execute("""
        SELECT DISTINCT e.incident_id AS id, MIN(e.retrieved_at) AS received_at FROM evidence e
        WHERE e.type IN ('report', 'image', 'voice') AND json_extract(e.payload_json, '$.channel') = 'whatsapp'
          AND e.incident_id NOT IN (SELECT incident_id FROM wa_showcase)
        GROUP BY e.incident_id ORDER BY received_at DESC LIMIT 50""")]
    heat = [dict(r) for r in conn.execute("""
        SELECT f.id, f.incident_id, f.kind, f.note, f.created_at, (SELECT COUNT(*) FROM heat_source_flags g WHERE g.incident_id = f.incident_id AND g.status = 'pending') AS flags
        FROM heat_source_flags f WHERE f.status = 'pending' AND f.id = (SELECT MIN(id) FROM heat_source_flags h WHERE h.incident_id = f.incident_id AND h.status = 'pending')
        ORDER BY f.created_at DESC LIMIT 50""")]
    decisions = [dict(r) for r in conn.execute("""
        SELECT v.id, v.incident_id, v.decision, v.note, v.verifier_hash, v.created_at, i.status, i.simulation
        FROM verdicts v JOIN incidents i ON i.id = v.incident_id ORDER BY v.id DESC LIMIT 20""")]
    real = [a for a in awaiting if not a["simulation"]]
    return {
        "awaiting_decision": awaiting, "citizen_pending": citizen, "drone_proposed": drone, "wa_unshown": wa, "heat_flags": heat,
        "recent_decisions": decisions,
        # The badge counts what a verifier must act on in real incidents; replays are practice.
        "count": len(real) + len(citizen) + len(drone) + len(heat),
    }


def overview_extra(settings: Settings, conn) -> dict:
    status = {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM incidents WHERE simulation=0 GROUP BY status")}
    newest = conn.execute("SELECT MAX(acquired_at) FROM hotspots").fetchone()[0]
    last24 = 0
    if newest:
        since = (datetime.fromisoformat(newest) - timedelta(hours=24)).isoformat()
        last24 = conn.execute("SELECT COUNT(*) FROM hotspots WHERE acquired_at > ?", (since,)).fetchone()[0]
    eligible_sql = f"simulation=0 AND (trigger != 'hotspot_cluster' OR hotspot_count >= {evidence.MIN_HOTSPOTS} OR id IN (SELECT incident_id FROM hotspots GROUP BY incident_id HAVING MAX(frp) >= {evidence.MIN_MAX_FRP}))"
    eligible = conn.execute(f"SELECT COUNT(*) FROM incidents WHERE {eligible_sql}").fetchone()[0]
    enriched = conn.execute(f"SELECT COUNT(*) FROM incidents WHERE {eligible_sql} AND enriched_at IS NOT NULL").fetchone()[0]
    failed = {}
    for (js,) in conn.execute("SELECT enrichment_json FROM incidents WHERE simulation=0 AND enrichment_json LIKE '%failed%'"):
        for k, v in json.loads(js or "{}").items():
            if str(v).startswith("failed"):
                failed[k] = failed.get(k, 0) + 1
    runs = {r[0]: {"runs": r[1], "cost_usd": round(r[2] or 0, 4)} for r in conn.execute(
        "SELECT outcome, COUNT(*), SUM(cost_usd) FROM agent_runs WHERE incident_id NOT IN (SELECT id FROM incidents WHERE simulation=1) GROUP BY outcome")}
    ok = runs.get("ok", {"runs": 0, "cost_usd": 0})
    drone = {
        "proposals": {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM drone_proposals GROUP BY status")},
        "flights": {r[0]: r[1] for r in conn.execute("SELECT state, COUNT(*) FROM drone_flights GROUP BY state")},
        "latest": [dict(r) for r in conn.execute("""
            SELECT p.id, p.incident_id, p.status, p.source, p.created_at, f.state AS flight_state FROM drone_proposals p
            LEFT JOIN drone_flights f ON f.id = (SELECT MAX(id) FROM drone_flights WHERE proposal_id = p.id)
            ORDER BY p.id DESC LIMIT 8""")],
    }
    changes = [dict(r) for r in conn.execute("SELECT at, key, old, new FROM admin_log ORDER BY id DESC LIMIT 20")]
    return {
        "status_counts": status, "simulated_incidents": conn.execute("SELECT COUNT(*) FROM incidents WHERE simulation=1").fetchone()[0],
        "hotspots_last_24h": last24, "newest_acquisition": newest,
        "coverage": {"eligible": eligible, "enriched": enriched, "pending": eligible - enriched, "failed_by_source": failed},
        "runs_by_outcome": runs, "cost_per_ok_run": round(ok["cost_usd"] / ok["runs"], 4) if ok["runs"] else None,
        "drone": drone, "settings_log": changes, "offline": offline_file(settings), "jobs": jobs_snapshot(),
    }


def offline_file(settings: Settings) -> dict | None:
    path = settings.osm_fallback_path
    info = osm.fallback_info(path)
    if info is None:
        return {"present": False, "overpass_down": osm.overpass_down()}
    counts = {}
    for key in ("counts", "overture_counts"):
        try:
            counts[key] = json.loads(info.get(key, "{}").replace("'", '"'))
        except ValueError:
            counts[key] = {}
    return {"present": True, "source": info.get("source"), "built_at": info.get("built_at"), "overture_release": info.get("overture_release"),
            "overture_min_confidence": info.get("overture_min_confidence"), "size_mb": round(path.stat().st_size / 1e6, 1),
            "osm_counts": counts["counts"], "overture_counts": counts["overture_counts"], "overpass_down": osm.overpass_down()}


def log_settings(conn, old: dict, new: dict) -> None:
    for k, v in new.items():
        if old.get(k) != v:
            conn.execute("INSERT INTO admin_log (at, key, old, new) VALUES (?,?,?,?)", (db.utcnow(), k, json.dumps(old.get(k)), json.dumps(v)))


# Operator jobs: one of each at a time, in a thread, so the request returns at once and the panel shows progress.
JOBS = ("fetch", "enrich", "sentinel")
_jobs: dict[str, dict] = {}
_lock = threading.Lock()
SENTINEL_SWEEP_MAX = 10


def jobs_snapshot() -> dict:
    with _lock:
        return {k: dict(v) for k, v in _jobs.items()}


def start_job(settings: Settings, name: str, limit: int = 5) -> dict:
    if name not in JOBS:
        raise KeyError(name)
    with _lock:
        if _jobs.get(name, {}).get("state") == "running":
            return {"started": False, **_jobs[name]}
        _jobs[name] = {"state": "running", "started_at": db.utcnow(), "finished_at": None, "result": None, "error": None}
    threading.Thread(target=_run_job, args=(settings, name, max(1, min(SENTINEL_SWEEP_MAX, limit))), daemon=True).start()
    return {"started": True, **jobs_snapshot()[name]}


def _run_job(settings: Settings, name: str, limit: int) -> None:
    try:
        if name == "fetch":
            result = ingest.run_once(settings)
            with db.session(settings.db_path) as conn:
                admin_settings.mark_fetch(conn)  # the schedule counts from this fetch
            result["enrichment"] = evidence.run_enrichment(settings)
        elif name == "enrich":
            result = evidence.run_enrichment(settings)
        else:
            result = sentinel_sweep(settings, limit)
        state, error = "done", None
    except Exception as exc:  # noqa: BLE001 - the panel shows the reason; the log keeps the trace
        log.exception("admin job %s failed", name)
        result, state, error = None, "failed", str(exc)[:300]
    with _lock:
        _jobs[name].update(state=state, finished_at=db.utcnow(), result=_short(result), error=error)


def _short(result):
    """Counts, not lists of ids, so the overview stays small."""
    if not isinstance(result, dict):
        return result
    return {k: (len(v) if isinstance(v, list) else _short(v)) for k, v in result.items()}


def sentinel_sweep(settings: Settings, limit: int) -> dict:
    """Look for Sentinel-2 pairs for the largest real incidents that have none from the reuse window. Open data, no model call."""
    fresh = timedelta(hours=admin_settings.value(settings, "satellite_fresh_hours"))
    since = (datetime.now(timezone.utc) - fresh).isoformat()
    with db.session(settings.db_path) as conn:
        ids = [r[0] for r in conn.execute("""
            SELECT i.id FROM incidents i WHERE i.simulation = 0 AND i.hotspot_count >= 3
              AND i.id NOT IN (SELECT incident_id FROM evidence WHERE type = 'satellite' AND retrieved_at > ?)
            ORDER BY i.hotspot_count DESC, i.last_seen DESC LIMIT ?""", (since, limit))]
    out = {"checked": 0, "stored": 0, "no_pair": 0, "incidents": []}
    for inc in ids:
        r = satellite.collect(settings, inc)
        out["checked"] += 1
        if r.get("evidence_id"):
            out["stored"] += 1
        else:
            out["no_pair"] += 1
        out["incidents"].append(inc)
    return out


def export_csv(conn, name: str) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    if name == "incidents":
        w.writerow(["id", "trigger", "status", "confidence", "risk_score", "lat", "lon", "first_seen", "last_seen", "hotspot_count", "max_frp_mw",
                    "burned_area_ha", "latest_decision", "simulation"])
        rows = conn.execute(f"""
            SELECT i.id, i.trigger, i.status, i.confidence, i.risk_score, i.lat, i.lon, i.first_seen, i.last_seen, i.hotspot_count,
                   (SELECT MAX(frp) FROM hotspots h WHERE h.incident_id = i.id),
                   (SELECT json_extract(e.payload_json, '$.burned_area_ha') FROM evidence e WHERE e.incident_id = i.id AND e.type = 'satellite' ORDER BY e.rowid DESC LIMIT 1),
                   CASE WHEN lv.decision IN ('approve', 'reject') THEN lv.decision END, i.simulation
            FROM incidents i LEFT JOIN ({LATEST_VERDICT}) lv ON lv.incident_id = i.id ORDER BY i.last_seen DESC""")
    elif name == "verdicts":
        w.writerow(["id", "incident_id", "decision", "note", "verifier_hash", "channel", "created_at"])
        rows = conn.execute("SELECT id, incident_id, decision, note, verifier_hash, channel, created_at FROM verdicts ORDER BY id")
    else:
        raise KeyError(name)
    w.writerows(rows)
    return buf.getvalue()

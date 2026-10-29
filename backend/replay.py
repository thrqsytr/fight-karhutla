"""Replay mode: three recorded historical incidents, loaded as SIMULASI so a demo works with no active fire.

Each case under replay/incidents/<slug>/ was recorded by eval/build_replay.py from the 2025 dry season: the
app's own ingest, enrichment, Sentinel-2 pair, triage, gate, and critique, written out as files. Loading copies
them into the database with the simulation flag and needs no network and no model call. The recorded text uses
the token {INC} for the incident ID, which is filled in at load time; evidence IDs (EV-001...) are kept.
"""

import json
import shutil
from datetime import datetime
from pathlib import Path

from . import db
from .config import Settings

STATE_KEY = "replay:incidents"
ROLES = ("burned", "false_positive", "needs_more_info")
TOKEN = "{INC}"


def cases(replay_dir: Path) -> list[dict]:
    root = replay_dir / "incidents"
    found = [json.loads(p.read_text(encoding="utf-8")) | {"_dir": p.parent} for p in root.glob("*/case.json")] if root.exists() else []
    return sorted(found, key=lambda c: ROLES.index(c["role"]) if c["role"] in ROLES else len(ROLES))


def _loaded(conn) -> list[str]:
    row = conn.execute("SELECT value FROM app_state WHERE key=?", (STATE_KEY,)).fetchone()
    ids = json.loads(row["value"]) if row else []
    return [i for i in ids if conn.execute("SELECT 1 FROM incidents WHERE id=?", (i,)).fetchone()]


def _columns(conn, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def status(settings: Settings) -> dict:
    with db.session(settings.db_path) as conn:
        ids = _loaded(conn)
    return {"loaded": ids, "cases": summaries(settings.replay_dir)}


def summaries(replay_dir: Path) -> list[dict]:
    return [{"slug": c["slug"], "role": c["role"], "title": c["title"], "note": c.get("note"), "recorded_with": c.get("recorded_with"), "translated_with": c.get("translated_with")}
            for c in cases(replay_dir)]


def start(settings: Settings) -> dict:
    """Load every case once. A second start returns the incidents already loaded instead of duplicating them."""
    found = cases(settings.replay_dir)
    if not found:
        raise FileNotFoundError("no replay cases under replay/incidents")
    with db.session(settings.db_path) as conn:
        existing = _loaded(conn)
        if existing:
            return {"started": False, "incidents": existing}
        inc_cols, hs_cols = _columns(conn, "incidents"), _columns(conn, "hotspots")
        ids = []
        for case in found:
            inc_id = db.next_incident_id(conn, datetime.fromisoformat(case["incident"]["first_seen"]))
            c = json.loads(json.dumps({k: v for k, v in case.items() if k != "_dir"}).replace(TOKEN, inc_id))
            now = db.utcnow()
            row = {**{k: v for k, v in c["incident"].items() if k in inc_cols}, "id": inc_id, "created_at": now, "updated_at": now, "simulation": 1}
            for k in ("triage_json", "gate_json", "enrichment_json"):
                if k in row and not isinstance(row[k], str) and row[k] is not None:
                    row[k] = json.dumps(row[k], ensure_ascii=False)
            conn.execute(f"INSERT INTO incidents ({','.join(row)}) VALUES ({','.join('?' * len(row))})", list(row.values()))
            for h in c["hotspots"]:
                h = {k: v for k, v in h.items() if k in hs_cols} | {"incident_id": inc_id, "ingested_at": now}
                conn.execute(f"INSERT OR IGNORE INTO hotspots ({','.join(h)}) VALUES ({','.join('?' * len(h))})", list(h.values()))
            for e in c["evidence"]:
                conn.execute("INSERT INTO evidence (id, incident_id, type, source, retrieved_at, summary, payload_json) VALUES (?,?,?,?,?,?,?)",
                             (f"{inc_id}/{e['id']}", inc_id, e["type"], e["source"], e["retrieved_at"], e["summary"], json.dumps(e["payload"], ensure_ascii=False)))
            for r in c.get("runs", []):
                conn.execute("INSERT INTO agent_runs (incident_id, started_at, finished_at, outcome, error, seconds, cost_usd, detail_json) VALUES (?,?,?,?,?,?,?,?)",
                             (inc_id, r["started_at"], r["finished_at"], r["outcome"], r.get("error"), r["seconds"], r.get("cost_usd"), json.dumps(r.get("detail"), ensure_ascii=False)))
            images = case["_dir"] / "satellite"
            if images.exists():
                shutil.copytree(images, settings.data_dir / "satellite" / inc_id, dirs_exist_ok=True)
            ids.append(inc_id)
        conn.execute("INSERT OR REPLACE INTO app_state (key, value, updated_at) VALUES (?,?,?)", (STATE_KEY, json.dumps(ids), db.utcnow()))
    return {"started": True, "incidents": ids}


def sync_text(settings: Settings) -> list[str]:
    """Bring the triage text of loaded replay incidents up to date with the case files (for example the Indonesian
    translation added later). Only triage_json of simulation incidents this module loaded changes: verdicts, drone
    plans, and flights given on them stay. Returns the incidents that changed."""
    found = cases(settings.replay_dir)
    changed = []
    with db.session(settings.db_path) as conn:
        ids = _loaded(conn)
        for case, inc_id in zip(found, ids):
            recorded = case["incident"].get("triage_json")
            if recorded is None:
                continue
            text = (recorded if isinstance(recorded, str) else json.dumps(recorded, ensure_ascii=False)).replace(TOKEN, inc_id)
            cur = conn.execute("UPDATE incidents SET triage_json=? WHERE id=? AND simulation=1 AND COALESCE(triage_json,'') != ?", (text, inc_id, text))
            if cur.rowcount:
                changed.append(inc_id)
    return changed


def stop(settings: Settings) -> dict:
    """Remove the replayed incidents and everything attached to them. Only incidents this module loaded are touched."""
    with db.session(settings.db_path) as conn:
        row = conn.execute("SELECT value FROM app_state WHERE key=?", (STATE_KEY,)).fetchone()
        ids = json.loads(row["value"]) if row else []
        for inc_id in ids:
            sim = conn.execute("SELECT simulation FROM incidents WHERE id=?", (inc_id,)).fetchone()
            if sim is None or not sim["simulation"]:
                continue  # never delete a real incident, whatever the stored list says
            for table in ("evidence", "verdicts", "drone_flights", "drone_proposals", "agent_runs", "hotspots"):
                conn.execute(f"DELETE FROM {table} WHERE incident_id=?", (inc_id,))
            conn.execute("DELETE FROM incidents WHERE id=?", (inc_id,))
            shutil.rmtree(settings.data_dir / "satellite" / inc_id, ignore_errors=True)
            for og in (settings.data_dir / "og").glob(f"{inc_id}_*.png"):
                og.unlink(missing_ok=True)
        conn.execute("DELETE FROM app_state WHERE key=?", (STATE_KEY,))
    return {"removed": ids}

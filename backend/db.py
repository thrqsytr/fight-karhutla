"""SQLite storage. Every tool result is stored with an ID and timestamp; that ID is the evidence."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS hotspots (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    acquired_at TEXT NOT NULL,
    satellite TEXT,
    instrument TEXT,
    confidence TEXT,
    frp REAL,
    daynight TEXT,
    incident_id TEXT,
    ingested_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS hotspots_time ON hotspots(acquired_at);

CREATE TABLE IF NOT EXISTS incidents (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    trigger TEXT NOT NULL,             -- hotspot_cluster | citizen_report | merged
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    hotspot_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending_triage',
    confidence REAL,
    risk_score REAL,
    triage_json TEXT,
    gate_json TEXT,
    simulation INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS evidence (
    id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES incidents(id),
    type TEXT NOT NULL,                -- hotspot|weather|image|voice|report|satellite|osm|peat
    source TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    summary TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS evidence_incident ON evidence(incident_id);

CREATE TABLE IF NOT EXISTS verdicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id TEXT NOT NULL REFERENCES incidents(id),
    verifier_hash TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('approve', 'reject')),
    note TEXT,
    channel TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


# Columns added after the first deploy. SQLite has no ADD COLUMN IF NOT EXISTS, so check first.
MIGRATIONS = {
    "incidents": {
        "enriched_at": "TEXT",
        "enrichment_json": "TEXT",  # per source: ok, or failed with the reason
        "reporter_count": "INTEGER NOT NULL DEFAULT 0",
    },
}


def init(path: Path) -> None:
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        for table, cols in MIGRATIONS.items():
            have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            for name, ddl in cols.items():
                if name not in have:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


@contextmanager
def session(path: Path) -> Iterator[sqlite3.Connection]:
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def next_incident_id(conn: sqlite3.Connection, day: datetime) -> str:
    prefix = f"INC-{day:%Y%m%d}-"
    row = conn.execute("SELECT id FROM incidents WHERE id LIKE ? ORDER BY id DESC LIMIT 1", (prefix + "%",)).fetchone()
    n = int(row["id"].rsplit("-", 1)[1]) + 1 if row else 1
    return f"{prefix}{n:04d}"


def next_evidence_id(conn: sqlite3.Connection, incident_id: str) -> str:
    n = conn.execute("SELECT COUNT(*) FROM evidence WHERE incident_id = ?", (incident_id,)).fetchone()[0] + 1
    return f"EV-{n:03d}"


def add_evidence(
    conn: sqlite3.Connection, incident_id: str, type_: str, source: str, summary: str, payload: dict
) -> str:
    """Evidence IDs are scoped per incident (EV-001, EV-002, ...) and stored as incident_id/EV-nnn."""
    ev = next_evidence_id(conn, incident_id)
    conn.execute(
        "INSERT INTO evidence (id, incident_id, type, source, retrieved_at, summary, payload_json) VALUES (?,?,?,?,?,?,?)",
        (f"{incident_id}/{ev}", incident_id, type_, source, utcnow(), summary, json.dumps(payload)),
    )
    return ev


def list_evidence(conn: sqlite3.Connection, incident_id: str) -> list[dict]:
    rows = conn.execute("SELECT * FROM evidence WHERE incident_id = ? ORDER BY id", (incident_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["id"] = d["id"].split("/", 1)[1]
        d["payload"] = json.loads(d.pop("payload_json"))
        out.append(d)
    return out


def incident_row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["simulation"] = bool(d["simulation"])
    for k in ("triage_json", "gate_json", "enrichment_json"):
        v = d.pop(k)
        d[k.removesuffix("_json")] = json.loads(v) if v else None
    return d

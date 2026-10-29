"""SQLite storage. Every tool result is stored with an ID and timestamp; that ID is the evidence."""

import hashlib
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

CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    outcome TEXT NOT NULL,             -- running | ok | schema_failed | error
    error TEXT,
    seconds REAL,
    cost_usd REAL,
    detail_json TEXT
);
CREATE INDEX IF NOT EXISTS agent_runs_incident ON agent_runs(incident_id);

CREATE TABLE IF NOT EXISTS drone_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id TEXT NOT NULL REFERENCES incidents(id),
    source TEXT NOT NULL CHECK (source IN ('agent', 'verifier')),
    reason TEXT,
    status TEXT NOT NULL CHECK (status IN ('proposed', 'approved', 'rejected', 'cancelled')),
    plan_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    decided_at TEXT,
    decided_by TEXT,
    note TEXT
);
CREATE INDEX IF NOT EXISTS drone_proposals_incident ON drone_proposals(incident_id);
CREATE TABLE IF NOT EXISTS drone_flights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id INTEGER NOT NULL REFERENCES drone_proposals(id),
    incident_id TEXT NOT NULL REFERENCES incidents(id),
    state TEXT NOT NULL CHECK (state IN ('flying', 'completed', 'aborted')),
    speed INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    pilot_hash TEXT NOT NULL,
    telemetry_json TEXT NOT NULL,
    note TEXT
);
CREATE INDEX IF NOT EXISTS drone_flights_proposal ON drone_flights(proposal_id);

-- WhatsApp: delivered message ids (webhooks repeat), open conversations, reporters to tell the decision,
-- and every outgoing message with its result. Numbers live only in wa_sessions and wa_reporters, both expire.
CREATE TABLE IF NOT EXISTS wa_messages (msg_id TEXT PRIMARY KEY, received_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS wa_sessions (wa_id TEXT PRIMARY KEY, state_json TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS wa_reporters (incident_id TEXT NOT NULL, wa_id TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS wa_reporters_incident ON wa_reporters(incident_id);
CREATE TABLE IF NOT EXISTS wa_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    to_hash TEXT NOT NULL,
    purpose TEXT NOT NULL,
    incident_id TEXT,
    status TEXT NOT NULL,
    detail TEXT
);

-- WhatsApp reports a verifier chose to show on the public report log.
CREATE TABLE IF NOT EXISTS wa_showcase (incident_id TEXT PRIMARY KEY, marked_at TEXT NOT NULL, marked_by TEXT NOT NULL);
-- Operator settings changes, for the admin panel: what changed, from what, to what, when.
CREATE TABLE IF NOT EXISTS admin_log (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, key TEXT NOT NULL, old TEXT, new TEXT);

CREATE TABLE IF NOT EXISTS verdicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id TEXT NOT NULL REFERENCES incidents(id),
    verifier_hash TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('approve', 'reject', 'revoke')),
    note TEXT,
    channel TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Writers wait up to 15 s for the lock instead of failing at the 5 s default.
    conn = sqlite3.connect(path, timeout=15)
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
    # "sent" only means WhatsApp accepted the message; the delivery result arrives later as a status webhook.
    "wa_outbox": {"delivery": "TEXT", "body": "TEXT"},
}


def init(path: Path) -> None:
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        for table, cols in MIGRATIONS.items():
            have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            for name, ddl in cols.items():
                if name not in have:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
        _allow_revoke(conn)
        _allow_cancelled(conn)
        _hash_message_ids(conn)


def _hash_message_ids(conn: sqlite3.Connection) -> None:
    """Rows written before message ids were hashed: a wamid carries the recipient's number in base64."""
    for r in conn.execute("SELECT id, detail FROM wa_outbox WHERE detail LIKE 'wamid.%'").fetchall():
        conn.execute("UPDATE wa_outbox SET detail=? WHERE id=?", ("wamid:" + hashlib.sha256(r["detail"].encode()).hexdigest()[:16], r["id"]))


def _allow_revoke(conn: sqlite3.Connection) -> None:
    """Databases made before 'revoke' existed carry the old CHECK. SQLite cannot alter a CHECK, so the
    table is rebuilt once with the same rows and ids."""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='verdicts'").fetchone()
    if row is None or "'revoke'" in row[0]:
        return
    conn.executescript("""
        ALTER TABLE verdicts RENAME TO verdicts_old;
        CREATE TABLE verdicts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_id TEXT NOT NULL REFERENCES incidents(id),
            verifier_hash TEXT NOT NULL,
            decision TEXT NOT NULL CHECK (decision IN ('approve', 'reject', 'revoke')),
            note TEXT,
            channel TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        INSERT INTO verdicts SELECT * FROM verdicts_old;
        DROP TABLE verdicts_old;
    """)


def _allow_cancelled(conn: sqlite3.Connection) -> None:
    """Drone plans gained 'cancelled' (an approved plan withdrawn so a new one can be made). Same rebuild as _allow_revoke."""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='drone_proposals'").fetchone()
    if row is None or "'cancelled'" in row[0]:
        return
    conn.executescript("""
        ALTER TABLE drone_proposals RENAME TO drone_proposals_old;
        CREATE TABLE drone_proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_id TEXT NOT NULL REFERENCES incidents(id),
            source TEXT NOT NULL CHECK (source IN ('agent', 'verifier')),
            reason TEXT,
            status TEXT NOT NULL CHECK (status IN ('proposed', 'approved', 'rejected', 'cancelled')),
            plan_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            decided_at TEXT,
            decided_by TEXT,
            note TEXT
        );
        INSERT INTO drone_proposals SELECT * FROM drone_proposals_old;
        DROP TABLE drone_proposals_old;
        CREATE INDEX IF NOT EXISTS drone_proposals_incident ON drone_proposals(incident_id);
    """)


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

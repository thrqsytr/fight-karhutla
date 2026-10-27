"""Health of each data source, as last observed by the app itself. Shown on the map so a verifier
knows which evidence can be trusted to be current. Nothing here calls a source: it only reads what
the last ingest and enrichment runs recorded."""

import json

from . import db
from .config import Settings

# Not an outage: the run chose not to call the source.
NOT_A_CALL = ("failed: deferred", "failed: outside fixture coverage")
RECENT_ENRICHMENTS = 40


def record(settings: Settings, name: str, ok: bool, detail: str) -> None:
    """For sources called outside enrichment (FIRMS ingest, Sentinel-2 on request)."""
    with db.session(settings.db_path) as conn:
        conn.execute(
            "INSERT INTO app_state (key, value, updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (f"source:{name}", json.dumps({"ok": ok, "detail": detail[:200]}), db.utcnow()),
        )


def _recorded(conn, name: str) -> dict:
    row = conn.execute("SELECT value, updated_at FROM app_state WHERE key=?", (f"source:{name}",)).fetchone()
    if not row:
        return {"state": "unknown", "checked_at": None, "detail": None}
    v = json.loads(row["value"])
    return {"state": "ok" if v["ok"] else "error", "checked_at": row["updated_at"], "detail": v["detail"]}


def _from_enrichment(conn, key: str) -> dict:
    """Latest real result for one source across recently enriched incidents."""
    rows = conn.execute(
        "SELECT enriched_at, enrichment_json FROM incidents WHERE enriched_at IS NOT NULL ORDER BY enriched_at DESC LIMIT ?",
        (RECENT_ENRICHMENTS,),
    ).fetchall()
    for r in rows:
        v = str(json.loads(r["enrichment_json"] or "{}").get(key, ""))
        if not v or v.startswith(NOT_A_CALL):
            continue
        return {"state": "ok" if v == "ok" else "error", "checked_at": r["enriched_at"], "detail": None if v == "ok" else v[:200]}
    return {"state": "unknown", "checked_at": None, "detail": None}


def snapshot(settings: Settings) -> dict:
    with db.session(settings.db_path) as conn:
        newest = conn.execute("SELECT MAX(acquired_at) FROM hotspots").fetchone()[0]
        if settings.fixture_mode:
            firms = {"state": "sim", "checked_at": None, "detail": "fixture data"}
        else:
            firms = _recorded(conn, "firms")
        firms["newest_acquisition"] = newest
        out = {"firms": firms, "weather": _from_enrichment(conn, "weather"), "osm": _from_enrichment(conn, "osm"),
               "sentinel2": _recorded(conn, "sentinel2")}
    if settings.fixture_mode:
        # Fixture answers are not the live service, so they never read as "on".
        for k in ("weather", "osm"):
            if out[k]["state"] == "ok":
                out[k]["state"] = "sim"
    return out

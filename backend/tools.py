"""Tools the triage agent may call. Each one is bound to a single incident and returns stored evidence.

The agent never sees raw coordinates it could wander off with, never writes to the database itself,
and every value it can quote comes back with the evidence ID that proves it.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field

from . import db, evidence, meta_client, reports
from .config import Settings

log = logging.getLogger(__name__)

MAX_SEGMENT_CALLS = 3
CITIZEN_TYPES = {"report", "voice", "image"}

TOOL_DEFS = [
    {
        "type": "function",
        "name": "get_hotspots",
        "description": "NASA FIRMS satellite hotspots in this incident: count, time range, max fire radiative power (MW), confidence counts. A hotspot is a thermal anomaly, not a confirmed fire.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "type": "function",
        "name": "get_weather",
        "description": "Measured weather at the incident from Open-Meteo: wind speed (m/s) and the direction it blows FROM (degrees), relative humidity (%), rain over the previous 24 h (mm), and the next 6 h of wind.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "type": "function",
        "name": "get_nearby_assets",
        "description": "Nearest settlement, main road, river, lake or pond (within 10 km), and fire station (within 30 km) from OpenStreetMap, with distances in metres. Null means none found in that radius.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "type": "function",
        "name": "get_citizen_reports",
        "description": "Citizen evidence for this incident: typed descriptions, voice note transcripts, and photo readings (what is visible: smoke, flames, burned ground), each with its evidence ID.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "type": "function",
        "name": "segment",
        "description": "Run SAM 3.1 segmentation on one citizen photo for a short noun phrase such as 'burned ground' or 'charred tree stump'. Returns the share of the frame covered. Photos are oblique, so this never yields hectares. SAM does not reliably segment smoke.",
        "parameters": {
            "type": "object",
            "properties": {
                "evidence_id": {"type": "string", "description": "ID of an image evidence item, for example EV-004"},
                "concept": {"type": "string", "description": "Short noun phrase naming one visible thing"},
            },
            "required": ["evidence_id", "concept"],
            "additionalProperties": False,
        },
    },
]


@dataclass
class ToolContext:
    settings: Settings
    incident_id: str
    meter: meta_client.Meter
    segment_calls: int = 0
    log: list[dict] = field(default_factory=list)


def _evidence_of(settings: Settings, incident_id: str, types: set[str]) -> list[dict]:
    with db.session(settings.db_path) as conn:
        return [e for e in db.list_evidence(conn, incident_id) if e["type"] in types]


def _status(settings: Settings, incident_id: str, source: str) -> str | None:
    with db.session(settings.db_path) as conn:
        row = conn.execute("SELECT enrichment_json FROM incidents WHERE id=?", (incident_id,)).fetchone()
    return json.loads(row[0]).get(source) if row and row[0] else None


RECENT_FAILURE_MINUTES = 30


def _failed_recently(ctx: ToolContext, type_: str) -> str | None:
    """A source that just failed is not retried inside a triage: Overpass retries cost about 100 s each."""
    from datetime import datetime, timedelta, timezone

    with db.session(ctx.settings.db_path) as conn:
        row = conn.execute("SELECT enrichment_json, enriched_at FROM incidents WHERE id=?", (ctx.incident_id,)).fetchone()
    if not row or not row[0] or not row[1]:
        return None
    st = json.loads(row[0]).get(type_)
    recent = datetime.fromisoformat(row[1]) > datetime.now(timezone.utc) - timedelta(minutes=RECENT_FAILURE_MINUTES)
    return st if st and str(st).startswith("failed") and recent else None


def _one_source(ctx: ToolContext, type_: str, compact) -> dict:
    if type_ == "hotspot":
        # Cheap and local: refresh the snapshot first if the cluster has grown since it was taken.
        evidence.enrich_incident(ctx.settings, ctx.incident_id, sources={"hotspot"})
    found = _evidence_of(ctx.settings, ctx.incident_id, {type_})
    if not found and (reason := _failed_recently(ctx, type_)):
        return {"status": "unknown", "reason": reason[:300]}
    if not found:
        evidence.enrich_incident(ctx.settings, ctx.incident_id, osm_tiles_left=[1], sources={type_})
        found = _evidence_of(ctx.settings, ctx.incident_id, {type_})
    if not found:
        return {"status": "unknown", "reason": _status(ctx.settings, ctx.incident_id, type_) or "no data"}
    e = found[-1]
    return {"evidence_id": e["id"], "source": e["source"], "retrieved_at": e["retrieved_at"], **compact(e["payload"])}


def get_hotspots(ctx: ToolContext, **_) -> dict:
    return _one_source(ctx, "hotspot", lambda p: {k: p[k] for k in ("count", "first_acquired", "last_acquired", "max_frp_mw", "confidence_counts", "satellites")})


def get_weather(ctx: ToolContext, **_) -> dict:
    keys = ("observed_hour", "wind_speed_ms", "wind_direction_deg", "relative_humidity_pct", "rain_24h_mm", "rain_24h_hours_covered", "forecast_6h")
    return _one_source(ctx, "weather", lambda p: {k: p.get(k) for k in keys})


def get_nearby_assets(ctx: ToolContext, **_) -> dict:
    return _one_source(ctx, "osm", lambda p: {"nearest": p["nearest"], "search_radius_m": p["search_radius_m"], "fire_station_search_radius_m": p["fire_station_search_radius_m"]})


def get_citizen_reports(ctx: ToolContext, **_) -> dict:
    items = []
    for e in _evidence_of(ctx.settings, ctx.incident_id, CITIZEN_TYPES):
        p = e["payload"]
        if e["type"] == "report":
            items.append({"evidence_id": e["id"], "type": "report", "text": p.get("text"), "submitted_at": p.get("submitted_at")})
        elif e["type"] == "voice":
            items.append({"evidence_id": e["id"], "type": "voice", "transcript": p.get("transcript"), "asr_status": p.get("asr_status")})
        elif e["type"] == "image" and p.get("capture_source") == "citizen" and p.get("concept") is None:
            items.append({"evidence_id": e["id"], "type": "photo", "reading": p.get("reading"), "width": p.get("width"), "height": p.get("height")})
        elif e["type"] == "image" and p.get("concept"):
            items.append({"evidence_id": e["id"], "type": "segmentation", "of": p.get("of_evidence"), "concept": p["concept"], "fraction_of_frame": p.get("fraction_of_frame")})
    return {"items": items} if items else {"status": "none", "reason": "no citizen report for this incident"}


def segment(ctx: ToolContext, evidence_id: str = "", concept: str = "", **_) -> dict:
    if ctx.segment_calls >= MAX_SEGMENT_CALLS:
        return {"status": "refused", "reason": f"segment budget of {MAX_SEGMENT_CALLS} calls per triage is used"}
    concept = (concept or "").strip()[:60]
    target = next((e for e in _evidence_of(ctx.settings, ctx.incident_id, {"image"}) if e["id"] == evidence_id and e["payload"].get("media_id")), None)
    if target is None or not concept:
        return {"status": "refused", "reason": "evidence_id must be a citizen photo of this incident and concept must be non-empty"}
    path = reports.media_path(ctx.settings.data_dir, target["payload"]["media_id"])
    if path is None:
        return {"status": "unknown", "reason": "photo file is missing"}
    ctx.segment_calls += 1
    try:
        r = asyncio.run(meta_client.segment(ctx.settings, path.read_bytes(), concept, ctx.meter))
    except Exception as exc:
        log.warning("segment failed for %s %s: %r", ctx.incident_id, evidence_id, exc)
        return {"status": "unknown", "reason": f"segmentation failed: {str(exc)[:200]}"}
    p = target["payload"]
    payload = {
        "lat": p["lat"], "lon": p["lon"], "of_evidence": evidence_id, "concept": concept, "capture_source": p.get("capture_source"),
        "image_size": r["image_size"], "objects": r["objects"], "pixel_count": r["pixel_count"], "fraction_of_frame": r["fraction_of_frame"],
        # An oblique photo has no ground pixel size, so the gate can never turn this into hectares.
        "pixel_size_m": None,
    }
    summary = f"SAM 3.1 '{concept}' on {evidence_id}: {len(r['objects'])} object(s), {r['fraction_of_frame'] * 100:.1f}% of frame"
    with db.session(ctx.settings.db_path) as conn:
        ev = db.add_evidence(conn, ctx.incident_id, "image", "SAM 3.1 segmentation", summary, payload)
    return {"evidence_id": ev, "concept": concept, "objects": len(r["objects"]), "fraction_of_frame": r["fraction_of_frame"], "hectares": None}


HANDLERS = {"get_hotspots": get_hotspots, "get_weather": get_weather, "get_nearby_assets": get_nearby_assets,
            "get_citizen_reports": get_citizen_reports, "segment": segment}


def call(ctx: ToolContext, name: str, arguments: str) -> dict:
    try:
        args = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        args = {}
    handler = HANDLERS.get(name)
    if handler is None:
        out = {"status": "refused", "reason": f"unknown tool {name}"}
    else:
        try:
            out = handler(ctx, **args) if isinstance(args, dict) else handler(ctx)
        except Exception as exc:  # a tool failure becomes an unknown, with its cause, never a crash
            log.exception("tool %s failed for %s", name, ctx.incident_id)
            out = {"status": "unknown", "reason": f"tool error: {str(exc)[:200]}"}
    ctx.log.append({"tool": name, "arguments": args, "result_status": out.get("status", "ok"), "evidence_id": out.get("evidence_id")})
    return out

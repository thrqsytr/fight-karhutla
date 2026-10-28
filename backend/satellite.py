"""Sentinel-2 evidence for an incident: a before and after pair, dNBR, and three images.

Like the other sources, the network work runs outside any database transaction. Images are public
satellite data, stored under data/satellite/<incident>/ and served without a token.
"""

import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import admin_settings, db, source_status
from .config import Settings
from .sources import sentinel
from .sources.http import SourceError

log = logging.getLogger(__name__)

SOURCE = "Sentinel-2 L2A (Earth Search)"
# A pass comes about every 5 days; checking more often only repeats the answer. Admin setting satellite_fresh_hours.
IMAGE_KINDS = ("pre", "post", "overlay", "mask")
IMAGE_NAME = re.compile(r"^\d{8}T\d{6}_(pre|post|overlay|mask)\.png$")


def image_dir(data_dir: Path, incident_id: str) -> Path:
    return data_dir / "satellite" / incident_id


def image_path(data_dir: Path, incident_id: str, name: str) -> Path | None:
    if not IMAGE_NAME.match(name) or not re.fullmatch(r"[A-Z0-9-]+", incident_id):
        return None
    p = image_dir(data_dir, incident_id) / name
    return p if p.exists() else None


def summary_text(p: dict) -> str:
    pre, post = p["pre"]["datetime"][:10], p["post"]["datetime"][:10]
    if p["status"] == "ok":
        return (f"Sentinel-2 dNBR, before {pre} and after {post}: {p['burned_area_ha']} ha at dNBR >= {p['dnbr_threshold']} "
                f"within {p['clear_area_ha']} ha of clear ground; {p['low_severity_area_ha']} ha more at low severity")
    return f"Sentinel-2 dNBR, before {pre} and after {post}: {p['reason']}"


def latest(settings: Settings, incident_id: str) -> dict | None:
    with db.session(settings.db_path) as conn:
        found = [e for e in db.list_evidence(conn, incident_id) if e["type"] == "satellite"]
    return found[-1] if found else None


def collect(settings: Settings, incident_id: str, *, now: datetime | None = None, reader=None, search_fn=None, force: bool = False) -> dict:
    """Store a satellite pair as evidence when one exists. Returns {"evidence_id", ...} or {"status": "unknown", "reason"}."""
    now = now or datetime.now(timezone.utc)
    prev = latest(settings, incident_id)
    fresh_for = timedelta(hours=admin_settings.value(settings, "satellite_fresh_hours"))
    if prev and not force and datetime.fromisoformat(prev["retrieved_at"]) > now - fresh_for:
        return {"evidence_id": prev["id"], "reused": True}
    if settings.fixture_mode:
        return {"status": "unknown", "reason": "Sentinel-2 has no fixture; fixture mode makes no network call"}

    with db.session(settings.db_path) as conn:
        inc = conn.execute("SELECT first_seen, lat, lon FROM incidents WHERE id=?", (incident_id,)).fetchone()
        if inc is None:
            raise KeyError(incident_id)
        points = [(r["lat"], r["lon"]) for r in conn.execute("SELECT lat, lon FROM hotspots WHERE incident_id=?", (incident_id,))]
    points = points or [(inc["lat"], inc["lon"])]

    kwargs = {"reader": reader} if reader else {}
    if search_fn:
        kwargs["search_fn"] = search_fn
    try:
        pair = sentinel.satellite_pair(points, datetime.fromisoformat(inc["first_seen"]), now=now, cache_dir=settings.cache_dir, **kwargs)
    except SourceError as exc:
        log.warning("sentinel-2 failed for %s: %s", incident_id, exc)
        source_status.record(settings, "sentinel2", False, str(exc))
        return {"status": "unknown", "reason": f"Sentinel-2 failed: {str(exc)[:200]}"}
    # The archive answered, even when no clear pair exists yet.
    source_status.record(settings, "sentinel2", True, f"{pair['status']} for {incident_id}")
    if pair["status"] not in ("ok", "hazy"):
        return {"status": "unknown", "reason": pair["reason"]}

    render = pair.pop("_render")
    stamp = now.strftime("%Y%m%dT%H%M%S")
    try:
        pngs = sentinel.render_pngs(render)
    except SourceError as exc:
        log.warning("sentinel-2 images failed for %s: %s", incident_id, exc)
        pngs = {}
    folder = image_dir(settings.data_dir, incident_id)
    folder.mkdir(parents=True, exist_ok=True)
    pair["images"] = {}
    for kind, data in pngs.items():
        name = f"{stamp}_{kind}.png"
        (folder / name).write_bytes(data)
        pair["images"][kind] = name
    pair["attribution"] = sentinel.ATTRIBUTION

    with db.session(settings.db_path) as conn:
        if conn.execute("SELECT 1 FROM incidents WHERE id=?", (incident_id,)).fetchone() is None:
            return {"status": "unknown", "reason": "incident was deleted"}
        ev = db.add_evidence(conn, incident_id, "satellite", SOURCE, summary_text(pair), pair)
    return {"evidence_id": ev, "reused": False}

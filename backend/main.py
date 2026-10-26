import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from typing import Literal

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db, ingest
from .config import Settings, get_settings
from .sources import firms, osm, weather

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("karhutla")

PUBLIC_COORD_DECIMALS = 3  # about 100 m
ATTRIBUTIONS = [firms.ATTRIBUTION, weather.ATTRIBUTION, osm.ATTRIBUTION]


def _start_scheduler(settings: Settings) -> BackgroundScheduler | None:
    if not settings.scheduler_enabled or settings.fixture_mode:
        return None
    sched = BackgroundScheduler(timezone="UTC")

    def job() -> None:
        try:
            ingest.run_once(settings)
        except Exception:
            log.exception("scheduled FIRMS ingest failed")

    sched.add_job(job, "interval", minutes=settings.firms_poll_minutes, id="firms", next_run_time=datetime.now(timezone.utc))
    sched.start()
    log.info("scheduler started: FIRMS every %d min", settings.firms_poll_minutes)
    return sched


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    db.init(settings.db_path)
    if settings.fixture_mode:
        with db.session(settings.db_path) as conn:
            empty = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 0
        if empty:
            log.info("fixture mode with empty database: loading fixture hotspots")
            ingest.run_once(settings)
    sched = _start_scheduler(settings)
    yield
    if sched:
        sched.shutdown(wait=False)


app = FastAPI(title="Fight Karhutla", version="0.1.0", lifespan=lifespan)


def is_verifier(authorization: str | None = Header(default=None), settings: Settings = Depends(get_settings)) -> bool:
    if not settings.verifier_token or not authorization or not authorization.startswith("Bearer "):
        return False
    return hmac.compare_digest(authorization.removeprefix("Bearer "), settings.verifier_token)


def require_verifier(ok: bool = Depends(is_verifier)) -> None:
    if not ok:
        raise HTTPException(status_code=401, detail="verifier token required")


def _public_view(inc: dict) -> dict:
    inc = dict(inc)
    inc["lat"] = round(inc["lat"], PUBLIC_COORD_DECIMALS)
    inc["lon"] = round(inc["lon"], PUBLIC_COORD_DECIMALS)
    return inc


def _visible_to_public(conn, inc: dict) -> bool:
    """Satellite-only incidents are open data. Citizen reports become public only after a human approves."""
    if inc["trigger"] == "hotspot_cluster":
        return True
    row = conn.execute(
        "SELECT decision FROM verdicts WHERE incident_id=? ORDER BY id DESC LIMIT 1", (inc["id"],)
    ).fetchone()
    return bool(row and row["decision"] == "approve")


@app.get("/health")
def health(settings: Settings = Depends(get_settings)) -> dict:
    with db.session(settings.db_path) as conn:
        n = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
    return {
        "status": "ok",
        "git_sha": settings.git_sha,
        "fixture_mode": settings.fixture_mode,
        "llm_provider": settings.llm_provider,
        "asr_provider": settings.asr_provider,
        "map_provider": settings.map_provider,
        "incidents": n,
    }


@app.get("/config.js", response_class=PlainTextResponse)
def config_js(settings: Settings = Depends(get_settings)) -> PlainTextResponse:
    cfg = {
        "mapProvider": settings.map_provider if settings.google_maps_key or settings.map_provider == "maplibre" else "maplibre",
        "googleMapsKey": settings.google_maps_key if settings.map_provider == "google" else "",
        "fixtureMode": settings.fixture_mode,
        "attributions": ATTRIBUTIONS,
        "gitSha": settings.git_sha,
    }
    return PlainTextResponse(f"window.KARHUTLA_CONFIG = {json.dumps(cfg)};\n", media_type="application/javascript")


@app.get("/api/incidents")
def list_incidents(verifier: bool = Depends(is_verifier), settings: Settings = Depends(get_settings)) -> dict:
    with db.session(settings.db_path) as conn:
        rows = conn.execute("SELECT * FROM incidents ORDER BY last_seen DESC").fetchall()
        items = [db.incident_row_to_dict(r) for r in rows]
        if not verifier:
            items = [_public_view(i) for i in items if _visible_to_public(conn, i)]
    for i in items:
        i.pop("triage", None)
    return {"count": len(items), "items": items}


@app.get("/api/incidents/{incident_id}")
def get_incident(incident_id: str, verifier: bool = Depends(is_verifier), settings: Settings = Depends(get_settings)) -> dict:
    with db.session(settings.db_path) as conn:
        row = conn.execute("SELECT * FROM incidents WHERE id=?", (incident_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="incident not found")
        inc = db.incident_row_to_dict(row)
        if not verifier and not _visible_to_public(conn, inc):
            raise HTTPException(status_code=404, detail="incident not found")
        evidence = db.list_evidence(conn, incident_id)
        hotspots = [dict(r) for r in conn.execute(
            "SELECT id, lat, lon, acquired_at, satellite, confidence, frp FROM hotspots WHERE incident_id=? ORDER BY acquired_at", (incident_id,)
        )]
        verdicts = [dict(r) for r in conn.execute(
            "SELECT decision, note, channel, created_at FROM verdicts WHERE incident_id=? ORDER BY id", (incident_id,)
        )]
    if not verifier:
        inc = _public_view(inc)
        # Citizen photos and voice are visible to verifiers only.
        evidence = [e for e in evidence if e["type"] not in {"image", "voice"}]
        for h in hotspots:
            h["lat"], h["lon"] = round(h["lat"], PUBLIC_COORD_DECIMALS), round(h["lon"], PUBLIC_COORD_DECIMALS)
    return {**inc, "evidence": evidence, "hotspots": hotspots, "verdicts": verdicts}


class VerdictIn(BaseModel):
    decision: Literal["approve", "reject"]
    note: str | None = Field(default=None, max_length=500)


@app.post("/api/incidents/{incident_id}/verdict", dependencies=[Depends(require_verifier)])
def post_verdict(incident_id: str, body: VerdictIn, settings: Settings = Depends(get_settings)) -> dict:
    verifier_hash = hashlib.sha256(("dashboard:" + settings.verifier_token).encode()).hexdigest()[:16]
    with db.session(settings.db_path) as conn:
        if not conn.execute("SELECT 1 FROM incidents WHERE id=?", (incident_id,)).fetchone():
            raise HTTPException(status_code=404, detail="incident not found")
        cur = conn.execute(
            "INSERT INTO verdicts (incident_id, verifier_hash, decision, note, channel, created_at) VALUES (?,?,?,?,?,?)",
            (incident_id, verifier_hash, body.decision, body.note, "dashboard", db.utcnow()),
        )
        vid = cur.lastrowid
    # Read back from the source to prove the row landed.
    with db.session(settings.db_path) as conn:
        row = conn.execute("SELECT id, decision, created_at FROM verdicts WHERE id=?", (vid,)).fetchone()
    if not row:
        raise HTTPException(status_code=500, detail="verdict was not persisted")
    return dict(row)


@app.get("/api/hotspots")
def list_hotspots(settings: Settings = Depends(get_settings)) -> dict:
    with db.session(settings.db_path) as conn:
        rows = conn.execute(
            "SELECT id, lat, lon, acquired_at, satellite, confidence, frp, incident_id FROM hotspots ORDER BY acquired_at DESC LIMIT 5000"
        ).fetchall()
    items = [dict(r) for r in rows]
    return {"count": len(items), "items": items, "attribution": firms.ATTRIBUTION}


@app.post("/api/ingest/run", dependencies=[Depends(require_verifier)])
def run_ingest(settings: Settings = Depends(get_settings)) -> dict:
    return ingest.run_once(settings)


@app.post("/api/replay/start", dependencies=[Depends(require_verifier)])
def replay_start() -> JSONResponse:
    # Replay of three historical incidents lands on Day 7 (spec section 10). Saying so beats a silent no-op.
    return JSONResponse(status_code=501, content={"detail": "replay mode is not built yet"})


settings_for_static = get_settings()
if settings_for_static.frontend_dir.exists():
    app.mount("/", StaticFiles(directory=settings_for_static.frontend_dir, html=True), name="frontend")

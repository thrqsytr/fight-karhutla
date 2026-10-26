import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
from typing import Literal

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db, evidence, ingest, reports
from .config import Settings, get_settings
from .sources import firms, osm, weather

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("karhutla")

PUBLIC_COORD_DECIMALS = 3  # about 100 m

# Severity is reported as measured FIRMS values (fire radiative power, confidence), not as a model score.
# VIIRS confidence is l|n|h; MODIS is 0-100, where >= 80 counts as high.
SEVERITY_SUBQUERY = """
    SELECT incident_id,
           MAX(frp) AS max_frp,
           ROUND(SUM(frp), 2) AS sum_frp,
           SUM(CASE WHEN confidence = 'h' OR (confidence GLOB '[0-9]*' AND CAST(confidence AS INTEGER) >= 80) THEN 1 ELSE 0 END) AS high_conf_count
    FROM hotspots WHERE incident_id IS NOT NULL GROUP BY incident_id
"""
SEVERITY_COLUMNS = "s.max_frp, s.sum_frp, COALESCE(s.high_conf_count, 0) AS high_conf_count"
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
        try:
            evidence.run_enrichment(settings)
        except Exception:
            log.exception("scheduled evidence enrichment failed")

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
            evidence.run_enrichment(settings)
    sched = _start_scheduler(settings)
    yield
    if sched:
        sched.shutdown(wait=False)


app = FastAPI(title="Fight Karhutla", version="0.1.0", lifespan=lifespan)
# The incident and hotspot lists are about 1.3 MB of JSON; gzip brings them to about 0.1 MB.
app.add_middleware(GZipMiddleware, minimum_size=1024)


def etag_json(request: Request, payload: dict, *, private: bool) -> Response:
    """JSON with an ETag. A refresh with unchanged data costs a 304 instead of the full body.

    no-cache means the browser always revalidates, so data is never shown stale.
    """
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    tag = '"' + hashlib.sha256(body).hexdigest()[:24] + '"'
    headers = {"ETag": tag, "Cache-Control": ("private" if private else "public") + ", no-cache", "Vary": "Authorization"}
    if tag in request.headers.get("if-none-match", ""):
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)


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


PUBLIC_TRIGGERS = ("hotspot_cluster", "merged")
PRIVATE_EVIDENCE = {"image", "voice", "report"}


def _visible_to_public(conn, inc: dict) -> bool:
    """Incidents with satellite hotspots are open data (citizen evidence inside stays private).
    Incidents built only from citizen reports become public after a human approves."""
    if inc["trigger"] in PUBLIC_TRIGGERS:
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


# Latest verdict per incident, so public visibility is decided in one query instead of one per row.
LATEST_VERDICT_SUBQUERY = """
    SELECT v.incident_id, v.decision FROM verdicts v
    WHERE v.id = (SELECT MAX(id) FROM verdicts WHERE incident_id = v.incident_id)
"""


@app.get("/api/incidents")
def list_incidents(request: Request, verifier: bool = Depends(is_verifier), settings: Settings = Depends(get_settings)) -> Response:
    # Must match _visible_to_public: satellite clusters are public, citizen reports only after approval.
    public_filter = "" if verifier else f"WHERE i.trigger IN {PUBLIC_TRIGGERS!r} OR lv.decision = 'approve'"
    with db.session(settings.db_path) as conn:
        rows = conn.execute(
            f"""SELECT i.*, {SEVERITY_COLUMNS} FROM incidents i
                LEFT JOIN ({SEVERITY_SUBQUERY}) s ON s.incident_id = i.id
                LEFT JOIN ({LATEST_VERDICT_SUBQUERY}) lv ON lv.incident_id = i.id
                {public_filter}
                ORDER BY i.last_seen DESC, i.id"""
        ).fetchall()
    items = [db.incident_row_to_dict(r) for r in rows]
    if not verifier:
        items = [_public_view(i) for i in items]
    for i in items:
        i.pop("triage", None)
    return etag_json(request, {"count": len(items), "items": items}, private=verifier)


@app.get("/api/incidents/{incident_id}")
def get_incident(incident_id: str, verifier: bool = Depends(is_verifier), settings: Settings = Depends(get_settings)) -> dict:
    with db.session(settings.db_path) as conn:
        row = conn.execute(
            f"SELECT i.*, {SEVERITY_COLUMNS} FROM incidents i LEFT JOIN ({SEVERITY_SUBQUERY}) s ON s.incident_id = i.id WHERE i.id=?",
            (incident_id,),
        ).fetchone()
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
        evidence = [e for e in evidence if e["type"] not in PRIVATE_EVIDENCE]
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
def list_hotspots(request: Request, settings: Settings = Depends(get_settings)) -> Response:
    with db.session(settings.db_path) as conn:
        rows = conn.execute(
            "SELECT id, lat, lon, acquired_at, satellite, confidence, frp, incident_id FROM hotspots ORDER BY acquired_at DESC LIMIT 5000"
        ).fetchall()
    items = [dict(r) for r in rows]
    return etag_json(request, {"count": len(items), "items": items, "attribution": firms.ATTRIBUTION}, private=False)


@app.get("/api/stats")
def stats(request: Request, settings: Settings = Depends(get_settings)) -> Response:
    """Headline numbers. Windows are anchored on the newest acquisition so fixture data stays meaningful."""
    with db.session(settings.db_path) as conn:
        newest = conn.execute("SELECT MAX(acquired_at) FROM hotspots").fetchone()[0]
        out = {
            "incidents": conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0],
            "incidents_multi_hotspot": conn.execute("SELECT COUNT(*) FROM incidents WHERE hotspot_count >= 3").fetchone()[0],
            "hotspots": conn.execute("SELECT COUNT(*) FROM hotspots").fetchone()[0],
            "newest_acquisition": newest,
            "hotspots_last_24h": 0,
            "high_conf_last_24h": 0,
        }
        if newest:
            since = (datetime.fromisoformat(newest) - timedelta(hours=24)).isoformat()
            out["hotspots_last_24h"] = conn.execute("SELECT COUNT(*) FROM hotspots WHERE acquired_at > ?", (since,)).fetchone()[0]
            out["high_conf_last_24h"] = conn.execute(
                "SELECT COUNT(*) FROM hotspots WHERE acquired_at > ? AND (confidence = 'h' OR (confidence GLOB '[0-9]*' AND CAST(confidence AS INTEGER) >= 80))",
                (since,),
            ).fetchone()[0]
    return etag_json(request, out, private=False)


async def _read_capped(f: UploadFile, cap: int) -> bytes:
    data = await f.read(cap + 1)
    if len(data) > cap:
        raise HTTPException(status_code=413, detail=f"{f.filename or 'file'} is too large")
    return data


def _enrich_after_report(settings: Settings, incident_id: str) -> None:
    try:
        with db.session(settings.db_path) as conn:
            evidence.enrich_incident(conn, settings, incident_id, osm_tiles_left=[1])
    except Exception:
        log.exception("enrichment after report failed for %s", incident_id)


@app.post("/api/reports", status_code=201)
async def post_report(
    request: Request,
    background: BackgroundTasks,
    lat: float = Form(...),
    lon: float = Form(...),
    location_source: str = Form(...),
    accuracy_m: float | None = Form(None),
    text: str | None = Form(None),
    photos: list[UploadFile] = File(default=[]),
    audio: UploadFile | None = File(None),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Public citizen report. No account and no phone number: the reporter is not identified."""
    client = request.client.host if request.client else "unknown"
    if reports.rate_limited(client):
        raise HTTPException(status_code=429, detail="Terlalu banyak laporan dari perangkat ini. Coba lagi dalam satu jam.")
    if len(photos) > reports.MAX_PHOTOS:
        raise HTTPException(status_code=422, detail=f"Maksimal {reports.MAX_PHOTOS} foto.")
    photo_items = [reports.Photo(await _read_capped(p, reports.MAX_PHOTO_BYTES), p.filename or "") for p in photos if p.filename]
    audio_item = None
    if audio is not None and audio.filename:
        audio_item = reports.Audio(await _read_capped(audio, reports.MAX_AUDIO_BYTES), audio.content_type or "")
    try:
        with db.session(settings.db_path) as conn:
            result = reports.submit(
                conn, settings.data_dir, lat=lat, lon=lon, accuracy_m=accuracy_m, location_source=location_source,
                text=text, photos=photo_items, audio=audio_item,
            )
    except reports.ReportError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    # Read back from the source to prove the report landed before telling the reporter it did.
    keys = [f"{result['incident_id']}/{e}" for e in result["evidence_ids"]]
    with db.session(settings.db_path) as conn:
        stored = conn.execute(
            f"SELECT COUNT(*) FROM evidence WHERE id IN ({','.join('?' * len(keys))})", keys
        ).fetchone()[0]
    if stored != len(keys):
        raise HTTPException(status_code=500, detail="report was not fully stored")
    background.add_task(_enrich_after_report, settings, result["incident_id"])
    return result


@app.delete("/api/incidents/{incident_id}/citizen-data", dependencies=[Depends(require_verifier)])
def delete_citizen_data(incident_id: str, settings: Settings = Depends(get_settings)) -> dict:
    """Retention and cleanup: remove citizen text, photos, and voice for one incident."""
    with db.session(settings.db_path) as conn:
        try:
            result = reports.delete_citizen_data(conn, settings.data_dir, incident_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="incident not found")
    # Read back: no citizen evidence may remain.
    with db.session(settings.db_path) as conn:
        left = conn.execute(
            "SELECT COUNT(*) FROM evidence WHERE incident_id=? AND type IN ('report','image','voice')", (incident_id,)
        ).fetchone()[0]
    if left:
        raise HTTPException(status_code=500, detail=f"{left} citizen evidence rows remain")
    return result


@app.get("/api/media/{media_id}", dependencies=[Depends(require_verifier)])
def get_media(media_id: str, settings: Settings = Depends(get_settings)) -> FileResponse:
    """Citizen photos and voice notes are visible to verifiers only."""
    path = reports.media_path(settings.data_dir, media_id)
    if path is None:
        raise HTTPException(status_code=404, detail="media not found")
    ext = path.suffix.lstrip(".")
    mime = "image/jpeg" if ext == "jpg" else next((m for m, e in reports.AUDIO_TYPES.items() if e == ext), "application/octet-stream")
    return FileResponse(path, media_type=mime, headers={"Cache-Control": "private, max-age=3600"})


@app.post("/api/incidents/{incident_id}/enrich", dependencies=[Depends(require_verifier)])
def enrich(incident_id: str, settings: Settings = Depends(get_settings)) -> dict:
    """Collect missing evidence for one incident now, regardless of the automatic priority thresholds."""
    with db.session(settings.db_path) as conn:
        try:
            status = evidence.enrich_incident(conn, settings, incident_id, osm_tiles_left=[1])
        except KeyError:
            raise HTTPException(status_code=404, detail="incident not found")
    return {"incident_id": incident_id, "sources": status}


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

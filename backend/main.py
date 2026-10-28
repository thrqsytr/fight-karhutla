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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import admin_settings, agent, db, drone_flight, drone_missions, drone_replay, ground, share, evidence, ingest, intake, replay, reports, satellite, source_status, triage_control, wa_reports, whatsapp
from .config import Settings, get_settings
from .sources import firms, osm, sentinel, weather

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
ATTRIBUTIONS = [firms.ATTRIBUTION, weather.ATTRIBUTION, osm.ATTRIBUTION, sentinel.ATTRIBUTION, "Satellite layers: NASA GIBS / Worldview"]


SCHEDULER_TICK_MIN = 5


def _start_scheduler(settings: Settings) -> BackgroundScheduler | None:
    if not settings.scheduler_enabled or settings.fixture_mode:
        return None
    sched = BackgroundScheduler(timezone="UTC")

    def job() -> None:
        # The tick is frequent; the fetch runs only when the interval chosen in the admin panel has passed.
        with db.session(settings.db_path) as conn:
            due = admin_settings.fetch_due(settings, conn)
            if due:
                admin_settings.mark_fetch(conn)
        if due:
            try:
                ingest.run_once(settings)
            except Exception:
                log.exception("scheduled FIRMS ingest failed")
            try:
                evidence.run_enrichment(settings)
            except Exception:
                log.exception("scheduled evidence enrichment failed")
        if settings.model_api_key:
            try:
                auto_triage(settings)
            except Exception:
                log.exception("scheduled triage failed")

    sched.add_job(job, "interval", minutes=SCHEDULER_TICK_MIN, id="tick", next_run_time=datetime.now(timezone.utc))
    sched.start()
    log.info("scheduler started: tick every %d min, FIRMS interval from admin settings", SCHEDULER_TICK_MIN)
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
    if settings.osm_fallback_url and not settings.osm_fallback_path.exists():
        # About 100 MB once; in a thread so the app answers while it downloads.
        import threading

        threading.Thread(target=osm.ensure_fallback, args=(settings.osm_fallback_path, settings.osm_fallback_url), daemon=True).start()
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
            f"""SELECT i.*, {SEVERITY_COLUMNS}, CASE WHEN lv.decision IN ('approve', 'reject') THEN lv.decision END AS latest_verdict FROM incidents i
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
        private_ids = {e["id"] for e in evidence if e["type"] in PRIVATE_EVIDENCE}
        evidence = [e for e in evidence if e["type"] not in PRIVATE_EVIDENCE]
        approved = bool(verdicts) and verdicts[-1]["decision"] == "approve"
        tri = inc.get("triage")
        if tri and not approved and any(set(c["evidence_ids"]) & private_ids for c in tri.get("claims", [])):
            # A brief built on citizen evidence can quote the citizen, so it stays private until a human approves.
            inc["triage"] = {"status": tri["status"], "hidden": "awaiting human verification"}
            inc["gate"] = None
        for h in hotspots:
            h["lat"], h["lon"] = round(h["lat"], PUBLIC_COORD_DECIMALS), round(h["lon"], PUBLIC_COORD_DECIMALS)
    return {**inc, "evidence": evidence, "hotspots": hotspots, "verdicts": verdicts}


class VerdictIn(BaseModel):
    decision: Literal["approve", "reject", "revoke"]
    note: str | None = Field(default=None, max_length=500)


@app.post("/api/incidents/{incident_id}/verdict", dependencies=[Depends(require_verifier)])
def post_verdict(incident_id: str, body: VerdictIn, background: BackgroundTasks, settings: Settings = Depends(get_settings)) -> dict:
    verifier_hash = hashlib.sha256(("dashboard:" + settings.verifier_token).encode()).hexdigest()[:16]
    with db.session(settings.db_path) as conn:
        if not conn.execute("SELECT 1 FROM incidents WHERE id=?", (incident_id,)).fetchone():
            raise HTTPException(status_code=404, detail="incident not found")
        last = conn.execute("SELECT decision FROM verdicts WHERE incident_id=? ORDER BY id DESC LIMIT 1", (incident_id,)).fetchone()
        active = last["decision"] if last and last["decision"] != "revoke" else None
        # One standing decision at a time: change it by revoking first, so every change is on record.
        if body.decision == "revoke" and active is None:
            raise HTTPException(status_code=409, detail="there is no decision to revoke")
        if body.decision != "revoke" and active is not None:
            raise HTTPException(status_code=409, detail=f"already decided ({active}); revoke it first")
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
    if body.decision in ("approve", "reject"):
        background.add_task(whatsapp.notify_reporters, settings, incident_id, body.decision)
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


@app.post("/api/incidents/{incident_id}/satellite", dependencies=[Depends(require_verifier)])
def post_satellite(incident_id: str, settings: Settings = Depends(get_settings)) -> dict:
    """Look for a Sentinel-2 pair now. Free to call (open data), but it takes about 10 to 30 seconds."""
    try:
        return satellite.collect(settings, incident_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="incident not found")


@app.get("/api/satellite/{incident_id}/{name}")
def get_satellite_image(incident_id: str, name: str, settings: Settings = Depends(get_settings)) -> FileResponse:
    # Public: Sentinel-2 is open data. The name pattern rules out paths outside the incident folder.
    p = satellite.image_path(settings.data_dir, incident_id, name)
    if p is None:
        raise HTTPException(status_code=404, detail="image not found")
    return FileResponse(p, media_type="image/png", headers={"Cache-Control": "public, max-age=86400, immutable"})


@app.get("/api/drone/missions")
def list_drone_missions(incident_id: str | None = None, settings: Settings = Depends(get_settings)) -> dict:
    """Simulated drone surveys (SIMULASI). Shown beside an incident, never stored as its evidence."""
    items = [drone_replay.summary(m) for m in drone_replay.missions(settings.replay_dir) if incident_id in (None, m["incident_id"])]
    return {"count": len(items), "items": items}


@app.get("/api/drone/missions/{mission_id}")
def get_drone_mission(mission_id: str, settings: Settings = Depends(get_settings)) -> dict:
    m = drone_replay.find(settings.replay_dir, mission_id)
    if m is None:
        raise HTTPException(status_code=404, detail="mission not found")
    return m


@app.get("/api/drone/missions/{mission_id}/{kind}/{name}")
def drone_file(mission_id: str, kind: str, name: str, settings: Settings = Depends(get_settings)) -> FileResponse:
    p = drone_replay.file_path(settings.replay_dir, mission_id, kind, name)
    if p is None:
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(p, headers={"Cache-Control": "public, max-age=86400"})


def _shareable(settings: Settings, incident_id: str) -> tuple[dict, list[dict], list[dict], str | None] | None:
    """The public view of an incident, or None when the public may not see it."""
    with db.session(settings.db_path) as conn:
        row = conn.execute(
            f"SELECT i.*, {SEVERITY_COLUMNS} FROM incidents i LEFT JOIN ({SEVERITY_SUBQUERY}) s ON s.incident_id = i.id WHERE i.id=?", (incident_id,)
        ).fetchone()
        if not row:
            return None
        inc = db.incident_row_to_dict(row)
        if not _visible_to_public(conn, inc):
            return None
        evidence = [e for e in db.list_evidence(conn, incident_id) if e["type"] not in PRIVATE_EVIDENCE]
        hotspots = [{"lat": round(r["lat"], PUBLIC_COORD_DECIMALS), "lon": round(r["lon"], PUBLIC_COORD_DECIMALS), "frp": r["frp"]}
                    for r in conn.execute("SELECT lat, lon, frp FROM hotspots WHERE incident_id=?", (incident_id,))]
        last = conn.execute("SELECT decision FROM verdicts WHERE incident_id=? ORDER BY id DESC LIMIT 1", (incident_id,)).fetchone()
    verdict = last["decision"] if last and last["decision"] != "revoke" else None
    return _public_view(inc), evidence, hotspots, verdict


SHARE_LANGS = ("id", "en")


@app.get("/og/{incident_id}.png")
def og_image(incident_id: str, lang: str = "id", settings: Settings = Depends(get_settings)) -> Response:
    """Static share image. Cached on disk until the incident changes (status, hotspots, verdict)."""
    lang = lang if lang in SHARE_LANGS else "id"
    found = _shareable(settings, incident_id)
    if found is None:
        raise HTTPException(status_code=404, detail="incident not found")
    inc, evidence, hotspots, verdict = found
    folder = settings.data_dir / "og"
    path = folder / f"{incident_id}_{share.cache_key(inc, verdict, lang, evidence)}.png"
    if not path.exists():
        folder.mkdir(parents=True, exist_ok=True)
        png = share.render(share.facts(inc, evidence, verdict, lang), hotspots, cache_dir=settings.cache_dir, lang=lang)
        for old in folder.glob(f"{incident_id}_*.png"):
            old.unlink(missing_ok=True)
        path.write_bytes(png)
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})


@app.get("/api/incidents/{incident_id}/share")
def share_info(incident_id: str, lang: str = "id", settings: Settings = Depends(get_settings)) -> dict:
    """The link, image, and text a share shows, so the dialog previews exactly what others will see."""
    lang = lang if lang in SHARE_LANGS else "id"
    found = _shareable(settings, incident_id)
    if found is None:
        raise HTTPException(status_code=404, detail="not shareable: citizen-only incidents are public only after a verifier approves them")
    inc, evidence, _, verdict = found
    base = settings.public_url.rstrip("/")
    url = f"{base}/i/{incident_id}" + ("?lang=en" if lang == "en" else "")
    title, desc = share.summary(share.facts(inc, evidence, verdict, lang), url, lang)
    return {"url": url, "image": f"/og/{incident_id}.png?lang={lang}&v={share.cache_key(inc, verdict, lang, evidence)}", "title": title, "description": desc}


@app.get("/i/{incident_id}", response_class=HTMLResponse)
def share_page(incident_id: str, lang: str = "id", settings: Settings = Depends(get_settings)) -> HTMLResponse:
    """What a shared link opens: OG tags for WhatsApp and Facebook previews, then a jump to the incident on the map."""
    from html import escape

    lang = lang if lang in SHARE_LANGS else "id"
    found = _shareable(settings, incident_id)
    if found is None:
        raise HTTPException(status_code=404, detail="incident not found")
    inc, evidence, _, verdict = found
    base = settings.public_url.rstrip("/")
    url = f"{base}/i/{incident_id}" + ("?lang=en" if lang == "en" else "")
    target = f"/?incident={incident_id}"
    image = f"{base}/og/{incident_id}.png?lang={lang}&v={share.cache_key(inc, verdict, lang, evidence)}"
    title, desc = share.summary(share.facts(inc, evidence, verdict, lang), url, lang)
    t, d = escape(title), escape(desc)
    html = f"""<!doctype html><html lang="{lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{t}: Fight Karhutla</title>
<meta name="description" content="{d}">
<meta property="og:type" content="website"><meta property="og:site_name" content="Fight Karhutla">
<meta property="og:title" content="{t}"><meta property="og:description" content="{d}"><meta property="og:url" content="{escape(url)}">
<meta property="og:image" content="{escape(image)}"><meta property="og:image:width" content="1200"><meta property="og:image:height" content="630">
<meta property="og:image:alt" content="{t}"><meta name="twitter:card" content="summary_large_image">
<meta http-equiv="refresh" content="0; url={escape(target)}"><link rel="stylesheet" href="/style.css"></head>
<body class="page-doc"><main class="doc"><article class="card doc-body"><h2>{t}</h2><p>{d}</p>
<p><img src="{escape(image)}" alt="{t}" style="width:100%;border-radius:12px"></p><p><a href="{escape(target)}">Fight Karhutla</a></p></article></main>
<script>location.replace({target!r});</script></body></html>"""
    return HTMLResponse(html, headers={"Cache-Control": "public, max-age=300"})


@app.get("/api/sources")
def sources(request: Request, settings: Settings = Depends(get_settings)) -> Response:
    """Last observed health of FIRMS, Open-Meteo, and Overpass. NASA GIBS is checked by the browser."""
    return etag_json(request, source_status.snapshot(settings), private=False)


async def _read_capped(f: UploadFile, cap: int) -> bytes:
    data = await f.read(cap + 1)
    if len(data) > cap:
        raise HTTPException(status_code=413, detail=f"{f.filename or 'file'} is too large")
    return data


def _enrich_after_report(settings: Settings, incident_id: str) -> None:
    try:
        intake.process(settings, incident_id)
    except Exception:
        log.exception("media processing after report failed for %s", incident_id)
    try:
        evidence.enrich_incident(settings, incident_id, osm_tiles_left=[1])
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
    what = whatsapp._what(len(photo_items), 1 if audio_item else 0, bool((text or "").strip()))
    background.add_task(whatsapp.notify_verifiers, settings, result["incident_id"], what, "form web")
    return result


@app.get("/api/wa/webhook")
def wa_verify(request: Request, settings: Settings = Depends(get_settings)) -> Response:
    """Meta calls this once when the webhook is registered and expects the challenge back."""
    q = request.query_params
    if not settings.wa_verify_token or q.get("hub.mode") != "subscribe" or not hmac.compare_digest(q.get("hub.verify_token", ""), settings.wa_verify_token):
        raise HTTPException(status_code=403, detail="verification failed")
    return Response(q.get("hub.challenge", ""), media_type="text/plain")


class CropIn(BaseModel):
    box: tuple[int, int, int, int]  # left, top, right, bottom in pixels of the stored photo
    reason: str = Field(default="", max_length=200)


@app.post("/api/incidents/{incident_id}/evidence/{evidence_id}/crop", dependencies=[Depends(require_verifier)])
def crop_citizen_photo(incident_id: str, evidence_id: str, body: CropIn, settings: Settings = Depends(get_settings)) -> dict:
    """Redaction by a verifier: crop a citizen photo. The cut-away part is deleted with the original file."""
    who = hashlib.sha256(("dashboard:" + settings.verifier_token).encode()).hexdigest()[:16]
    try:
        with db.session(settings.db_path) as conn:
            out = reports.crop_photo(conn, settings.data_dir, incident_id, evidence_id, body.box, who, body.reason)
    except KeyError:
        raise HTTPException(status_code=404, detail="photo evidence not found")
    except reports.ReportError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if reports.media_path(settings.data_dir, out["media_id"]) is None:  # read back
        raise HTTPException(status_code=500, detail="cropped photo was not stored")
    return out


class ShowcaseIn(BaseModel):
    public: bool


@app.get("/api/wa/reports")
def wa_report_log(verifier: bool = Depends(is_verifier), settings: Settings = Depends(get_settings)) -> dict:
    """WhatsApp report log. Verifiers see every report; the public sees only those marked as public examples."""
    with db.session(settings.db_path) as conn:
        ids = wa_reports.incident_ids(conn, public=not verifier)
        items = [r for r in (wa_reports.build(conn, i, verifier=verifier) for i in ids) if r]
    return {"verifier": verifier, "count": len(items), "items": items, "templates": wa_reports.reply_templates(),
            "whatsapp": {"configured": whatsapp.configured(settings)}}


@app.put("/api/wa/reports/{incident_id}/showcase", dependencies=[Depends(require_verifier)])
def wa_showcase(incident_id: str, body: ShowcaseIn, settings: Settings = Depends(get_settings)) -> dict:
    who = hashlib.sha256(("dashboard:" + settings.verifier_token).encode()).hexdigest()[:16]
    with db.session(settings.db_path) as conn:
        if not wa_reports.incident_ids(conn, public=False).count(incident_id):
            raise HTTPException(status_code=404, detail="no WhatsApp report for this incident")
        wa_reports.set_public(conn, incident_id, body.public, who)
    with db.session(settings.db_path) as conn:  # read back
        return {"incident_id": incident_id, "public": wa_reports.is_public(conn, incident_id)}


@app.get("/api/wa/reports/{incident_id}/media/{media_id}")
def wa_report_media(incident_id: str, media_id: str, verifier: bool = Depends(is_verifier), settings: Settings = Depends(get_settings)) -> FileResponse:
    """A photo or voice note of a WhatsApp report marked public (or any, for a verifier)."""
    with db.session(settings.db_path) as conn:
        allowed = wa_reports.media_belongs(conn, incident_id, media_id) and (verifier or wa_reports.is_public(conn, incident_id))
    path = reports.media_path(settings.data_dir, media_id) if allowed else None
    if path is None:
        raise HTTPException(status_code=404, detail="media not found")
    ext = path.suffix.lstrip(".")
    mime = "image/jpeg" if ext == "jpg" else next((m for m, e in reports.AUDIO_TYPES.items() if e == ext), "application/octet-stream")
    return FileResponse(path, media_type=mime, headers={"Cache-Control": "private, no-cache"})


@app.post("/api/wa/webhook")
async def wa_webhook(request: Request, background: BackgroundTasks, settings: Settings = Depends(get_settings)) -> dict:
    """WhatsApp Cloud API events. The signature is checked on the raw body; work runs after the 200 so Meta does not resend."""
    if not whatsapp.configured(settings):
        raise HTTPException(status_code=503, detail="whatsapp is not configured")
    body = await request.body()
    if not whatsapp.verify_signature(settings.wa_app_secret, body, request.headers.get("x-hub-signature-256")):
        raise HTTPException(status_code=401, detail="bad signature")
    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(status_code=400, detail="body is not JSON")
    background.add_task(whatsapp.process_webhook, settings, payload, _enrich_after_report)
    return {"ok": True}


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


STALE_RUN_MINUTES = 15


def auto_triage(settings: Settings) -> list[str]:
    """Scheduled triage: only when the stored schedule says it is due, only citizen-reported incidents,
    within the per-run budget and the daily cap."""
    with db.session(settings.db_path) as conn:
        if not triage_control.due_auto_run(conn):
            return []
        ids = triage_control.auto_candidates(settings, conn)
        triage_control.mark_auto_run(conn)
        if ids:
            triage_control.record_batch(conn, ids, "schedule")
    for i in ids:
        _run_triage(settings, i)
    return ids


def _run_triage(settings: Settings, incident_id: str) -> None:
    try:
        intake.process(settings, incident_id)
        agent.triage(settings, incident_id)
    except Exception:
        log.exception("triage task failed for %s", incident_id)


def _run_batch(settings: Settings, ids: list[str]) -> None:
    for i in ids:
        with db.session(settings.db_path) as conn:
            if triage_control.usage_today(conn)["runs"] >= settings.triage_daily_cap:
                log.warning("daily triage cap reached; %s and later incidents in the batch were not run", i)
                return
        _run_triage(settings, i)


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _control(fn):
    try:
        return fn()
    except triage_control.ControlError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)


class CodeIn(BaseModel):
    code: str = Field(default="", max_length=64)


class BatchIn(CodeIn):
    incident_ids: list[str] = Field(default_factory=list, max_length=100)


class ModeIn(CodeIn):
    mode: str


def _require_model(settings: Settings) -> None:
    if not settings.model_api_key:
        raise HTTPException(status_code=503, detail="MODEL_API_KEY is not configured")


def _not_running(conn, incident_id: str) -> bool:
    running = conn.execute(
        "SELECT started_at FROM agent_runs WHERE incident_id=? AND outcome='running' ORDER BY id DESC LIMIT 1", (incident_id,)
    ).fetchone()
    return not (running and datetime.fromisoformat(running["started_at"]) > datetime.now(timezone.utc) - timedelta(minutes=STALE_RUN_MINUTES))


@app.post("/api/incidents/{incident_id}/triage", status_code=202, dependencies=[Depends(require_verifier)])
def start_triage(incident_id: str, body: CodeIn, request: Request, background: BackgroundTasks, settings: Settings = Depends(get_settings)) -> dict:
    """Triage one incident in the background. Needs the execution code; counts toward the daily cap."""
    _require_model(settings)
    _control(lambda: triage_control.check_code(settings, body.code, _client(request)))
    with db.session(settings.db_path) as conn:
        ids = _control(lambda: triage_control.validate_batch(settings, conn, [incident_id]))
        if not _not_running(conn, incident_id):
            raise HTTPException(status_code=409, detail="a triage run is already in progress for this incident")
        triage_control.record_batch(conn, ids, "single")
    background.add_task(_run_batch, settings, ids)
    return {"incident_id": incident_id, "status": "triaging"}


@app.post("/api/triage/batch", status_code=202, dependencies=[Depends(require_verifier)])
def start_batch(body: BatchIn, request: Request, background: BackgroundTasks, settings: Settings = Depends(get_settings)) -> dict:
    """Triage a deliberate selection (checked in the list or drawn on the map), at most triage_batch_limit."""
    _require_model(settings)
    _control(lambda: triage_control.check_code(settings, body.code, _client(request)))
    with db.session(settings.db_path) as conn:
        ids = _control(lambda: triage_control.validate_batch(settings, conn, body.incident_ids))
        skipped = [i for i in ids if not _not_running(conn, i)]
        ids = [i for i in ids if i not in skipped]
        if ids:
            triage_control.record_batch(conn, ids, "selection")
    if ids:
        background.add_task(_run_batch, settings, ids)
    return {"accepted": ids, "skipped_running": skipped, "est_cost_usd": round(len(ids) * triage_control.EST_COST_USD, 3)}


@app.get("/api/triage/control", dependencies=[Depends(require_verifier)])
def triage_status(settings: Settings = Depends(get_settings)) -> dict:
    with db.session(settings.db_path) as conn:
        return triage_control.status(settings, conn)


@app.put("/api/triage/control/mode", dependencies=[Depends(require_verifier)])
def triage_mode(body: ModeIn, request: Request, settings: Settings = Depends(get_settings)) -> dict:
    """Change the schedule: off, daily (default), or hourly. Needs the execution code."""
    _control(lambda: triage_control.check_code(settings, body.code, _client(request)))
    with db.session(settings.db_path) as conn:
        _control(lambda: triage_control.set_mode(conn, body.mode))
    with db.session(settings.db_path) as conn:
        return triage_control.status(settings, conn)


class ProposalIn(BaseModel):
    reason: str = Field(default="", max_length=300)


class ProposalDecisionIn(BaseModel):
    decision: Literal["approve", "reject", "cancel"]
    note: str | None = Field(default=None, max_length=300)


def _proposal(fn):
    try:
        return fn()
    except drone_missions.ProposalError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)


@app.get("/api/incidents/{incident_id}/drone-proposals", dependencies=[Depends(require_verifier)])
def list_proposals(incident_id: str, settings: Settings = Depends(get_settings)) -> dict:
    with db.session(settings.db_path) as conn:
        items = drone_missions.list_for(conn, incident_id)
    return {"count": len(items), "items": items}


@app.post("/api/incidents/{incident_id}/drone-proposals", status_code=201, dependencies=[Depends(require_verifier)])
def create_proposal(incident_id: str, body: ProposalIn, settings: Settings = Depends(get_settings)) -> dict:
    """Plan a survey without running the agent. Free: it is geometry, no model call."""
    with db.session(settings.db_path) as conn:
        p, created = _proposal(lambda: drone_missions.propose(conn, incident_id, source="verifier", reason=body.reason))
    return {**p, "created": created}


@app.post("/api/drone-proposals/{proposal_id}/decision", dependencies=[Depends(require_verifier)])
def decide_proposal(proposal_id: int, body: ProposalDecisionIn, settings: Settings = Depends(get_settings)) -> dict:
    who = hashlib.sha256(("dashboard:" + settings.verifier_token).encode()).hexdigest()[:16]
    with db.session(settings.db_path) as conn:
        return _proposal(lambda: drone_missions.decide(conn, proposal_id, body.decision, who, body.note))


class FlightIn(BaseModel):
    speed: int = 30


class FlightFinishIn(BaseModel):
    outcome: Literal["completed", "aborted"]
    note: str | None = Field(default=None, max_length=300)


def _flight(fn):
    try:
        return fn()
    except drone_flight.FlightError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)


@app.post("/api/drone-proposals/{proposal_id}/flight", status_code=201, dependencies=[Depends(require_verifier)])
def start_flight(proposal_id: int, body: FlightIn, settings: Settings = Depends(get_settings)) -> dict:
    """Simulated pilot: fly an approved plan. Telemetry is simulated from the plan and the incident's wind."""
    who = hashlib.sha256(("pilot:" + settings.verifier_token).encode()).hexdigest()[:16]
    with db.session(settings.db_path) as conn:
        return _flight(lambda: drone_flight.start(conn, proposal_id, body.speed, who))


@app.post("/api/drone-flights/{flight_id}/finish", dependencies=[Depends(require_verifier)])
def finish_flight(flight_id: int, body: FlightFinishIn, settings: Settings = Depends(get_settings)) -> dict:
    with db.session(settings.db_path) as conn:
        return _flight(lambda: drone_flight.finish(conn, flight_id, body.outcome, body.note))


def _plan_row(conn, proposal_id: int):
    row = conn.execute("SELECT incident_id, plan_json FROM drone_proposals WHERE id=?", (proposal_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="proposal not found")
    return row["incident_id"], json.loads(row["plan_json"])


@app.get("/api/drone-proposals/{proposal_id}/ground", dependencies=[Depends(require_verifier)])
def plan_ground(proposal_id: int, settings: Settings = Depends(get_settings)) -> dict:
    """What the 3D flight view lays under the plan: the incident's Sentinel-2 pair when it covers the plan, else OSM tiles."""
    with db.session(settings.db_path) as conn:
        incident_id, plan = _plan_row(conn, proposal_id)
        evidence = db.list_evidence(conn, incident_id)
    sat = ground.satellite_ground(evidence, plan["bbox"])
    if sat:
        base = f"/api/satellite/{incident_id}/"
        return {"kind": "sentinel2", "flat": True, "corners": sat["corners"], "image": base + sat["post"],
                "mask": base + sat["mask"] if sat["mask"] else None, "evidence_id": sat["evidence_id"], "burned_area_ha": sat["burned_area_ha"],
                "dates": sat["dates"], "attribution": sat["attribution"]}
    try:
        corners = ground.osm_extent(plan["bbox"])[4]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"kind": "osm", "flat": True, "corners": corners, "image": f"/api/drone-proposals/{proposal_id}/ground.jpg", "mask": None,
            "attribution": "© OpenStreetMap contributors"}


@app.get("/api/drone-proposals/{proposal_id}/ground.jpg", dependencies=[Depends(require_verifier)])
def plan_ground_image(proposal_id: int, settings: Settings = Depends(get_settings)) -> Response:
    with db.session(settings.db_path) as conn:
        _, plan = _plan_row(conn, proposal_id)
    path = settings.data_dir / "ground" / f"plan-{proposal_id}.jpg"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(ground.osm_mosaic(plan["bbox"], settings.cache_dir))
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@app.get("/api/auth/check", dependencies=[Depends(require_verifier)])
def auth_check() -> dict:
    """Lets the browser confirm a verifier token before storing it."""
    return {"ok": True}


@app.get("/api/admin/overview", dependencies=[Depends(require_verifier)])
def admin_overview(settings: Settings = Depends(get_settings)) -> dict:
    with db.session(settings.db_path) as conn:
        runs = [dict(r) for r in conn.execute(
            "SELECT incident_id, started_at, outcome, seconds, cost_usd FROM agent_runs WHERE incident_id NOT IN (SELECT id FROM incidents WHERE simulation=1) ORDER BY id DESC LIMIT 15")]
        counts = {
            "incidents": conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0],
            "hotspots": conn.execute("SELECT COUNT(*) FROM hotspots").fetchone()[0],
            "citizen_reports": conn.execute("SELECT COUNT(*) FROM evidence WHERE type='report'").fetchone()[0],
            "satellite_pairs": conn.execute("SELECT COUNT(*) FROM evidence WHERE type='satellite'").fetchone()[0],
            "triaged": conn.execute("SELECT COUNT(*) FROM incidents WHERE triage_json IS NOT NULL").fetchone()[0],
        }
        # Replayed runs carry the cost of their recording; they are not spend on this server.
        total = conn.execute("SELECT COUNT(*), COALESCE(SUM(cost_usd), 0) FROM agent_runs WHERE incident_id NOT IN (SELECT id FROM incidents WHERE simulation=1)").fetchone()
        return {
            "settings": admin_settings.describe(settings, conn),
            "schedule": admin_settings.schedule(settings, conn),
            "triage": triage_control.status(settings, conn),
            "counts": counts, "recent_runs": runs,
            "spend_total": {"runs": total[0], "cost_usd": round(total[1], 4)},
            "sources": source_status.snapshot(settings),
            "replay": {"loaded": replay._loaded(conn), "cases": replay.summaries(settings.replay_dir)},
            "whatsapp": whatsapp.status(settings),
            "build": {"git_sha": settings.git_sha, "fixture_mode": settings.fixture_mode, "scheduler_enabled": settings.scheduler_enabled,
                      "model_configured": bool(settings.model_api_key), "baseten_configured": bool(settings.baseten_api_key)},
        }


class SettingsIn(CodeIn):
    values: dict[str, int]


@app.put("/api/admin/settings", dependencies=[Depends(require_verifier)])
def admin_update(body: SettingsIn, request: Request, settings: Settings = Depends(get_settings)) -> dict:
    """Change operator settings. Needs the execution code, because several settings change spend."""
    _control(lambda: triage_control.check_code(settings, body.code, _client(request)))
    try:
        with db.session(settings.db_path) as conn:
            changed = admin_settings.update(settings, conn, body.values)
    except admin_settings.SettingError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    log.info("admin settings changed: %s", ", ".join(changed))
    return {"changed": changed, **admin_overview(settings)}


@app.get("/api/incidents/{incident_id}/runs", dependencies=[Depends(require_verifier)])
def list_runs(incident_id: str, settings: Settings = Depends(get_settings)) -> dict:
    """Agent run history with outcome, duration, metered cost, and tool calls."""
    with db.session(settings.db_path) as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM agent_runs WHERE incident_id=? ORDER BY id DESC LIMIT 20", (incident_id,))]
    for r in rows:
        r["detail"] = json.loads(r.pop("detail_json")) if r.get("detail_json") else None
    return {"count": len(rows), "items": rows}


@app.post("/api/incidents/{incident_id}/enrich", dependencies=[Depends(require_verifier)])
def enrich(incident_id: str, settings: Settings = Depends(get_settings)) -> dict:
    """Collect missing evidence for one incident now, regardless of the automatic priority thresholds."""
    try:
        status = evidence.enrich_incident(settings, incident_id, osm_tiles_left=[1])
    except KeyError:
        raise HTTPException(status_code=404, detail="incident not found")
    return {"incident_id": incident_id, "sources": status}


@app.post("/api/ingest/run", dependencies=[Depends(require_verifier)])
def run_ingest(settings: Settings = Depends(get_settings)) -> dict:
    return ingest.run_once(settings)


@app.get("/api/replay", dependencies=[Depends(require_verifier)])
def replay_status(settings: Settings = Depends(get_settings)) -> dict:
    return replay.status(settings)


@app.post("/api/replay/start", dependencies=[Depends(require_verifier)])
def replay_start(settings: Settings = Depends(get_settings)) -> dict:
    """Load the three recorded historical incidents as SIMULASI. Works offline; a second call changes nothing."""
    try:
        return replay.start(settings)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.delete("/api/replay", dependencies=[Depends(require_verifier)])
def replay_stop(settings: Settings = Depends(get_settings)) -> dict:
    return replay.stop(settings)


class RevalidatedStaticFiles(StaticFiles):
    """Without Cache-Control, browsers cache JS and CSS heuristically and can keep running an old build
    after a deploy. no-cache forces a revalidation each time; an unchanged file costs a 304."""

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


settings_for_static = get_settings()
if settings_for_static.frontend_dir.exists():
    app.mount("/", RevalidatedStaticFiles(directory=settings_for_static.frontend_dir, html=True), name="frontend")

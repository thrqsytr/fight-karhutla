"""WhatsApp Cloud API: citizen reports in, verifier and reporter notifications out.

A resident sends a photo, a voice note, text, and a location pin in any order. Once the conversation holds a
location and at least one item, the items go through reports.submit, the same path as the web form (EXIF removed,
joined to an incident within 2 km and 24 h, then transcription, photo reading, and enrichment). Later items in the
same conversation join that incident.

The phone number is needed to reply, so it is kept only in the conversation state (wa_sessions, cleared after
SESSION_TTL) and in wa_reporters (to tell the reporter the verifier's decision, cleared with the citizen data or
after REPORTER_TTL). Evidence carries a salted hash of the number, never the number.

Every outgoing message is recorded in wa_outbox with its result. Outside the 24 hour customer service window
WhatsApp refuses free-form text (error 131047); that is recorded as failed with the reason, not retried.
"""

import hashlib
import hmac
import json
import logging
import threading
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from . import db, reports
from .config import Settings
from .geo import in_indonesia

log = logging.getLogger(__name__)

GRAPH = "https://graph.facebook.com/v26.0"
TIMEOUT_S = 20.0
SESSION_TTL = timedelta(hours=2)
REPORTER_TTL = timedelta(days=7)
MAX_MEDIA_BYTES = reports.MAX_PHOTO_BYTES
MESSAGE_LIMIT = 30  # per sender per hour; a report is several messages (photos, voice, pin)
MESSAGE_WINDOW_S = 3600
_recent: dict[str, deque] = defaultdict(deque)
# One conversation step at a time: a photo and a pin sent together arrive as two webhook calls.
_lock = threading.Lock()

TEXT = {
    "help": ("Fight Karhutla menerima laporan kebakaran hutan dan lahan. Kirim foto, pesan suara, atau keterangan, "
             "lalu bagikan lokasi kejadian (ikon lampiran, Lokasi). Ini bukan kanal darurat: bila ada bahaya, hubungi 112."),
    "need_location": "Terima kasih, sudah kami terima. Sekarang bagikan lokasi kejadian: ikon lampiran, lalu Lokasi.",
    "need_content": "Lokasi diterima. Kirim foto, pesan suara, atau keterangan tentang kejadiannya.",
    "outside": "Lokasi itu berada di luar wilayah Indonesia. Bagikan lokasi kejadian di Indonesia.",
    "unsupported": "Jenis pesan ini belum bisa kami proses. Kirim foto, pesan suara, teks, atau lokasi.",
    "media_failed": "Lampiran tidak dapat kami unduh dari WhatsApp. Coba kirim sekali lagi.",
    "rate": "Terlalu banyak pesan dalam satu jam. Coba lagi nanti.",
    "received": ("Laporan diterima sebagai {inc}{joined}. Seorang verifikator akan memeriksanya, dan kami kabari di sini. "
                 "Kirim foto atau keterangan lain untuk menambah laporan ini. Ini bukan kanal darurat: bila ada bahaya, hubungi 112."),
    "joined": ", digabung dengan insiden yang sudah ada di sekitar lokasi itu",
    "added": "Tambahan diterima dan dimasukkan ke {inc}.",
    "verdict": {
        "approve": "Laporan {inc} sudah diperiksa verifikator dan dinyatakan perlu ditindaklanjuti. Terima kasih atas laporan Anda.",
        "reject": "Laporan {inc} sudah diperiksa verifikator dan tidak ditindaklanjuti saat ini. Terima kasih atas laporan Anda.",
    },
    "verifier": "Laporan warga baru lewat {channel}: {inc}, {what}. Buka dengan mode verifikator: {url}",
}
HELP_WORDS = {"halo", "hai", "hi", "hello", "help", "bantuan", "info", "mulai", "start", "menu"}


class GraphError(RuntimeError):
    """WhatsApp Cloud API refused or failed. The message is recorded as the reason."""


def configured(settings: Settings) -> bool:
    return bool(settings.wa_access_token and settings.wa_phone_number_id and settings.wa_app_secret)


def verify_signature(app_secret: str, body: bytes, header: str | None) -> bool:
    """X-Hub-Signature-256 is sha256=<hex HMAC of the raw body with the app secret>."""
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[7:])


def reporter_hash(settings: Settings, wa_id: str) -> str:
    return hashlib.sha256(f"wa:{settings.wa_app_secret}:{wa_id}".encode()).hexdigest()[:16]


@dataclass
class Inbound:
    msg_id: str
    wa_id: str
    kind: str  # text, image, audio, location, other
    text: str = ""
    media_id: str = ""
    mime: str = ""
    lat: float | None = None
    lon: float | None = None


def parse(payload: dict) -> list[Inbound]:
    """Messages from a webhook body. Status updates (sent, delivered, read) carry no message and are skipped."""
    out = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            for m in value.get("messages") or []:
                kind = m.get("type", "other")
                base = {"msg_id": m.get("id", ""), "wa_id": m.get("from", "")}
                if kind == "text":
                    out.append(Inbound(**base, kind="text", text=(m.get("text") or {}).get("body", "")))
                elif kind in ("image", "audio"):
                    media = m.get(kind) or {}
                    out.append(Inbound(**base, kind=kind, media_id=media.get("id", ""), mime=media.get("mime_type", ""),
                                       text=media.get("caption", "")))
                elif kind == "location":
                    loc = m.get("location") or {}
                    out.append(Inbound(**base, kind="location", lat=loc.get("latitude"), lon=loc.get("longitude")))
                else:
                    out.append(Inbound(**base, kind="other"))
    return [m for m in out if m.msg_id and m.wa_id]


# Graph API. Network calls happen outside any database session (PITFALLS 8).

def _graph(settings: Settings, method: str, path: str, **kwargs) -> httpx.Response:
    headers = {"Authorization": f"Bearer {settings.wa_access_token}"}
    url = path if path.startswith("https://") else f"{GRAPH}/{path}"
    try:
        return httpx.request(method, url, headers=headers, timeout=TIMEOUT_S, **kwargs)
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise GraphError(f"network: {exc!r}"[:200]) from exc


def _error(resp: httpx.Response) -> str:
    try:
        e = resp.json().get("error") or {}
        return f"HTTP {resp.status_code} code {e.get('code')}: {e.get('message', '')}"[:200]
    except ValueError:
        return f"HTTP {resp.status_code}: {resp.text[:150]}"


def download_media(settings: Settings, media_id: str) -> tuple[bytes, str]:
    meta = _graph(settings, "GET", media_id)
    if meta.status_code != 200:
        raise GraphError(_error(meta))
    info = meta.json()
    if int(info.get("file_size") or 0) > MAX_MEDIA_BYTES:
        raise GraphError("media is larger than 12 MB")
    blob = _graph(settings, "GET", info["url"])
    if blob.status_code != 200:
        raise GraphError(_error(blob))
    return blob.content, (info.get("mime_type") or "").split(";")[0]


def send_text(settings: Settings, to: str, body: str, purpose: str, incident_id: str | None = None) -> bool:
    """Send one text and record the outcome in wa_outbox. Returns whether WhatsApp accepted it."""
    status, detail = "sent", None
    try:
        resp = _graph(settings, "POST", f"{settings.wa_phone_number_id}/messages",
                      json={"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": body}})
        if resp.status_code == 200:
            detail = ((resp.json().get("messages") or [{}])[0]).get("id")
        else:
            status, detail = "failed", _error(resp)
    except GraphError as exc:
        status, detail = "failed", str(exc)
    if status == "failed":
        log.warning("whatsapp %s to %s failed: %s", purpose, reporter_hash(settings, to), detail)
    with db.session(settings.db_path) as conn:
        conn.execute("INSERT INTO wa_outbox (created_at, to_hash, purpose, incident_id, status, detail) VALUES (?,?,?,?,?,?)",
                     (db.utcnow(), reporter_hash(settings, to), purpose, incident_id, status, detail))
    return status == "sent"


# Conversation state

def _pending_dir(settings: Settings) -> Path:
    d = settings.data_dir / "wa_pending"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load(conn, wa_id: str, now: datetime) -> dict:
    row = conn.execute("SELECT * FROM wa_sessions WHERE wa_id=?", (wa_id,)).fetchone()
    fresh = {"wa_id": wa_id, "lat": None, "lon": None, "incident_id": None, "pending": [], "asked": False}
    if row is None or datetime.fromisoformat(row["updated_at"]) < now - SESSION_TTL:
        return fresh
    state = json.loads(row["state_json"])
    return {**fresh, **state, "wa_id": wa_id}


def _save(conn, s: dict, now: datetime) -> None:
    state = {k: s[k] for k in ("lat", "lon", "incident_id", "pending", "asked")}
    conn.execute("INSERT INTO wa_sessions (wa_id, state_json, updated_at) VALUES (?,?,?) "
                 "ON CONFLICT(wa_id) DO UPDATE SET state_json=excluded.state_json, updated_at=excluded.updated_at",
                 (s["wa_id"], json.dumps(state), now.isoformat(timespec="seconds")))


def _drop_files(items: list[dict]) -> None:
    for it in items:
        if it.get("path"):
            Path(it["path"]).unlink(missing_ok=True)


def cleanup(settings: Settings, now: datetime | None = None) -> None:
    """Forget expired conversations (and their unsent media) and old reporter links."""
    now = now or datetime.now(timezone.utc)
    with db.session(settings.db_path) as conn:
        old = conn.execute("SELECT wa_id, state_json FROM wa_sessions WHERE updated_at < ?",
                           ((now - SESSION_TTL).isoformat(timespec="seconds"),)).fetchall()
        for r in old:
            _drop_files(json.loads(r["state_json"]).get("pending") or [])
        conn.execute("DELETE FROM wa_sessions WHERE updated_at < ?", ((now - SESSION_TTL).isoformat(timespec="seconds"),))
        conn.execute("DELETE FROM wa_reporters WHERE created_at < ?", ((now - REPORTER_TTL).isoformat(timespec="seconds"),))


def _rate_limited(wa_id: str, now: float) -> bool:
    q = _recent[wa_id]
    while q and now - q[0] > MESSAGE_WINDOW_S:
        q.popleft()
    if len(q) >= MESSAGE_LIMIT:
        return True
    q.append(now)
    return False


def _what(photos: int, voices: int, text: bool) -> str:
    parts = ([f"{photos} foto"] if photos else []) + ([f"{voices} pesan suara"] if voices else []) + (["keterangan"] if text else [])
    return ", ".join(parts) or "laporan"


def _submit(settings: Settings, s: dict, now: datetime) -> tuple[dict, list[dict]]:
    """Turn the pending items into evidence. Photos go in groups of 3 and voice notes one per call, as on the web form."""
    items = s["pending"]
    photos = [it for it in items if it["kind"] == "image"]
    voices = [it for it in items if it["kind"] == "audio"]
    text = "\n".join(it["text"] for it in items if it["kind"] == "text")[: reports.MAX_TEXT]
    groups = [photos[i:i + reports.MAX_PHOTOS] for i in range(0, len(photos), reports.MAX_PHOTOS)] or [[]]
    calls = [{"photos": g, "audio": None, "text": text if i == 0 else ""} for i, g in enumerate(groups)]
    if voices:
        calls[0]["audio"] = voices[0]
        calls += [{"photos": [], "audio": v, "text": ""} for v in voices[1:]]
    calls = [c for c in calls if c["photos"] or c["audio"] or c["text"]]
    result, rejected = None, []
    for k, c in enumerate(calls):
        try:
            with db.session(settings.db_path) as conn:
                r = reports.submit(
                    conn, settings.data_dir, lat=s["lat"], lon=s["lon"], accuracy_m=None, location_source="whatsapp",
                    text=c["text"], photos=[reports.Photo(Path(p["path"]).read_bytes(), "wa.jpg") for p in c["photos"]],
                    audio=reports.Audio(Path(c["audio"]["path"]).read_bytes(), c["audio"]["mime"]) if c["audio"] else None,
                    now=now, channel="whatsapp", reporter=reporter_hash(settings, s["wa_id"]),
                    new_reporter=s["incident_id"] is None and k == 0,
                )
        except reports.ReportError as exc:
            rejected.append({"reason": str(exc)})
            continue
        result = result or r
        s["incident_id"] = s["incident_id"] or r["incident_id"]
    _drop_files(items)
    s["pending"] = []
    return ({**result, "what": _what(len(photos), len(voices), bool(text))} if result else None), rejected


def handle(settings: Settings, m: Inbound, now: datetime | None = None) -> list[tuple[str, str]]:
    """One inbound message. Returns the replies to send (purpose, text); notifications are sent by the caller."""
    now = now or datetime.now(timezone.utc)
    with db.session(settings.db_path) as conn:
        cur = conn.execute("INSERT OR IGNORE INTO wa_messages (msg_id, received_at) VALUES (?,?)", (m.msg_id, db.utcnow()))
        if cur.rowcount == 0:
            return []  # WhatsApp delivers a webhook again when it is not answered fast enough
    if _rate_limited(m.wa_id, time.time()):
        return [("reply", TEXT["rate"])]
    if m.kind == "text" and m.text.strip().lower().strip("!.?") in HELP_WORDS:
        return [("reply", TEXT["help"])]
    if m.kind == "other":
        return [("reply", TEXT["unsupported"])]

    item = None
    if m.kind in ("image", "audio"):
        try:
            data, mime = download_media(settings, m.media_id)
        except GraphError as exc:
            log.warning("whatsapp media %s failed: %s", m.msg_id, exc)
            return [("reply", TEXT["media_failed"])]
        if m.kind == "audio" and mime not in reports.AUDIO_TYPES:
            return [("reply", TEXT["unsupported"])]
        path = _pending_dir(settings) / f"{uuid.uuid4().hex}.bin"
        path.write_bytes(data)
        item = {"kind": m.kind, "path": str(path), "mime": mime}
    elif m.kind == "text" and m.text.strip():
        item = {"kind": "text", "text": m.text.strip()}

    replies = []
    with db.session(settings.db_path) as conn:
        s = _load(conn, m.wa_id, now)
    if m.kind == "location":
        if m.lat is None or m.lon is None or not in_indonesia(m.lat, m.lon):
            return [("reply", TEXT["outside"])]
        if s["incident_id"] and (s["lat"], s["lon"]) != (m.lat, m.lon):
            s["incident_id"] = None  # a new pin starts a new report
        s["lat"], s["lon"] = m.lat, m.lon
    if item:
        s["pending"].append(item)
        if m.kind in ("image", "audio") and m.text.strip():
            s["pending"].append({"kind": "text", "text": m.text.strip()})  # the caption

    notify = None
    if s["lat"] is not None and s["pending"]:
        first = s["incident_id"] is None
        result, rejected = _submit(settings, s, now)
        if result and first:
            replies.append(("reply", TEXT["received"].format(inc=result["incident_id"], joined=TEXT["joined"] if result["joined_existing"] else "")))
            notify = result
            with db.session(settings.db_path) as conn:
                conn.execute("INSERT INTO wa_reporters (incident_id, wa_id, created_at) VALUES (?,?,?)",
                             (result["incident_id"], m.wa_id, now.isoformat(timespec="seconds")))
        elif result:
            replies.append(("reply", TEXT["added"].format(inc=s["incident_id"])))
            replies.append(("after", s["incident_id"]))
        for r in rejected:
            replies.append(("reply", r["reason"]))
    elif s["lat"] is None and s["pending"] and not s["asked"]:
        replies.append(("reply", TEXT["need_location"]))
        s["asked"] = True
    elif m.kind == "location" and not s["pending"] and s["incident_id"] is None:
        replies.append(("reply", TEXT["need_content"]))
    with db.session(settings.db_path) as conn:
        _save(conn, s, now)
    if notify:
        replies.append(("notify", json.dumps(notify)))
    return replies


def notify_verifiers(settings: Settings, incident_id: str, what: str, channel: str) -> int:
    """Tell every verifier number about a new citizen report. Returns how many WhatsApp accepted."""
    if not configured(settings):
        return 0
    url = f"{settings.public_url}/?incident={incident_id}"
    body = TEXT["verifier"].format(channel=channel, inc=incident_id, what=what, url=url)
    numbers = [n.strip().lstrip("+") for n in settings.wa_verifier_numbers.split(",") if n.strip()]
    return sum(send_text(settings, n, body, "verifier", incident_id) for n in numbers)


def notify_reporters(settings: Settings, incident_id: str, decision: str) -> int:
    """Tell WhatsApp reporters of an incident the verifier's decision (approve or reject)."""
    if not configured(settings) or decision not in TEXT["verdict"]:
        return 0
    with db.session(settings.db_path) as conn:
        rows = conn.execute("SELECT DISTINCT wa_id FROM wa_reporters WHERE incident_id=?", (incident_id,)).fetchall()
    body = TEXT["verdict"][decision].format(inc=incident_id)
    return sum(send_text(settings, r["wa_id"], body, "verdict", incident_id) for r in rows)


def record_statuses(settings: Settings, payload: dict) -> int:
    """Delivery results for messages we sent: delivered, read, or failed with WhatsApp's error code and reason."""
    rows = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            for st in (change.get("value") or {}).get("statuses") or []:
                errs = st.get("errors") or []
                why = "; ".join(f"code {e.get('code')}: {e.get('title') or e.get('message', '')}"
                                + (f" ({(e.get('error_data') or {}).get('details')})" if (e.get("error_data") or {}).get("details") else "")
                                for e in errs)
                rows.append(((f"{st.get('status')}: {why}" if why else str(st.get("status")))[:300], st.get("id", "")))
    if rows:
        with db.session(settings.db_path) as conn:
            conn.executemany("UPDATE wa_outbox SET delivery=? WHERE detail=?", rows)
        for delivery, _ in rows:
            if delivery.startswith("failed"):
                log.warning("whatsapp delivery %s", delivery)
    return len(rows)


def process_webhook(settings: Settings, payload: dict, after_report=None) -> None:
    """Background work for one webhook call: each message in turn, then its replies and notifications."""
    try:
        record_statuses(settings, payload)
    except Exception:
        log.exception("whatsapp status update failed")
    for m in parse(payload):
        try:
            with _lock:
                out = handle(settings, m)
        except Exception:
            log.exception("whatsapp message %s failed", m.msg_id)
            out = []
        for purpose, body in out:
            if purpose == "reply":
                send_text(settings, m.wa_id, body, "reply")
            elif purpose == "after":
                if after_report:
                    after_report(settings, body)
            else:
                r = json.loads(body)
                notify_verifiers(settings, r["incident_id"], r["what"], "WhatsApp")
                if after_report:  # the same media processing and enrichment as the web form
                    after_report(settings, r["incident_id"])
    try:
        cleanup(settings)
    except Exception:
        log.exception("whatsapp cleanup failed")


def status(settings: Settings) -> dict:
    """For the admin panel: configuration and the last outgoing messages, numbers hashed."""
    with db.session(settings.db_path) as conn:
        last_in = conn.execute("SELECT MAX(received_at) FROM wa_messages").fetchone()[0]
        out = [dict(r) for r in conn.execute(
            "SELECT created_at, to_hash, purpose, incident_id, status, delivery, detail FROM wa_outbox ORDER BY id DESC LIMIT 10")]
    return {"configured": configured(settings), "last_inbound": last_in, "recent_outbound": out,
            "verifier_numbers": len([n for n in settings.wa_verifier_numbers.split(",") if n.strip()])}

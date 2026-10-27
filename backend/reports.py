"""Citizen report intake from the web form (and later WhatsApp).

Photos are re-encoded without metadata, so EXIF (including GPS and device data) never reaches storage.
A report within 2 km and 24 h of an existing incident joins it; otherwise it opens a new incident.
"""

import hashlib
import shutil
import io
import json
import re
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from . import db
from .evidence import image_payload
from .geo import haversine_m, in_indonesia

MAX_PHOTOS = 3
MAX_PHOTO_BYTES = 12 * 1024 * 1024
MAX_AUDIO_BYTES = 6 * 1024 * 1024
MAX_TEXT = 1000
MAX_EDGE_PX = 2048
JOIN_RADIUS_M = 2000
JOIN_WINDOW = timedelta(hours=24)
AUDIO_TYPES = {
    "audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "m4a", "audio/x-m4a": "m4a",
    "audio/mpeg": "mp3", "audio/wav": "wav", "audio/x-wav": "wav", "audio/aac": "aac",
}
MEDIA_ID = re.compile(r"^MED-[0-9a-f]{16}\.(jpg|webm|ogg|m4a|mp3|wav|aac)$")

# Simple per-client limit for a public form. In memory: it resets on restart, which is acceptable for a demo.
RATE_LIMIT = 6
RATE_WINDOW_S = 3600
_recent: dict[str, deque] = defaultdict(deque)


class ReportError(ValueError):
    """The report is malformed. The message is safe to show to the reporter."""


@dataclass
class Photo:
    data: bytes
    filename: str


@dataclass
class Audio:
    data: bytes
    content_type: str


def rate_limited(client_key: str, now: float | None = None) -> bool:
    now = now or time.time()
    key = hashlib.sha256(client_key.encode()).hexdigest()[:16]  # the raw IP is never kept
    q = _recent[key]
    while q and now - q[0] > RATE_WINDOW_S:
        q.popleft()
    if len(q) >= RATE_LIMIT:
        return True
    q.append(now)
    return False


def clean_photo(raw: bytes) -> tuple[bytes, int, int]:
    """Decode, apply orientation, drop every metadata block, downscale, and re-encode as JPEG."""
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ReportError("Foto tidak dapat dibaca. Gunakan JPG, PNG, atau WEBP.") from exc
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((MAX_EDGE_PX, MAX_EDGE_PX))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=85, optimize=True)  # no exif= argument: nothing is copied over
    return out.getvalue(), img.width, img.height


def _media_dir(data_dir: Path) -> Path:
    d = data_dir / "media"
    d.mkdir(parents=True, exist_ok=True)
    return d


def media_path(data_dir: Path, media_id: str) -> Path | None:
    if not MEDIA_ID.match(media_id):
        return None
    p = _media_dir(data_dir) / media_id
    return p if p.exists() else None


def _store(data_dir: Path, data: bytes, ext: str) -> tuple[str, str]:
    media_id = f"MED-{uuid.uuid4().hex[:16]}.{ext}"
    (_media_dir(data_dir) / media_id).write_bytes(data)
    return media_id, hashlib.sha256(data).hexdigest()


def _find_incident_to_join(conn, lat: float, lon: float, now: datetime) -> str | None:
    since = (now - JOIN_WINDOW).isoformat()
    rows = conn.execute("SELECT id, lat, lon FROM incidents WHERE last_seen >= ?", (since,)).fetchall()
    near = [(haversine_m(lat, lon, r["lat"], r["lon"]), r["id"]) for r in rows]
    near = [n for n in near if n[0] <= JOIN_RADIUS_M]
    return min(near)[1] if near else None


CITIZEN_TYPES = ("report", "image", "voice")


def delete_citizen_data(conn, data_dir: Path, incident_id: str) -> dict:
    """Remove a report's text, photos, and voice notes, including the files.

    An incident that exists only because of citizen reports is removed entirely. An incident with
    satellite hotspots keeps its hotspot, weather, and OSM evidence and goes back to hotspot_cluster.
    """
    inc = conn.execute("SELECT id, trigger, hotspot_count FROM incidents WHERE id=?", (incident_id,)).fetchone()
    if inc is None:
        raise KeyError(incident_id)
    rows = conn.execute(
        f"SELECT id, payload_json FROM evidence WHERE incident_id=? AND type IN ({','.join('?' * len(CITIZEN_TYPES))})",
        (incident_id, *CITIZEN_TYPES),
    ).fetchall()
    files = 0
    for r in rows:
        media_id = json.loads(r["payload_json"]).get("media_id")
        p = media_path(data_dir, media_id) if media_id else None
        if p is not None:
            p.unlink()
            files += 1
    conn.execute(f"DELETE FROM evidence WHERE incident_id=? AND type IN ({','.join('?' * len(CITIZEN_TYPES))})", (incident_id, *CITIZEN_TYPES))
    removed_incident = inc["hotspot_count"] == 0
    if removed_incident:
        conn.execute("DELETE FROM evidence WHERE incident_id=?", (incident_id,))
        conn.execute("DELETE FROM verdicts WHERE incident_id=?", (incident_id,))
        conn.execute("DELETE FROM incidents WHERE id=?", (incident_id,))
        shutil.rmtree(data_dir / "satellite" / incident_id, ignore_errors=True)
    else:
        conn.execute("UPDATE incidents SET trigger='hotspot_cluster', reporter_count=0, updated_at=? WHERE id=?", (db.utcnow(), incident_id))
    return {"incident_id": incident_id, "evidence_removed": len(rows), "files_removed": files, "incident_removed": removed_incident}


def submit(
    conn,
    data_dir: Path,
    *,
    lat: float,
    lon: float,
    accuracy_m: float | None,
    location_source: str,
    text: str | None,
    photos: list[Photo],
    audio: Audio | None,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(timezone.utc)
    text = (text or "").strip()
    if not in_indonesia(lat, lon):
        raise ReportError("Lokasi berada di luar wilayah Indonesia.")
    if location_source not in {"gps", "map_pick"}:
        raise ReportError("Sumber lokasi tidak dikenal.")
    if accuracy_m is not None and not 0 <= accuracy_m <= 50_000:
        raise ReportError("Nilai akurasi lokasi tidak valid.")
    if len(photos) > MAX_PHOTOS:
        raise ReportError(f"Maksimal {MAX_PHOTOS} foto.")
    if len(text) > MAX_TEXT:
        raise ReportError(f"Keterangan maksimal {MAX_TEXT} karakter.")
    if not photos and audio is None and not text:
        raise ReportError("Kirim minimal satu foto, pesan suara, atau keterangan.")
    for p in photos:
        if len(p.data) > MAX_PHOTO_BYTES:
            raise ReportError("Ukuran foto maksimal 12 MB.")
    if audio is not None:
        if audio.content_type.split(";")[0] not in AUDIO_TYPES:
            raise ReportError("Format pesan suara tidak didukung.")
        if len(audio.data) > MAX_AUDIO_BYTES:
            raise ReportError("Pesan suara maksimal 6 MB.")

    # Decode every photo before writing anything, so a bad file leaves no partial report behind.
    cleaned = [clean_photo(p.data) for p in photos]

    ts = now.isoformat(timespec="seconds")
    joined = _find_incident_to_join(conn, lat, lon, now)
    if joined:
        inc_id = joined
        conn.execute(
            """UPDATE incidents SET trigger = CASE WHEN trigger = 'hotspot_cluster' THEN 'merged' ELSE trigger END,
               last_seen = MAX(last_seen, ?), reporter_count = reporter_count + 1, updated_at = ? WHERE id = ?""",
            (ts, db.utcnow(), inc_id),
        )
    else:
        inc_id = db.next_incident_id(conn, now)
        conn.execute(
            """INSERT INTO incidents (id, created_at, updated_at, trigger, lat, lon, first_seen, last_seen, hotspot_count, reporter_count)
               VALUES (?,?,?,?,?,?,?,?,0,1)""",
            (inc_id, db.utcnow(), db.utcnow(), "citizen_report", lat, lon, ts, ts),
        )

    loc = {"lat": lat, "lon": lon, "accuracy_m": accuracy_m, "location_source": location_source}
    ev_ids = []
    if text:
        ev_ids.append(db.add_evidence(conn, inc_id, "report", "Citizen report (web)", f"Citizen description: {text[:160]}",
                                      {**loc, "text": text, "submitted_at": ts, "channel": "web"}))
    for i, (data, w, h) in enumerate(cleaned, start=1):
        media_id, sha = _store(data_dir, data, "jpg")
        payload = image_payload(media_id=media_id, sha256=sha, width=w, height=h, lat=lat, lon=lon, captured_at=ts,
                                capture_source="citizen", location_source=location_source)
        ev_ids.append(db.add_evidence(conn, inc_id, "image", "Citizen photo (web)", f"Citizen photo {i} of {len(cleaned)}, {w}x{h}, metadata removed", payload))
    if audio is not None:
        mime = audio.content_type.split(";")[0]
        media_id, sha = _store(data_dir, audio.data, AUDIO_TYPES[mime])
        ev_ids.append(db.add_evidence(conn, inc_id, "voice", "Citizen voice note (web)", "Citizen voice note, transcription pending",
                                      {**loc, "media_id": media_id, "sha256": sha, "mime": mime, "bytes": len(audio.data),
                                       "transcript": None, "asr_status": "pending", "asr_provider": None}))
    return {"incident_id": inc_id, "joined_existing": bool(joined), "evidence_ids": ev_ids, "needs_human": True}

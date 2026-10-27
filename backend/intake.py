"""Citizen media processing at intake, before any triage: voice notes are transcribed, photos are read.

Both run on Meta Model API in Standard mode, so citizen content is never used for training.
A failure is written into the evidence (asr_status / reading_status) with its reason, never dropped.
"""

import json
import logging

from . import db, meta_client, reports
from .config import Settings

log = logging.getLogger(__name__)

PHOTO_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["smoke_visible", "flames_visible", "burned_ground_visible", "description", "segment_concepts", "image_quality_issues"],
    "properties": {
        "smoke_visible": {"type": "boolean"}, "flames_visible": {"type": "boolean"}, "burned_ground_visible": {"type": "boolean"},
        "description": {"type": "string"},
        "segment_concepts": {"type": "array", "items": {"type": "string"}},
        "image_quality_issues": {"type": "array", "items": {"type": "string"}},
    },
}
PHOTO_PROMPT = (
    "A resident sent this photo as a possible forest or land fire report in Indonesia. Describe only what is visible, in one or two sentences. "
    "Say whether smoke, flames, and burned ground are visible. List up to three short noun phrases worth segmenting, such as 'burned ground' or "
    "'charred tree stump'. Note quality problems such as darkness, blur, or a screenshot. Do not guess causes or name anyone. Do not use dashes."
)


def _update(settings: Settings, incident_id: str, ev_id: str, payload: dict, summary: str) -> None:
    with db.session(settings.db_path) as conn:
        conn.execute("UPDATE evidence SET payload_json=?, summary=? WHERE id=?", (json.dumps(payload, ensure_ascii=False), summary, f"{incident_id}/{ev_id}"))


def _pending(settings: Settings, incident_id: str) -> list[dict]:
    with db.session(settings.db_path) as conn:
        return [e for e in db.list_evidence(conn, incident_id)
                if (e["type"] == "voice" and e["payload"].get("asr_status") == "pending")
                or (e["type"] == "image" and e["payload"].get("capture_source") == "citizen" and not e["payload"].get("concept") and "reading_status" not in e["payload"])]


def transcribe_voice(settings: Settings, incident_id: str, e: dict, meter: meta_client.Meter) -> None:
    p = dict(e["payload"])
    path = reports.media_path(settings.data_dir, p["media_id"])
    try:
        if path is None:
            raise meta_client.ModelUnavailable("voice file is missing")
        r = meta_client.transcribe(settings, meta_client.to_wav_16k(path.read_bytes()), meter)
        p.update({"transcript": r["transcript"], "asr_status": "ok", "asr_provider": meta_client.ASR, "audio_ms": r["audio_ms"], "asr_session": r["session_id"]})
        summary = f"Citizen voice note transcript: {r['transcript'][:160]}"
    except Exception as exc:
        log.warning("transcription failed for %s %s: %r", incident_id, e["id"], exc)
        p.update({"asr_status": f"failed: {str(exc)[:200]}", "asr_provider": meta_client.ASR})
        summary = "Citizen voice note, transcription failed (see asr_status)"
    _update(settings, incident_id, e["id"], p, summary)


def read_photo(settings: Settings, incident_id: str, e: dict, meter: meta_client.Meter) -> None:
    p = dict(e["payload"])
    path = reports.media_path(settings.data_dir, p["media_id"])
    try:
        if path is None:
            raise meta_client.ModelUnavailable("photo file is missing")
        resp = meta_client.meta(settings).responses.create(
            model=meta_client.SPARK, reasoning={"effort": "low"},
            input=[{"role": "user", "content": [{"type": "input_text", "text": PHOTO_PROMPT},
                                                {"type": "input_image", "image_url": meta_client.image_data_url(path.read_bytes())}]}],
            text={"format": {"type": "json_schema", "name": "photo_reading", "schema": PHOTO_SCHEMA, "strict": True}},
        )
        meter.spark(resp.usage)
        reading = json.loads(resp.output_text)
        p.update({"reading": reading, "reading_status": "ok", "reading_model": meta_client.SPARK})
        seen = [k for k, v in (("smoke", reading["smoke_visible"]), ("flames", reading["flames_visible"]), ("burned ground", reading["burned_ground_visible"])) if v]
        summary = f"Citizen photo {p['width']}x{p['height']}, metadata removed. Visible: {', '.join(seen) or 'no smoke, flames, or burned ground'}. {reading['description'][:140]}"
    except Exception as exc:
        log.warning("photo reading failed for %s %s: %r", incident_id, e["id"], exc)
        p.update({"reading_status": f"failed: {str(exc)[:200]}"})
        summary = e["summary"] + " Photo reading failed (see reading_status)."
    _update(settings, incident_id, e["id"], p, summary)


def process(settings: Settings, incident_id: str) -> dict:
    """Process every pending voice note and unread photo of one incident. Model calls run outside transactions."""
    if not settings.model_api_key:
        # Leave media pending rather than marking it failed, so it is processed once a key is configured.
        return {"processed": [], "skipped": "MODEL_API_KEY is not set"}
    meter = meta_client.Meter()
    done = []
    for e in _pending(settings, incident_id):
        (transcribe_voice if e["type"] == "voice" else read_photo)(settings, incident_id, e, meter)
        done.append(e["id"])
    return {"processed": done, "usage": meter.summary()}

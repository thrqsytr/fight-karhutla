"""WhatsApp report log: what each resident sent, what the Meta models made of it, how it was processed, and
what the bot answered. Verifiers see every WhatsApp report; the public sees only reports a verifier marked
as a public example (wa_showcase). The reporter appears only as the first characters of the salted hash."""

from datetime import datetime, timedelta

from . import db
from .whatsapp import TEXT

CITIZEN = ("report", "image", "voice")
PUBLIC_COORD_DECIMALS = 3  # about 100 m, as on the public map
REPLY_WINDOW = timedelta(hours=2)  # the conversation lifetime (whatsapp.SESSION_TTL)


def incident_ids(conn, *, public: bool) -> list[str]:
    rows = conn.execute(
        f"""SELECT e.incident_id, MIN(e.retrieved_at) AS first FROM evidence e
            {"JOIN wa_showcase s ON s.incident_id = e.incident_id" if public else ""}
            WHERE e.type IN ({",".join("?" * len(CITIZEN))}) AND json_extract(e.payload_json, '$.channel') = 'whatsapp'
            GROUP BY e.incident_id ORDER BY first DESC""",
        CITIZEN,
    ).fetchall()
    return [r["incident_id"] for r in rows]


def is_public(conn, incident_id: str) -> bool:
    return conn.execute("SELECT 1 FROM wa_showcase WHERE incident_id=?", (incident_id,)).fetchone() is not None


def media_belongs(conn, incident_id: str, media_id: str) -> bool:
    for e in db.list_evidence(conn, incident_id):
        if e["type"] in ("image", "voice") and e["payload"].get("media_id") == media_id and e["payload"].get("channel") == "whatsapp":
            return True
    return False


def _sent(row) -> dict:
    d = dict(row)
    if d.get("body") is None and d["purpose"] == "reply":
        d["body_note"] = "not recorded"  # sent before message bodies were stored
    return {k: d.get(k) for k in ("created_at", "purpose", "body", "body_note", "status", "delivery")}


def build(conn, incident_id: str, *, verifier: bool) -> dict | None:
    row = conn.execute("SELECT * FROM incidents WHERE id=?", (incident_id,)).fetchone()
    if row is None:
        return None
    inc = db.incident_row_to_dict(row)
    evidence = db.list_evidence(conn, incident_id)
    wa = [e for e in evidence if e["type"] in CITIZEN and e["payload"].get("channel") == "whatsapp"]
    if not wa:
        return None
    reporters = sorted({e["payload"].get("reporter") for e in wa if e["payload"].get("reporter")})
    media = (lambda mid: f"/api/media/{mid}") if verifier else (lambda mid: f"/api/wa/reports/{incident_id}/media/{mid}")

    items = []
    for e in wa:
        p = e["payload"]
        it = {"id": e["id"], "type": e["type"], "at": p.get("submitted_at") or p.get("captured_at") or e["retrieved_at"]}
        if e["type"] == "report":
            it["text"] = p.get("text")
        elif e["type"] == "voice":
            it.update({"media_url": media(p["media_id"]), "mime": p.get("mime"), "transcript": p.get("transcript"),
                       "asr_status": p.get("asr_status"), "asr_provider": p.get("asr_provider"), "audio_ms": p.get("audio_ms")})
        else:
            it.update({"media_url": media(p["media_id"]), "width": p.get("width"), "height": p.get("height"),
                       "reading": p.get("reading"), "reading_status": p.get("reading_status"), "reading_model": p.get("reading_model")})
        items.append(it)
    other = [{"id": e["id"], "type": e["type"], "summary": e["summary"]} for e in evidence if e["type"] not in CITIZEN]

    # Replies before the incident existed (the bot asking for a pin) carry no incident id: match them by the
    # reporter hash and time. A reply tied to another incident of the same reporter is not shown here.
    t0 = datetime.fromisoformat(min(it["at"] for it in items)) - REPLY_WINDOW
    t1 = datetime.fromisoformat(max(it["at"] for it in items)) + REPLY_WINDOW
    marks = ",".join("?" * len(reporters)) or "''"
    rows = conn.execute(f"SELECT * FROM wa_outbox WHERE incident_id = ? OR (purpose = 'reply' AND to_hash IN ({marks})) ORDER BY id",
                        (incident_id, *reporters)).fetchall()
    sent = [r for r in rows if r["incident_id"] == incident_id
            or (r["incident_id"] is None and t0 <= datetime.fromisoformat(r["created_at"]) <= t1)]
    verdict = conn.execute("SELECT decision, created_at FROM verdicts WHERE incident_id=? ORDER BY id DESC LIMIT 1", (incident_id,)).fetchone()

    dec = None if verifier else PUBLIC_COORD_DECIMALS
    return {
        "incident_id": incident_id,
        "created_at": inc["created_at"],
        "status": inc["status"],
        "trigger": inc["trigger"],
        "lat": round(inc["lat"], dec) if dec else inc["lat"],
        "lon": round(inc["lon"], dec) if dec else inc["lon"],
        "reporter": (reporters[0][:6] if reporters else None),
        "reporter_count": inc["reporter_count"],
        "enrichment": inc["enrichment"],
        "items": items,
        "other_evidence": other,
        "replies": [_sent(r) for r in sent if r["purpose"] == "reply"],
        "notifications": [_sent(r) for r in sent if r["purpose"] in ("verifier", "verdict")],
        "verdict": dict(verdict) if verdict else None,
        "public": is_public(conn, incident_id),
    }


def reply_templates() -> dict:
    """The bot's fixed replies, shown on the page so a reader sees what each step answers."""
    return {k: v for k, v in TEXT.items() if isinstance(v, str) and k != "verifier"}


def set_public(conn, incident_id: str, public: bool, who: str) -> None:
    if public:
        conn.execute("INSERT OR IGNORE INTO wa_showcase (incident_id, marked_at, marked_by) VALUES (?,?,?)", (incident_id, db.utcnow(), who))
    else:
        conn.execute("DELETE FROM wa_showcase WHERE incident_id=?", (incident_id,))

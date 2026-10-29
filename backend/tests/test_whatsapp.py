import hashlib
import hmac
import io
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend import db, reports, whatsapp
from backend.config import get_settings

AUTH = {"Authorization": "Bearer test-token"}
SECRET = "app-secret"
REPORTER = "15550001115"
VERIFIER = "6281200000822"
PIN = {"latitude": -2.21, "longitude": 113.92}  # Palangka Raya


@pytest.fixture
def wa(settings, monkeypatch):
    for k, v in {"WA_ACCESS_TOKEN": "tok", "WA_PHONE_NUMBER_ID": "PNID", "WA_APP_SECRET": SECRET,
                 "WA_VERIFY_TOKEN": "verify-me", "WA_VERIFIER_NUMBERS": f"+{VERIFIER}"}.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    s = get_settings()
    sent = []

    def graph(settings_, method, path, **kw):
        req = httpx.Request(method, path if path.startswith("https://") else f"{whatsapp.GRAPH}/{path}")
        if method == "POST" and path.endswith("/messages"):
            sent.append(kw["json"])
            if kw["json"]["to"] == "unreachable":
                return httpx.Response(400, json={"error": {"code": 131047, "message": "Re-engagement message"}}, request=req)
            return httpx.Response(200, json={"messages": [{"id": f"wamid.out{len(sent)}"}]}, request=req)
        if path == "MEDIA-IMG":
            return httpx.Response(200, json={"url": "https://lookaside.example/img", "mime_type": "image/jpeg", "file_size": 1000}, request=req)
        if path == "https://lookaside.example/img":
            buf = io.BytesIO()
            Image.new("RGB", (64, 48), (120, 80, 40)).save(buf, format="JPEG")
            return httpx.Response(200, content=buf.getvalue(), request=req)
        return httpx.Response(404, json={"error": {"code": 100, "message": "unknown"}}, request=req)

    monkeypatch.setattr(whatsapp, "_graph", graph)
    whatsapp._recent.clear()
    reports._recent.clear()
    yield s, sent
    whatsapp._recent.clear()


def body(*messages):
    return json.dumps({"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "messages": list(messages)}}]}]}).encode()


def msg(mid, kind, **content):
    return {"from": REPORTER, "id": mid, "timestamp": "1761700000", "type": kind, **({kind: content} if content else {})}


def post(client, raw, secret=SECRET):
    sig = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return client.post("/api/wa/webhook", content=raw, headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"})


def texts_to(sent, number):
    return [m["text"]["body"] for m in sent if m["to"] == number]


def test_signature_check():
    raw = b'{"a":1}'
    good = "sha256=" + hmac.new(b"s", raw, hashlib.sha256).hexdigest()
    assert whatsapp.verify_signature("s", raw, good)
    assert not whatsapp.verify_signature("s", raw + b" ", good)
    assert not whatsapp.verify_signature("s", raw, None)
    assert not whatsapp.verify_signature("", raw, good)


def test_webhook_verification_returns_the_challenge(wa):
    from backend.main import app
    with TestClient(app) as c:
        ok = c.get("/api/wa/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "12345"})
        assert ok.status_code == 200 and ok.text == "12345"
        assert c.get("/api/wa/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "1"}).status_code == 403


def test_unsigned_or_badly_signed_post_is_refused(wa):
    from backend.main import app
    with TestClient(app) as c:
        raw = body(msg("wamid.1", "text", body="ada asap"))
        assert c.post("/api/wa/webhook", content=raw).status_code == 401
        assert post(c, raw, secret="other").status_code == 401


def test_photo_then_pin_becomes_a_report_and_notifies(wa):
    s, sent = wa
    from backend.main import app
    with TestClient(app) as c:
        assert post(c, body(msg("wamid.1", "image", id="MEDIA-IMG", mime_type="image/jpeg", caption="asap tebal dekat kebun"))).status_code == 200
        assert texts_to(sent, REPORTER) == [whatsapp.TEXT["need_location"]]
        assert post(c, body(msg("wamid.2", "location", **PIN))).status_code == 200
        # A resent webhook is not processed twice.
        post(c, body(msg("wamid.2", "location", **PIN)))

    replies = texts_to(sent, REPORTER)
    assert len(replies) == 2 and "INC-" in replies[1] and "112" in replies[1]
    inc_id = replies[1].split("sebagai ")[1].split(".")[0]
    to_verifier = texts_to(sent, VERIFIER)
    assert len(to_verifier) == 1 and inc_id in to_verifier[0] and f"?incident={inc_id}" in to_verifier[0] and "1 foto" in to_verifier[0]

    with db.session(s.db_path) as conn:
        inc = conn.execute("SELECT trigger, reporter_count FROM incidents WHERE id=?", (inc_id,)).fetchone()
        ev = db.list_evidence(conn, inc_id)
        outbox = conn.execute("SELECT purpose, status FROM wa_outbox ORDER BY id").fetchall()
    assert inc["trigger"] == "citizen_report" and inc["reporter_count"] == 1
    kinds = sorted(e["type"] for e in ev if e["type"] in reports.CITIZEN_TYPES)
    assert kinds == ["image", "report"]
    for e in ev:
        if e["type"] in reports.CITIZEN_TYPES:
            assert e["payload"]["channel"] == "whatsapp" and e["payload"]["location_source"] == "whatsapp"
            assert REPORTER not in json.dumps(e["payload"])  # only the salted hash
            assert e["payload"]["reporter"] == whatsapp.reporter_hash(s, REPORTER)
    assert [tuple(r) for r in outbox] == [("reply", "sent"), ("reply", "sent"), ("verifier", "sent")]
    assert not list((s.data_dir / "wa_pending").iterdir())  # downloaded media is not kept twice


def test_later_text_joins_the_same_incident_without_a_new_reporter(wa):
    s, sent = wa
    from backend.main import app
    with TestClient(app) as c:
        post(c, body(msg("wamid.1", "location", **PIN)))
        assert texts_to(sent, REPORTER) == [whatsapp.TEXT["need_content"]]
        post(c, body(msg("wamid.2", "text", body="api terlihat di pinggir jalan")))
        post(c, body(msg("wamid.3", "text", body="angin ke arah kampung")))
    replies = texts_to(sent, REPORTER)
    inc_id = replies[1].split("sebagai ")[1].split(".")[0]
    assert replies[2] == whatsapp.TEXT["added"].format(inc=inc_id)
    with db.session(s.db_path) as conn:
        assert conn.execute("SELECT reporter_count FROM incidents WHERE id=?", (inc_id,)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM evidence WHERE incident_id=? AND type='report'", (inc_id,)).fetchone()[0] == 2
    assert len(texts_to(sent, VERIFIER)) == 1


def test_pin_outside_indonesia_and_unsupported_types_get_an_answer(wa):
    _, sent = wa
    from backend.main import app
    with TestClient(app) as c:
        post(c, body(msg("wamid.1", "location", latitude=40.7, longitude=-74.0)))
        post(c, body(msg("wamid.2", "sticker", id="x")))
        post(c, body(msg("wamid.3", "text", body="Halo")))
    assert texts_to(sent, REPORTER) == [whatsapp.TEXT["outside"], whatsapp.TEXT["unsupported"], whatsapp.TEXT["help"]]


def test_verdict_is_sent_to_the_reporter_and_deletion_forgets_the_number(wa):
    s, sent = wa
    from backend.main import app
    with TestClient(app) as c:
        post(c, body(msg("wamid.1", "text", body="ada kebakaran lahan")))
        post(c, body(msg("wamid.2", "location", **PIN)))
        inc_id = texts_to(sent, REPORTER)[-1].split("sebagai ")[1].split(".")[0]
        assert c.post(f"/api/incidents/{inc_id}/verdict", json={"decision": "approve"}, headers=AUTH).status_code == 200
        assert texts_to(sent, REPORTER)[-1] == whatsapp.TEXT["verdict"]["approve"].format(inc=inc_id)
        assert c.delete(f"/api/incidents/{inc_id}/citizen-data", headers=AUTH).status_code == 200
    with db.session(s.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM wa_reporters").fetchone()[0] == 0


def test_refused_send_is_recorded_with_the_reason(wa):
    s, _ = wa
    db.init(s.db_path)
    assert whatsapp.send_text(s, "unreachable", "hi", "verifier", None) is False
    with db.session(s.db_path) as conn:
        r = conn.execute("SELECT status, detail, to_hash FROM wa_outbox").fetchone()
    assert r["status"] == "failed" and "131047" in r["detail"] and r["to_hash"] != "unreachable"


def test_delivery_status_is_recorded_on_the_sent_message(wa):
    s, _ = wa
    db.init(s.db_path)
    assert whatsapp.send_text(s, REPORTER, "hi", "reply")
    status = {"entry": [{"changes": [{"value": {"statuses": [{"id": "wamid.out1", "status": "failed", "recipient_id": REPORTER,
              "errors": [{"code": 131030, "title": "Recipient phone number not in allowed list", "error_data": {"details": "add it in API Setup"}}]}]}}]}]}
    assert whatsapp.record_statuses(s, status) == 1
    with db.session(s.db_path) as conn:
        d = conn.execute("SELECT delivery FROM wa_outbox").fetchone()[0]
    assert d.startswith("failed: code 131030") and "allowed list" in d and "API Setup" in d


def test_message_ids_are_stored_hashed_because_they_carry_the_number(wa):
    s, _ = wa
    db.init(s.db_path)
    whatsapp.send_text(s, REPORTER, "hi", "reply")
    with db.session(s.db_path) as conn:
        conn.execute("INSERT INTO wa_outbox (created_at, to_hash, purpose, status, detail) VALUES ('t','h','reply','sent','wamid.HBgLMTU1NTAwMDExMTU')")
    db.init(s.db_path)  # rows from before the fix are hashed on start
    with db.session(s.db_path) as conn:
        details = [r[0] for r in conn.execute("SELECT detail FROM wa_outbox")]
    assert all(d.startswith("wamid:") for d in details) and not any("wamid." in d for d in details)


def test_public_form_can_not_claim_whatsapp_location(settings):
    from backend.main import app
    with TestClient(app) as c:
        r = c.post("/api/reports", data={"lat": -2.21, "lon": 113.92, "location_source": "whatsapp", "text": "asap"})
    assert r.status_code == 422


def test_web_report_notifies_verifiers(wa):
    _, sent = wa
    from backend.main import app
    with TestClient(app) as c:
        r = c.post("/api/reports", data={"lat": -2.21, "lon": 113.92, "location_source": "gps", "text": "asap tebal"})
    assert r.status_code == 201
    msgs = texts_to(sent, VERIFIER)
    assert len(msgs) == 1 and "form web" in msgs[0] and r.json()["incident_id"] in msgs[0]


def test_unconfigured_webhook_is_unavailable(settings):
    from backend.main import app
    with TestClient(app) as c:
        assert c.post("/api/wa/webhook", content=b"{}").status_code == 503


def test_verifier_can_crop_a_citizen_photo_and_the_original_is_gone(wa):
    s, sent = wa
    from backend.main import app
    with TestClient(app) as c:
        post(c, body(msg("wamid.1", "image", id="MEDIA-IMG", mime_type="image/jpeg")))
        post(c, body(msg("wamid.2", "location", **PIN)))
        inc_id = texts_to(sent, REPORTER)[-1].split("sebagai ")[1].split(".")[0]
        with db.session(s.db_path) as conn:
            ev = next(e for e in db.list_evidence(conn, inc_id) if e["type"] == "image")
        old = reports.media_path(s.data_dir, ev["payload"]["media_id"])
        assert c.post(f"/api/incidents/{inc_id}/evidence/{ev['id']}/crop", json={"box": [0, 0, 64, 20]}, headers=AUTH).status_code == 422  # too small
        assert c.post(f"/api/incidents/{inc_id}/evidence/{ev['id']}/crop", json={"box": [0, 0, 64, 48]}).status_code == 401
        r = c.post(f"/api/incidents/{inc_id}/evidence/{ev['id']}/crop", json={"box": [0, 0, 64, 48], "reason": "screen"}, headers=AUTH)
    assert r.status_code == 200 and (r.json()["width"], r.json()["height"]) == (64, 48)
    assert not old.exists()
    with db.session(s.db_path) as conn:
        p = next(e for e in db.list_evidence(conn, inc_id) if e["type"] == "image")["payload"]
    assert p["media_id"] == r.json()["media_id"] and p["sha256"] != p["redactions"][0]["original_sha256"]
    assert p["redactions"][0]["box"] == [0, 0, 64, 48] and p["redactions"][0]["from_size"] == [64, 48]


def test_report_log_is_public_only_after_a_verifier_marks_it(wa):
    s, sent = wa
    from backend.main import app
    with TestClient(app) as c:
        post(c, body(msg("wamid.1", "image", id="MEDIA-IMG", mime_type="image/jpeg", caption="asap tebal")))
        post(c, body(msg("wamid.2", "location", **PIN)))
        inc_id = texts_to(sent, REPORTER)[-1].split("sebagai ")[1].split(".")[0]

        assert c.get("/api/wa/reports").json()["count"] == 0
        full = c.get("/api/wa/reports", headers=AUTH).json()
        assert full["verifier"] and full["count"] == 1
        r = full["items"][0]
        assert r["incident_id"] == inc_id and not r["public"]
        assert [i["type"] for i in r["items"]] == ["report", "image"]
        # Both replies are on record with their text, the first one sent before the incident existed.
        assert [x["body"] for x in r["replies"]] == [whatsapp.TEXT["need_location"], texts_to(sent, REPORTER)[-1]]
        assert [n["purpose"] for n in r["notifications"]] == ["verifier"]
        assert REPORTER not in json.dumps(r) and VERIFIER not in json.dumps(r)
        media = r["items"][1]["media_url"].rsplit("/", 1)[1]
        assert c.get(f"/api/wa/reports/{inc_id}/media/{media}").status_code == 404

        assert c.put(f"/api/wa/reports/{inc_id}/showcase", json={"public": True}).status_code == 401
        assert c.put(f"/api/wa/reports/{inc_id}/showcase", json={"public": True}, headers=AUTH).json()["public"] is True
        pub = c.get("/api/wa/reports").json()
        assert pub["count"] == 1 and not pub["verifier"]
        p = pub["items"][0]
        assert p["items"][1]["media_url"] == f"/api/wa/reports/{inc_id}/media/{media}"
        assert len(str(p["lat"]).split(".")[1]) <= 3
        got = c.get(p["items"][1]["media_url"])
        assert got.status_code == 200 and got.headers["content-type"] == "image/jpeg"
        assert c.get(f"/api/wa/reports/{inc_id}/media/MED-0000000000000000.jpg").status_code == 404  # not this report's file

        # Marked public, the incident also opens on the public map, without its citizen evidence.
        inc = c.get(f"/api/incidents/{inc_id}")
        assert inc.status_code == 200 and not [e for e in inc.json()["evidence"] if e["type"] in ("report", "image", "voice")]
        assert inc_id in [i["id"] for i in c.get("/api/incidents").json()["items"]]

        assert c.put(f"/api/wa/reports/{inc_id}/showcase", json={"public": False}, headers=AUTH).json()["public"] is False
        assert c.get("/api/wa/reports").json()["count"] == 0
        assert c.get(f"/api/incidents/{inc_id}").status_code == 404

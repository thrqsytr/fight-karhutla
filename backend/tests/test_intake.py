import io
import json
from types import SimpleNamespace as NS

from PIL import Image

from backend import db, intake, meta_client, reports


def jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), (120, 110, 100)).save(buf, format="JPEG")
    return buf.getvalue()


def submit(settings):
    db.init(settings.db_path)
    with db.session(settings.db_path) as conn:
        return reports.submit(conn, settings.data_dir, lat=-3.0, lon=104.7, accuracy_m=10, location_source="gps", text="asap",
                              photos=[reports.Photo(jpeg(), "a.jpg")], audio=reports.Audio(b"fake-webm", "audio/webm"))


def test_without_key_media_stays_pending(settings):
    r = submit(settings)
    assert intake.process(settings, r["incident_id"])["skipped"]
    with db.session(settings.db_path) as conn:
        voice = next(e for e in db.list_evidence(conn, r["incident_id"]) if e["type"] == "voice")
    assert voice["payload"]["asr_status"] == "pending"


def test_voice_and_photo_are_processed_in_place(settings, monkeypatch):
    r = submit(settings)
    live = settings.model_copy(update={"model_api_key": "test"})
    monkeypatch.setattr(meta_client, "to_wav_16k", lambda b: b"RIFFwav")
    monkeypatch.setattr(meta_client, "transcribe", lambda s, wav, meter=None: {"transcript": "ada asap tebal", "audio_ms": 4000, "session_id": "s1"})
    reading = {"smoke_visible": True, "flames_visible": False, "burned_ground_visible": True, "description": "Grey smoke over burned ground.",
               "segment_concepts": ["burned ground"], "image_quality_issues": []}
    fake = NS(responses=NS(create=lambda **kw: NS(output_text=json.dumps(reading), usage=None)))
    monkeypatch.setattr(meta_client, "meta", lambda s: fake)
    out = intake.process(live, r["incident_id"])
    assert len(out["processed"]) == 2
    with db.session(settings.db_path) as conn:
        ev = {e["type"]: e for e in db.list_evidence(conn, r["incident_id"])}
    assert ev["voice"]["payload"]["transcript"] == "ada asap tebal" and ev["voice"]["payload"]["asr_status"] == "ok"
    assert ev["voice"]["summary"].startswith("Citizen voice note transcript")
    assert ev["image"]["payload"]["reading"]["smoke_visible"] is True
    assert "Visible: smoke, burned ground" in ev["image"]["summary"]
    # IDs stay stable: processing updates the same evidence items.
    assert sorted(e["id"] for e in ev.values()) == ["EV-001", "EV-002", "EV-003"]
    assert intake.process(live, r["incident_id"])["processed"] == []  # idempotent


def test_failed_transcription_is_recorded_with_reason(settings, monkeypatch):
    r = submit(settings)
    live = settings.model_copy(update={"model_api_key": "test"})

    def bad(b):
        raise meta_client.ModelUnavailable("ffmpeg could not convert the audio: invalid data")

    monkeypatch.setattr(meta_client, "to_wav_16k", bad)
    monkeypatch.setattr(meta_client, "meta", lambda s: (_ for _ in ()).throw(meta_client.ModelUnavailable("down")))
    intake.process(live, r["incident_id"])
    with db.session(settings.db_path) as conn:
        ev = {e["type"]: e for e in db.list_evidence(conn, r["incident_id"])}
    assert ev["voice"]["payload"]["asr_status"].startswith("failed: ffmpeg could not convert")
    assert ev["image"]["payload"]["reading_status"] == "failed: down"


def test_meter_prices_spark_from_usage():
    m = meta_client.Meter()
    m.spark(NS(model_dump=lambda: {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "input_tokens_details": {"cached_tokens": 200_000}}))
    m.sam(4)
    m.asr(3_600_000)
    s = m.summary()
    # 800k * 1.25 + 200k * 0.15 + 1M * 4.25 = 1.00 + 0.03 + 4.25 = 5.28; SAM 4 * 0.0025; ASR one hour 0.18
    assert s["cost_usd"] == round(5.28 + 0.01 + 0.18, 6)

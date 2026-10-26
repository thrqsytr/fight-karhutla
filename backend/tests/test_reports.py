import io
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend import db, ingest, reports

AUTH = {"Authorization": "Bearer test-token"}


def jpeg_with_exif() -> bytes:
    img = Image.new("RGB", (3000, 2000), (200, 120, 60))
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"  # Make
    exif[0x0110] = "PhoneModel"  # Model
    exif[0x8825] = {2: (0.0, 1.0, 12.0)}  # GPS IFD with a latitude
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


@pytest.fixture(autouse=True)
def reset_rate_limit():
    reports._recent.clear()
    yield
    reports._recent.clear()


def test_clean_photo_removes_all_metadata_and_downscales():
    raw = jpeg_with_exif()
    assert Image.open(io.BytesIO(raw)).getexif()  # the input really carries EXIF
    out, w, h = reports.clean_photo(raw)
    img = Image.open(io.BytesIO(out))
    assert not img.getexif() and "exif" not in img.info
    assert max(w, h) == reports.MAX_EDGE_PX and img.size == (w, h)


def test_unreadable_photo_is_rejected_without_partial_writes(settings):
    db.init(settings.db_path)
    with db.session(settings.db_path) as conn:
        with pytest.raises(reports.ReportError):
            reports.submit(conn, settings.data_dir, lat=-0.5, lon=116.0, accuracy_m=20, location_source="gps",
                           text="asap", photos=[reports.Photo(b"not an image", "x.jpg")], audio=None)
    with db.session(settings.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 0
    assert not (settings.data_dir / "media").exists() or not any((settings.data_dir / "media").iterdir())


@pytest.mark.parametrize("kwargs, msg", [
    ({"lat": 14.6, "lon": 121.0}, "luar wilayah"),
    ({"text": "", "photos": []}, "minimal satu"),
    ({"location_source": "ip"}, "Sumber lokasi"),
    ({"text": "x" * 1001}, "maksimal 1000"),
])
def test_invalid_reports_are_rejected(settings, kwargs, msg):
    db.init(settings.db_path)
    base = dict(lat=-0.5, lon=116.0, accuracy_m=20, location_source="gps", text="asap tebal", photos=[], audio=None)
    base.update(kwargs)
    with db.session(settings.db_path) as conn:
        with pytest.raises(reports.ReportError, match=msg):
            reports.submit(conn, settings.data_dir, **base)


def test_report_far_from_incidents_opens_citizen_incident(settings):
    db.init(settings.db_path)
    with db.session(settings.db_path) as conn:
        r = reports.submit(conn, settings.data_dir, lat=-3.0, lon=104.7, accuracy_m=15, location_source="gps",
                           text="Asap tebal di belakang kebun", photos=[reports.Photo(jpeg_with_exif(), "a.jpg")], audio=None)
        assert not r["joined_existing"] and r["evidence_ids"] == ["EV-001", "EV-002"]
        inc = conn.execute("SELECT * FROM incidents WHERE id=?", (r["incident_id"],)).fetchone()
        assert inc["trigger"] == "citizen_report" and inc["reporter_count"] == 1
        ev = db.list_evidence(conn, r["incident_id"])
        assert [e["type"] for e in ev] == ["report", "image"]
        img = ev[1]["payload"]
        assert img["capture_source"] == "citizen" and img["camera"] is None and img["segmentation"] is None
        stored = reports.media_path(settings.data_dir, img["media_id"])
        assert stored and not Image.open(stored).getexif()


def test_report_near_hotspot_cluster_joins_it_and_stays_public(settings):
    with TestClient(__import__("backend.main", fromlist=["app"]).app) as c:
        target = c.get("/api/incidents", headers=AUTH).json()["items"][0]
        with db.session(settings.db_path) as conn:
            # Fixture data is frozen in time; report "now" as an hour after the cluster's last detection.
            now = datetime.fromisoformat(target["last_seen"]) + timedelta(hours=1)
            r = reports.submit(conn, settings.data_dir, lat=target["lat"] + 0.005, lon=target["lon"], accuracy_m=30,
                               location_source="map_pick", text="Ada api kecil", photos=[], audio=None, now=now)
        assert r["joined_existing"] and r["incident_id"] == target["id"]
        pub = c.get(f"/api/incidents/{target['id']}").json()
        assert pub["trigger"] == "merged"
        assert all(e["type"] not in {"report", "image", "voice"} for e in pub["evidence"])
        priv = c.get(f"/api/incidents/{target['id']}", headers=AUTH).json()
        assert any(e["type"] == "report" for e in priv["evidence"])


def test_new_hotspots_near_citizen_report_merge_into_it(settings):
    db.init(settings.db_path)
    hs_rows = ingest.firms.get_area(ingest.INDONESIA_FIRMS_BBOX, 2, map_key="", cache_dir=settings.cache_dir,
                                    fixtures_dir=settings.fixtures_dir, fixture=True)
    first = min(hs_rows, key=lambda h: h.acquired_at)
    with db.session(settings.db_path) as conn:
        r = reports.submit(conn, settings.data_dir, lat=first.lat, lon=first.lon, accuracy_m=10, location_source="gps",
                           text="asap", photos=[], audio=None, now=datetime.fromisoformat(first.acquired_at) - timedelta(hours=1))
    ingest.run_once(settings)
    with db.session(settings.db_path) as conn:
        inc = conn.execute("SELECT trigger, hotspot_count, enriched_at FROM incidents WHERE id=?", (r["incident_id"],)).fetchone()
    assert inc["trigger"] == "merged" and inc["hotspot_count"] > 0 and inc["enriched_at"] is None


def test_report_api_end_to_end_and_media_is_verifier_only(settings):
    from backend.main import app

    with TestClient(app) as c:
        files = [("photos", ("p.jpg", jpeg_with_exif(), "image/jpeg")), ("audio", ("v.webm", b"\x1aE\xdf\xa3fake-webm", "audio/webm"))]
        r = c.post("/api/reports", data={"lat": "-3.0", "lon": "104.7", "location_source": "gps", "accuracy_m": "12", "text": "Asap"}, files=files)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["needs_human"] is True and len(body["evidence_ids"]) == 3
        inc_id = body["incident_id"]
        # Citizen-only incidents are hidden from the public until approved.
        assert c.get(f"/api/incidents/{inc_id}").status_code == 404
        detail = c.get(f"/api/incidents/{inc_id}", headers=AUTH).json()
        media = [e["payload"]["media_id"] for e in detail["evidence"] if e["type"] in {"image", "voice"}]
        assert len(media) == 2
        assert c.get(f"/api/media/{media[0]}").status_code == 401
        got = c.get(f"/api/media/{media[0]}", headers=AUTH)
        assert got.status_code == 200 and got.headers["content-type"] == "image/jpeg"
        assert c.get("/api/media/..%2F..%2Fkarhutla.sqlite3", headers=AUTH).status_code == 404
        # Enrichment ran in the background after the response.
        types = [e["type"] for e in c.get(f"/api/incidents/{inc_id}", headers=AUTH).json()["evidence"]]
        assert "weather" in types and "osm" in types


def test_report_api_rejects_bad_input_with_readable_message(settings):
    from backend.main import app

    with TestClient(app) as c:
        r = c.post("/api/reports", data={"lat": "14.6", "lon": "121.0", "location_source": "gps", "text": "x"})
        assert r.status_code == 422 and "Indonesia" in r.json()["detail"]


def test_rate_limit_per_client(settings):
    assert not any(reports.rate_limited("1.2.3.4", now=1000.0 + i) for i in range(reports.RATE_LIMIT))
    assert reports.rate_limited("1.2.3.4", now=1010.0)
    assert not reports.rate_limited("1.2.3.4", now=1000.0 + reports.RATE_WINDOW_S + 20)
    assert not reports.rate_limited("5.6.7.8", now=1010.0)

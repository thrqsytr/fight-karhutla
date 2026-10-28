"""Sharing: public incidents only, OG tags that point at a real image, nothing private in the summary."""

from fastapi.testclient import TestClient
from PIL import Image

from backend import share

AUTH = {"Authorization": "Bearer test-token"}


def grey_tile(z, x, y, cache_dir):
    return Image.new("RGB", (256, 256), (200, 205, 210))


def test_share_page_has_og_tags_and_a_working_image(settings, monkeypatch):
    monkeypatch.setattr(share, "fetch_tile", grey_tile)
    from backend.main import app

    with TestClient(app) as c:
        inc = c.get("/api/incidents").json()["items"][0]
        page = c.get(f"/i/{inc['id']}")
        assert page.status_code == 200
        html = page.text
        assert 'property="og:image"' in html and 'og:image:width" content="1200"' in html and inc["id"] in html
        info = c.get(f"/api/incidents/{inc['id']}/share").json()
        assert info["url"].endswith(f"/i/{inc['id']}") and "112" in info["description"]
        img = c.get(info["image"])
        assert img.status_code == 200 and img.headers["content-type"] == "image/png"
        assert Image.open(__import__("io").BytesIO(img.content)).size == (1200, 630)
        en = c.get(f"/api/incidents/{inc['id']}/share", params={"lang": "en"}).json()
        assert "Not an official emergency channel" in en["description"]


def test_citizen_only_incident_is_not_shareable_until_approved(settings, monkeypatch):
    monkeypatch.setattr(share, "fetch_tile", grey_tile)
    from backend import db
    from backend.main import app

    with TestClient(app) as c:
        with db.session(settings.db_path) as conn:
            ts = db.utcnow()
            conn.execute("INSERT INTO incidents (id, created_at, updated_at, trigger, lat, lon, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?)",
                         ("INC-20261027-9002", ts, ts, "citizen_report", -0.5, 116.0, ts, ts))
        assert c.get("/i/INC-20261027-9002").status_code == 404
        assert c.get("/og/INC-20261027-9002.png").status_code == 404
        assert c.get("/api/incidents/INC-20261027-9002/share").status_code == 404
        c.post("/api/incidents/INC-20261027-9002/verdict", json={"decision": "approve"}, headers=AUTH)
        assert c.get("/api/incidents/INC-20261027-9002/share").status_code == 200


def test_summary_marks_ai_status_and_verification():
    inc = {"id": "INC-1", "status": "likely_fire", "hotspot_count": 4, "max_frp": 12.5, "last_seen": "2026-10-27T05:00:00+00:00", "lat": 0.0, "lon": 116.0}
    f = share.facts(inc, [], None, "id")
    title, desc = share.summary(f, "u", "id")
    assert "rekomendasi AI" in title and "4 titik panas" in desc and "12,5 MW" in desc and "13.00 WITA" in desc
    title, _ = share.summary(share.facts(inc, [], "approve", "en"), "u", "en")
    assert "Verified" in title and "AI recommendation" not in title

"""Ground texture for the 3D flight view: Sentinel-2 when it covers the plan, OSM tiles otherwise."""

import json

from fastapi.testclient import TestClient
from PIL import Image

from backend import db, ground, share

AUTH = {"Authorization": "Bearer test-token"}


def plan_for(c, inc_id):
    return c.post(f"/api/incidents/{inc_id}/drone-proposals", json={"reason": "3d"}, headers=AUTH).json()


def test_osm_mosaic_when_no_satellite_pair(settings, monkeypatch):
    from backend.main import app

    calls = []
    monkeypatch.setattr(share, "fetch_tile", lambda z, x, y, cache: calls.append((z, x, y)) or Image.new("RGB", (256, 256), (200, 210, 190)))
    with TestClient(app) as c:
        inc_id = c.get("/api/incidents").json()["items"][0]["id"]
        p = plan_for(c, inc_id)
        g = c.get(f"/api/drone-proposals/{p['id']}/ground", headers=AUTH).json()
        assert g["kind"] == "osm" and g["flat"] and len(g["corners"]) == 4
        w, s, e, n = p["plan"]["bbox"]
        lons, lats = [x[0] for x in g["corners"]], [x[1] for x in g["corners"]]
        assert min(lons) < w and max(lons) > e and min(lats) < s and max(lats) > n  # the mosaic covers the plan plus a margin
        assert c.get(g["image"]).status_code == 401
        img = c.get(g["image"], headers=AUTH)
        assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg" and calls
        assert all(z == ground.OSM_ZOOM for z, _, _ in calls)


def test_sentinel_pair_is_used_when_it_covers_the_plan(settings):
    from backend.main import app

    with TestClient(app) as c:
        inc_id = c.get("/api/incidents").json()["items"][0]["id"]
        p = plan_for(c, inc_id)
        w, s, e, n = p["plan"]["bbox"]
        payload = {"status": "ok", "burned_area_ha": 12.0, "image_corners": [[w - 0.01, n + 0.01], [e + 0.01, n + 0.01], [e + 0.01, s - 0.01], [w - 0.01, s - 0.01]],
                   "images": {"post": "20261028T000000_post.png", "mask": "20261028T000000_mask.png"},
                   "pre": {"datetime": "2026-10-01T02:00:00Z"}, "post": {"datetime": "2026-10-26T02:00:00Z"}, "attribution": "Copernicus"}
        with db.session(settings.db_path) as conn:
            ev = db.add_evidence(conn, inc_id, "satellite", "Sentinel-2", "pair", payload)
        g = c.get(f"/api/drone-proposals/{p['id']}/ground", headers=AUTH).json()
        assert g["kind"] == "sentinel2" and g["evidence_id"] == ev and g["image"].endswith("_post.png") and g["mask"].endswith("_mask.png")


def test_unknown_plan_404(settings):
    from backend.main import app

    with TestClient(app) as c:
        assert c.get("/api/drone-proposals/9999/ground", headers=AUTH).status_code == 404

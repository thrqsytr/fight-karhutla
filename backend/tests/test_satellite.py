"""Sentinel-2 pair selection and dNBR, with synthetic scenes: no network."""

import json
from datetime import datetime, timezone

import numpy as np
from fastapi.testclient import TestClient

from backend import db, gate, satellite
from backend.sources import sentinel

NOW = datetime(2026, 10, 28, tzinfo=timezone.utc)
FIRST_SEEN = datetime(2026, 10, 10, 6, tzinfo=timezone.utc)
POINTS = [(-0.02, 116.45)]


def item(day: str, name: str, cloud: float = 5.0) -> dict:
    bands = {"raster:bands": [{"scale": 0.0001, "offset": -0.1}]}
    assets = {k: {"href": f"mem://{name}/{k}", **(bands if k in ("nir", "swir22") else {})} for k in ("nir", "swir22", "scl", "visual")}
    assets["aot"] = {"href": f"mem://{name}/aot", "raster:bands": [{"scale": 0.001, "offset": 0}]}
    return {"id": f"S2_{name}", "properties": {"datetime": f"{day}T02:00:00Z", "eo:cloud_cover": cloud, "proj:epsg": 32750}, "assets": assets}


def scenes(post_aot=300, post_scl=4, burned_left=True):
    """Pre: healthy everywhere (NBR 0.5). Post: the left half burned (NBR -0.33), the right half unchanged."""
    def reader(href, bounds, shape, resampling, bands=1):
        name, band = href.split("//")[1].split("/")
        a = np.zeros((bands, *shape), dtype="u2")
        left = np.zeros(shape, dtype=bool)
        left[:, : shape[1] // 2] = burned_left
        if band == "scl":
            a[0] = post_scl if name == "post" else 4
        elif band == "aot":
            a[0] = post_aot if name == "post" else 300
        elif band == "nir":
            a[0] = np.where(left & (name == "post"), 2000, 4000)  # reflectance 0.1 or 0.3
        elif band == "swir22":
            a[0] = np.where(left & (name == "post"), 3000, 2000)  # reflectance 0.2 or 0.1
        else:
            a[:] = 120
        return a

    def search_fn(bbox, start, end, cache_dir):
        items = [item("2026-10-20", "post"), item("2026-09-30", "pre")]
        return [i for i in items if start.isoformat()[:10] <= i["properties"]["datetime"][:10] <= end.isoformat()[:10]]

    return reader, search_fn


def test_dark_pixels_are_not_counted_as_burned(tmp_path):
    """Water or deep shadow in the after image: reflectance near zero would make NBR explode, so it is excluded."""
    base, search_fn = scenes(burned_left=False)

    def reader(href, bounds, shape, resampling, bands=1):
        a = base(href, bounds, shape, resampling, bands)
        if "post/" in href and href.endswith(("nir", "swir22")):
            a[0, :, : shape[1] // 2] = 1010  # reflectance 0.001: dark
        return a

    r = sentinel.satellite_pair(POINTS, FIRST_SEEN, now=NOW, cache_dir=tmp_path, reader=reader, search_fn=search_fn)
    rows, cols = r["_render"]["shape"]
    assert r["status"] == "ok" and r["pixel_count"] == 0
    assert r["valid_pixel_count"] == rows * (cols - cols // 2)
    assert -2 <= r["dnbr_p95"] <= 2


def test_dnbr_counts_burned_pixels_at_20_m(tmp_path):
    reader, search_fn = scenes()
    r = sentinel.satellite_pair(POINTS, FIRST_SEEN, now=NOW, cache_dir=tmp_path, reader=reader, search_fn=search_fn)
    assert r["status"] == "ok" and r["pre"]["id"] == "S2_pre" and r["post"]["id"] == "S2_post"
    rows, cols = r["_render"]["shape"]
    assert r["pixel_count"] == rows * (cols // 2)
    assert r["burned_area_ha"] == round(rows * (cols // 2) * 0.04, 2)  # a 20 m pixel is 0.04 ha
    assert r["low_severity_pixel_count"] == 0
    pngs = sentinel.render_pngs(r["_render"])
    assert all(v.startswith(b"\x89PNG") for v in pngs.values())


def test_heavy_haze_claims_no_hectares(tmp_path):
    reader, search_fn = scenes(post_aot=950)
    r = sentinel.satellite_pair(POINTS, FIRST_SEEN, now=NOW, cache_dir=tmp_path, reader=reader, search_fn=search_fn)
    assert r["status"] == "hazy" and r["pixel_count"] is None and r["burned_area_ha"] is None
    assert "haze" in r["reason"] and r["burned_pixel_count"] > 0


def test_cloudy_after_image_is_unknown(tmp_path):
    reader, search_fn = scenes(post_scl=9)  # cloud, high probability
    r = sentinel.satellite_pair(POINTS, FIRST_SEEN, now=NOW, cache_dir=tmp_path, reader=reader, search_fn=search_fn)
    assert r["status"] == "no_post_image" and r["pixel_count"] is None
    assert r["scenes_checked"] == 1  # the interface says how many passes were looked at


def test_no_scene_since_first_detection(tmp_path):
    reader, search_fn = scenes()
    r = sentinel.satellite_pair(POINTS, datetime(2026, 10, 25, tzinfo=timezone.utc), now=NOW, cache_dir=tmp_path, reader=reader, search_fn=search_fn)
    assert r["status"] == "no_post_image" and r["scenes_checked"] == 0


def test_collect_stores_evidence_images_and_gate_recomputes_area(settings):
    from backend.main import app

    reader, search_fn = scenes()
    with TestClient(app) as c:
        inc_id = c.get("/api/incidents").json()["items"][0]["id"]
        assert satellite.collect(settings, inc_id)["status"] == "unknown"  # fixture mode makes no network call
        settings.fixture_mode = False
        with db.session(settings.db_path) as conn:  # fixture incidents are from late October; put the synthetic pair around this one
            conn.execute("UPDATE incidents SET first_seen=? WHERE id=?", (FIRST_SEEN.isoformat(), inc_id))
        r = satellite.collect(settings, inc_id, now=NOW, reader=reader, search_fn=search_fn)
        assert r["reused"] is False
        reused = satellite.collect(settings, inc_id, now=NOW, reader=reader, search_fn=search_fn)
        assert reused["reused"] is True and reused["fresh_hours"] == 24
        # A pair stored before the dark-pixel guard is measured again rather than reused.
        with db.session(settings.db_path) as conn:
            row = conn.execute("SELECT id, payload_json FROM evidence WHERE id=?", (f"{inc_id}/{r['evidence_id']}",)).fetchone()
            legacy = {k: v for k, v in json.loads(row["payload_json"]).items() if k != "min_nbr_sum"}
            conn.execute("UPDATE evidence SET payload_json=? WHERE id=?", (json.dumps(legacy), row["id"]))
        again = satellite.collect(settings, inc_id, now=NOW, reader=reader, search_fn=search_fn)
        assert again["reused"] is False and again["evidence_id"] != r["evidence_id"]
        with db.session(settings.db_path) as conn:
            evs = db.list_evidence(conn, inc_id)
        ev = next(e for e in evs if e["id"] == r["evidence_id"])
        assert ev["type"] == "satellite" and set(ev["payload"]["images"]) == {"pre", "post", "overlay", "mask"}
        assert len(ev["payload"]["image_corners"]) == 4
        assert "_render" not in ev["payload"]

        img = c.get(f"/api/satellite/{inc_id}/{ev['payload']['images']['overlay']}")
        assert img.status_code == 200 and img.headers["content-type"] == "image/png"
        assert c.get(f"/api/satellite/{inc_id}/..%2Fkarhutla.sqlite3").status_code == 404
        detail = c.get(f"/api/incidents/{inc_id}").json()
        assert any(e["type"] == "satellite" for e in detail["evidence"])  # satellite evidence is public

    raw = {
        "incident_id": inc_id, "location": {"lat": ev["payload"]["aoi_bbox"][1], "lon": ev["payload"]["aoi_bbox"][0], "admin": "unknown", "accuracy_m": 375},
        "status": "possible_fire", "confidence": 0.6, "evidence": [],
        "claims": [{"text": "Citra Sentinel-2 menunjukkan bekas terbakar.", "evidence_ids": [ev["id"]]}],
        "risk": {"score": 50, "drivers": []}, "burn_area_ha": {"value": 99.0, "method": "sam_mask", "evidence_id": ev["id"]},
        "recommended_actions": [], "dispatch_brief": {"id": "Bekas terbakar terlihat.", "en": "A burn scar is visible."},
        "uncertainties": [], "needs_human": True,
    }
    out = gate.run(raw, evs, inc_id).output
    assert out["burn_area_ha"] == {"value": ev["payload"]["burned_area_ha"], "method": "dnbr", "evidence_id": ev["id"]}


def test_collect_says_which_image_is_missing(settings):
    from backend.main import app

    reader, search_fn = scenes(post_scl=9)
    with TestClient(app) as c:
        inc_id = c.get("/api/incidents").json()["items"][0]["id"]
        settings.fixture_mode = False
        with db.session(settings.db_path) as conn:
            conn.execute("UPDATE incidents SET first_seen=? WHERE id=?", (FIRST_SEEN.isoformat(), inc_id))
        r = satellite.collect(settings, inc_id, now=NOW, reader=reader, search_fn=search_fn)
    assert r["status"] == "unknown" and r["pair_status"] == "no_post_image" and r["scenes_checked"] == 1

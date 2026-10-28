"""Export the ground layers of a real incident for the drone simulator, from the same Sentinel-2 pair the app stored.

Writes, on one 10 m grid in the scene's UTM zone:
  post_rgb.png   true color after the fire (the terrain texture)
  burned.png     dNBR >= 0.27 and clear in both images (white = burned); the simulator's ground truth
  water.png      scene classification "water" in the after image
  terrain.json   UTM bounds, EPSG, corner lon/lat, scene IDs, hotspots

Usage (repo root): .venv/Scripts/python sim/export_terrain.py INCIDENT_ID OUT_DIR
"""

import json
import sys
from pathlib import Path

import httpx
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.sources import sentinel  # noqa: E402

API = "https://karhutla.thariq.xyz/api/incidents/"
ITEM = "https://earth-search.aws.element84.com/v1/collections/sentinel-2-l2a/items/"


def main(incident_id: str, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    inc = httpx.get(API + incident_id, timeout=30).json()
    sat = [e for e in inc["evidence"] if e["type"] == "satellite"][-1]["payload"]
    pre = httpx.get(ITEM + sat["pre"]["id"], timeout=30).json()
    post = httpx.get(ITEM + sat["post"]["id"], timeout=30).json()
    bounds, shape20 = sentinel.grid(sat["aoi_bbox"], sat["epsg"])
    shape10 = (shape20[0] * 2, shape20[1] * 2)
    read = sentinel.read_window

    scl_pre = read(pre["assets"]["scl"]["href"], bounds, shape20, "nearest", 1)[0]
    scl_post = read(post["assets"]["scl"]["href"], bounds, shape20, "nearest", 1)[0]
    # The app's own NBR, so dark pixels (water, deep shadow) are excluded exactly as in the stored evidence.
    pre_nbr, pre_ok = sentinel._nbr(pre, bounds, shape20, read)
    post_nbr, post_ok = sentinel._nbr(post, bounds, shape20, read)
    valid = np.isin(scl_pre, sentinel.CLEAR_CLASSES) & np.isin(scl_post, sentinel.CLEAR_CLASSES) & pre_ok & post_ok
    burned = valid & ((pre_nbr - post_nbr) >= sentinel.DNBR_BURNED)
    print("burned px (20 m):", int(burned.sum()), "=", round(int(burned.sum()) * 0.04, 2), "ha; app stored", sat["burned_area_ha"])
    up = lambda m: np.kron(m, np.ones((2, 2), dtype=bool))
    Image.fromarray((up(burned) * 255).astype("u1")).save(out / "burned.png")
    Image.fromarray((up(scl_post == 6) * 255).astype("u1")).save(out / "water.png")
    rgb = read(post["assets"]["visual"]["href"], bounds, shape10, "bilinear", 3)
    Image.fromarray(np.transpose(rgb, (1, 2, 0)).astype("u1")).save(out / "post_rgb.png")
    meta = {
        "incident_id": incident_id, "epsg": sat["epsg"], "utm_bounds": list(bounds), "pixel_m": 10, "shape": list(shape10),
        "corners_lonlat": sat["image_corners"], "pre_scene": sat["pre"]["id"], "post_scene": sat["post"]["id"],
        "burned_ha_20m": round(int(burned.sum()) * 0.04, 2),
        "hotspots": [{"lat": h["lat"], "lon": h["lon"], "frp": h["frp"], "at": h["acquired_at"]} for h in inc["hotspots"]],
        "attribution": sentinel.ATTRIBUTION,
    }
    (out / "terrain.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print("wrote", out)


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]))

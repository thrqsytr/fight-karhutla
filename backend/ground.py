"""Ground texture for the 3D flight view: what lies under a drone plan.

Prefers the incident's own Sentinel-2 pair (true color after the fire, plus the dNBR burn mask) when it covers
the plan. Otherwise the server stitches OpenStreetMap raster tiles over the plan area, cached like the share
image tiles. The ground is flat: no elevation model is used, and the view says so.
"""

import io
import math
from pathlib import Path

from PIL import Image

from . import share

MARGIN_M = 400
OSM_ZOOM = 16  # about 2.4 m per pixel at the equator: enough for a 120 m flight seen from a chase camera
MAX_TILES = 64


def _covers(corners: list[list[float]], bbox: list[float]) -> bool:
    lons, lats = [c[0] for c in corners], [c[1] for c in corners]
    w, s, e, n = bbox
    return min(lons) <= w and max(lons) >= e and min(lats) <= s and max(lats) >= n


def satellite_ground(evidence: list[dict], bbox: list[float]) -> dict | None:
    for e in reversed(evidence):
        p = e["payload"] if e["type"] == "satellite" else None
        if p and p.get("images", {}).get("post") and p.get("image_corners") and _covers(p["image_corners"], bbox):
            return {"kind": "sentinel2", "evidence_id": e["id"], "corners": p["image_corners"], "post": p["images"]["post"],
                    "mask": p["images"].get("mask"), "burned_area_ha": p.get("burned_area_ha"),
                    "dates": [p["pre"]["datetime"][:10], p["post"]["datetime"][:10]], "attribution": p.get("attribution")}
    return None


def _tile_of(lat: float, lon: float, z: int) -> tuple[float, float]:
    x, y = share._px(lat, lon, z)
    return x / share.TILE, y / share.TILE


def _lonlat(tx: float, ty: float, z: int) -> list[float]:
    n = 2 ** z
    lon = tx / n * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * ty / n))))
    return [round(lon, 6), round(lat, 6)]


def osm_extent(bbox: list[float]) -> tuple[int, int, int, int, list[list[float]]]:
    w, s, e, n = bbox
    clat = (s + n) / 2
    dlat, dlon = MARGIN_M / 110_574, MARGIN_M / (111_320 * math.cos(math.radians(clat)))
    x0, y0 = _tile_of(n + dlat, w - dlon, OSM_ZOOM)
    x1, y1 = _tile_of(s - dlat, e + dlon, OSM_ZOOM)
    tx0, ty0, tx1, ty1 = math.floor(x0), math.floor(y0), math.floor(x1), math.floor(y1)
    if (tx1 - tx0 + 1) * (ty1 - ty0 + 1) > MAX_TILES:
        raise ValueError("plan area too large for a ground mosaic")
    corners = [_lonlat(tx0, ty0, OSM_ZOOM), _lonlat(tx1 + 1, ty0, OSM_ZOOM), _lonlat(tx1 + 1, ty1 + 1, OSM_ZOOM), _lonlat(tx0, ty1 + 1, OSM_ZOOM)]
    return tx0, ty0, tx1, ty1, corners


def osm_mosaic(bbox: list[float], cache_dir: Path, tile_fetch=None) -> bytes:
    tile_fetch = tile_fetch or share.fetch_tile
    tx0, ty0, tx1, ty1, _ = osm_extent(bbox)
    img = Image.new("RGB", ((tx1 - tx0 + 1) * share.TILE, (ty1 - ty0 + 1) * share.TILE), (233, 236, 239))
    for tx in range(tx0, tx1 + 1):
        for ty in range(ty0, ty1 + 1):
            try:
                img.paste(tile_fetch(OSM_ZOOM, tx, ty, cache_dir), ((tx - tx0) * share.TILE, (ty - ty0) * share.TILE))
            except Exception:  # a missing tile stays grey; the view still works
                pass
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=82)
    return buf.getvalue()

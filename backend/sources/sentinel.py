"""Sentinel-2 L2A before and after images from Earth Search (Element 84, AWS open data).

Bands are read as windows of Cloud Optimized GeoTIFFs, so only the area of interest is downloaded.
dNBR is pre-fire NBR minus post-fire NBR, with NBR = (NIR - SWIR2) / (NIR + SWIR2). Burned hectares are
a count of 20 m pixels at or above a fixed dNBR threshold, computed here and never by a model.

Planetary Computer was the first choice, but its STAC search answered HTTP 504 repeatedly on 2026-10-28;
Earth Search answered in under a second with the same Sentinel-2 L2A scenes.
"""

import json
import logging
import math
import os
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from .http import SourceError, fetch_text

log = logging.getLogger(__name__)

STAC_SEARCH = "https://earth-search.aws.element84.com/v1/search"
COLLECTION = "sentinel-2-l2a"
ATTRIBUTION = "Contains modified Copernicus Sentinel data 2026, via Earth Search (Element 84)"
SEARCH_TTL_S = 3600

PIXEL_M = 20  # SWIR2 and the scene classification are 20 m; NIR (10 m) is averaged onto that grid
# Scene classification classes counted as clear ground. 2 is "dark area": fresh burn scars often land there.
CLEAR_CLASSES = (2, 4, 5, 7)
DNBR_BURNED = 0.27  # USGS (Key and Benson) moderate-low severity and above
DNBR_LOW = 0.10  # low severity starts here; reported separately, never counted as burned
MIN_CLEAR = 0.6  # share of the area of interest that must be clear in an image
MIN_NBR_SUM = 0.05  # NIR + SWIR2 reflectance below this is water or deep shadow: NBR is noise there
HEAVY_HAZE_AOT = 0.8  # aerosol optical thickness above this: smoke distorts NIR, so no hectares are claimed
MAX_TILE_CLOUD = 90
MAX_CANDIDATES = 6
PRE_WINDOW_DAYS = 120
PRE_GAP_DAYS = 2  # fires often start before the first hotspot; keep the "before" image clear of that
AOI_BUFFER_M = 750
AOI_MIN_HALF_M = 1000
AOI_MAX_HALF_M = 5000
READ_ATTEMPTS = 3
HTTP_TIMEOUT_S = 20
RGB_SCALE = 2  # true color pixels per analysis pixel per side: 2 is the native 10 m, 1 reads the 20 m overview

# reader(href, bounds, shape, resampling, bands) -> array (bands, rows, cols)
Reader = Callable[[str, tuple, tuple, str, int], np.ndarray]


def aoi_bbox(points: list[tuple[float, float]]) -> list[float]:
    """Box around the hotspots plus a buffer, as [west, south, east, north] in degrees."""
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    clat, clon = (min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2
    m_lat = 110_574.0
    m_lon = 111_320.0 * math.cos(math.radians(clat))
    half_n = min(max((max(lats) - min(lats)) / 2 * m_lat + AOI_BUFFER_M, AOI_MIN_HALF_M), AOI_MAX_HALF_M)
    half_e = min(max((max(lons) - min(lons)) / 2 * m_lon + AOI_BUFFER_M, AOI_MIN_HALF_M), AOI_MAX_HALF_M)
    return [round(clon - half_e / m_lon, 5), round(clat - half_n / m_lat, 5), round(clon + half_e / m_lon, 5), round(clat + half_n / m_lat, 5)]


def search(bbox: list[float], start: datetime, end: datetime, *, cache_dir: Path) -> list[dict]:
    body = {
        "collections": [COLLECTION], "bbox": bbox, "limit": 100,
        "datetime": f"{start.strftime('%Y-%m-%dT%H:%M:%SZ')}/{end.strftime('%Y-%m-%dT%H:%M:%SZ')}",
    }
    text = fetch_text("sentinel2", "POST", STAC_SEARCH, cache_dir=cache_dir, cache_key=body, ttl_s=SEARCH_TTL_S, json_body=body)
    try:
        items = json.loads(text)["features"]
    except (ValueError, KeyError) as exc:
        raise SourceError(f"sentinel2 search returned no features: {exc}") from exc
    items = [i for i in items if (i["properties"].get("eo:cloud_cover") or 0) < MAX_TILE_CLOUD]
    return sorted(items, key=lambda i: i["properties"]["datetime"], reverse=True)


def read_window(href: str, bounds: tuple, shape: tuple, resampling: str, bands: int = 1) -> np.ndarray:
    """Read one window of a COG over HTTP range requests."""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.windows import from_bounds

    env = {"GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR", "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif", "GDAL_HTTP_TIMEOUT": str(HTTP_TIMEOUT_S), "GDAL_HTTP_MAX_RETRY": "2"}
    last = None
    # A range request can come back short (seen on 2026-10-28: a truncated TCI tile); a fresh read usually works.
    for attempt in range(READ_ATTEMPTS):
        try:
            with rasterio.Env(**env), rasterio.open(href) as ds:
                return ds.read(list(range(1, bands + 1)), window=from_bounds(*bounds, transform=ds.transform), out_shape=(bands, *shape),
                               resampling=getattr(Resampling, resampling), boundless=True, fill_value=0)
        except Exception as exc:  # rasterio raises its own error types for HTTP failures
            last = exc
            log.warning("sentinel2 read attempt %d failed for %s: %s", attempt + 1, os.path.basename(href), str(exc)[:200])
            time.sleep(1.5 * (attempt + 1))
    raise SourceError(f"sentinel2 read failed for {os.path.basename(href)}: {last}") from last


def _epsg(item: dict) -> int:
    p = item["properties"]
    return int(p.get("proj:epsg") or str(p["proj:code"]).split(":")[1])


def grid(bbox: list[float], epsg: int) -> tuple[tuple, tuple]:
    """Area of interest snapped to the 20 m pixel grid in the scene's UTM zone, so every pixel is exactly 0.04 ha."""
    from rasterio.warp import transform_bounds

    b = transform_bounds("EPSG:4326", f"EPSG:{epsg}", *bbox)
    b = (math.floor(b[0] / PIXEL_M) * PIXEL_M, math.floor(b[1] / PIXEL_M) * PIXEL_M,
         math.ceil(b[2] / PIXEL_M) * PIXEL_M, math.ceil(b[3] / PIXEL_M) * PIXEL_M)
    return b, (int((b[3] - b[1]) / PIXEL_M), int((b[2] - b[0]) / PIXEL_M))


def _scale(item: dict, asset: str) -> tuple[float, float]:
    band = (item["assets"][asset].get("raster:bands") or [{}])[0]
    return float(band.get("scale") or 1e-4), float(band.get("offset") or 0.0)


def _summary(item: dict, clear: float, aot: float) -> dict:
    p = item["properties"]
    return {"id": item["id"], "datetime": p["datetime"], "tile_cloud_pct": round(float(p.get("eo:cloud_cover") or 0), 1),
            "aoi_clear_frac": round(clear, 3), "aot_median": round(aot, 3), "mgrs_tile": p.get("s2:mgrs_tile") or p.get("grid:code")}


def _screen(item: dict, bounds: tuple, shape: tuple, reader: Reader) -> tuple[float, float, np.ndarray]:
    scl = reader(item["assets"]["scl"]["href"], bounds, shape, "nearest", 1)[0]
    aot_scale, aot_off = _scale(item, "aot") if "aot" in item["assets"] else (1e-3, 0.0)
    aot = reader(item["assets"]["aot"]["href"], bounds, shape, "nearest", 1)[0].astype("f4") * aot_scale + aot_off if "aot" in item["assets"] else None
    clear = np.isin(scl, CLEAR_CLASSES)
    aot_med = float(np.median(aot[clear])) if aot is not None and clear.any() else 0.0
    return float(clear.mean()), aot_med, clear


def _choose(items: list[dict], bounds: tuple, shape: tuple, reader: Reader) -> tuple[dict, float, float, np.ndarray] | None:
    """Newest clear, haze-free image; if every clear one is hazy, the clearest hazy one."""
    hazy = None
    for item in items[:MAX_CANDIDATES]:
        clear, aot, mask = _screen(item, bounds, shape, reader)
        if clear < MIN_CLEAR:
            continue
        if aot <= HEAVY_HAZE_AOT:
            return item, clear, aot, mask
        if hazy is None or aot < hazy[2]:
            hazy = (item, clear, aot, mask)
    return hazy


def _nbr(item: dict, bounds: tuple, shape: tuple, reader: Reader) -> tuple[np.ndarray, np.ndarray]:
    """NBR and where it means something. Reflectance is DN * scale + offset (offset -0.1 since baseline 04.00),
    so dark pixels go to zero or below and the ratio explodes (dNBR values in the hundreds were seen on
    2026-10-28). Those pixels are returned as not usable rather than counted."""
    ns, no = _scale(item, "nir")
    ss, so = _scale(item, "swir22")
    nir = np.clip(reader(item["assets"]["nir"]["href"], bounds, shape, "average", 1)[0].astype("f4") * ns + no, 0, None)
    swir = np.clip(reader(item["assets"]["swir22"]["href"], bounds, shape, "nearest", 1)[0].astype("f4") * ss + so, 0, None)
    total = nir + swir
    ok = total >= MIN_NBR_SUM
    return np.where(ok, (nir - swir) / np.maximum(total, MIN_NBR_SUM), 0.0), ok


def _rgb(item: dict, bounds: tuple, shape: tuple, reader: Reader) -> np.ndarray:
    # True color at 10 m by default: twice the analysis grid in each direction.
    return np.transpose(reader(item["assets"]["visual"]["href"], bounds, (shape[0] * RGB_SCALE, shape[1] * RGB_SCALE), "bilinear", 3), (1, 2, 0)).astype("u1")


def satellite_pair(points: list[tuple[float, float]], first_seen: datetime, *, now: datetime, cache_dir: Path,
                   reader: Reader = read_window, search_fn=search) -> dict:
    """Find a before and after pair for an incident and measure dNBR over its area of interest.

    Returns a payload. `status` is ok, hazy, no_post_image, or no_pre_image. Only ok carries a pixel_count,
    which is what the evidence gate turns into hectares. Arrays for rendering are under "_render" and are not stored.
    """
    bbox = aoi_bbox(points)
    base = {"aoi_bbox": bbox, "pixel_size_m": PIXEL_M, "method": "dnbr", "dnbr_threshold": DNBR_BURNED, "low_severity_threshold": DNBR_LOW, "min_nbr_sum": MIN_NBR_SUM,
            "first_seen": first_seen.isoformat(), "pixel_count": None, "burned_area_ha": None}
    post_items = [i for i in search_fn(bbox, first_seen, now, cache_dir=cache_dir) if i["properties"]["datetime"] > first_seen.isoformat()[:19]]
    if not post_items:
        return {**base, "status": "no_post_image", "reason": "no Sentinel-2 scene of this area since the first detection yet (revisit is about 5 days)"}
    epsg = _epsg(post_items[0])
    bounds, shape = grid(bbox, epsg)
    post = _choose([i for i in post_items if _epsg(i) == epsg], bounds, shape, reader)
    if post is None:
        return {**base, "status": "no_post_image", "reason": f"{min(len(post_items), MAX_CANDIDATES)} scene(s) since the first detection, none at least {int(MIN_CLEAR * 100)}% clear over the area"}
    pre_end = first_seen - timedelta(days=PRE_GAP_DAYS)
    pre_items = [i for i in search_fn(bbox, pre_end - timedelta(days=PRE_WINDOW_DAYS), pre_end, cache_dir=cache_dir) if _epsg(i) == epsg]
    pre = _choose(pre_items, bounds, shape, reader)
    if pre is None:
        return {**base, "status": "no_pre_image", "post": _summary(post[0], post[1], post[2]),
                "reason": f"no clear Sentinel-2 scene in the {PRE_WINDOW_DAYS} days before the first detection"}

    pre_nbr, pre_ok = _nbr(pre[0], bounds, shape, reader)
    post_nbr, post_ok = _nbr(post[0], bounds, shape, reader)
    valid = pre[3] & post[3] & pre_ok & post_ok
    dnbr = pre_nbr - post_nbr
    burned = valid & (dnbr >= DNBR_BURNED)
    low = valid & (dnbr >= DNBR_LOW) & ~burned
    px_ha = PIXEL_M * PIXEL_M / 10_000
    hazy = max(pre[2], post[2]) > HEAVY_HAZE_AOT
    out = {
        **base,
        "status": "hazy" if hazy else "ok",
        "epsg": epsg,
        "pre": _summary(*pre[:3]),
        "post": _summary(*post[:3]),
        "aoi_pixel_count": int(valid.size),
        "valid_pixel_count": int(valid.sum()),
        "clear_area_ha": round(int(valid.sum()) * px_ha, 2),
        "dnbr_median": round(float(np.median(dnbr[valid])), 3) if valid.any() else None,
        "dnbr_p95": round(float(np.percentile(dnbr[valid], 95)), 3) if valid.any() else None,
        "burned_pixel_count": int(burned.sum()),
        "low_severity_pixel_count": int(low.sum()),
        "low_severity_area_ha": round(int(low.sum()) * px_ha, 2),
        "reason": None,
    }
    if hazy:
        out["reason"] = f"heavy haze (aerosol optical thickness {max(pre[2], post[2]):.2f} > {HEAVY_HAZE_AOT}); smoke lowers NIR, so dNBR may overstate the burn and no hectares are claimed"
    else:
        out["pixel_count"] = int(burned.sum())
        out["burned_area_ha"] = round(int(burned.sum()) * px_ha, 2)
    from rasterio.warp import transform

    # Corners of the analysis grid in lon/lat (top left, top right, bottom right, bottom left), for the map overlay.
    lons, lats = transform(f"EPSG:{epsg}", "EPSG:4326", [bounds[0], bounds[2], bounds[2], bounds[0]], [bounds[3], bounds[3], bounds[1], bounds[1]])
    out["image_corners"] = [[round(a, 6), round(b, 6)] for a, b in zip(lons, lats)]
    out["_render"] = {"pre": pre[0], "post": post[0], "bounds": bounds, "shape": shape, "burned": burned, "valid": valid, "epsg": epsg, "reader": reader}
    return out


def render_pngs(render: dict) -> dict[str, bytes]:
    """True color before and after, the after image with burned pixels tinted red, and a transparent burn mask for the map."""
    import io

    from PIL import Image

    def png(arr) -> bytes:
        buf = io.BytesIO()
        Image.fromarray(arr).save(buf, format="PNG", optimize=True)
        return buf.getvalue()

    pre = _rgb(render["pre"], render["bounds"], render["shape"], render["reader"])
    post = _rgb(render["post"], render["bounds"], render["shape"], render["reader"])
    mask = np.kron(render["burned"], np.ones((RGB_SCALE, RGB_SCALE), dtype=bool))[: post.shape[0], : post.shape[1]]
    over = post.copy()
    over[mask] = (0.45 * over[mask] + 0.55 * np.array([230, 40, 30])).astype("u1")
    mask_rgba = np.zeros((*render["burned"].shape, 4), dtype="u1")
    mask_rgba[render["burned"]] = (230, 40, 30, 210)
    return {"pre": png(pre), "post": png(post), "overlay": png(over), "mask": png(mask_rgba)}

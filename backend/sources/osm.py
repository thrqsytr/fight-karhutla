"""OpenStreetMap via the Overpass API (ODbL).

The public Overpass servers are shared and often at capacity, so queries go per 0.2 degree tile,
are cached for a week, and one tile serves every incident inside it. Endpoints are tried in order;
when all fail, the caller records OSM as unknown instead of stopping triage.
"""

import json
import logging
import math
from pathlib import Path

from ..geo import haversine_m, point_to_polyline_m
from .http import SourceError, fetch_text

log = logging.getLogger(__name__)

OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)
FIXTURE_FILE = "overpass_kaltim.json"
ATTRIBUTION = "Map data (c) OpenStreetMap contributors, ODbL"
CATEGORIES = ("settlement", "road", "river", "lake", "fire_station")

TILE_DEG = 0.2
PAD_DEG = 0.1  # about 11 km: any point in the tile is covered for at least this far
STATION_PAD_DEG = 0.3  # about 33 km for fire stations
SEARCH_RADIUS_M = 10_000
STATION_RADIUS_M = 30_000
TTL_S = 7 * 24 * 3600


def tile_of(lat: float, lon: float) -> tuple[int, int]:
    return (math.floor(lat / TILE_DEG), math.floor(lon / TILE_DEG))


def _bbox(tile: tuple[int, int], pad: float) -> str:
    s, w = tile[0] * TILE_DEG - pad, tile[1] * TILE_DEG - pad
    n, e = (tile[0] + 1) * TILE_DEG + pad, (tile[1] + 1) * TILE_DEG + pad
    return f"{s:.4f},{w:.4f},{n:.4f},{e:.4f}"


def build_tile_query(tile: tuple[int, int]) -> str:
    b, sb = _bbox(tile, PAD_DEG), _bbox(tile, STATION_PAD_DEG)
    return (
        "[out:json][timeout:60];("
        f'node["place"~"^(village|hamlet|town|city)$"]({b});'
        f'way["highway"~"^(primary|secondary|tertiary|trunk)$"]({b});'
        f'way["waterway"="river"]({b});'
        f'way["natural"="water"]({b});'
        f'nwr["amenity"="fire_station"]({sb});'
        f");out geom({b}) tags;"
    )


def _category(tags: dict) -> str | None:
    if "place" in tags:
        return "settlement"
    if "highway" in tags:
        return "road"
    if tags.get("waterway") == "river":
        return "river"
    if tags.get("natural") == "water":
        return "lake"
    if tags.get("amenity") == "fire_station":
        return "fire_station"
    return None


def _distance(lat: float, lon: float, el: dict) -> float | None:
    if "lat" in el:
        return haversine_m(lat, lon, el["lat"], el["lon"])
    geom = [g for g in (el.get("geometry") or []) if g]
    if geom:
        return point_to_polyline_m(lat, lon, [(g["lat"], g["lon"]) for g in geom])
    if "center" in el:
        return haversine_m(lat, lon, el["center"]["lat"], el["center"]["lon"])
    return None


def nearest_assets(payload: dict, lat: float, lon: float) -> dict:
    """Nearest feature per category within the covered radius. Nothing found is None, not omitted."""
    best: dict[str, dict | None] = {c: None for c in CATEGORIES}
    for el in payload.get("elements", []):
        tags = el.get("tags", {})
        cat = _category(tags)
        d = _distance(lat, lon, el)
        if cat is None or d is None:
            continue
        if d > (STATION_RADIUS_M if cat == "fire_station" else SEARCH_RADIUS_M):
            continue
        if best[cat] is None or d < best[cat]["distance_m"]:
            best[cat] = {
                "name": tags.get("name"),
                "kind": tags.get("place") or tags.get("highway") or tags.get("waterway") or tags.get("water") or tags.get("natural") or tags.get("amenity"),
                "distance_m": round(d),
                "osm_id": f"{el['type']}/{el['id']}",
            }
    return best


def fetch_tile(tile: tuple[int, int], *, cache_dir: Path) -> tuple[dict, str]:
    """Return (payload, endpoint). Raises SourceError when every endpoint fails."""
    query = build_tile_query(tile)
    errors = []
    for url in OVERPASS_ENDPOINTS:
        try:
            body = fetch_text("overpass", "POST", url, cache_dir=cache_dir, cache_key={"q": query}, ttl_s=TTL_S, data={"data": query})
            return json.loads(body), url
        except (SourceError, json.JSONDecodeError) as exc:
            log.warning("overpass %s failed for tile %s: %s", url, tile, exc)
            errors.append(f"{url}: {exc}")
    raise SourceError("all Overpass endpoints failed: " + " | ".join(errors))


def get_nearby_assets(lat: float, lon: float, *, cache_dir: Path, fixtures_dir: Path, fixture: bool) -> dict:
    if fixture:
        payload, endpoint, tile = json.loads((fixtures_dir / FIXTURE_FILE).read_text(encoding="utf-8")), "fixture", None
    else:
        tile = tile_of(lat, lon)
        payload, endpoint = fetch_tile(tile, cache_dir=cache_dir)
    return {
        "lat": lat,
        "lon": lon,
        "search_radius_m": SEARCH_RADIUS_M,
        "fire_station_search_radius_m": STATION_RADIUS_M,
        "nearest": nearest_assets(payload, lat, lon),
        "osm_timestamp": payload.get("osm3s", {}).get("timestamp_osm_base"),
        "tile": list(tile) if tile else None,
        "endpoint": endpoint,
    }

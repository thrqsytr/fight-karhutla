"""OpenStreetMap via the Overpass API (ODbL). Results are cached to spare the public server."""

import json
from pathlib import Path

from ..geo import haversine_m, point_to_polyline_m
from .http import fetch_text

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
FIXTURE_FILE = "overpass_kaltim.json"
ATTRIBUTION = "Map data (c) OpenStreetMap contributors, ODbL"
CATEGORIES = ("settlement", "road", "water", "fire_station")


def build_query(lat: float, lon: float, radius_m: int, station_radius_m: int) -> str:
    a = f"(around:{radius_m},{lat:.5f},{lon:.5f})"
    s = f"(around:{station_radius_m},{lat:.5f},{lon:.5f})"
    return (
        "[out:json][timeout:25];("
        f'node["place"~"^(village|hamlet|town|city)$"]{a};'
        f'way["highway"~"^(primary|secondary|tertiary|trunk)$"]{a};'
        f'way["waterway"="river"]{a};'
        f'nwr["amenity"="fire_station"]{s};'
        ");out geom tags 300;"
    )


def _category(tags: dict) -> str | None:
    if "place" in tags:
        return "settlement"
    if "highway" in tags:
        return "road"
    if tags.get("waterway") == "river" or tags.get("natural") == "water":
        return "water"
    if tags.get("amenity") == "fire_station":
        return "fire_station"
    return None


def _distance(lat: float, lon: float, el: dict) -> float | None:
    if "lat" in el:
        return haversine_m(lat, lon, el["lat"], el["lon"])
    geom = el.get("geometry") or []
    if geom:
        return point_to_polyline_m(lat, lon, [(g["lat"], g["lon"]) for g in geom])
    if "center" in el:
        return haversine_m(lat, lon, el["center"]["lat"], el["center"]["lon"])
    return None


def nearest_assets(payload: dict, lat: float, lon: float) -> dict:
    """Nearest feature per category. A category with nothing found is reported as None, not omitted."""
    best: dict[str, dict | None] = {c: None for c in CATEGORIES}
    for el in payload.get("elements", []):
        tags = el.get("tags", {})
        cat = _category(tags)
        d = _distance(lat, lon, el)
        if cat is None or d is None:
            continue
        if best[cat] is None or d < best[cat]["distance_m"]:
            best[cat] = {
                "name": tags.get("name"),
                "kind": tags.get("place") or tags.get("highway") or tags.get("waterway") or tags.get("amenity"),
                "distance_m": round(d),
                "osm_id": f"{el['type']}/{el['id']}",
            }
    return best


def get_nearby_assets(
    lat: float,
    lon: float,
    *,
    radius_m: int = 10_000,
    station_radius_m: int = 30_000,
    cache_dir: Path,
    fixtures_dir: Path,
    fixture: bool,
) -> dict:
    if fixture:
        payload = json.loads((fixtures_dir / FIXTURE_FILE).read_text(encoding="utf-8"))
    else:
        query = build_query(lat, lon, radius_m, station_radius_m)
        payload = json.loads(
            fetch_text(
                "overpass", "POST", OVERPASS_URL, cache_dir=cache_dir, cache_key={"q": query}, ttl_s=7 * 24 * 3600, data={"data": query}
            )
        )
    return {
        "search_radius_m": radius_m,
        "fire_station_search_radius_m": station_radius_m,
        "nearest": nearest_assets(payload, lat, lon),
        "osm_timestamp": payload.get("osm3s", {}).get("timestamp_osm_base"),
    }

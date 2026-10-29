"""OpenStreetMap via the Overpass API (ODbL), plus Overture Maps places from the offline file.

The public Overpass servers are shared and often at capacity, so queries go per 0.2 degree tile,
are cached for a week, and one tile serves every incident inside it. Endpoints are tried in order;
when all fail, the caller records OSM as unknown instead of stopping triage.
"""

import json
import logging
import math
import sqlite3
import struct
import time
import zlib
from contextlib import closing
from pathlib import Path

from ..geo import nearest_on_polyline
from .http import SourceError, fetch_text

log = logging.getLogger(__name__)

OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)
FIXTURE_FILE = "overpass_kaltim.json"
ATTRIBUTION = "Map data (c) OpenStreetMap contributors, ODbL"
OVERTURE_ATTRIBUTION = "Places (c) Overture Maps Foundation and its sources (Meta, Microsoft, Foursquare, AllThePlaces), CDLA Permissive 2.0 / Apache 2.0"
# Water: river, lake (any water body), canal (also drain and ditch, the peatland canals), stream.
# Offices: fire station, police, military (barracks, base, office), government (village to regency offices).
WATER = ("river", "lake", "canal", "stream")
OFFICES = ("fire_station", "police", "military", "government")
CATEGORIES = ("settlement", "road", *WATER, *OFFICES)

TILE_DEG = 0.2
PAD_DEG = 0.1  # about 11 km: any point in the tile is covered for at least this far
STATION_PAD_DEG = 0.3  # about 33 km for fire stations
SEARCH_RADIUS_M = 10_000
STATION_RADIUS_M = 30_000  # every office category
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
        f'way["waterway"~"^(river|canal|stream|drain|ditch)$"]({b});'
        f'way["natural"="water"]({b});'
        f");out geom({b}) tags;("
        # Offices are searched farther than the lines are cut, so they come back as centre points.
        f'nwr["amenity"~"^(fire_station|police|townhall)$"]({sb});'
        f'nwr["office"="government"]({sb});'
        f'nwr["landuse"="military"]({sb});'
        f'nwr["military"~"^(barracks|base|office)$"]({sb});'
        f");out center tags;"
    )


WATERWAY = {"river": "river", "canal": "canal", "drain": "canal", "ditch": "canal", "stream": "stream"}


def _category(tags: dict) -> str | None:
    """Same rules as tools/build_osm_fallback.py. Offline elements carry their category in _cat."""
    if tags.get("_cat"):
        return tags["_cat"]
    amenity = tags.get("amenity")
    if amenity == "fire_station":
        return "fire_station"
    if amenity == "police":
        return "police"
    if tags.get("landuse") == "military" or tags.get("military") in ("barracks", "base", "office"):
        return "military"
    if amenity == "townhall" or tags.get("office") == "government":
        return "government"
    if "place" in tags:
        return "settlement"
    if "highway" in tags:
        return "road"
    if tags.get("waterway") in WATERWAY:
        return WATERWAY[tags["waterway"]]
    if tags.get("natural") == "water":
        return "lake"
    return None


def _kind(tags: dict) -> str | None:
    for k in ("_kind", "place", "highway", "waterway", "water", "military", "office", "amenity", "landuse", "natural"):
        if tags.get(k):
            return tags[k]
    return None


def _nearest(lat: float, lon: float, el: dict) -> tuple[float, tuple[float, float]] | None:
    """Distance to the element and its closest point."""
    if "lat" in el:
        return nearest_on_polyline(lat, lon, [(el["lat"], el["lon"])])
    geom = [g for g in (el.get("geometry") or []) if g]
    if geom:
        return nearest_on_polyline(lat, lon, [(g["lat"], g["lon"]) for g in geom])
    if "center" in el:
        return nearest_on_polyline(lat, lon, [(el["center"]["lat"], el["center"]["lon"])])
    return None


def nearest_assets(payload: dict, lat: float, lon: float) -> dict:
    """Nearest feature per category within the covered radius. Nothing found is None, not omitted."""
    best: dict[str, dict | None] = {c: None for c in CATEGORIES}
    for el in payload.get("elements", []):
        tags = el.get("tags", {})
        cat = _category(tags)
        near = _nearest(lat, lon, el)
        if cat is None or near is None:
            continue
        d, (plat, plon) = near
        if d > (STATION_RADIUS_M if cat in OFFICES else SEARCH_RADIUS_M):
            continue
        if best[cat] is None or d < best[cat]["distance_m"]:
            overture = el["type"] == "overture"
            best[cat] = {
                "name": " ".join(tags["name"].split()) if tags.get("name") else None,  # Overture names can carry double spaces
                "kind": _kind(tags),
                "distance_m": round(d),
                # The closest point, so the map can draw a line to it.
                "at": [round(plat, 6), round(plon, 6)],
                "source": "overture" if overture else "osm",
                **({"overture_id": el["id"]} if overture else {"osm_id": f"{el['type']}/{el['id']}"}),
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


# Offline fallback: a Geofabrik extract reduced by tools/build_osm_fallback.py. After every Overpass endpoint
# fails, lookups go straight to it for a while, so each incident does not wait on servers that are down.
OVERPASS_DOWN_S = 30 * 60
_overpass_down_until = 0.0


def overpass_down() -> bool:
    return time.time() < _overpass_down_until


def fallback_info(path: Path | None) -> dict | None:
    if not path or not path.exists():
        return None
    try:
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as conn:
            return dict(conn.execute("SELECT key, value FROM meta").fetchall())
    except sqlite3.Error:
        return None


def ensure_fallback(path: Path, url: str) -> bool:
    """Download the offline extract once. It is written next to the target and renamed only after it opens
    as a valid fallback database, so a broken download never replaces a good file."""
    import httpx

    if path.exists() or not url:
        return path.exists()
    part = path.with_suffix(".part")
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=60, headers={"User-Agent": "fight-karhutla"}) as r:
            r.raise_for_status()
            with part.open("wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
        if fallback_info(part) is None:
            raise SourceError("downloaded file is not an OSM fallback database")
        part.replace(path)
        log.info("offline OSM extract ready: %s (%.0f MB)", path, path.stat().st_size / 1e6)
        for old in path.parent.glob("osm_fallback*.sqlite"):  # earlier releases, now replaced
            if old != path:
                old.unlink(missing_ok=True)
                log.info("removed the earlier offline extract %s", old.name)
        return True
    except (httpx.HTTPError, SourceError, OSError) as exc:
        log.warning("offline OSM extract download failed: %s", exc)
        part.unlink(missing_ok=True)
        return False


def _unpack(blob: bytes) -> list[tuple[float, float]]:
    raw = zlib.decompress(blob)
    return [(a / 1e6, b / 1e6) for a, b in struct.iter_unpack("<ii", raw)]


def fallback_payload(path: Path, lat: float, lon: float, *, overture_only: bool = False) -> dict:
    """Overpass shaped elements around a point, read from the offline extract. Overture places (ids starting
    with overture/) live in the same file; with overture_only they are added to a live Overpass answer."""
    t0, t1 = tile_of(lat, lon)
    reach = math.ceil(STATION_PAD_DEG / TILE_DEG)
    elements = []
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as conn:
        rows = conn.execute(
            """SELECT DISTINCT f.id, f.cat, f.kind, f.name, f.geom FROM tiles t JOIN features f ON f.id = t.fid
               WHERE t.tlat BETWEEN ? AND ? AND t.tlon BETWEEN ? AND ?""" + (" AND f.id LIKE 'overture/%'" if overture_only else ""),
            (t0 - reach, t0 + reach, t1 - reach, t1 + reach),
        ).fetchall()
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    for fid, cat, kind, name, blob in rows:
        if cat not in CATEGORIES:
            continue
        coords = _unpack(blob)  # the radius per category is applied later, in nearest_assets
        tags = {"_cat": cat, "_kind": kind, **({"name": name} if name else {})}
        typ, num = fid.split("/", 1)
        num = num if typ == "overture" else int(num)
        if len(coords) == 1:
            elements.append({"type": typ, "id": num, "lat": coords[0][0], "lon": coords[0][1], "tags": tags})
        else:
            elements.append({"type": typ, "id": num, "geometry": [{"lat": a, "lon": b} for a, b in coords], "tags": tags})
    return {"elements": elements, "osm3s": {"timestamp_osm_base": meta.get("source")}, "_fallback": meta}


def get_nearby_assets(lat: float, lon: float, *, cache_dir: Path, fixtures_dir: Path, fixture: bool, fallback: Path | None = None) -> dict:
    global _overpass_down_until
    if fixture:
        payload, endpoint, tile = json.loads((fixtures_dir / FIXTURE_FILE).read_text(encoding="utf-8")), "fixture", None
    else:
        tile = tile_of(lat, lon)
        have_fallback = fallback is not None and fallback.exists()
        if have_fallback and overpass_down():
            payload, endpoint = fallback_payload(fallback, lat, lon), "offline extract"
        else:
            try:
                payload, endpoint = fetch_tile(tile, cache_dir=cache_dir)
                if have_fallback:  # Overture places come only from the offline file
                    extra = fallback_payload(fallback, lat, lon, overture_only=True)
                    payload = {**payload, "elements": payload.get("elements", []) + extra["elements"], "_fallback": extra["_fallback"]}
            except SourceError:
                if not have_fallback:
                    raise
                _overpass_down_until = time.time() + OVERPASS_DOWN_S
                log.warning("overpass down; using the offline extract for %d min", OVERPASS_DOWN_S // 60)
                payload, endpoint = fallback_payload(fallback, lat, lon), "offline extract"
    return {
        "lat": lat,
        "lon": lon,
        "search_radius_m": SEARCH_RADIUS_M,
        "fire_station_search_radius_m": STATION_RADIUS_M,
        "nearest": nearest_assets(payload, lat, lon),
        "osm_timestamp": payload.get("osm3s", {}).get("timestamp_osm_base"),
        "tile": list(tile) if tile else None,
        "endpoint": endpoint,
        "offline_source": (payload.get("_fallback") or {}).get("source"),
        "overture_release": (payload.get("_fallback") or {}).get("overture_release"),
    }

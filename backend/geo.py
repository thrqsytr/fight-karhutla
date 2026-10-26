"""Deterministic geometry. Every distance, cluster, and area in the system comes from here, never from a model."""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

EARTH_RADIUS_M = 6_371_008.8

# Generous bounding box of Indonesian territory, used only as a sanity gate.
INDONESIA_BBOX = (94.5, -11.5, 141.5, 6.5)  # west, south, east, north


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def point_to_segment_m(lat: float, lon: float, a: tuple[float, float], b: tuple[float, float]) -> float:
    """Distance from a point to a segment, using a local equirectangular projection (accurate at km scale)."""
    k = math.cos(math.radians(lat))
    ax, ay = (a[1] - lon) * k, a[0] - lat
    bx, by = (b[1] - lon) * k, b[0] - lat
    dx, dy = bx - ax, by - ay
    seg2 = dx * dx + dy * dy
    t = 0.0 if seg2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / seg2))
    px, py = ax + t * dx, ay + t * dy
    return math.hypot(px, py) * math.radians(1) * EARTH_RADIUS_M


def point_to_polyline_m(lat: float, lon: float, line: list[tuple[float, float]]) -> float:
    if len(line) == 1:
        return haversine_m(lat, lon, line[0][0], line[0][1])
    return min(point_to_segment_m(lat, lon, line[i], line[i + 1]) for i in range(len(line) - 1))


def in_indonesia(lat: float, lon: float) -> bool:
    w, s, e, n = INDONESIA_BBOX
    return w <= lon <= e and s <= lat <= n


def pixel_area_ha(pixel_count: int, pixel_size_m: float) -> float:
    """Burned or masked area from a pixel count. 1 ha = 10,000 m2."""
    if pixel_count < 0 or pixel_size_m <= 0:
        raise ValueError("pixel_count must be >= 0 and pixel_size_m > 0")
    return pixel_count * pixel_size_m * pixel_size_m / 10_000


@dataclass(frozen=True)
class Point:
    id: str
    lat: float
    lon: float
    at: datetime


def cluster_points(points: list[Point], radius_m: float = 2000, window: timedelta = timedelta(hours=24)) -> list[list[Point]]:
    """Single-linkage clustering: two points join when within radius_m and window of each other.

    Output order is deterministic: clusters sorted by earliest point, members sorted by time then id.
    """
    pts = sorted(points, key=lambda p: (p.at, p.id))
    parent = list(range(len(pts)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            if pts[j].at - pts[i].at > window:
                break
            if haversine_m(pts[i].lat, pts[i].lon, pts[j].lat, pts[j].lon) <= radius_m:
                parent[find(j)] = find(i)

    groups: dict[int, list[Point]] = {}
    for i, p in enumerate(pts):
        groups.setdefault(find(i), []).append(p)
    return sorted(groups.values(), key=lambda g: (g[0].at, g[0].id))


def centroid(points: list[Point]) -> tuple[float, float]:
    return (sum(p.lat for p in points) / len(points), sum(p.lon for p in points) / len(points))

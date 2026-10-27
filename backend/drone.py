"""Drone survey: mission planning and georeferencing for nadir frames. SIMULATION ONLY in this build.

The interface is shaped for a real mapping drone (waypoints in lon/lat, altitude above ground, camera
intrinsics, per-frame telemetry), so a MAVLink mission could replace the simulator later. Ground size
comes from geometry, never from a model: GSD = altitude x sensor width / (focal length x image width).
"""

import math
from dataclasses import asdict, dataclass

MAX_ALTITUDE_M = 120  # Indonesian small drone rules and most civil aviation rules cap at about 120 m AGL
MAX_SURVEY_KM2 = 2.0
MAX_FRAMES = 80


@dataclass(frozen=True)
class Camera:
    """Defaults match a 1 inch mapping camera (13.2 x 8.8 mm sensor, 8.8 mm lens), rendered at 1024 x 683."""
    sensor_w_mm: float = 13.2
    sensor_h_mm: float = 8.8
    focal_mm: float = 8.8
    image_w_px: int = 1024
    image_h_px: int = 683

    def gsd_m(self, altitude_m: float) -> float:
        return altitude_m * self.sensor_w_mm / (self.focal_mm * self.image_w_px)

    def footprint_m(self, altitude_m: float) -> tuple[float, float]:
        return altitude_m * self.sensor_w_mm / self.focal_mm, altitude_m * self.sensor_h_mm / self.focal_mm

    def hfov_deg(self) -> float:
        return math.degrees(2 * math.atan(self.sensor_w_mm / (2 * self.focal_mm)))


def utm_epsg(lat: float, lon: float) -> int:
    zone = int((lon + 180) // 6) + 1
    return (32600 if lat >= 0 else 32700) + zone


def _to_utm(epsg: int, lons: list[float], lats: list[float]) -> tuple[list[float], list[float]]:
    from rasterio.warp import transform

    return transform("EPSG:4326", f"EPSG:{epsg}", lons, lats)


def _to_lonlat(epsg: int, xs: list[float], ys: list[float]) -> tuple[list[float], list[float]]:
    from rasterio.warp import transform

    return transform(f"EPSG:{epsg}", "EPSG:4326", xs, ys)


def plan_survey(bbox: list[float], *, altitude_m: float = MAX_ALTITUDE_M, camera: Camera = Camera(),
                side_overlap: float = 0.1, front_overlap: float = 0.1) -> dict:
    """Lawnmower mission over [west, south, east, north]. Lines run north-south, alternating direction.

    Raises ValueError when the request breaks a limit, so the agent gets a reason instead of a mission.
    """
    if not 0 < altitude_m <= MAX_ALTITUDE_M:
        raise ValueError(f"altitude must be above 0 and at most {MAX_ALTITUDE_M} m")
    w, s, e, n = bbox
    epsg = utm_epsg((s + n) / 2, (w + e) / 2)
    (x0, x1), (y0, y1) = [list(v) for v in _to_utm(epsg, [w, e], [s, n])]
    area_km2 = (x1 - x0) * (y1 - y0) / 1e6
    if area_km2 > MAX_SURVEY_KM2:
        raise ValueError(f"survey area {area_km2:.2f} km2 is above the {MAX_SURVEY_KM2} km2 limit")
    fw, fh = camera.footprint_m(altitude_m)
    step_x, step_y = fw * (1 - side_overlap), fh * (1 - front_overlap)
    cols = max(1, math.ceil((x1 - x0 - fw) / step_x) + 1)
    rows = max(1, math.ceil((y1 - y0 - fh) / step_y) + 1)
    if cols * rows > MAX_FRAMES:
        raise ValueError(f"{cols * rows} frames is above the {MAX_FRAMES} frame limit; raise the altitude or shrink the area")
    xs, ys, heading = [], [], []
    for c in range(cols):
        cx = x0 + fw / 2 + c * step_x
        order = range(rows) if c % 2 == 0 else range(rows - 1, -1, -1)
        for r in order:
            xs.append(cx)
            ys.append(y0 + fh / 2 + r * step_y)
            heading.append(0.0 if c % 2 == 0 else 180.0)
    lons, lats = _to_lonlat(epsg, xs, ys)
    waypoints = [{"seq": i, "lat": round(la, 7), "lon": round(lo, 7), "utm_x": round(x, 2), "utm_y": round(y, 2), "heading_deg": h}
                 for i, (la, lo, x, y, h) in enumerate(zip(lats, lons, xs, ys, heading))]
    return {
        "bbox": bbox, "epsg": epsg, "altitude_agl_m": altitude_m, "camera": asdict(camera), "pattern": "lawnmower_ns",
        "gsd_m": round(camera.gsd_m(altitude_m), 4), "footprint_m": [round(fw, 2), round(fh, 2)],
        "side_overlap": side_overlap, "front_overlap": front_overlap, "lines": cols, "frames_per_line": rows,
        "area_km2": round(area_km2, 3), "waypoints": waypoints,
    }


def frame_footprint(frame: dict, camera: Camera) -> list[tuple[float, float]]:
    """Ground corners (UTM) of a nadir frame from its telemetry: top left, top right, bottom right, bottom left in image order."""
    fw, fh = camera.footprint_m(frame["altitude_agl_m"])
    h = math.radians(frame["heading_deg"])
    # Image up points along the heading; image right is 90 degrees clockwise of it.
    ux, uy = math.sin(h), math.cos(h)
    rx, ry = math.cos(h), -math.sin(h)
    cx, cy = frame["utm_x"], frame["utm_y"]
    pts = []
    for sx, sy in ((-1, 1), (1, 1), (1, -1), (-1, -1)):
        pts.append((cx + sx * fw / 2 * rx + sy * fh / 2 * ux, cy + sx * fw / 2 * ry + sy * fh / 2 * uy))
    return pts


def pixel_to_utm(frame: dict, camera: Camera, col: float, row: float) -> tuple[float, float]:
    """Ground position of an image pixel (column, row from top left) for a nadir frame on flat ground."""
    g = camera.gsd_m(frame["altitude_agl_m"])
    dx = (col + 0.5 - camera.image_w_px / 2) * g
    dy = (camera.image_h_px / 2 - row - 0.5) * g
    h = math.radians(frame["heading_deg"])
    return (frame["utm_x"] + dx * math.cos(h) + dy * math.sin(h), frame["utm_y"] - dx * math.sin(h) + dy * math.cos(h))


def ground_cells(frame: dict, camera: Camera, mask, cell_m: float, step: int = 3) -> set[tuple[int, int]]:
    """Ground grid cells (UTM / cell_m). Use cell_m >= 1.0 with step 3, so every cell gets a sample covered by mask pixels of one nadir frame.

    Sampling every `step` pixels keeps it fast; with GSD 0.18 m and step 3 a sample lands every 0.53 m,
    so 1 m cells are covered with no gaps. Overlapping frames hit the same cells, so the union never double counts.
    """
    import numpy as np

    rows, cols = np.nonzero(mask[::step, ::step])
    if rows.size == 0:
        return set()
    g = camera.gsd_m(frame["altitude_agl_m"])
    dx = (cols * step + step / 2 - camera.image_w_px / 2) * g
    dy = (camera.image_h_px / 2 - rows * step - step / 2) * g
    h = math.radians(frame["heading_deg"])
    x = frame["utm_x"] + dx * math.cos(h) + dy * math.sin(h)
    y = frame["utm_y"] - dx * math.sin(h) + dy * math.cos(h)
    return set(zip((x // cell_m).astype(int).tolist(), (y // cell_m).astype(int).tolist()))

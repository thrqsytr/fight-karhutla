"""Plan the simulated survey with the same planner the app uses, and add local coordinates for Blender.

Usage (repo root): .venv/Scripts/python sim/plan_mission.py SIM_DIR X0 Y0_FROM_TOP X1 Y1_FROM_TOP WIND_FROM_DEG WIND_MS WIND_EVIDENCE
Box edges are metres from the terrain's west and top edges.
"""

import json
import sys
from pathlib import Path

from rasterio.warp import transform

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import drone  # noqa: E402

d = Path(sys.argv[1])
x0, ytop0, x1, ytop1 = map(float, sys.argv[2:6])
t = json.loads((d / "terrain.json").read_text(encoding="utf-8"))
b, epsg = t["utm_bounds"], t["epsg"]
lons, lats = transform(f"EPSG:{epsg}", "EPSG:4326", [b[0] + x0, b[0] + x1], [b[3] - ytop1, b[3] - ytop0])
plan = drone.plan_survey([round(lons[0], 6), round(lats[0], 6), round(lons[1], 6), round(lats[1], 6)])
assert plan["epsg"] == epsg, "terrain and mission must share a UTM zone"
for w in plan["waypoints"]:
    w["local_x"], w["local_y"] = round(w["utm_x"] - b[0], 2), round(w["utm_y"] - b[1], 2)
(bx0, bx1), (by0, by1) = transform("EPSG:4326", f"EPSG:{epsg}", [plan["bbox"][0], plan["bbox"][2]], [plan["bbox"][1], plan["bbox"][3]])
plan["local_box"] = [round(bx0 - b[0], 1), round(by0 - b[1], 1), round(bx1 - b[0], 1), round(by1 - b[1], 1)]
xs, ys = transform("EPSG:4326", f"EPSG:{epsg}", [h["lon"] for h in t["hotspots"]], [h["lat"] for h in t["hotspots"]])
for h, x, y in zip(t["hotspots"], xs, ys):
    h["local_x"], h["local_y"] = round(x - b[0], 1), round(y - b[1], 1)
(d / "terrain.json").write_text(json.dumps(t, indent=1), encoding="utf-8")
plan["incident_id"] = t["incident_id"]
plan["simulation"] = True
plan["wind"] = {"from_deg": float(sys.argv[6]), "speed_ms": float(sys.argv[7]), "evidence": sys.argv[8]}
(d / "mission.json").write_text(json.dumps(plan, indent=1), encoding="utf-8")
print(plan["local_box"], plan["lines"], "x", plan["frames_per_line"], "frames, GSD", plan["gsd_m"])

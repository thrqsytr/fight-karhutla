"""Package a rendered mission for the app: telemetry per frame, truth burned area, and a mosaic preview.

Usage (repo root): .venv/Scripts/python sim/package_mission.py SIM_DIR OUT_DIR
Writes OUT_DIR/mission.json (plan + telemetry + truth). Truth comes from the truth pass: the burned share
of the rendered ground, so the measured error of SAM later is against what the camera actually saw.
"""

import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import drone  # noqa: E402

SPEED_MS = 8.0
START = datetime(2026, 10, 28, 2, 30, tzinfo=timezone.utc)  # 10:30 WITA, near the Sentinel-2 and drone light


def main(sim_dir: Path, out: Path) -> None:
    plan = json.loads((sim_dir / "mission.json").read_text(encoding="utf-8"))
    terrain = json.loads((sim_dir / "terrain.json").read_text(encoding="utf-8"))
    cam = drone.Camera(**plan["camera"])
    t = START
    prev = None
    frames = []
    for wp in plan["waypoints"]:
        if prev:
            t += timedelta(seconds=math.dist((prev["utm_x"], prev["utm_y"]), (wp["utm_x"], wp["utm_y"])) / SPEED_MS)
        prev = wp
        name = f"F{wp['seq']:03d}.png"
        truth = np.array(Image.open(out / "truth" / name).convert("L")) > 127
        frames.append({
            "seq": wp["seq"], "file": name, "captured_at": t.isoformat(), "lat": wp["lat"], "lon": wp["lon"],
            "utm_x": wp["utm_x"], "utm_y": wp["utm_y"], "altitude_agl_m": plan["altitude_agl_m"], "heading_deg": wp["heading_deg"],
            "gimbal_pitch_deg": -90.0,
            "truth_burned_px": int(truth.sum()), "truth_burned_fraction": round(float(truth.mean()), 4),
        })
    gsd = cam.gsd_m(plan["altitude_agl_m"])
    package = {
        "simulation": True, "label": "SIMULASI", "incident_id": plan["incident_id"], "mission_id": f"SIM-{plan['incident_id']}-01",
        "plan": {k: v for k, v in plan.items() if k not in ("waypoints",)},
        "terrain": {"post_scene": terrain["post_scene"], "pre_scene": terrain["pre_scene"], "attribution": terrain["attribution"],
                    "burned_ha_sentinel2": terrain["burned_ha_20m"]},
        "renderer": "Blender 5.2 EEVEE, sim/build_scene.py", "frames": frames,
        "truth_note": "Truth is the burned ground rendered in each frame (planimetric, before canopy and smoke). Synthetic, not real-world accuracy.",
        "gsd_m": round(gsd, 4),
    }
    (out / "mission.json").write_text(json.dumps(package, indent=1), encoding="utf-8")

    # Mosaic check: place each frame at its footprint on a coarse ground grid.
    xs = [f["utm_x"] for f in frames]
    ys = [f["utm_y"] for f in frames]
    fw, fh = cam.footprint_m(plan["altitude_agl_m"])
    scale = 0.5  # metres per mosaic pixel = 2
    W = int((max(xs) - min(xs) + fw) * scale) + 2
    H = int((max(ys) - min(ys) + fh) * scale) + 2
    mosaic = Image.new("RGB", (W, H))
    for f in frames:
        im = Image.open(out / "frames" / f["file"]).convert("RGB")
        if f["heading_deg"] == 180.0:
            im = im.rotate(180)
        im = im.resize((int(fw * scale), int(fh * scale)))
        mosaic.paste(im, (int((f["utm_x"] - min(xs)) * scale), int((max(ys) - f["utm_y"]) * scale)))
    mosaic.save(out / "mosaic_preview.jpg", quality=80)
    print(len(frames), "frames; truth burned share", round(float(np.mean([f["truth_burned_fraction"] for f in frames])), 3), "; mosaic", W, "x", H)


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))

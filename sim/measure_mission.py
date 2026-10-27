"""Measure a simulated survey: SAM 3.1 on the frames Glimmer uplinked, masks georeferenced from telemetry,
hectares counted on a 1 m ground grid by code, and compared with the Blender truth.

Usage (repo root, MODEL_API_KEY set): .venv/Scripts/python sim/measure_mission.py MISSION_DIR [CONCEPT]
Writes MISSION_DIR/survey.json and MISSION_DIR/sam/<frame>.png. Costs $0.0025 per uplinked frame (SAM list price).

Concept: "bare ground". On these nadir frames SAM returned nothing for "burned ground", "ash", or "forest",
while "bare ground" matched the truth at IoU 0.92 on F002 (2026-10-28). It measures unvegetated ground; in
this survey box that is the burn scar (Sentinel-2 dNBR), so it stands for burned area here only.

SAM returns no mask on frames that are burned edge to edge (no boundary to segment; "ground", "soil",
"burnt area", "scorched earth", and "dead tree" also gave nothing on F014). Those frames count as not
measured: the area and its error cover measured frames only, and the share of measured frames is reported.
--reuse reads masks saved by an earlier run instead of calling SAM again.
"""

import asyncio
import io
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import drone, meta_client  # noqa: E402
from backend.config import get_settings  # noqa: E402

CELL_M = 1.0


async def main(mdir: Path, concept: str) -> None:
    settings = get_settings()
    meter = meta_client.Meter()
    mission = json.loads((mdir / "mission.json").read_text(encoding="utf-8"))
    glimmer = json.loads((mdir / "glimmer.json").read_text(encoding="utf-8"))
    cam = drone.Camera(**mission["plan"]["camera"])
    uplink = {r["file"] for r in glimmer["results"] if r["decision"] and r["decision"]["uplink"]}
    (mdir / "sam").mkdir(exist_ok=True)
    reuse = "--reuse" in sys.argv
    previous_cost = json.loads((mdir / "survey.json").read_text(encoding="utf-8")).get("cost_usd") if reuse else None
    previous = {r["file"]: r for r in json.loads((mdir / "survey.json").read_text(encoding="utf-8"))["frames"]} if reuse else {}
    sam_cells, truth_up_cells, truth_all_cells, truth_measured_cells = set(), set(), set(), set()
    per_frame = []
    for f in mission["frames"]:
        truth = np.array(Image.open(mdir / "truth" / f["file"].replace(".jpg", ".png")).convert("L")) > 127
        tcells = drone.ground_cells(f, cam, truth, CELL_M)
        truth_all_cells |= tcells
        row = {"seq": f["seq"], "file": f["file"], "uplinked": f["file"] in uplink, "truth_fraction": f["truth_burned_fraction"]}
        if row["uplinked"]:
            truth_up_cells |= tcells
            saved = mdir / "sam" / f["file"].replace(".jpg", ".png")
            if reuse and saved.exists() and f["file"] in previous:
                mask = np.array(Image.open(saved).convert("L")) > 127
                objects = previous[f["file"]]["sam_objects"]
            else:
                r = await meta_client.segment(settings, (mdir / "frames" / f["file"]).read_bytes(), concept, meter, want_mask=True)
                mask, objects = r["mask"], len(r["objects"])
                png = io.BytesIO()
                Image.fromarray((mask * 255).astype("u1")).convert("1").save(png, format="PNG", optimize=True)
                saved.write_bytes(png.getvalue())
            measured = objects > 0
            if measured:
                sam_cells |= drone.ground_cells(f, cam, mask, CELL_M)
                truth_measured_cells |= tcells
            inter, union = int((mask & truth).sum()), int((mask | truth).sum())
            row.update({"sam_objects": objects, "measured": measured, "sam_fraction": round(float(mask.mean()), 4),
                        "iou": round(inter / union, 3) if measured and union else None})
        per_frame.append(row)
        print(row)

    ha = lambda cells: round(len(cells) * CELL_M * CELL_M / 10_000, 2)
    sam_ha, truth_ha = ha(sam_cells), ha(truth_measured_cells)
    measured_n = sum(1 for r in per_frame if r.get("measured"))
    survey = {
        "simulation": True, "label": "SIMULASI", "mission_id": mission["mission_id"], "incident_id": mission["incident_id"],
        "concept": concept, "cell_m": CELL_M, "gsd_m": mission["gsd_m"],
        "frames_total": len(mission["frames"]), "frames_uplinked": len(uplink), "frames_dropped": len(mission["frames"]) - len(uplink),
        "frames_measured": measured_n, "frames_not_measured": len(uplink) - measured_n,
        "sam_burned_ha": sam_ha, "sam_cell_count": len(sam_cells), "truth_burned_ha_measured": truth_ha,
        "truth_burned_ha_uplinked": ha(truth_up_cells), "truth_burned_ha_all_frames": ha(truth_all_cells),
        "missed_by_filter_ha": round(ha(truth_all_cells) - ha(truth_up_cells), 2),
        "area_error_pct": round((sam_ha - truth_ha) / truth_ha * 100, 1) if truth_ha else None,
        "median_iou": round(float(np.median(ious)), 3) if (ious := [r["iou"] for r in per_frame if r.get("iou") is not None]) else None,
        "cost_usd": previous_cost if reuse else round(sum(c["cost_usd"] for c in meter.calls), 4), "frames": per_frame,
        "note": "Synthetic frames rendered in Blender over the real Sentinel-2 burn mask. The error is against the render truth, not real-world accuracy.",
    }
    (mdir / "survey.json").write_text(json.dumps(survey, indent=1), encoding="utf-8")
    print({k: v for k, v in survey.items() if k != "frames"})


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    asyncio.run(main(Path(args[0]), args[1] if len(args) > 1 else "bare ground"))

"""Simulated drone surveys, replayed from files under replay/drone/<mission>/. SIMULATION ONLY.

A mission folder holds the plan and telemetry (mission.json), Glimmer's uplink decisions (glimmer.json),
SAM measurements (survey.json), and the frames, SAM masks, and Blender truth masks. The results are shown
next to the real incident but never become its evidence: a triage brief can not quote a simulated hectare.
"""

import json
import re
from functools import lru_cache
from pathlib import Path

from . import drone

KINDS = {"frames": ".jpg", "sam": ".png", "truth": ".png"}
FILE_NAME = re.compile(r"^F\d{3}\.(jpg|png)$")


def _dirs(replay_dir: Path) -> list[Path]:
    root = replay_dir / "drone"
    return sorted(p for p in root.iterdir() if (p / "mission.json").exists()) if root.exists() else []


@lru_cache(maxsize=16)
def _load(path: str, mtime: float) -> dict:
    d = Path(path)
    mission = json.loads((d / "mission.json").read_text(encoding="utf-8"))
    glimmer = json.loads((d / "glimmer.json").read_text(encoding="utf-8")) if (d / "glimmer.json").exists() else None
    survey = json.loads((d / "survey.json").read_text(encoding="utf-8")) if (d / "survey.json").exists() else None
    cam = drone.Camera(**mission["plan"]["camera"])
    epsg = mission["plan"]["epsg"]
    decisions = {r["file"]: r for r in (glimmer or {}).get("results", [])}
    measured = {r["file"]: r for r in (survey or {}).get("frames", [])}
    frames = []
    for f in mission["frames"]:
        corners = drone.frame_footprint(f, cam)
        lons, lats = drone._to_lonlat(epsg, [c[0] for c in corners], [c[1] for c in corners])
        g = decisions.get(f["file"], {})
        frames.append({
            **{k: f[k] for k in ("seq", "file", "captured_at", "lat", "lon", "altitude_agl_m", "heading_deg", "truth_burned_fraction")},
            "footprint": [[round(a, 7), round(b, 7)] for a, b in zip(lons, lats)],
            "glimmer": g.get("decision"), "glimmer_seconds": g.get("seconds"),
            "sam_fraction": measured.get(f["file"], {}).get("sam_fraction"), "iou": measured.get(f["file"], {}).get("iou"),
        })
    return {
        "mission_id": mission["mission_id"], "incident_id": mission["incident_id"], "simulation": True, "label": "SIMULASI",
        "folder": d.name, "plan": mission["plan"], "terrain": mission["terrain"], "renderer": mission["renderer"],
        "gsd_m": mission["gsd_m"], "truth_note": mission["truth_note"], "frames": frames,
        "glimmer": {k: v for k, v in glimmer.items() if k != "results"} if glimmer else None,
        "survey": {k: v for k, v in survey.items() if k != "frames"} if survey else None,
    }


def missions(replay_dir: Path) -> list[dict]:
    out = []
    for d in _dirs(replay_dir):
        m = _load(str(d), (d / "mission.json").stat().st_mtime + sum((d / n).stat().st_mtime for n in ("glimmer.json", "survey.json") if (d / n).exists()))
        out.append(m)
    return out


def summary(m: dict) -> dict:
    return {k: m[k] for k in ("mission_id", "incident_id", "simulation", "label")} | {
        "frames": len(m["frames"]), "uplinked": (m["glimmer"] or {}).get("uplinked"), "sam_burned_ha": (m["survey"] or {}).get("sam_burned_ha")}


def find(replay_dir: Path, mission_id: str) -> dict | None:
    return next((m for m in missions(replay_dir) if m["mission_id"] == mission_id), None)


def file_path(replay_dir: Path, mission_id: str, kind: str, name: str) -> Path | None:
    m = find(replay_dir, mission_id)
    if m is None or kind not in KINDS or not FILE_NAME.match(name) or not name.endswith(KINDS[kind]):
        return None
    p = replay_dir / "drone" / m["folder"] / kind / name
    return p if p.exists() else None

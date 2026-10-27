"""Render mission frames and truth masks inside Blender. Usage from MCP: run_path, then render(first, last, out_dir)."""

import json
import time
from pathlib import Path

import bpy


def _truth(on: bool) -> None:
    scene = bpy.context.scene
    scene.view_layers[0]["mask_mode"] = 1.0 if on else 0.0
    for ob in scene.objects:
        if ob.get("truth_hide"):
            ob.hide_render = on
    scene.view_settings.view_transform = "Standard"
    bg = next(n for n in scene.world.node_tree.nodes if n.type == "BACKGROUND")
    bg.inputs["Strength"].default_value = 0.0 if on else 0.8
    bpy.data.objects["Sun"].hide_render = on


def render(build_ns: dict, first: int, last: int, out_dir: str) -> list[dict]:
    terrain, mission = build_ns["load"]()
    cam = bpy.data.objects["DroneCam"]
    out = Path(out_dir)
    (out / "frames").mkdir(parents=True, exist_ok=True)
    (out / "truth").mkdir(parents=True, exist_ok=True)
    scene = bpy.context.scene
    done = []
    for wp in mission["waypoints"][first:last]:
        build_ns["place_camera"](cam, wp, mission["altitude_agl_m"])
        name = f"F{wp['seq']:03d}.png"
        t = time.time()
        _truth(False)
        scene.render.filepath = str(out / "frames" / name)
        bpy.ops.render.render(write_still=True)
        _truth(True)
        scene.render.filepath = str(out / "truth" / name)
        bpy.ops.render.render(write_still=True)
        _truth(False)
        done.append({"seq": wp["seq"], "file": name, "seconds": round(time.time() - t, 1)})
    return done

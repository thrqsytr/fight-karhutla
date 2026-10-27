"""Blender scene for the drone survey simulator. SIMULATION: every output is labelled as such.

Run inside Blender 5.x (MCP, the Python console, or `blender -b -P`) with SIM_DIR pointing at an asset
folder written by export_terrain.py and plan_mission.py. Units are metres; X is east and Y is north from
the south-west corner of the Sentinel-2 terrain grid. Blender's Python has no projection library, so every
position arrives precomputed in local metres (local_x, local_y).

The ground shader decides what is burned (the real Sentinel-2 dNBR mask with a little edge noise). The
same value drives the truth pass: with the view layer property mask_mode = 1 the ground emits white where
it is burned and trees, fire, and smoke are hidden, so the truth mask always matches the rendered ground.
"""

import json
import math
import os
import random
from pathlib import Path

import bpy

SIM_DIR = Path(os.environ.get("SIM_DIR", r"D:\HACKATHON\KARHUTLA\Code\fight-karhutla\sim\assets\inc-0407"))
MARGIN_M = 200


def load():
    terrain = json.loads((SIM_DIR / "terrain.json").read_text(encoding="utf-8"))
    mission = json.loads((SIM_DIR / "mission.json").read_text(encoding="utf-8"))
    return terrain, mission


def reset_scene():
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    for coll in (bpy.data.meshes, bpy.data.materials, bpy.data.cameras, bpy.data.lights, bpy.data.node_groups, bpy.data.images):
        for item in list(coll):
            if item.users == 0:
                coll.remove(item)


def node(nt, kind, x, y, **props):
    n = nt.nodes.new(kind)
    n.location = (x, y)
    for k, v in props.items():
        setattr(n, k, v)
    return n


def image(name):
    img = bpy.data.images.load(str(SIM_DIR / name), check_existing=True)
    img.colorspace_settings.name = "sRGB" if name == "post_rgb.png" else "Non-Color"
    return img


def mask_mode(nt, x, y):
    """0 renders the photo, 1 the truth pass. Looked up on the view layer, then the scene."""
    return node(nt, "ShaderNodeAttribute", x, y, attribute_type="VIEW_LAYER", attribute_name="mask_mode").outputs["Fac"]


def terrain_scale(terrain):
    b = terrain["utm_bounds"]
    return (1 / (b[2] - b[0]), 1 / (b[3] - b[1]), 1)


def ground_material(terrain):
    mat = bpy.data.materials.new("Ground")
    nt = mat.node_tree
    nt.nodes.clear()
    tc = node(nt, "ShaderNodeTexCoord", -1600, 0)
    uv = node(nt, "ShaderNodeMapping", -1400, 0)
    uv.inputs["Scale"].default_value = terrain_scale(terrain)
    nt.links.new(tc.outputs["Object"], uv.inputs["Vector"])
    # Edge noise so the 20 m mask does not look like stairs; it moves the lookup by up to about 12 m.
    noise = node(nt, "ShaderNodeTexNoise", -1400, -350)
    noise.inputs["Scale"].default_value = 180.0
    noise.inputs["Detail"].default_value = 6.0
    nt.links.new(uv.outputs["Vector"], noise.inputs["Vector"])
    centered = node(nt, "ShaderNodeVectorMath", -1200, -350, operation="SUBTRACT")
    nt.links.new(noise.outputs["Color"], centered.inputs[0])
    centered.inputs[1].default_value = (0.5, 0.5, 0.0)
    small = node(nt, "ShaderNodeVectorMath", -1050, -350, operation="SCALE")
    nt.links.new(centered.outputs["Vector"], small.inputs[0])
    small.inputs["Scale"].default_value = 0.008
    warped = node(nt, "ShaderNodeVectorMath", -900, -200, operation="ADD")
    nt.links.new(uv.outputs["Vector"], warped.inputs[0])
    nt.links.new(small.outputs["Vector"], warped.inputs[1])

    burn_tex = node(nt, "ShaderNodeTexImage", -700, -200, interpolation="Linear", extension="EXTEND")
    burn_tex.image = image("burned.png")
    nt.links.new(warped.outputs["Vector"], burn_tex.inputs["Vector"])
    burned = node(nt, "ShaderNodeMath", -450, -200, operation="GREATER_THAN")
    nt.links.new(burn_tex.outputs["Color"], burned.inputs[0])
    burned.inputs[1].default_value = 0.5

    photo = node(nt, "ShaderNodeTexImage", -700, 250, interpolation="Cubic", extension="EXTEND")
    photo.image = image("post_rgb.png")
    nt.links.new(uv.outputs["Vector"], photo.inputs["Vector"])

    # Forest floor between crowns: dark green with crown sized cells.
    vor = node(nt, "ShaderNodeTexVoronoi", -700, 650)
    vor.inputs["Scale"].default_value = 300.0
    nt.links.new(uv.outputs["Vector"], vor.inputs["Vector"])
    green = node(nt, "ShaderNodeValToRGB", -500, 650)
    green.color_ramp.elements[0].color = (0.01, 0.022, 0.008, 1)
    green.color_ramp.elements[1].color = (0.035, 0.07, 0.02, 1)
    nt.links.new(vor.outputs["Distance"], green.inputs["Fac"])
    # Burned peat: charcoal, grey ash, and brown scorch.
    ash = node(nt, "ShaderNodeTexNoise", -900, -700)
    ash.inputs["Scale"].default_value = 1400.0
    ash.inputs["Detail"].default_value = 10.0
    ash.inputs["Roughness"].default_value = 0.65
    nt.links.new(uv.outputs["Vector"], ash.inputs["Vector"])
    patches = node(nt, "ShaderNodeTexNoise", -900, -950)
    patches.inputs["Scale"].default_value = 160.0
    patches.inputs["Detail"].default_value = 4.0
    nt.links.new(uv.outputs["Vector"], patches.inputs["Vector"])
    blend = node(nt, "ShaderNodeMath", -760, -800, operation="MULTIPLY_ADD")
    nt.links.new(patches.outputs["Fac"], blend.inputs[0])
    blend.inputs[1].default_value = 0.6
    nt.links.new(ash.outputs["Fac"], blend.inputs[2])
    half = node(nt, "ShaderNodeMath", -700, -650, operation="MULTIPLY")
    nt.links.new(blend.outputs["Value"], half.inputs[0])
    half.inputs[1].default_value = 0.62
    char = node(nt, "ShaderNodeValToRGB", -550, -700)
    ramp = char.color_ramp
    ramp.elements[0].position, ramp.elements[0].color = 0.4, (0.01, 0.009, 0.008, 1)
    ramp.elements[1].position, ramp.elements[1].color = 0.8, (0.2, 0.19, 0.18, 1)
    mid = ramp.elements.new(0.52)
    mid.color = (0.075, 0.042, 0.024, 1)
    nt.links.new(half.outputs["Value"], char.inputs["Fac"])

    # The Sentinel-2 color carries the large pattern; procedural detail adds what 10 m pixels cannot.
    forest = node(nt, "ShaderNodeMix", -250, 450, data_type="RGBA", blend_type="MIX")
    forest.inputs["Factor"].default_value = 0.75
    nt.links.new(photo.outputs["Color"], forest.inputs["A"])
    nt.links.new(green.outputs["Color"], forest.inputs["B"])
    scar = node(nt, "ShaderNodeMix", -250, -450, data_type="RGBA", blend_type="MIX")
    scar.inputs["Factor"].default_value = 0.85
    nt.links.new(photo.outputs["Color"], scar.inputs["A"])
    nt.links.new(char.outputs["Color"], scar.inputs["B"])
    color = node(nt, "ShaderNodeMix", 0, 0, data_type="RGBA")
    nt.links.new(burned.outputs["Value"], color.inputs["Factor"])
    nt.links.new(forest.outputs["Result"], color.inputs["A"])
    nt.links.new(scar.outputs["Result"], color.inputs["B"])

    bsdf = node(nt, "ShaderNodeBsdfPrincipled", 250, 200)
    bsdf.inputs["Roughness"].default_value = 0.95
    nt.links.new(color.outputs["Result"], bsdf.inputs["Base Color"])
    truth = node(nt, "ShaderNodeEmission", 250, -250)
    truth.inputs["Color"].default_value = (1, 1, 1, 1)
    nt.links.new(burned.outputs["Value"], truth.inputs["Strength"])
    mix = node(nt, "ShaderNodeMixShader", 500, 0)
    nt.links.new(mask_mode(nt, 250, -450), mix.inputs["Fac"])
    nt.links.new(bsdf.outputs["BSDF"], mix.inputs[1])
    nt.links.new(truth.outputs["Emission"], mix.inputs[2])
    out = node(nt, "ShaderNodeOutputMaterial", 750, 0)
    nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
    return mat


def principled(name, color, roughness=0.85, emission=None, strength=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Base Color"].default_value = color
    bsdf.inputs["Roughness"].default_value = roughness
    if emission:
        bsdf.inputs["Emission Color"].default_value = emission
        bsdf.inputs["Emission Strength"].default_value = strength
    return mat


def crown_material():
    mat = principled("Crown", (0.06, 0.12, 0.03, 1), 0.9)
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    info = node(nt, "ShaderNodeObjectInfo", -600, 300)
    ramp = node(nt, "ShaderNodeValToRGB", -350, 300)
    ramp.color_ramp.elements[0].color = (0.015, 0.04, 0.012, 1)
    ramp.color_ramp.elements[1].color = (0.06, 0.13, 0.03, 1)
    nt.links.new(info.outputs["Random"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    return mat


def add_ground(terrain, box):
    x0, y0, x1, y1 = box
    me = bpy.data.meshes.new("Ground")
    me.from_pydata([(x0, y0, 0), (x1, y0, 0), (x1, y1, 0), (x0, y1, 0)], [], [(0, 1, 2, 3)])
    g = bpy.data.objects.new("Ground", me)
    bpy.context.collection.objects.link(g)
    me.materials.append(ground_material(terrain))
    return g


def prototypes():
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=3, radius=1, location=(0, 0, 0))
    crown = bpy.context.active_object
    crown.name = "CrownProto"
    crown.scale = (4.0, 4.0, 2.4)
    bpy.ops.object.transform_apply(scale=True)
    # Lumpy crowns instead of perfect spheres.
    tex = bpy.data.textures.new("CrownLumps", "CLOUDS")
    tex.noise_scale = 1.2
    disp = crown.modifiers.new("Lumps", "DISPLACE")
    disp.texture, disp.strength = tex, 1.4
    bpy.ops.object.modifier_apply(modifier="Lumps")
    bpy.ops.object.shade_smooth()
    bpy.ops.mesh.primitive_cylinder_add(vertices=6, radius=0.35, depth=9, location=(0, 0, 4.5))
    snag = bpy.context.active_object
    snag.name = "SnagProto"
    bpy.ops.object.transform_apply(location=True)
    for p in (crown, snag):
        p.hide_render = p.hide_viewport = True
    return crown, snag


def scatter(name, terrain, box, *, density, on_burned, proto, scale, z, material):
    """Points on the ground that read the same burn mask: crowns where unburned, dead snags where burned."""
    x0, y0, x1, y1 = box
    me = bpy.data.meshes.new(name)
    me.from_pydata([(x0, y0, 0), (x1, y0, 0), (x1, y1, 0), (x0, y1, 0)], [], [(0, 1, 2, 3)])
    holder = bpy.data.objects.new(name, me)
    bpy.context.collection.objects.link(holder)
    ng = bpy.data.node_groups.new(name, "GeometryNodeTree")
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    gi = node(ng, "NodeGroupInput", -1100, 0)
    go = node(ng, "NodeGroupOutput", 1000, 0)
    dist = node(ng, "GeometryNodeDistributePointsOnFaces", -850, 0)
    dist.inputs["Density"].default_value = density
    ng.links.new(gi.outputs["Geometry"], dist.inputs["Mesh"])
    pos = node(ng, "GeometryNodeInputPosition", -1000, -350)
    sc = node(ng, "ShaderNodeVectorMath", -800, -350, operation="MULTIPLY")
    sc.inputs[1].default_value = terrain_scale(terrain)
    ng.links.new(pos.outputs["Position"], sc.inputs[0])
    tex = node(ng, "GeometryNodeImageTexture", -600, -350)
    tex.inputs["Image"].default_value = image("burned.png")
    ng.links.new(sc.outputs["Vector"], tex.inputs["Vector"])
    cmp = node(ng, "FunctionNodeCompare", -350, -350, data_type="FLOAT", operation="LESS_THAN" if on_burned else "GREATER_THAN")
    ng.links.new(tex.outputs["Color"], cmp.inputs["A"])
    cmp.inputs["B"].default_value = 0.75 if on_burned else 0.25  # keep a gap at the edge so canopy never covers the scar line
    delete = node(ng, "GeometryNodeDeleteGeometry", -150, 0, domain="POINT")
    ng.links.new(dist.outputs["Points"], delete.inputs["Geometry"])
    ng.links.new(cmp.outputs["Result"], delete.inputs["Selection"])
    info = node(ng, "GeometryNodeObjectInfo", -150, 350)
    info.inputs["Object"].default_value = proto
    inst = node(ng, "GeometryNodeInstanceOnPoints", 150, 0)
    ng.links.new(delete.outputs["Geometry"], inst.inputs["Points"])
    ng.links.new(info.outputs["Geometry"], inst.inputs["Instance"])
    rs = node(ng, "FunctionNodeRandomValue", -150, -600, data_type="FLOAT")
    rs.inputs["Min"].default_value, rs.inputs["Max"].default_value = scale
    ng.links.new(rs.outputs["Value"], inst.inputs["Scale"])
    rr = node(ng, "FunctionNodeRandomValue", -150, -850, data_type="FLOAT_VECTOR")
    rr.inputs["Max"].default_value = (0.15, 0.15, 6.283) if on_burned else (0, 0, 6.283)
    ng.links.new(rr.outputs["Value"], inst.inputs["Rotation"])
    lift = node(ng, "GeometryNodeTranslateInstances", 450, 0)
    lift.inputs["Translation"].default_value = (0, 0, z)
    ng.links.new(inst.outputs["Instances"], lift.inputs["Instances"])
    setm = node(ng, "GeometryNodeSetMaterial", 750, 0)
    setm.inputs["Material"].default_value = material
    ng.links.new(lift.outputs["Instances"], setm.inputs["Geometry"])
    ng.links.new(setm.outputs["Geometry"], go.inputs["Geometry"])
    holder.modifiers.new(name, "NODES").node_group = ng
    holder["truth_hide"] = True
    return holder


def smoke_material():
    mat = bpy.data.materials.new("Smoke")
    nt = mat.node_tree
    nt.nodes.clear()
    tc = node(nt, "ShaderNodeTexCoord", -1000, 0)
    noise = node(nt, "ShaderNodeTexNoise", -750, 150)
    noise.inputs["Scale"].default_value = 4.0
    noise.inputs["Detail"].default_value = 8.0
    nt.links.new(tc.outputs["Generated"], noise.inputs["Vector"])
    centered = node(nt, "ShaderNodeVectorMath", -850, -250, operation="SUBTRACT")
    nt.links.new(tc.outputs["Generated"], centered.inputs[0])
    centered.inputs[1].default_value = (0.5, 0.5, 0.5)
    length = node(nt, "ShaderNodeVectorMath", -650, -250, operation="LENGTH")
    nt.links.new(centered.outputs["Vector"], length.inputs[0])
    fade = node(nt, "ShaderNodeMapRange", -450, -250)
    fade.inputs["From Min"].default_value, fade.inputs["From Max"].default_value = 0.5, 0.15
    nt.links.new(length.outputs["Value"], fade.inputs["Value"])
    mul = node(nt, "ShaderNodeMath", -250, 0, operation="MULTIPLY")
    nt.links.new(noise.outputs["Fac"], mul.inputs[0])
    nt.links.new(fade.outputs["Result"], mul.inputs[1])
    dens = node(nt, "ShaderNodeMapRange", -50, 0)
    dens.inputs["From Min"].default_value, dens.inputs["From Max"].default_value = 0.25, 0.65
    dens.inputs["To Max"].default_value = 0.14
    nt.links.new(mul.outputs["Value"], dens.inputs["Value"])
    vol = node(nt, "ShaderNodeVolumePrincipled", 200, 0)
    vol.inputs["Color"].default_value = (0.75, 0.73, 0.7, 1)
    nt.links.new(dens.outputs["Result"], vol.inputs["Density"])
    out = node(nt, "ShaderNodeOutputMaterial", 450, 0)
    nt.links.new(vol.outputs["Volume"], out.inputs["Volume"])
    return mat


def add_fire_and_smoke(terrain, mission, box):
    """Active fire at the newest FIRMS hotspots inside the box, smoke leaning downwind (wind from Open-Meteo)."""
    newest_day = max(h["at"] for h in terrain["hotspots"])[:10]
    x0, y0, x1, y1 = box
    active = [h for h in terrain["hotspots"] if h["at"].startswith(newest_day) and x0 <= h["local_x"] <= x1 and y0 <= h["local_y"] <= y1]
    to_rad = math.radians((mission["wind"]["from_deg"] + 180) % 360)
    dx, dy = math.sin(to_rad), math.cos(to_rad)
    flame = principled("Flame", (1.0, 0.5, 0.08, 1), 0.5, emission=(1.0, 0.5, 0.06, 1), strength=14.0)
    ember = principled("Ember", (0.12, 0.02, 0.005, 1), 0.8, emission=(0.7, 0.07, 0.01, 1), strength=2.5)
    smoke = smoke_material()
    made = []
    for i, h in enumerate(active):
        # A fire front: an arc of small flame patches across the wind, about 70 m long.
        px, py = -dy, dx
        rng = random.Random(i)
        for k in range(36):
            t = (k - 17.5) * 2.2
            bend = -0.0035 * t * t
            jitter = rng.uniform(-1.5, 1.5)
            cx = h["local_x"] + px * t + dx * (bend * 10 + jitter)
            cy = h["local_y"] + py * t + dy * (bend * 10 + jitter)
            for layer, (mat, back, size) in enumerate(((ember, -2.5, 2.6), (flame, 0.0, 1.1))):
                bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1, radius=1, location=(cx - dx * back, cy - dy * back, 0.4 + layer * 0.5))
                f = bpy.context.active_object
                f.name = f"Fire_{i}_{k}_{layer}"
                f.scale = (size * rng.uniform(0.7, 1.4), size * rng.uniform(0.5, 1.0), 0.6)
                f.rotation_euler[2] = math.atan2(py, px) + rng.uniform(-0.4, 0.4)
                f.data.materials.append(mat)
                f["truth_hide"] = True
                made.append(f)
        # Smoke: a soft volume that starts at the front and drifts downwind as it rises.
        bpy.ops.mesh.primitive_cube_add(size=1, location=(h["local_x"] + dx * 110, h["local_y"] + dy * 110, 45))
        s = bpy.context.active_object
        s.name = f"Smoke_{i}"
        s.scale = (90, 260, 80)
        s.rotation_euler = (math.radians(12) * dy, -math.radians(12) * dx, math.atan2(-dx, dy))
        s.data.materials.append(smoke)
        s["truth_hide"] = True
        made.append(s)
    return active, made


def camera_and_world(mission):
    cam_data = bpy.data.cameras.new("DroneCam")
    cam_data.sensor_fit = "HORIZONTAL"
    cam_data.sensor_width = mission["camera"]["sensor_w_mm"]
    cam_data.sensor_height = mission["camera"]["sensor_h_mm"]
    cam_data.lens = mission["camera"]["focal_mm"]
    cam_data.clip_start, cam_data.clip_end = 1, 2000
    cam = bpy.data.objects.new("DroneCam", cam_data)
    bpy.context.collection.objects.link(cam)
    scene = bpy.context.scene
    scene.camera = cam
    r = scene.render
    r.resolution_x, r.resolution_y, r.resolution_percentage = mission["camera"]["image_w_px"], mission["camera"]["image_h_px"], 100
    sun_data = bpy.data.lights.new("Sun", "SUN")
    sun_data.energy = 4.5
    sun_data.angle = math.radians(1.5)
    sun = bpy.data.objects.new("Sun", sun_data)
    sun.rotation_euler = (math.radians(32), 0, math.radians(105))  # about 10:30 local time, high in the east
    bpy.context.collection.objects.link(sun)
    world = scene.world or bpy.data.worlds.new("World")
    scene.world = world
    world.use_nodes = True
    bg = next(n for n in world.node_tree.nodes if n.type == "BACKGROUND")
    bg.inputs["Color"].default_value = (0.6, 0.68, 0.8, 1)
    bg.inputs["Strength"].default_value = 0.8
    scene.view_layers[0]["mask_mode"] = 0.0
    return cam


def place_camera(cam, frame: dict, altitude: float):
    """Nadir camera at a waypoint. Image up points along the heading."""
    cam.location = (frame["local_x"], frame["local_y"], altitude)
    cam.rotation_euler = (0.0, 0.0, -math.radians(frame["heading_deg"]))


def build():
    terrain, mission = load()
    reset_scene()
    bx0, by0, bx1, by1 = mission["local_box"]
    box = (bx0 - MARGIN_M, by0 - MARGIN_M, bx1 + MARGIN_M, by1 + MARGIN_M)
    add_ground(terrain, box)
    crown, snag = prototypes()
    scatter("Canopy", terrain, box, density=0.06, on_burned=False, proto=crown, scale=(0.75, 1.5), z=18.0, material=crown_material())
    scatter("Snags", terrain, box, density=0.0012, on_burned=True, proto=snag, scale=(0.6, 1.2), z=0.0, material=principled("Charred", (0.015, 0.012, 0.01, 1), 0.9))
    active, _ = add_fire_and_smoke(terrain, mission, box)
    cam = camera_and_world(mission)
    place_camera(cam, mission["waypoints"][0], mission["altitude_agl_m"])
    return {"active_fires": len(active), "frames": len(mission["waypoints"]), "box": box}


if __name__ == "__main__":
    print(build())

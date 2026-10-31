"""Mission files for a real drone, from an approved survey plan (backend/drone.py, drone_missions.py).

Formats, checked against their sources on 2026-10-31 (copies in Resources/mavlink):
- QGroundControl `.plan` (JSON, fileType Plan v1, mission v2), as read by MissionController::_loadJsonMissionFileV2
  and MissionItem::load: every item has type, frame, command, 7 params, autoContinue; nav items add AltitudeMode
  (1 = relative to home), Altitude, AMSLAltAboveTerrain. One file per autopilot family, because QGroundControl
  refuses to send a plan whose firmware type differs from the connected vehicle (MAV_AUTOPILOT 12 PX4, 3 ArduPilot).
- Mission Planner `.waypoints` (QGC WPL 110, tab separated: index, current, frame, command, param1-4, lat, lon, alt,
  autocontinue); row 0 is home.
- KML with the survey area, the route, and the photo points, for Google Earth, QGIS, and apps that import a KML area.
  A DJI WPML route (.kmz) is not written: it needs the drone and camera model codes and could not be tested here.

The mission: take off to the survey height, set ground speed, then for each north-south line fly to its first
photo point, trigger the camera every frame spacing (MAV_CMD_DO_SET_CAM_TRIGG_DIST), fly the line through its
photo points, stop the camera, and return to launch. Heights are relative to the launch point over flat ground:
no elevation model is used yet, so the pilot checks terrain and obstacles.
"""

import io
import json
import zipfile
from xml.sax.saxutils import escape

# MAVLink common.xml / minimal.xml
CMD_WAYPOINT, CMD_RTL, CMD_TAKEOFF, CMD_CHANGE_SPEED, CMD_CAM_TRIGG_DIST = 16, 20, 22, 178, 206
FRAME_GLOBAL, FRAME_MISSION, FRAME_RELATIVE = 0, 2, 3
AUTOPILOT = {"px4": 12, "ardupilot": 3}
MAV_TYPE_QUADROTOR = 2
QGC_ALT_RELATIVE = 1  # QGroundControlQmlGlobal::AltitudeFrameRelative
SPEED_GROUND = 1

FORMATS = {
    "px4": ("plan", "application/json"),
    "ardupilot": ("plan", "application/json"),
    "waypoints": ("waypoints", "text/plain"),
    "kml": ("kml", "application/vnd.google-earth.kml+xml"),
    "zip": ("zip", "application/zip"),
}

WARNINGS = [
    "Heights are relative to the launch point and assume flat ground: no elevation model is used. Check terrain, trees, towers, and power lines before flight.",
    "Fly only as a licensed pilot, within civil aviation rules and with the permits the area needs.",
    "Do not fly while firefighting aircraft or helicopters work in the area. Keep clear of smoke and flames: heat and smoke affect sensors and batteries.",
    "The launch point is set at the start of the first line. Ground control software moves home to the drone's real position when it connects; review the route from there.",
    "Photo spacing gives 10% overlap: every frame covers new ground for spotting fire and burned area. It is not enough for a stitched orthomosaic (use 70 to 80%).",
]
WARNINGS_ID = [
    "Ketinggian dihitung dari titik lepas landas dan menganggap tanah datar: belum memakai data elevasi. Periksa medan, pohon, menara, dan kabel listrik sebelum terbang.",
    "Terbang hanya oleh pilot berlisensi, sesuai aturan penerbangan sipil dan izin yang dibutuhkan di area itu.",
    "Jangan terbang saat pesawat atau helikopter pemadam bekerja di area itu. Jauhi asap dan api: panas dan asap mengganggu sensor dan baterai.",
    "Titik lepas landas dipasang di awal jalur pertama. Aplikasi pengendali memindahkan home ke posisi drone sebenarnya saat tersambung; periksa ulang rutenya dari sana.",
    "Jarak foto memberi tumpang tindih 10%: setiap foto menutup tanah baru untuk mencari api dan bekas terbakar. Itu tidak cukup untuk ortomosaik gabungan (pakai 70 sampai 80%).",
]


class ExportError(ValueError):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def lines_of(plan: dict) -> list[list[dict]]:
    """Photo points grouped into flight lines, in flying order."""
    n = plan["frames_per_line"]
    wps = plan["waypoints"]
    return [wps[i:i + n] for i in range(0, len(wps), n)]


def spacing_m(plan: dict) -> float:
    return round(plan["footprint_m"][1] * (1 - plan["front_overlap"]), 2)


def mission_items(plan: dict) -> list[dict]:
    """Autopilot-neutral mission: command, frame, params[7] (lat, lon, alt in 5 to 7). None means NaN."""
    alt = float(plan["altitude_agl_m"])
    first = plan["waypoints"][0]
    speed = float(plan.get("estimate", {}).get("speed_ms") or 8.0)
    items = [
        {"command": CMD_TAKEOFF, "frame": FRAME_RELATIVE, "params": [0, 0, 0, None, first["lat"], first["lon"], alt]},
        {"command": CMD_CHANGE_SPEED, "frame": FRAME_MISSION, "params": [SPEED_GROUND, speed, -1, 0, 0, 0, 0]},
    ]
    for line in lines_of(plan):
        head, *rest = line
        items.append({"command": CMD_WAYPOINT, "frame": FRAME_RELATIVE, "params": [0, 0, 0, None, head["lat"], head["lon"], alt]})
        items.append({"command": CMD_CAM_TRIGG_DIST, "frame": FRAME_MISSION, "params": [spacing_m(plan), 0, 1, 0, 0, 0, 0]})
        for wp in rest:
            items.append({"command": CMD_WAYPOINT, "frame": FRAME_RELATIVE, "params": [0, 0, 0, None, wp["lat"], wp["lon"], alt]})
        items.append({"command": CMD_CAM_TRIGG_DIST, "frame": FRAME_MISSION, "params": [0, 0, 0, 0, 0, 0, 0]})
    items.append({"command": CMD_RTL, "frame": FRAME_MISSION, "params": [0, 0, 0, 0, 0, 0, 0]})
    return items


def to_qgc_plan(plan: dict, autopilot: str) -> dict:
    first = plan["waypoints"][0]
    out = []
    for i, it in enumerate(mission_items(plan), start=1):
        item = {"autoContinue": True, "command": it["command"], "doJumpId": i, "frame": it["frame"], "params": it["params"], "type": "SimpleItem"}
        if it["frame"] == FRAME_RELATIVE:
            item.update({"AltitudeMode": QGC_ALT_RELATIVE, "Altitude": it["params"][6], "AMSLAltAboveTerrain": None})
        out.append(item)
    speed = float(plan.get("estimate", {}).get("speed_ms") or 8.0)
    return {
        "fileType": "Plan", "version": 1, "groundStation": "Fight Karhutla",
        "mission": {"version": 2, "firmwareType": AUTOPILOT[autopilot], "vehicleType": MAV_TYPE_QUADROTOR,
                    "cruiseSpeed": speed, "hoverSpeed": speed, "globalPlanAltitudeMode": QGC_ALT_RELATIVE,
                    "plannedHomePosition": [first["lat"], first["lon"], 0], "items": out},
        "geoFence": {"version": 2, "circles": [], "polygons": []},
        "rallyPoints": {"version": 2, "points": []},
    }


def _num(v) -> str:
    return "0" if v is None else (f"{v:.8f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v))


def to_waypoints(plan: dict) -> str:
    first = plan["waypoints"][0]
    rows = [["0", "1", str(FRAME_GLOBAL), str(CMD_WAYPOINT), "0", "0", "0", "0", _num(first["lat"]), _num(first["lon"]), "0", "1"]]
    for i, it in enumerate(mission_items(plan), start=1):
        rows.append([str(i), "0", str(it["frame"]), str(it["command"]), *(_num(p) for p in it["params"]), "1"])
    return "QGC WPL 110\n" + "\n".join("\t".join(r) for r in rows) + "\n"


def to_kml(plan: dict, meta: dict) -> str:
    w, s, e, n = plan["bbox"]
    alt = plan["altitude_agl_m"]
    area = f"{w},{s},0 {e},{s},0 {e},{n},0 {w},{n},0 {w},{s},0"
    route = " ".join(f"{p['lon']},{p['lat']},{alt}" for p in plan["waypoints"])
    first = plan["waypoints"][0]
    desc = escape(f"{meta['title']}. {summary_line(plan)}. " + " ".join(WARNINGS))
    points = "\n".join(
        f"      <Placemark><name>{p['seq'] + 1}</name><styleUrl>#photo</styleUrl><Point><altitudeMode>relativeToGround</altitudeMode>"
        f"<coordinates>{p['lon']},{p['lat']},{alt}</coordinates></Point></Placemark>" for p in plan["waypoints"])
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>{escape(meta['basename'])}</name>
    <description>{desc}</description>
    <Style id="area"><LineStyle><color>ffd65a7a</color><width>2</width></LineStyle><PolyStyle><color>337a5af8</color></PolyStyle></Style>
    <Style id="route"><LineStyle><color>fff85a7a</color><width>2</width></LineStyle></Style>
    <Style id="photo"><IconStyle><scale>0.5</scale></IconStyle><LabelStyle><scale>0</scale></LabelStyle></Style>
    <Placemark>
      <name>Survey area</name><styleUrl>#area</styleUrl>
      <Polygon><outerBoundaryIs><LinearRing><coordinates>{area}</coordinates></LinearRing></outerBoundaryIs></Polygon>
    </Placemark>
    <Placemark>
      <name>Route, {alt} m above the launch point</name><styleUrl>#route</styleUrl>
      <LineString><altitudeMode>relativeToGround</altitudeMode><coordinates>{route}</coordinates></LineString>
    </Placemark>
    <Placemark><name>Launch point (planned)</name><Point><coordinates>{first['lon']},{first['lat']},0</coordinates></Point></Placemark>
    <Folder>
      <name>Photo points ({len(plan['waypoints'])})</name>
{points}
    </Folder>
  </Document>
</kml>
"""


def summary_line(plan: dict) -> str:
    est = plan.get("estimate", {})
    return (f"{plan['lines']} lines x {plan['frames_per_line']} photos at {plan['altitude_agl_m']} m, photo every {spacing_m(plan)} m, "
            f"area {plan['area_km2']} km2, about {est.get('flight_minutes')} min over {est.get('path_km')} km, {est.get('batteries')} battery(ies)")


def readme(plan: dict, meta: dict) -> str:
    est = plan.get("estimate", {})
    cam = plan["camera"]
    lines = [
        meta["title"], "",
        "Files", "",
        f"  {meta['basename']}-px4.plan        QGroundControl, PX4 autopilot (File > Open, then Upload)",
        f"  {meta['basename']}-ardupilot.plan  QGroundControl, ArduPilot autopilot",
        f"  {meta['basename']}.waypoints       Mission Planner, ArduPilot (Plan > Load WP File)",
        f"  {meta['basename']}.kml             Survey area, route, and photo points: Google Earth, QGIS, and apps that import a KML area",
        "",
        "Plan", "",
        f"  Incident: {meta['incident_id']} (open it at {meta['incident_url']})",
        f"  Plan #{meta['proposal_id']}, approved by a verifier on {meta['approved_at']}",
        f"  Height: {plan['altitude_agl_m']} m above the launch point; ground speed {est.get('speed_ms')} m/s",
        f"  Pattern: {plan['lines']} north-south lines x {plan['frames_per_line']} photos; camera triggered every {spacing_m(plan)} m along each line",
        f"  Area: {plan['area_km2']} km2, box W {plan['bbox'][0]} S {plan['bbox'][1]} E {plan['bbox'][2]} N {plan['bbox'][3]}",
        f"  Ground pixel: {plan['gsd_m']} m with a {cam['sensor_w_mm']} x {cam['sensor_h_mm']} mm sensor and {cam['focal_mm']} mm lens; other cameras change it",
        f"  Estimate: about {est.get('flight_minutes')} min over {est.get('path_km')} km, {est.get('batteries')} battery(ies)",
        "",
        "Before flight", "",
        *[f"  - {w}" for w in WARNINGS],
        "",
        "After flight", "",
        f"  Send the photos as evidence through the report form with this location: {meta['report_url']}",
        "",
        "Bahasa Indonesia", "",
        f"  Misi survei untuk {meta['incident_id']}, rencana #{meta['proposal_id']}, disetujui verifikator {meta['approved_at']}.",
        "  Berkas .plan untuk QGroundControl (pilih sesuai autopilot: PX4 atau ArduPilot), .waypoints untuk Mission Planner, .kml untuk Google Earth dan QGIS.",
        *[f"  - {w}" for w in WARNINGS_ID],
        f"  Setelah terbang, kirim fotonya sebagai bukti lewat formulir laporan: {meta['report_url']}",
        "",
        "Made by Fight Karhutla (https://karhutla.thariq.xyz). Map data (c) OpenStreetMap contributors where shown.",
    ]
    return "\r\n".join(lines) + "\r\n"  # opens cleanly in Notepad


def check_exportable(row: dict) -> None:
    if row["status"] != "approved":
        raise ExportError(409, f"this plan is {row['status']}; mission files are made only for a plan a verifier approved")


def build(row: dict, fmt: str, public_url: str) -> tuple[bytes, str, str]:
    """(content, file name, media type) for one approved proposal row (with plan decoded)."""
    if fmt not in FORMATS:
        raise ExportError(404, "format must be px4, ardupilot, waypoints, kml, or zip")
    check_exportable(row)
    plan = row["plan"]
    base = f"karhutla-{row['incident_id']}-plan{row['id']}"
    loc = plan["waypoints"][len(plan["waypoints"]) // 2]
    meta = {"basename": base, "incident_id": row["incident_id"], "proposal_id": row["id"], "approved_at": (row.get("decided_at") or "")[:16].replace("T", " ") + " UTC",
            "title": f"Fight Karhutla drone survey for {row['incident_id']}, plan #{row['id']}",
            "incident_url": f"{public_url}/i/{row['incident_id']}",
            "report_url": f"{public_url}/lapor/?lat={loc['lat']:.5f}&lon={loc['lon']:.5f}&ref={row['incident_id']}"}
    ext, media = FORMATS[fmt]
    if fmt in ("px4", "ardupilot"):
        return json.dumps(to_qgc_plan(plan, fmt), indent=2).encode(), f"{base}-{fmt}.plan", media
    if fmt == "waypoints":
        return to_waypoints(plan).encode(), f"{base}.waypoints", media
    if fmt == "kml":
        return to_kml(plan, meta).encode(), f"{base}.kml", media
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{base}-px4.plan", json.dumps(to_qgc_plan(plan, "px4"), indent=2))
        z.writestr(f"{base}-ardupilot.plan", json.dumps(to_qgc_plan(plan, "ardupilot"), indent=2))
        z.writestr(f"{base}.waypoints", to_waypoints(plan))
        z.writestr(f"{base}.kml", to_kml(plan, meta))
        z.writestr("README.txt", readme(plan, meta))
    return buf.getvalue(), f"{base}.zip", media

"""Mission files for a real drone: only from an approved plan, and each format says what the plan says."""

import io
import json
import xml.etree.ElementTree as ET
import zipfile

from fastapi.testclient import TestClient

from backend import drone_export, drone_missions

AUTH = {"Authorization": "Bearer test-token"}
POINTS = [(0.018, 116.457), (0.021, 116.462)]


def approved_row(plan=None):
    return {"id": 7, "incident_id": "INC-20261025-0407", "status": "approved", "decided_at": "2026-10-29T03:00:00+00:00",
            "plan": plan or drone_missions.plan_for(POINTS)}


def test_mission_flies_every_photo_point_with_the_camera_on_each_line():
    plan = drone_missions.plan_for(POINTS)
    items = drone_export.mission_items(plan)
    assert items[0]["command"] == drone_export.CMD_TAKEOFF and items[-1]["command"] == drone_export.CMD_RTL
    nav = [i for i in items if i["command"] == drone_export.CMD_WAYPOINT]
    assert [(i["params"][4], i["params"][5]) for i in nav] == [(w["lat"], w["lon"]) for w in plan["waypoints"]]
    assert all(i["params"][6] == plan["altitude_agl_m"] and i["frame"] == drone_export.FRAME_RELATIVE for i in nav)
    trig = [i["params"][0] for i in items if i["command"] == drone_export.CMD_CAM_TRIGG_DIST]
    assert len(trig) == 2 * plan["lines"] and trig[::2] == [drone_export.spacing_m(plan)] * plan["lines"] and set(trig[1::2]) == {0}
    # Spacing equals the along-track step of the plan's photo points.
    a, b = plan["waypoints"][0], plan["waypoints"][1]
    assert abs(abs(a["utm_y"] - b["utm_y"]) - drone_export.spacing_m(plan)) < 0.05


def test_qgc_plan_has_what_qgroundcontrol_requires():
    for fw, code in (("px4", 12), ("ardupilot", 3)):
        doc = drone_export.to_qgc_plan(drone_missions.plan_for(POINTS), fw)
        assert doc["fileType"] == "Plan" and doc["version"] == 1 and "geoFence" in doc and "rallyPoints" in doc
        m = doc["mission"]
        assert m["firmwareType"] == code and len(m["plannedHomePosition"]) == 3 and m["items"]
        for i, it in enumerate(m["items"], start=1):
            assert it["type"] == "SimpleItem" and len(it["params"]) == 7 and it["autoContinue"] is True and it["doJumpId"] == i
            if it["frame"] == drone_export.FRAME_RELATIVE:
                assert it["AltitudeMode"] == 1 and it["Altitude"] == it["params"][6] and it["AMSLAltAboveTerrain"] is None
        json.dumps(doc, allow_nan=False)  # NaN is written as null, never as a bare NaN


def test_waypoints_file_is_qgc_wpl_110_with_home_first():
    plan = drone_missions.plan_for(POINTS)
    text = drone_export.to_waypoints(plan)
    lines = text.strip("\n").split("\n")
    assert lines[0] == "QGC WPL 110"
    rows = [l.split("\t") for l in lines[1:]]
    assert all(len(r) == 12 for r in rows) and [int(r[0]) for r in rows] == list(range(len(rows)))
    assert rows[0][1] == "1" and rows[0][3] == "16"  # home
    assert len(rows) == len(drone_export.mission_items(plan)) + 1
    assert "nan" not in text.lower() and "none" not in text.lower()


def test_kml_parses_and_holds_area_route_and_every_photo():
    plan = drone_missions.plan_for(POINTS)
    _, name, _ = drone_export.build(approved_row(plan), "kml", "https://example.org")
    body, _, _ = drone_export.build(approved_row(plan), "kml", "https://example.org")
    ns = {"k": "http://www.opengis.net/kml/2.2"}
    root = ET.fromstring(body)
    assert root.find(".//k:Polygon", ns) is not None and root.find(".//k:LineString", ns) is not None
    assert len(root.findall(".//k:Folder/k:Placemark", ns)) == len(plan["waypoints"]) and name.endswith(".kml")


def test_zip_carries_all_files_and_a_readme_with_the_warnings():
    body, name, _ = drone_export.build(approved_row(), "zip", "https://example.org")
    z = zipfile.ZipFile(io.BytesIO(body))
    names = z.namelist()
    assert name == "karhutla-INC-20261025-0407-plan7.zip" and "README.txt" in names and len(names) == 5
    readme = z.read("README.txt").decode()
    assert all(w in readme for w in drone_export.WARNINGS) and "INC-20261025-0407" in readme and "/lapor/?lat=" in readme


def test_only_an_approved_plan_exports_and_only_for_a_verifier(settings):
    from backend.main import app

    with TestClient(app) as c:
        inc_id = c.get("/api/incidents").json()["items"][0]["id"]
        p = c.post(f"/api/incidents/{inc_id}/drone-proposals", json={"reason": "test"}, headers=AUTH).json()
        url = f"/api/drone-proposals/{p['id']}/mission/zip"
        assert c.get(url).status_code == 401
        refused = c.get(url, headers=AUTH)
        assert refused.status_code == 409 and "approved" in refused.json()["detail"]
        c.post(f"/api/drone-proposals/{p['id']}/decision", json={"decision": "approve"}, headers=AUTH)
        r = c.get(url, headers=AUTH)
        assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
        assert f'filename="karhutla-{inc_id}-plan{p["id"]}.zip"' in r.headers["content-disposition"]
        assert c.get(f"/api/drone-proposals/{p['id']}/mission/dji", headers=AUTH).status_code == 404
        assert c.get(f"/api/drone-proposals/{p['id']}/mission/px4", headers=AUTH).json()["fileType"] == "Plan"
        assert c.get("/api/drone-proposals/999999/mission/kml", headers=AUTH).status_code == 404

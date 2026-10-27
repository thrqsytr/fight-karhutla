"""Drone survey planning, georeferencing, and the simulated replay endpoints."""

import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend import drone

CAM = drone.Camera()


def test_gsd_and_footprint_follow_the_camera_geometry():
    assert round(CAM.gsd_m(120), 4) == 0.1758  # 120 m x 13.2 mm / (8.8 mm x 1024 px)
    assert CAM.footprint_m(120) == pytest.approx((180.0, 120.0))


def test_lawnmower_covers_the_box_and_alternates_heading():
    plan = drone.plan_survey([116.450379, 0.01348, 116.458916, 0.020717])
    assert (plan["lines"], plan["frames_per_line"], len(plan["waypoints"])) == (6, 8, 48)
    headings = [w["heading_deg"] for w in plan["waypoints"]]
    assert headings[0] == 0.0 and headings[8] == 180.0
    assert plan["epsg"] == 32650


@pytest.mark.parametrize("kwargs, msg", [({"altitude_m": 150}, "altitude"), ({"altitude_m": 30}, "frame limit")])
def test_limits_are_refused_with_a_reason(kwargs, msg):
    with pytest.raises(ValueError, match=msg):
        drone.plan_survey([116.45, 0.01, 116.46, 0.02], **kwargs)


def test_overlapping_frames_never_double_count_ground():
    f = {"utm_x": 1000.0, "utm_y": 2000.0, "altitude_agl_m": 120, "heading_deg": 0.0}
    full = np.ones((CAM.image_h_px, CAM.image_w_px), dtype=bool)
    a = drone.ground_cells(f, CAM, full, 1.0)
    assert abs(len(a) - 180 * 120) / (180 * 120) < 0.01  # 1 m cells: the footprint in m2
    turned = drone.ground_cells({**f, "heading_deg": 180.0}, CAM, full, 1.0)
    assert len(a | turned) < len(a) * 1.01  # the same footprint flown the other way adds nothing


def test_pixel_and_footprint_corners_agree():
    f = {"utm_x": 1000.0, "utm_y": 2000.0, "altitude_agl_m": 120, "heading_deg": 0.0}
    x, y = drone.pixel_to_utm(f, CAM, 0, 0)
    cx, cy = drone.frame_footprint(f, CAM)[0]
    assert abs(x - cx) < 0.2 and abs(y - cy) < 0.2


def test_replay_endpoints_label_simulation(settings):
    from backend.main import app

    with TestClient(app) as c:
        items = c.get("/api/drone/missions", params={"incident_id": "INC-20261025-0407"}).json()["items"]
        assert items and items[0]["simulation"] is True and items[0]["label"] == "SIMULASI"
        m = c.get(f"/api/drone/missions/{items[0]['mission_id']}").json()
        assert len(m["frames"]) == 48 and len(m["frames"][0]["footprint"]) == 4
        assert m["survey"]["frames_measured"] <= m["survey"]["frames_uplinked"] <= 48
        first = m["frames"][0]["file"]
        assert c.get(f"/api/drone/missions/{m['mission_id']}/frames/{first}").status_code == 200
        assert c.get(f"/api/drone/missions/{m['mission_id']}/frames/..%2Fmission.json").status_code == 404
        assert c.get(f"/api/drone/missions/{m['mission_id']}/secret/{first}").status_code == 404

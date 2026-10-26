from datetime import datetime, timedelta, timezone

import pytest

from backend.geo import Point, bearing_deg, cluster_points, haversine_m, in_indonesia, pixel_area_ha, point_to_polyline_m

T0 = datetime(2026, 10, 25, 5, 0, tzinfo=timezone.utc)


def test_haversine_one_degree_at_equator():
    assert haversine_m(0, 0, 0, 1) == pytest.approx(111_195, rel=1e-3)


def test_bearing_east_is_90():
    assert bearing_deg(0, 0, 0, 1) == pytest.approx(90, abs=1e-6)


def test_point_to_polyline_matches_perpendicular_distance():
    line = [(0.0, 116.0), (0.0, 117.0)]
    assert point_to_polyline_m(0.01, 116.5, line) == pytest.approx(haversine_m(0, 116.5, 0.01, 116.5), rel=1e-3)


def test_pixel_area_sentinel2_10m():
    assert pixel_area_ha(1000, 10) == 10.0


def test_pixel_area_rejects_bad_input():
    with pytest.raises(ValueError):
        pixel_area_ha(-1, 10)


def test_in_indonesia():
    assert in_indonesia(-0.02, 116.45)
    assert not in_indonesia(1.35, 103.0 - 10)


def test_cluster_joins_within_radius_and_window():
    a = Point("a", 0.0, 116.0, T0)
    b = Point("b", 0.0, 116.015, T0 + timedelta(hours=3))  # about 1.7 km
    c = Point("c", 0.0, 116.030, T0 + timedelta(hours=6))  # chains through b
    far = Point("far", 0.0, 116.2, T0)
    late = Point("late", 0.0, 116.0, T0 + timedelta(hours=30))
    groups = cluster_points([late, c, far, b, a])
    ids = [[p.id for p in g] for g in groups]
    assert ["a", "b", "c"] in ids
    assert ["far"] in ids
    assert ["late"] in ids
    assert len(groups) == 3

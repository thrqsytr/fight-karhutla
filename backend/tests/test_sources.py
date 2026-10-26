from datetime import datetime, timezone

from backend.sources import firms, osm, weather


def test_firms_fixture_parses_real_rows(settings):
    hs = firms.get_area((115.5, -0.5, 116.5, 0.5), 2, map_key="", cache_dir=settings.cache_dir, fixtures_dir=settings.fixtures_dir, fixture=True)
    assert len(hs) == 129
    h = hs[0]
    assert h.id.startswith("HS-VIIRS_SNPP_NRT-")
    assert h.acquired_at.endswith("+00:00")
    assert h.confidence in {"l", "n", "h"}


def test_firms_bbox_filter(settings):
    hs = firms.get_area((116.4, -0.1, 116.5, 0.1), 2, map_key="", cache_dir=settings.cache_dir, fixtures_dir=settings.fixtures_dir, fixture=True)
    assert hs and all(116.4 <= h.lon <= 116.5 and -0.1 <= h.lat <= 0.1 for h in hs)


def test_weather_fixture_summary(settings):
    at = datetime(2026, 10, 26, 6, 0, tzinfo=timezone.utc)
    w = weather.get_weather(-0.02, 116.45, at=at, cache_dir=settings.cache_dir, fixtures_dir=settings.fixtures_dir, fixture=True)
    assert w["observed_hour"] == "2026-10-26T06:00:00+00:00"
    assert isinstance(w["wind_speed_ms"], (int, float))
    assert w["rain_24h_hours_covered"] == 24
    assert len(w["forecast_6h"]) == 6


def test_osm_fixture_nearest_assets(settings):
    a = osm.get_nearby_assets(-0.02, 116.45, cache_dir=settings.cache_dir, fixtures_dir=settings.fixtures_dir, fixture=True)
    near = a["nearest"]
    assert near["settlement"]["name"]
    assert near["road"]["name"] == "Jalan Poros Tabang"
    assert near["water"]["distance_m"] >= 0
    # No fire station within 30 km in this fixture: reported as None, not omitted.
    assert "fire_station" in near and near["fire_station"] is None

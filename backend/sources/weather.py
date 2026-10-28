"""Open-Meteo hourly forecast (CC BY 4.0). Values are read from the API, never estimated."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .http import fetch_text

BASE_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
ARCHIVE_AFTER = timedelta(days=5)  # the forecast API keeps a few past days; older moments use the archive
HOURLY = "wind_speed_10m,wind_direction_10m,precipitation,relative_humidity_2m"
FIXTURE_FILE = "open_meteo_kaltim.json"
ATTRIBUTION = "Weather data by Open-Meteo.com (CC BY 4.0)"


def summarize(payload: dict, at: datetime) -> dict:
    """Wind and humidity at the last hour <= at, rain over the 24 h before, and the next 6 h of wind."""
    h = payload["hourly"]
    times = [datetime.fromisoformat(t).replace(tzinfo=timezone.utc) for t in h["time"]]
    past = [i for i, t in enumerate(times) if t <= at]
    if not past:
        raise ValueError("weather payload has no hour at or before the requested time")
    i = past[-1]
    rain_idx = [k for k, t in enumerate(times) if at - timedelta(hours=24) < t <= at]
    rain = [h["precipitation"][k] for k in rain_idx if h["precipitation"][k] is not None]
    nxt = [k for k, t in enumerate(times) if at < t <= at + timedelta(hours=6)]
    return {
        "observed_hour": times[i].isoformat(),
        "wind_speed_ms": h["wind_speed_10m"][i],
        "wind_direction_deg": h["wind_direction_10m"][i],
        "relative_humidity_pct": h["relative_humidity_2m"][i],
        "rain_24h_mm": round(sum(rain), 2) if rain else None,
        "rain_24h_hours_covered": len(rain),
        "forecast_6h": [
            {"hour": times[k].isoformat(), "wind_speed_ms": h["wind_speed_10m"][k], "wind_direction_deg": h["wind_direction_10m"][k]}
            for k in nxt
        ],
        "grid_lat": payload.get("latitude"),
        "grid_lon": payload.get("longitude"),
    }


def get_weather(
    lat: float, lon: float, *, at: datetime | None, cache_dir: Path, fixtures_dir: Path, fixture: bool
) -> dict:
    if fixture:
        payload = json.loads((fixtures_dir / FIXTURE_FILE).read_text(encoding="utf-8"))
        if at is None:
            # Fixtures are frozen in time; anchor on the last observed hour of the fixture's past day.
            at = datetime.fromisoformat(payload["hourly"]["time"][23]).replace(tzinfo=timezone.utc)
    else:
        at = at or datetime.now(timezone.utc)
        if datetime.now(timezone.utc) - at > ARCHIVE_AFTER:
            # Evaluation and replay need the weather of a past fire, from the reanalysis archive.
            params = {
                "latitude": f"{lat:.3f}", "longitude": f"{lon:.3f}", "hourly": HOURLY, "wind_speed_unit": "ms", "timezone": "UTC",
                "start_date": (at - timedelta(days=3)).date().isoformat(), "end_date": (at + timedelta(days=1)).date().isoformat(),
            }
            payload = json.loads(fetch_text("open_meteo", "GET", ARCHIVE_URL, cache_dir=cache_dir, cache_key=params, ttl_s=30 * 24 * 3600, params=params))
            return {**summarize(payload, at), "source_api": "archive"}
        params = {
            "latitude": f"{lat:.3f}",
            "longitude": f"{lon:.3f}",
            "hourly": HOURLY,
            "past_days": 3,  # incidents look back 48 h; rain still sums the 24 h before the incident
            "forecast_days": 1,
            "wind_speed_unit": "ms",
            "timezone": "UTC",
        }
        payload = json.loads(
            fetch_text("open_meteo", "GET", BASE_URL, cache_dir=cache_dir, cache_key=params, ttl_s=30 * 60, params=params)
        )
    return summarize(payload, at)

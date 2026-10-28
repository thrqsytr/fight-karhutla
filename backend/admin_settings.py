"""Operator settings, stored in the database and changed from the admin panel.

Each setting has a fixed list of choices or a bounded range, so a mistyped value can never turn into a
runaway schedule or spend. Defaults come from the environment (config.py); a stored value overrides it.
Reading needs the verifier token; changing needs the execution code as well (see main.py).
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from . import db
from .config import Settings

KEY_PREFIX = "setting:"


@dataclass(frozen=True)
class Spec:
    key: str
    group: str
    label: str
    help: str
    default_attr: str | None = None  # Settings attribute that gives the default
    default: int | None = None
    choices: tuple[int, ...] | None = None
    min: int | None = None
    max: int | None = None
    unit: str = ""


SPECS = (
    Spec("firms_interval_minutes", "data", "FIRMS fetch interval",
         "How often new satellite hotspots are fetched for Indonesia. Each fetch is one FIRMS call; enrichment of new incidents follows it.",
         default_attr="firms_poll_minutes", choices=(30, 60, 180, 360, 720, 1440, 2880), unit="min"),
    Spec("weather_per_run", "data", "Weather lookups per fetch",
         "Incidents that get Open-Meteo weather after each fetch. Open-Meteo allows under 10,000 calls a day.",
         default=40, min=0, max=100),
    Spec("osm_tiles_per_run", "data", "OSM tiles per fetch",
         "New OpenStreetMap tiles queried after each fetch. The public Overpass servers are shared and often down.",
         default=12, min=0, max=30),
    Spec("satellite_fresh_hours", "data", "Sentinel-2 reuse window",
         "A stored Sentinel-2 pair is reused this long before the archive is searched again. A pass comes about every 5 days.",
         default=24, min=6, max=168, unit="h"),
    Spec("triage_daily_cap", "triage", "Triage runs per day",
         "Hard limit on triage runs per WITA day, manual and scheduled together. Each run costs about $0.05 of Meta credit.",
         default_attr="triage_daily_cap", min=0, max=100),
    Spec("triage_batch_limit", "triage", "Incidents per selection",
         "How many incidents one confirmed selection may triage.", default_attr="triage_batch_limit", min=1, max=10),
    Spec("triage_budget_per_run", "triage", "Incidents per scheduled pass",
         "How many citizen-reported incidents one scheduled pass may triage.", default_attr="triage_budget_per_run", min=0, max=10),
)
BY_KEY = {s.key: s for s in SPECS}


class SettingError(ValueError):
    pass


def _default(spec: Spec, settings: Settings) -> int:
    return int(getattr(settings, spec.default_attr)) if spec.default_attr else int(spec.default)


def validate(spec: Spec, value) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
        raise SettingError(f"{spec.key} must be a whole number")
    value = int(value)
    if spec.choices and value not in spec.choices:
        raise SettingError(f"{spec.key} must be one of {list(spec.choices)}")
    if spec.min is not None and value < spec.min or spec.max is not None and value > spec.max:
        raise SettingError(f"{spec.key} must be between {spec.min} and {spec.max}")
    return value


def get(settings: Settings, conn, key: str) -> int:
    spec = BY_KEY[key]
    row = conn.execute("SELECT value FROM app_state WHERE key=?", (KEY_PREFIX + key,)).fetchone()
    if row is None:
        return _default(spec, settings)
    try:
        return validate(spec, json.loads(row[0]))
    except (SettingError, ValueError):
        return _default(spec, settings)  # a stored value that no longer fits the spec falls back safely


def value(settings: Settings, key: str) -> int:
    with db.session(settings.db_path) as conn:
        return get(settings, conn, key)


def update(settings: Settings, conn, changes: dict) -> list[str]:
    unknown = [k for k in changes if k not in BY_KEY]
    if unknown:
        raise SettingError(f"unknown settings: {', '.join(unknown)}")
    clean = {k: validate(BY_KEY[k], v) for k, v in changes.items()}
    for k, v in clean.items():
        conn.execute(
            "INSERT INTO app_state (key, value, updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (KEY_PREFIX + k, json.dumps(v), db.utcnow()),
        )
    return sorted(clean)


def describe(settings: Settings, conn) -> list[dict]:
    out = []
    for s in SPECS:
        row = conn.execute("SELECT updated_at FROM app_state WHERE key=?", (KEY_PREFIX + s.key,)).fetchone()
        out.append({"key": s.key, "group": s.group, "label": s.label, "help": s.help, "unit": s.unit,
                    "value": get(settings, conn, s.key), "default": _default(s, settings),
                    "choices": list(s.choices) if s.choices else None, "min": s.min, "max": s.max,
                    "updated_at": row[0] if row else None})
    return out


# The scheduler ticks every few minutes; the fetch itself runs only when the chosen interval has passed.
LAST_FETCH_KEY = "sched:last_fetch"


def fetch_due(settings: Settings, conn, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    row = conn.execute("SELECT value FROM app_state WHERE key=?", (LAST_FETCH_KEY,)).fetchone()
    if row is None:
        return True
    return now - datetime.fromisoformat(json.loads(row[0])) >= timedelta(minutes=get(settings, conn, "firms_interval_minutes"))


def mark_fetch(conn, now: datetime | None = None) -> None:
    at = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO app_state (key, value, updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (LAST_FETCH_KEY, json.dumps(at), db.utcnow()),
    )


def schedule(settings: Settings, conn) -> dict:
    row = conn.execute("SELECT value FROM app_state WHERE key=?", (LAST_FETCH_KEY,)).fetchone()
    last = json.loads(row[0]) if row else None
    interval = get(settings, conn, "firms_interval_minutes")
    nxt = (datetime.fromisoformat(last) + timedelta(minutes=interval)).isoformat(timespec="seconds") if last else None
    return {"last_fetch": last, "next_fetch": nxt, "interval_minutes": interval, "enabled": settings.scheduler_enabled and not settings.fixture_mode}

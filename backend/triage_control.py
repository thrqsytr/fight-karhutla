"""Who may spend triage credit, how much, and when.

Every triage spends Meta Model API credit (about $0.15 measured), so runs happen only on purpose:
a verifier selects incidents and confirms with an execution code, and the scheduler runs on a
daily (default) or hourly schedule over citizen-reported incidents only. A daily cap, checked on the
server, bounds manual and automatic runs together.
"""

import hashlib
import hmac
import json
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from . import admin_settings, db
from .config import Settings

MODES = {"off": None, "daily": timedelta(hours=24), "hourly": timedelta(hours=1)}
DEFAULT_MODE = "daily"
WITA = timezone(timedelta(hours=8))
# Shown before confirming, never billed by us. Median of the five bilingual production runs on 2026-10-29
# ($0.082 to $0.210); the English-only runs before cost about $0.05.
EST_COST_USD = 0.15

# Wrong execution codes are limited per client so the code cannot be guessed by brute force.
CODE_ATTEMPTS = 5
CODE_WINDOW_S = 15 * 60
_failed: dict[str, deque] = defaultdict(deque)


class ControlError(ValueError):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _get(conn, key: str, default=None):
    row = conn.execute("SELECT value FROM app_state WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def _set(conn, key: str, value) -> None:
    conn.execute(
        "INSERT INTO app_state (key, value, updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, json.dumps(value), db.utcnow()),
    )


def check_code(settings: Settings, code: str | None, client: str, now: float | None = None) -> None:
    if not settings.execution_code:
        raise ControlError(503, "execution code is not configured on the server")
    now = now or time.time()
    key = hashlib.sha256(client.encode()).hexdigest()[:16]
    q = _failed[key]
    while q and now - q[0] > CODE_WINDOW_S:
        q.popleft()
    if len(q) >= CODE_ATTEMPTS:
        raise ControlError(429, "too many wrong codes; try again in 15 minutes")
    if not code or not hmac.compare_digest(code.strip(), settings.execution_code):
        q.append(now)
        raise ControlError(403, f"wrong execution code ({CODE_ATTEMPTS - len(q)} attempts left)")


def day_start_utc(now: datetime | None = None) -> datetime:
    """Start of today in WITA, where the operators work, expressed in UTC."""
    local = (now or datetime.now(timezone.utc)).astimezone(WITA)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def usage_today(conn, now: datetime | None = None) -> dict:
    since = day_start_utc(now).isoformat()
    row = conn.execute("SELECT COUNT(*), COALESCE(SUM(cost_usd), 0) FROM agent_runs WHERE started_at >= ?", (since,)).fetchone()
    return {"runs": row[0], "cost_usd": round(row[1], 4), "since": since}


def get_mode(conn) -> str:
    m = _get(conn, "triage_mode", DEFAULT_MODE)
    return m if m in MODES else DEFAULT_MODE


def set_mode(conn, mode: str) -> None:
    if mode not in MODES:
        raise ControlError(422, f"mode must be one of {sorted(MODES)}")
    _set(conn, "triage_mode", mode)


def status(settings: Settings, conn, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    mode = get_mode(conn)
    last = _get(conn, "last_auto_run")
    interval = MODES[mode]
    next_run = None
    if interval is not None:
        next_run = (datetime.fromisoformat(last) + interval).isoformat() if last else "at the next scheduler pass"
    used = usage_today(conn, now)
    cap = admin_settings.get(settings, conn, "triage_daily_cap")
    return {
        "mode": mode, "modes": list(MODES), "last_auto_run": last, "next_auto_run": next_run,
        "auto_scope": "citizen-reported incidents only", "auto_budget_per_run": admin_settings.get(settings, conn, "triage_budget_per_run"),
        "batch_limit": admin_settings.get(settings, conn, "triage_batch_limit"), "daily_cap": cap,
        "today": used, "remaining_today": max(0, cap - used["runs"]),
        "est_cost_per_triage_usd": EST_COST_USD, "last_batch": _get(conn, "last_batch"),
        "code_configured": bool(settings.execution_code), "model_configured": bool(settings.model_api_key),
    }


def validate_batch(settings: Settings, conn, incident_ids: list[str], now: datetime | None = None) -> list[str]:
    ids = list(dict.fromkeys(i.strip() for i in incident_ids if i and i.strip()))
    if not ids:
        raise ControlError(422, "select at least one incident")
    limit = admin_settings.get(settings, conn, "triage_batch_limit")
    if len(ids) > limit:
        raise ControlError(422, f"at most {limit} incidents per execution; {len(ids)} selected")
    known = {r[0] for r in conn.execute(f"SELECT id FROM incidents WHERE id IN ({','.join('?' * len(ids))})", ids)}
    missing = [i for i in ids if i not in known]
    if missing:
        raise ControlError(404, f"unknown incidents: {', '.join(missing[:5])}")
    cap = admin_settings.get(settings, conn, "triage_daily_cap")
    left = cap - usage_today(conn, now)["runs"]
    if len(ids) > left:
        raise ControlError(429, f"daily cap of {cap} triage runs: {max(0, left)} left today, {len(ids)} requested")
    return ids


def record_batch(conn, ids: list[str], source: str) -> None:
    _set(conn, "last_batch", {"incident_ids": ids, "source": source, "started_at": db.utcnow()})


def due_auto_run(conn, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    interval = MODES[get_mode(conn)]
    if interval is None:
        return False
    last = _get(conn, "last_auto_run")
    return last is None or now - datetime.fromisoformat(last) >= interval


def auto_candidates(settings: Settings, conn, now: datetime | None = None) -> list[str]:
    """Citizen-reported incidents not yet triaged, newest first, within the per-run budget and the daily cap."""
    left = admin_settings.get(settings, conn, "triage_daily_cap") - usage_today(conn, now)["runs"]
    n = max(0, min(admin_settings.get(settings, conn, "triage_budget_per_run"), left))
    if n == 0:
        return []
    rows = conn.execute(
        """SELECT i.id FROM incidents i
           WHERE i.reporter_count > 0 AND i.triage_json IS NULL AND i.status != 'triaging'
             AND NOT EXISTS (SELECT 1 FROM agent_runs r WHERE r.incident_id = i.id AND r.outcome IN ('error', 'schema_failed', 'running'))
           ORDER BY i.last_seen DESC, i.id LIMIT ?""",
        (n,),
    ).fetchall()
    return [r[0] for r in rows]


def mark_auto_run(conn, now: datetime | None = None) -> None:
    _set(conn, "last_auto_run", (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"))
